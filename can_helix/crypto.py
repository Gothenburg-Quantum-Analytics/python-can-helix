"""
Cryptographic utilities for Helix backend communication.

This module provides ECDH key exchange and encryption/decryption for
secure communication with the Helix CAN backend.

Encryption schemes:
- AES-256-GCM for HTTP requests (browser compatibility)
- ChaCha20-Poly1305 for WebSocket (faster on ARM without crypto extensions)

Both use the same message format: [Nonce 12B][Ciphertext][AuthTag 16B]
"""

import base64
import hashlib
import logging
import os
import struct
import threading

try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
except ImportError:
    raise ImportError(
        "cryptography library is required for encrypted communication. "
        "Install with: pip install cryptography"
    )

logger = logging.getLogger(__name__)

# Constants
NONCE_SIZE = 12  # 96 bits for GCM/ChaCha20
AUTH_TAG_SIZE = 16  # 128 bits
KEY_SIZE = 32  # 256 bits


class CryptoError(Exception):
    """Cryptographic operation error."""
    pass


class KeyPair:
    """
    ECDH P-256 key pair for secure key exchange.

    Generates a new random key pair and provides methods for:
    - Exporting the public key
    - Deriving shared secrets with remote public keys
    """

    def __init__(self):
        """Generate a new random P-256 key pair."""
        self._private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
        self._public_key = self._private_key.public_key()

    def public_key_bytes(self) -> bytes:
        """
        Get the public key as uncompressed SEC1 bytes (65 bytes for P-256).

        Format: 0x04 || X (32 bytes) || Y (32 bytes)
        """
        return bytes(self._public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint
        ))

    def public_key_base64(self) -> str:
        """Get the public key as base64-encoded string."""
        return base64.b64encode(self.public_key_bytes()).decode('ascii')

    def fingerprint(self) -> str:
        """Get the SHA-256 fingerprint of the public key (hex string)."""
        return hashlib.sha256(self.public_key_bytes()).hexdigest()

    def derive_shared_secret(self, remote_public_key_bytes: bytes) -> bytes:
        """
        Derive a shared secret using ECDH with the remote public key.

        Args:
            remote_public_key_bytes: Remote party's public key (uncompressed SEC1 format)

        Returns:
            32-byte shared secret
        """
        remote_public_key = ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), remote_public_key_bytes
        )
        shared_key = self._private_key.exchange(ec.ECDH(), remote_public_key)
        return bytes(shared_key)

    def derive_aes_key(self, remote_public_key_bytes: bytes) -> bytes:
        """
        Derive an AES-256-GCM key for HTTP encryption.

        Uses HKDF-SHA256 with info string "helix-aes-gcm-key".
        """
        shared_secret = self.derive_shared_secret(remote_public_key_bytes)
        return _hkdf_derive(shared_secret, b"helix-aes-gcm-key")

    def derive_chacha_key(self, remote_public_key_bytes: bytes) -> bytes:
        """
        Derive a ChaCha20-Poly1305 key for WebSocket encryption.

        Uses HKDF-SHA256 with info string "helix-chacha20-key".
        """
        shared_secret = self.derive_shared_secret(remote_public_key_bytes)
        return _hkdf_derive(shared_secret, b"helix-chacha20-key")


def _hkdf_derive(shared_secret: bytes, info: bytes) -> bytes:
    """Derive a 32-byte key using HKDF-SHA256."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_SIZE,
        salt=b'\x00' * 32,  # Fixed zero salt (matches Rust implementation)
        info=info,
        backend=default_backend()
    )
    return hkdf.derive(shared_secret)


def import_public_key_base64(base64_str: str) -> bytes:
    """
    Import a public key from base64-encoded string.

    Args:
        base64_str: Base64-encoded uncompressed SEC1 public key

    Returns:
        Raw public key bytes (65 bytes)
    """
    return base64.b64decode(base64_str)


class AESCipher:
    """
    AES-256-GCM cipher for HTTP encryption.

    Uses counter-based nonce generation with a random prefix for efficiency
    and safety across session restarts.

    Message format: [Nonce 12B][Ciphertext][AuthTag 16B]
    """

    def __init__(self, key: bytes):
        """
        Initialize cipher with a 32-byte key.

        Args:
            key: 32-byte AES-256 key
        """
        if len(key) != KEY_SIZE:
            raise CryptoError(f"Key must be {KEY_SIZE} bytes, got {len(key)}")

        self._cipher = AESGCM(key)
        self._nonce_prefix = os.urandom(4)  # Random 4-byte prefix
        self._nonce_counter = 0
        self._lock = threading.Lock()

    def _next_nonce(self) -> bytes:
        """Generate the next nonce using counter. Thread-safe."""
        with self._lock:
            counter = self._nonce_counter
            self._nonce_counter += 1

        # Format: [4-byte random prefix][8-byte counter (little-endian)]
        return self._nonce_prefix + struct.pack('<Q', counter)

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt data.

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        nonce = self._next_nonce()
        ciphertext = self._cipher.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt(self, encrypted_data: bytes) -> bytes:
        """
        Decrypt data.

        Args:
            encrypted_data: Data to decrypt [Nonce 12B][Ciphertext][AuthTag 16B]

        Returns:
            Decrypted plaintext

        Raises:
            CryptoError: If decryption fails
        """
        if len(encrypted_data) < NONCE_SIZE + AUTH_TAG_SIZE:
            raise CryptoError("Encrypted data too short")

        nonce = encrypted_data[:NONCE_SIZE]
        ciphertext = encrypted_data[NONCE_SIZE:]

        try:
            return self._cipher.decrypt(nonce, ciphertext, None)
        except Exception as e:
            raise CryptoError(f"Decryption failed: {e}") from e


class ChaCha20Cipher:
    """
    ChaCha20-Poly1305 cipher for WebSocket encryption.

    Uses counter-based nonce generation with a random prefix for efficiency
    and safety across session restarts.

    Message format: [Nonce 12B][Ciphertext][AuthTag 16B]
    """

    def __init__(self, key: bytes):
        """
        Initialize cipher with a 32-byte key.

        Args:
            key: 32-byte ChaCha20 key
        """
        if len(key) != KEY_SIZE:
            raise CryptoError(f"Key must be {KEY_SIZE} bytes, got {len(key)}")

        self._cipher = ChaCha20Poly1305(key)
        self._nonce_prefix = os.urandom(4)  # Random 4-byte prefix
        self._nonce_counter = 0
        self._lock = threading.Lock()

    def _next_nonce(self) -> bytes:
        """Generate the next nonce using counter. Thread-safe."""
        with self._lock:
            counter = self._nonce_counter
            self._nonce_counter += 1

        # Format: [4-byte random prefix][8-byte counter (little-endian)]
        return self._nonce_prefix + struct.pack('<Q', counter)

    def encrypt(self, plaintext: bytes) -> bytes:
        """
        Encrypt data.

        Args:
            plaintext: Data to encrypt

        Returns:
            Encrypted data: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        nonce = self._next_nonce()
        ciphertext = self._cipher.encrypt(nonce, plaintext, None)
        return nonce + ciphertext

    def decrypt(self, encrypted_data: bytes) -> bytes:
        """
        Decrypt data.

        Args:
            encrypted_data: Data to decrypt [Nonce 12B][Ciphertext][AuthTag 16B]

        Returns:
            Decrypted plaintext

        Raises:
            CryptoError: If decryption fails
        """
        if len(encrypted_data) < NONCE_SIZE + AUTH_TAG_SIZE:
            raise CryptoError("Encrypted data too short")

        nonce = encrypted_data[:NONCE_SIZE]
        ciphertext = encrypted_data[NONCE_SIZE:]

        try:
            return self._cipher.decrypt(nonce, ciphertext, None)
        except Exception as e:
            raise CryptoError(f"Decryption failed: {e}") from e


