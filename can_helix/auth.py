"""
Authentication and encrypted HTTP client for Helix backend.

This module provides session-based authentication and encrypted communication
with the Helix CAN backend REST API.

Features:
- ECDH key exchange with the device
- AES-256-GCM encrypted HTTP requests/responses
- Session token management (encrypted in headers)
- Automatic re-authentication on session expiry
"""

import base64
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from .crypto import CryptoError, CryptoSession

logger = logging.getLogger(__name__)

# HTTP headers for encrypted communication
HEADER_ENCRYPTED = "X-Encrypted"
HEADER_CLIENT_PUBLIC_KEY = "X-Client-Public-Key"
HEADER_SESSION_TOKEN = "X-Session-Token"
HEADER_ORIGINAL_CONTENT_TYPE = "X-Original-Content-Type"


class AuthError(Exception):
    """Authentication error."""
    pass


@dataclass
class UserInfo:
    """Authenticated user information."""
    id: str
    username: str
    role: str


@dataclass
class SessionInfo:
    """Session information after successful login."""
    token: str
    user: UserInfo
    must_change_password: bool
    expires_in_hours: int


class HelixAuthClient:
    """
    Authenticated and encrypted HTTP client for Helix backend.

    This client handles:
    - Key exchange with the device
    - Encrypted HTTP communication (AES-256-GCM)
    - Session-based authentication
    - Automatic session token management

    Example usage::

        client = HelixAuthClient("http://192.168.7.1:9999")

        # Login (required before other API calls)
        session = client.login("admin", "password123")
        print(f"Logged in as: {session.user.username}")

        # Make authenticated API calls
        state = client.get("/api/can/state")

        # Logout when done
        client.logout()
    """

    def __init__(self, base_url: str,
                 expected_device_fingerprint: str | None = None):
        """
        Initialize the auth client.

        Args:
            base_url: Base URL for REST API (e.g., 'http://192.168.7.1:9999')
        """
        if requests is None:
            raise ImportError(
                "requests library is required. "
                "Install with: pip install requests"
            )

        self._base_url = base_url.rstrip('/')
        self._session = requests.Session()
        self._crypto: CryptoSession | None = None
        self._session_token: str | None = None
        self._device_fingerprint: str | None = None
        self._expected_device_fingerprint = expected_device_fingerprint

    def _ensure_crypto(self) -> CryptoSession:
        """Ensure crypto session is initialized."""
        if self._crypto is None:
            self._init_crypto()
        crypto = self._crypto
        if crypto is None:  # _init_crypto either sets it or raises
            raise AuthError("Crypto session initialization failed")
        return crypto

    def _init_crypto(self) -> None:
        """Initialize cryptographic session by fetching device's public key."""
        logger.debug(f"Fetching device public key from {self._base_url}/api/crypto/public-key")

        try:
            # This endpoint is always unencrypted (bootstrap)
            resp = self._session.get(
                f"{self._base_url}/api/crypto/public-key",
                timeout=5.0
            )
            resp.raise_for_status()
            data = resp.json()

            device_public_key = data['public_key']

            # Compute the fingerprint locally from the key material -
            # never trust a fingerprint the (unauthenticated) server
            # claims about itself, especially when pinning.
            computed = hashlib.sha256(
                base64.b64decode(device_public_key)
            ).hexdigest()
            claimed = data.get('fingerprint')
            if claimed and claimed.lower() != computed:
                logger.warning(
                    "Device-claimed fingerprint does not match its public "
                    "key; using the computed value"
                )
            self._device_fingerprint = computed

            logger.info(
                f"Device fingerprint: {self._device_fingerprint[:16]}..."
            )

            if self._expected_device_fingerprint is not None:
                expected = self._expected_device_fingerprint.strip().lower()
                if expected != computed:
                    raise AuthError(
                        "Device fingerprint mismatch: expected "
                        f"{expected[:16]}..., got {computed[:16]}... "
                        "(wrong device, or a man-in-the-middle). "
                        "No credentials were sent."
                    )

            self._crypto = CryptoSession(device_public_key)

        except AuthError:
            raise
        except requests.RequestException as e:
            raise AuthError(f"Failed to fetch device public key: {e}") from e
        except KeyError as e:
            raise AuthError(f"Invalid public key response: missing {e}") from e
        except Exception as e:
            raise AuthError(f"Failed to initialize crypto: {e}") from e

    def _encrypted_request(
        self,
        method: str,
        path: str,
        data: dict | None = None,
        timeout: float = 5.0,
    ) -> tuple[int, Any]:
        """
        Make an encrypted HTTP request.

        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., '/api/auth/login')
            data: JSON data to send (will be encrypted)
            timeout: Request timeout in seconds

        Returns:
            Tuple of (status_code, response_data)
        """
        crypto = self._ensure_crypto()
        url = f"{self._base_url}{path}"

        # Build headers
        headers = {
            HEADER_ENCRYPTED: "1",
            HEADER_CLIENT_PUBLIC_KEY: crypto.client_public_key_base64,
        }

        # Add session token if we have one (encrypted)
        if self._session_token:
            encrypted_token = crypto.encrypt_http(self._session_token.encode('utf-8'))
            headers[HEADER_SESSION_TOKEN] = base64.b64encode(encrypted_token).decode('ascii')

        # Prepare request body
        if data is not None:
            plaintext = json.dumps(data).encode('utf-8')
            encrypted_body = crypto.encrypt_http(plaintext)
            headers[HEADER_ORIGINAL_CONTENT_TYPE] = "application/json"
            body = encrypted_body
            headers["Content-Type"] = "application/octet-stream"
        else:
            body = None

        logger.debug(f"Encrypted {method} {url}")

        # Make request
        try:
            resp = self._session.request(
                method=method,
                url=url,
                data=body,
                headers=headers,
                timeout=timeout,
            )
        except requests.RequestException as e:
            raise AuthError(f"Request failed: {e}") from e

        # Decrypt response if encrypted
        if resp.headers.get(HEADER_ENCRYPTED) == "1":
            try:
                decrypted = crypto.decrypt_http(resp.content)
                response_data = json.loads(decrypted.decode('utf-8'))
            except CryptoError as e:
                raise AuthError(f"Failed to decrypt response: {e}") from e
            except json.JSONDecodeError as e:
                raise AuthError(f"Invalid JSON in response: {e}") from e
        else:
            # Response not encrypted (error or unencrypted endpoint)
            try:
                response_data = resp.json()
            except json.JSONDecodeError:
                response_data = {"raw": resp.text}

        return resp.status_code, response_data

    def login(
        self,
        username: str,
        password: str,
        remember_me: bool = False
    ) -> SessionInfo:
        """
        Authenticate with the Helix backend.

        Args:
            username: Username
            password: Password
            remember_me: If True, session lasts 7 days instead of 24 hours

        Returns:
            SessionInfo with user info and session details

        Raises:
            AuthError: If authentication fails
        """
        status, data = self._encrypted_request(
            "POST",
            "/api/auth/login",
            data={
                "username": username,
                "password": password,
                "remember_me": remember_me,
            }
        )

        if status != 200 or not data.get("success"):
            error = data.get("error", "Authentication failed")
            raise AuthError(error)

        # Store session token
        self._session_token = data.get("session_token")

        user_data = data.get("user", {})
        user = UserInfo(
            id=user_data.get("id", ""),
            username=user_data.get("username", username),
            role=user_data.get("role", "user"),
        )

        session = SessionInfo(
            token=self._session_token,
            user=user,
            must_change_password=data.get("must_change_password", False),
            expires_in_hours=data.get("expires_in_hours", 24),
        )

        logger.info(f"Logged in as {user.username} (role: {user.role})")
        return session

    def logout(self) -> None:
        """Log out and invalidate the session."""
        if self._session_token:
            try:
                self._encrypted_request("POST", "/api/auth/logout")
            except AuthError as e:
                logger.warning(f"Logout request failed: {e}")

        self._session_token = None
        logger.info("Logged out")

    def get_session(self) -> SessionInfo | None:
        """
        Check current session status.

        Returns:
            SessionInfo if authenticated, None otherwise
        """
        status, data = self._encrypted_request("GET", "/api/auth/session")

        if status != 200 or not data.get("authenticated"):
            return None

        user_data = data.get("user", {})
        user = UserInfo(
            id=user_data.get("id", ""),
            username=user_data.get("username", ""),
            role=user_data.get("role", "user"),
        )

        return SessionInfo(
            token=self._session_token or "",
            user=user,
            must_change_password=False,
            expires_in_hours=24,
        )

    def change_password(self, current_password: str, new_password: str) -> None:
        """
        Change the current user's password.

        Args:
            current_password: Current password
            new_password: New password

        Raises:
            AuthError: If password change fails
        """
        status, data = self._encrypted_request(
            "POST",
            "/api/auth/change-password",
            data={
                "current_password": current_password,
                "new_password": new_password,
            }
        )

        if status != 200 or not data.get("success"):
            error = data.get("error", "Password change failed")
            raise AuthError(error)

        logger.info("Password changed successfully")

    def get_ws_ticket(self) -> str:
        """
        Mint a short-lived, single-use WebSocket authentication ticket.

        The Helix WebSocket handshake requires this ticket (passed as the
        ``ticket`` query parameter). Tickets are valid for ~30 s and
        consumed on first use, so fetch one immediately before each new
        WebSocket connection.

        Returns:
            The ticket string.

        Raises:
            AuthError: If the request fails or the response is malformed.
        """
        resp = self.get("/api/auth/ws-ticket")
        if not isinstance(resp, dict) or "ticket" not in resp:
            raise AuthError(f"Malformed ws-ticket response: {resp!r}")
        return str(resp["ticket"])

    def refresh_session(self) -> bool:
        """
        Refresh the current session to extend its lifetime.

        Returns:
            True if session was refreshed, False otherwise
        """
        status, data = self._encrypted_request("POST", "/api/auth/refresh")

        if status == 200 and data.get("success"):
            logger.debug("Session refreshed")
            return True
        return False

    @property
    def is_authenticated(self) -> bool:
        """Check if client has a session token."""
        return self._session_token is not None

    @property
    def crypto_session(self) -> CryptoSession | None:
        """Get the crypto session (for WebSocket encryption)."""
        return self._crypto

    @property
    def device_fingerprint(self) -> str | None:
        """Get the device's key fingerprint."""
        return self._device_fingerprint

    def get(self, path: str, timeout: float = 5.0) -> Any:
        """
        Make an authenticated encrypted GET request.

        Args:
            path: API path (e.g., '/api/can/state')
            timeout: Request timeout

        Returns:
            Response data (decrypted JSON)

        Raises:
            AuthError: If request fails
        """
        status, data = self._encrypted_request("GET", path, timeout=timeout)

        if status >= 400:
            error = data.get("error", f"Request failed with status {status}")
            raise AuthError(error)

        # Unwrap 'data' field if present
        if isinstance(data, dict) and 'data' in data:
            return data['data']
        return data

    def post(self, path: str, data: dict | None = None, timeout: float = 5.0) -> Any:
        """
        Make an authenticated encrypted POST request.

        Args:
            path: API path
            data: JSON data to send
            timeout: Request timeout

        Returns:
            Response data (decrypted JSON)

        Raises:
            AuthError: If request fails
        """
        status, resp_data = self._encrypted_request("POST", path, data=data, timeout=timeout)

        if status >= 400:
            error = resp_data.get("error", f"Request failed with status {status}")
            raise AuthError(error)

        return resp_data

    def close(self) -> None:
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
