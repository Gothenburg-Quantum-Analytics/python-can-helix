"""
Helix WebSocket CAN Bus interface for python-can.

This module provides the main Bus class that implements the python-can
BusABC interface for communication with Helix CAN backend via WebSocket.

Supports authenticated and encrypted communication with the Helix backend.
"""

import logging
import queue
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional, cast

if TYPE_CHECKING:
    from .session import HelixSession

import can
from can import BusABC, BusState, CanProtocol, Message
from can.typechecking import CanFilter

try:
    import websocket
except ImportError:
    websocket = None  # type: ignore[assignment]

try:
    import requests
except ImportError:
    requests = None  # type: ignore[assignment]

from . import protocol
from .auth import AuthError, HelixAuthClient
from .connection import HelixConnectionManager

logger = logging.getLogger(__name__)


class HelixRestClient:
    """
    REST API client for Helix backend configuration.

    This client communicates with the Helix backend's REST API (default port 9999)
    to configure CAN channels, bitrate, and bus state.

    Supports both encrypted (authenticated) and legacy (unencrypted) modes.
    """

    def __init__(
        self,
        base_url: str,
        username: str | None = None,
        password: str | None = None,
        expected_device_fingerprint: str | None = None,
    ):
        """
        Initialize REST client.

        Args:
            base_url: Base URL for REST API (e.g., 'http://192.168.1.100:9999')
            username: Username for authentication (enables encryption)
            password: Password for authentication
            expected_device_fingerprint: If set, refuse to authenticate
                unless the device's public-key fingerprint matches (pinning)
        """
        if requests is None:
            raise ImportError(
                "requests library is required for REST API features. "
                "Install with: pip install requests"
            )
        self._base_url = base_url.rstrip('/')
        self._username = username
        self._password = password
        self._expected_device_fingerprint = expected_device_fingerprint
        self._auth_client: HelixAuthClient | None = None

        # Initialize auth client if credentials provided
        if username and password:
            self._init_auth(username, password)

    def _init_auth(self, username: str, password: str) -> None:
        """Initialize authenticated client and login."""
        try:
            self._auth_client = HelixAuthClient(
                self._base_url,
                expected_device_fingerprint=self._expected_device_fingerprint,
            )
            self._auth_client.login(username, password)
            logger.info("Authenticated with Helix backend")
        except AuthError as e:
            raise can.CanInitializationError(
                f"Authentication failed: {e}"
            ) from e

    @property
    def device_fingerprint(self) -> str | None:
        """The connected device's public-key fingerprint (hex), if known."""
        if self._auth_client:
            return self._auth_client.device_fingerprint
        return None

    def _get(self, path: str) -> dict[str, Any]:
        """Make GET request (encrypted if authenticated)."""
        # Use /api/ prefix for new API
        api_path = f"/api{path}" if not path.startswith('/api') else path

        if self._auth_client:
            try:
                return cast(dict[str, Any], self._auth_client.get(api_path))
            except AuthError as e:
                raise can.CanOperationError(f"REST API error: {e}") from e
        else:
            # Legacy unencrypted mode
            url = f"{self._base_url}{api_path}"
            logger.debug(f"REST GET: {url}")
            try:
                session = requests.Session()
                resp = session.get(url, timeout=5.0)
                resp.raise_for_status()
                result = resp.json()
                if isinstance(result, dict) and 'data' in result:
                    return cast(dict[str, Any], result['data'])
                return cast(dict[str, Any], result)
            except requests.RequestException as e:
                raise can.CanOperationError(f"REST API error: {e}") from e

    def _post(self, path: str, data: dict | None = None) -> dict[str, Any]:
        """Make POST request (encrypted if authenticated)."""
        # Use /api/ prefix for new API
        api_path = f"/api{path}" if not path.startswith('/api') else path

        if self._auth_client:
            try:
                return cast(dict[str, Any], self._auth_client.post(api_path, data=data))
            except AuthError as e:
                raise can.CanOperationError(f"REST API error: {e}") from e
        else:
            # Legacy unencrypted mode
            url = f"{self._base_url}{api_path}"
            logger.debug(f"REST POST: {url} data={data}")
            try:
                session = requests.Session()
                resp = session.post(url, json=data or {}, timeout=5.0)
                resp.raise_for_status()
                return cast(dict[str, Any], resp.json())
            except requests.RequestException as e:
                raise can.CanOperationError(f"REST API error: {e}") from e

    @property
    def crypto_session(self):
        """Get the crypto session for WebSocket encryption."""
        if self._auth_client:
            return self._auth_client.crypto_session
        return None

    def get_state(self) -> dict[str, Any]:
        """Get CAN state for all channels."""
        return self._get('/can/state')

    def get_channel_state(self, channel: int) -> dict[str, Any]:
        """Get state for a specific channel."""
        state = self.get_state()
        channel_key = f'can{channel}'
        if channel_key not in state:
            raise can.CanOperationError(f"Channel {channel} not found in state")
        return cast(dict[str, Any], state[channel_key])

    def open_channel(self, channel: int, fd: bool = False) -> dict[str, Any]:
        """Open a CAN channel."""
        return self._post(f'/can/channels/{channel}/open', {'fd': fd})

    def close_channel(self, channel: int) -> dict[str, Any]:
        """Close a CAN channel."""
        return self._get(f'/can/channels/{channel}/close')

    def bus_on(self, channel: int) -> dict[str, Any]:
        """Put CAN channel on bus."""
        return self._get(f'/can/channels/{channel}/on_bus')

    def bus_off(self, channel: int) -> dict[str, Any]:
        """Take CAN channel off bus."""
        return self._get(f'/can/channels/{channel}/off_bus')

    def set_bitrate(
        self,
        channel: int,
        bitrate: int,
        *,
        data_bitrate: int | None = None,
        sample_point: float | None = None,
        data_sample_point: float | None = None,
        fd: bool = False,
        silent: bool = False,
        **kwargs
    ) -> dict[str, Any]:
        """
        Set CAN bitrate/timing parameters.

        Gets current channel state first and merges new values, since the
        backend requires all timing parameters to be specified.

        Args:
            channel: CAN channel number
            bitrate: Arbitration/nominal bitrate in bit/s
            data_bitrate: Data bitrate for CAN FD in bit/s (optional)
            sample_point: Sample point in percent (optional)
            data_sample_point: Data phase sample point for FD (optional)
            fd: Enable CAN FD mode
            silent: Enable silent/listen-only mode
            **kwargs: Additional timing params (tq, prop_seg, phase_seg1, etc.)

        Returns:
            API response dict
        """
        # Get current state to use as defaults (API requires all params)
        try:
            current = self.get_channel_state(channel)
        except can.CanOperationError:
            current = {}

        def get_val(key: str, default: Any = 0) -> Any:
            val = current.get(key, default)
            if isinstance(val, dict) and 'current' in val:
                return val['current']
            return val

        sp = sample_point if sample_point is not None else get_val(
            'sample_point', 0.875)
        dsp = data_sample_point if data_sample_point is not None else get_val(
            'dsample_point', 0.8)
        db = data_bitrate if data_bitrate is not None else get_val(
            'dbaud', bitrate)

        data = {
            'baud': bitrate,
            'can_fd': fd,
            'silent': silent,
            'berr_reporting': get_val('berr_reporting', False),
            'restart_ms': get_val('restart_ms', 0),
            'sample_point': sp,
            'tq': get_val('tq', 8),
            'prop_seg': get_val('prop_seg', 52),
            'phase_seg1': get_val('phase_seg1', 52),
            'phase_seg2': get_val('phase_seg2', 15),
            'sjw': get_val('sjw', 7),
            'brp': get_val('brp', 1),
            'dbaud': db,
            'dsample_point': dsp,
            'dtq': get_val('dtq', 8),
            'dprop_seg': get_val('dprop_seg', 5),
            'dphase_seg1': get_val('dphase_seg1', 6),
            'dphase_seg2': get_val('dphase_seg2', 3),
            'dsjw': get_val('dsjw', 1),
            'dbrp': get_val('dbrp', 1),
        }

        timing_keys = [
            'tq', 'prop_seg', 'phase_seg1', 'phase_seg2', 'sjw', 'brp',
            'dtq', 'dprop_seg', 'dphase_seg1', 'dphase_seg2', 'dsjw', 'dbrp',
            'berr_reporting', 'restart_ms'
        ]
        for key in timing_keys:
            if key in kwargs:
                data[key] = kwargs[key]

        return self._post(f'/can/channels/{channel}/baud', data)

    def get_ws_ticket(self) -> str:
        """
        Mint a single-use WebSocket authentication ticket.

        Requires authenticated mode (username/password). Tickets are
        consumed on first use and valid for ~30 s — fetch one immediately
        before each WebSocket connection.

        Raises:
            can.CanOperationError: If not authenticated or the request fails.
        """
        if not self._auth_client:
            raise can.CanOperationError(
                "WebSocket tickets require authentication. "
                "Create the client with username= and password= "
                "(or use a HelixSession)."
            )
        try:
            return self._auth_client.get_ws_ticket()
        except AuthError as e:
            raise can.CanOperationError(f"Failed to fetch WS ticket: {e}") from e

    def close(self):
        """Close the REST session."""
        if self._auth_client:
            try:
                self._auth_client.logout()
            except Exception:
                pass
            self._auth_client.close()
            self._auth_client = None