class CryptoSession:
    """
    Crypto session for secure communication with Helix backend.

    Manages key exchange and provides separate ciphers for:
    - HTTP requests (AES-256-GCM)
    - WebSocket messages (ChaCha20-Poly1305)
    """

    def __init__(self, device_public_key_base64: str):
        """
        Initialize crypto session with the device's public key.

        Args:
            device_public_key_base64: Device's public key from /api/crypto/public-key
        """
        # Generate our keypair
        self._keypair = KeyPair()

        # Import device's public key
        self._device_public_key = import_public_key_base64(device_public_key_base64)

        # Derive encryption keys
        aes_key = self._keypair.derive_aes_key(self._device_public_key)
        chacha_key = self._keypair.derive_chacha_key(self._device_public_key)

        # Create ciphers
        self._aes_cipher = AESCipher(aes_key)
        self._chacha_cipher = ChaCha20Cipher(chacha_key)
        # ChaCha20-Poly1305 over the HTTP (AES) session key — for streamed responses the device seals with
        # ChaCha20 (X-Cipher: chacha20, e.g. /api/log/select_db_binary) because it's ~2-3x faster than
        # software AES-GCM on the device's Cortex-M7. Same 32-byte key as the AES/HTTP path, different cipher.
        self._http_chacha = ChaCha20Cipher(aes_key)

        logger.debug(
            f"Crypto session initialized (client fingerprint: {self._keypair.fingerprint()[:16]}...)"
        )

    @property
    def client_public_key_base64(self) -> str:
        """Get the client's public key for sending to server."""
        return self._keypair.public_key_base64()

    @property
    def client_fingerprint(self) -> str:
        """Get the client's key fingerprint."""
        return self._keypair.fingerprint()

    def encrypt_http(self, plaintext: bytes) -> bytes:
        """
        Encrypt data for HTTP request using AES-256-GCM.

        Returns: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        return self._aes_cipher.encrypt(plaintext)

    def decrypt_http(self, ciphertext: bytes) -> bytes:
        """
        Decrypt HTTP response using AES-256-GCM.

        Input format: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        return self._aes_cipher.decrypt(ciphertext)

    def decrypt_http_chacha(self, ciphertext: bytes) -> bytes:
        """
        Decrypt a streamed HTTP response sealed with ChaCha20-Poly1305 over the HTTP (AES) session key.

        The device sets `X-Cipher: chacha20` on such responses (e.g. /api/log/select_db_binary) because
        software AES-GCM is ~2-3x slower than ChaCha20-Poly1305 on its Cortex-M7. Same key as decrypt_http,
        different cipher. Input format: [Nonce 12B][Ciphertext][AuthTag 16B].
        """
        return self._http_chacha.decrypt(ciphertext)

    def encrypt_ws(self, plaintext: bytes) -> bytes:
        """
        Encrypt data for WebSocket using ChaCha20-Poly1305.

        Returns: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        return self._chacha_cipher.encrypt(plaintext)

    def decrypt_ws(self, ciphertext: bytes) -> bytes:
        """
        Decrypt WebSocket message using ChaCha20-Poly1305.

        Input format: [Nonce 12B][Ciphertext][AuthTag 16B]
        """
        return self._chacha_cipher.decrypt(ciphertext)