class HelixBus(BusABC):
    """
    python-can interface for Helix CAN backend WebSocket.

    This class implements the BusABC interface to provide CAN communication
    over WebSocket with the Helix CAN backend server. Multiple HelixBus
    instances for the same host share a single WebSocket connection via
    the HelixConnectionManager singleton.

    Architecture::

        ┌──────────────────────────────────────────────────────────────┐
        │                    Helix Backend                             │
        │                  WebSocket :8080                             │
        └──────────────────────┬───────────────────────────────────────┘
                               │
                               │  SINGLE WebSocket connection
                               │  (shared via HelixConnectionManager)
                               │
        ┌──────────────────────▼───────────────────────────────────────┐
        │           HelixConnectionManager (singleton per host)        │
        │                                                              │
        │   Routes messages by channel number to subscribed buses      │
        └──────┬─────────────────────────────────┬─────────────────────┘
               │                                 │
          ┌────▼────┐                       ┌────▼────┐
          │HelixBus │                       │HelixBus │
          │  ch=1   │                       │  ch=2   │
          └─────────┘                       └─────────┘

    Connection Handling:
        Like other python-can interfaces, this interface does NOT
        automatically reconnect when the connection is lost.

    Example usage::

        import can

        # Create buses for different channels - they share ONE WebSocket!
        bus1 = can.Bus(interface='helix', host='192.168.7.1', channel=1)
        bus2 = can.Bus(interface='helix', host='192.168.7.1', channel=2)

        # Each bus only sees messages from its channel
        msg1 = bus1.recv()  # Only channel 1 messages
        msg2 = bus2.recv()  # Only channel 2 messages

        # Cleanup - WebSocket closes when last bus shuts down
        bus1.shutdown()
        bus2.shutdown()

    Args:
        channel: CAN channel number to use (1-255), defaults to 1
        host: Backend hostname or IP address (default: '192.168.7.1')
        username: Username for authentication (enables encryption)
        password: Password for authentication
        can_filters: Optional sequence of CAN filters
        fd: If True, use CAN FD mode
        bitrate: CAN bitrate in bit/s (optional, configures via REST API)
        data_bitrate: CAN FD data bitrate in bit/s (optional)
        sample_point: Sample point in percent (optional)
        data_sample_point: Data phase sample point in percent (optional)
        ws_port: Port for WebSocket connection (default: 8080)
        rest_port: Port for REST API (default: 9999)
        recv_queue_size: Max receive queue size (default: 8192)
        batch: Opt into device-side CAN frame batching (default: False).
            The device coalesces frames for up to ~2 ms and packs several
            into one sealed WebSocket message, greatly reducing per-frame
            crypto and transport overhead on busy buses (recommended for
            logging/monitoring). Leave off for request/response traffic
            where the lowest per-frame latency matters. Requires a Helix
            backend with batching support; older backends ignore the
            option and deliver single frames.

    Raises:
        can.CanInitializationError: If connection to backend fails
        can.CanOperationError: If send/recv fails due to lost connection
    """

    RECV_LOGGING_LEVEL = 9

    def __init__(
        self,
        channel: int | None = None,  # python-can passes this positionally
        can_filters: Sequence[CanFilter] | None = None,
        fd: bool = False,
        bitrate: int | None = None,
        data_bitrate: int | None = None,
        sample_point: float | None = None,
        data_sample_point: float | None = None,
        ws_port: int = 8080,
        batch: bool = False,
        recv_queue_size: int = 8192,
        rest_port: int = 9999,
        host: str = '192.168.7.1',
        session: Optional["HelixSession"] = None,
        **kwargs
    ):
        if websocket is None:
            raise ImportError(
                "websocket-client is required for HelixBus. "
                "Install with: pip install websocket-client"
            )

        # Credentials never travel through the bus config (python-can
        # logs that config at DEBUG level). Authentication happens in
        # HelixSession, created before the bus.
        if 'username' in kwargs or 'password' in kwargs:
            raise can.CanInitializationError(
                "username/password bus arguments were removed. "
                "Authenticate first, then pass the session:\n"
                "    session = HelixSession(host, username=..., password=...)\n"
                "    bus = can.Bus(interface='helix', channel=1, session=session)"
            )

        # channel is the CAN channel number (1-based indexing)
        can_channel = channel if channel is not None else 1

        self._session = session
        if session is not None:
            if host != '192.168.7.1' and host != session.host:
                logger.warning(
                    f"host={host!r} differs from session host "
                    f"{session.host!r}; using the session's host"
                )
            host = session.host
            rest_port = session.rest_port

        self._host = host
        self._ws_port = ws_port
        self._can_channel = can_channel
        self._fd = fd
        self._bitrate = bitrate
        self._data_bitrate = data_bitrate

        # REST API client (adopted from the HelixSession when given;
        # otherwise an unauthenticated client for legacy backends)
        self._rest_url = f"http://{host}:{rest_port}"
        self._rest_client: HelixRestClient | None = None
        self._owns_rest_client = session is None
        if session is not None:
            self._rest_client = session.rest_client

        # Build channel info string
        self.channel_info = f"helix:{host}:ch{can_channel}"

        # Message receive queue (fed by connection manager)
        self._recv_queue: queue.Queue[Message] = queue.Queue(maxsize=recv_queue_size)

        # Shutdown flag
        self._shutdown_flag = threading.Event()

        # Initialize parent class
        super().__init__(
            channel=f"ws://{host}:{ws_port}",
            can_filters=can_filters,
            **kwargs
        )


        # Configure bitrate if specified (requires REST API)
        if bitrate is not None:
            try:
                self._ensure_rest_client().set_bitrate(
                    channel=can_channel,
                    bitrate=bitrate,
                    data_bitrate=data_bitrate,
                    sample_point=sample_point,
                    data_sample_point=data_sample_point,
                    fd=fd,
                )
                db_str = f", data: {data_bitrate} bit/s" if data_bitrate else ""
                logger.info(f"Configured bitrate: {bitrate} bit/s{db_str}")
            except Exception as e:
                logger.warning(f"Could not configure bitrate via REST API: {e}")

        # Get crypto session from REST client (if authenticated)
        crypto_session = None
        ticket_provider = None
        if self._rest_client and self._rest_client.crypto_session:
            crypto_session = self._rest_client.crypto_session
            # The backend requires a fresh single-use ticket per WS
            # connection. Hand the manager a closure that mints one at
            # connect time rather than fetching eagerly here — the
            # manager is a singleton and may already be connected.
            rest_client = self._rest_client
            ticket_provider = rest_client.get_ws_ticket

        # Get shared connection manager and subscribe
        self._connection_manager = HelixConnectionManager.get_instance(
            host,
            ws_port,
            crypto_session=crypto_session,
            ticket_provider=ticket_provider,
            batch=batch,
        )
        self._connection_manager.acquire()
        self._connection_manager.subscribe(
            channel=can_channel,
            recv_queue=self._recv_queue,
            bus_ref=self,
        )

        logger.info(f"HelixBus initialized: {self.channel_info}")

    def _ensure_rest_client(self) -> HelixRestClient:
        """Ensure REST client is initialized and return it."""
        if self._rest_client is None:
            if self._session is not None:
                raise can.CanOperationError(
                    "The HelixSession backing this bus has been closed"
                )
            # No session: unauthenticated client (legacy backends only)
            self._rest_client = HelixRestClient(self._rest_url)
        return self._rest_client

    @property
    def protocol(self) -> CanProtocol:
        """Return the CAN protocol type."""
        return CanProtocol.CAN_FD if self._fd else CanProtocol.CAN_20

    @property
    def state(self) -> BusState:
        """Return the current state of the CAN bus."""
        try:
            client = self._ensure_rest_client()
            channel_state = client.get_channel_state(self._can_channel)

            is_open = channel_state.get('open', False)
            is_bus_on = channel_state.get('bus_on', False)

            if is_open and is_bus_on:
                return BusState.ACTIVE
            elif is_open:
                return BusState.PASSIVE
            return BusState.ERROR
        except Exception as e:
            logger.warning(f"Could not get bus state: {e}")
            # If connected to WebSocket, assume active
            if self._connection_manager.is_connected:
                return BusState.ACTIVE
            return BusState.ERROR

    @state.setter
    def state(self, new_state: BusState) -> None:
        raise NotImplementedError(
            "Bus state cannot be assigned; use bus_on() / bus_off()"
        )

    def _recv_internal(self, timeout: float | None) -> tuple[Message | None, bool]:
        """Internal receive implementation."""
        if self._shutdown_flag.is_set():
            return None, False

        if not self._connection_manager.is_connected:
            raise can.CanOperationError(
                "Connection to backend lost. "
                "Create a new Bus instance to reconnect."
            )

        try:
            msg = self._recv_queue.get(timeout=timeout)
            return msg, False
        except queue.Empty:
            if not self._connection_manager.is_connected and not self._shutdown_flag.is_set():
                raise can.CanOperationError(
                    "Connection to backend lost. "
                    "Create a new Bus instance to reconnect."
                )
            return None, False

    def send(self, msg: Message, timeout: float | None = None) -> None:
        """
        Send a CAN message.

        Args:
            msg: CAN message to send
            timeout: Not used (for interface compatibility)
        """
        if self._shutdown_flag.is_set():
            raise can.CanOperationError("Bus has been shut down")

        # Encode and send via shared connection
        data = protocol.encode_can_tx(msg, self._can_channel)

        self._connection_manager.send(data)
        logger.log(self.RECV_LOGGING_LEVEL, f"Sent CAN message: {msg}")

    def set_bitrate(
        self,
        bitrate: int,
        data_bitrate: int | None = None,
        sample_point: float | None = None,
        data_sample_point: float | None = None,
    ) -> None:
        """Set the CAN bitrate via REST API."""
        client = self._ensure_rest_client()
        client.set_bitrate(
            channel=self._can_channel,
            bitrate=bitrate,
            data_bitrate=data_bitrate,
            sample_point=sample_point,
            data_sample_point=data_sample_point,
            fd=self._fd,
        )
        self._bitrate = bitrate
        if data_bitrate is not None:
            self._data_bitrate = data_bitrate
        logger.info(f"Bitrate set to {bitrate} bit/s" +
                   (f", data: {data_bitrate} bit/s" if data_bitrate else ""))

    def bus_on(self) -> None:
        """Put the CAN channel on the bus."""
        client = self._ensure_rest_client()
        client.bus_on(self._can_channel)
        logger.info(f"CAN channel {self._can_channel} is now on bus")

    def bus_off(self) -> None:
        """Take the CAN channel off the bus."""
        client = self._ensure_rest_client()
        client.bus_off(self._can_channel)
        logger.info(f"CAN channel {self._can_channel} is now off bus")

    def open_channel(self, fd: bool | None = None) -> None:
        """Open the CAN channel."""
        client = self._ensure_rest_client()
        client.open_channel(self._can_channel, fd=fd if fd is not None else self._fd)
        logger.info(f"CAN channel {self._can_channel} opened")

    def close_channel(self) -> None:
        """Close the CAN channel."""
        client = self._ensure_rest_client()
        client.close_channel(self._can_channel)
        logger.info(f"CAN channel {self._can_channel} closed")

    def get_channel_config(self) -> dict[str, Any]:
        """Get current channel configuration."""
        client = self._ensure_rest_client()
        return client.get_channel_state(self._can_channel)

    def shutdown(self) -> None:
        """Clean up resources. Safe to call more than once."""
        # Idempotency guard: python-can may call shutdown() again from
        # __del__/context exit; a second release() would corrupt the
        # shared connection manager's reference count.
        if self._shutdown_flag.is_set():
            return
        logger.info("Shutting down HelixBus")
        self._shutdown_flag.set()

        # Unsubscribe from connection manager
        if hasattr(self, '_connection_manager'):
            self._connection_manager.unsubscribe(
                channel=self._can_channel,
                recv_queue=self._recv_queue,
            )
            self._connection_manager.release()

        # Close REST client - but never one adopted from a HelixSession:
        # the caller owns that session's lifecycle.
        if self._rest_client and self._owns_rest_client:
            self._rest_client.close()
        self._rest_client = None

        super().shutdown()

    @staticmethod
    def _detect_available_configs() -> list[can.typechecking.AutoDetectedConfig]:
        """Detect available Helix backends."""
        config: dict[str, Any] = {
            'interface': 'helix',
            'channel': 1,
            'host': '192.168.7.1',
        }
        return [cast(can.typechecking.AutoDetectedConfig, config)]
