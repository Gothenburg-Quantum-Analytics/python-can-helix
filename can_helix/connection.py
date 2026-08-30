"""
Shared WebSocket connection manager for Helix CAN backend.

This module provides a singleton connection manager that allows multiple
HelixBus instances to share a single WebSocket connection per backend host.
This is more efficient than opening a separate connection for each bus.

Supports encrypted WebSocket communication using ChaCha20-Poly1305 when
a crypto session is provided.
"""

import logging
import queue
import threading
import time
import urllib.parse
import weakref
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Optional

try:
    import websocket
except ImportError:
    websocket = None  # type: ignore[assignment]

from . import protocol

if TYPE_CHECKING:
    from .crypto import CryptoSession

logger = logging.getLogger(__name__)


class HelixConnectionManager:
    """
    Shared WebSocket connection manager for multiple HelixBus instances.

    This class implements a singleton pattern per host, allowing multiple
    bus instances to share a single WebSocket connection. Messages are
    routed to the appropriate bus based on channel number.

    Architecture::

        ┌──────────────────────────────────────────────────────────────┐
        │              HelixConnectionManager (singleton per host)     │
        │                                                              │
        │   WebSocket ──► _on_message() ──► route by channel           │
        │                                         │                    │
        │                          ┌──────────────┼──────────────┐     │
        │                          ▼              ▼              ▼     │
        │                     subscribers[1]  subscribers[2]  ...      │
        │                          │              │                    │
        │                    ┌─────┴─────┐  ┌─────┴─────┐              │
        │                    │  HelixBus │  │  HelixBus │              │
        │                    │   ch=1    │  │   ch=2    │              │
        │                    └───────────┘  └───────────┘              │
        └──────────────────────────────────────────────────────────────┘

    Usage::

        # Get or create connection manager for a host
        manager = HelixConnectionManager.get_instance("192.168.7.1", 8080)

        # Subscribe a bus to receive messages for a channel
        manager.subscribe(channel=1, callback=my_callback)

        # Send a message
        manager.send(data)

        # Unsubscribe when done
        manager.unsubscribe(channel=1, callback=my_callback)
    """

    # Class-level registry of instances (singleton per host:port)
    _instances: dict[str, 'HelixConnectionManager'] = {}
    _instances_lock = threading.Lock()

    # Minimum seconds between "subscriber queue full" warnings
    DROP_LOG_INTERVAL = 5.0

    @classmethod
    def get_instance(
        cls,
        host: str,
        ws_port: int = 8080,
        crypto_session: Optional['CryptoSession'] = None,
        ticket_provider: Callable[[], str] | None = None,
        batch: bool = False,
    ) -> 'HelixConnectionManager':
        """
        Get or create a connection manager for the given host.

        This implements a singleton pattern - only one connection manager
        (and thus one WebSocket) exists per host:port combination.

        Args:
            host: Backend hostname or IP address
            ws_port: WebSocket port number
            crypto_session: Optional crypto session for encrypted communication
            ticket_provider: Callable returning a fresh single-use WS auth
                ticket. Invoked once per WebSocket connect. Required by
                Helix backends with ticket authentication; without it the
                handshake is rejected with HTTP 401.
            batch: Opt into device-side CAN frame batching (``batch=1``):
                the device coalesces frames for up to ~2 ms and packs them
                into one sealed WebSocket message, amortizing crypto and
                transport overhead at high frame rates.

        Returns:
            HelixConnectionManager instance for this host
        """
        # The registry key includes the crypto-session identity: two
        # different authenticated sessions to the same device must NOT
        # share a WebSocket, or the second would ride the first's
        # authentication. Buses sharing one HelixSession still share
        # one connection (same client fingerprint -> same key).
        key = cls._registry_key(host, ws_port, crypto_session, batch)

        with cls._instances_lock:
            if key not in cls._instances:
                instance = cls(host, ws_port, crypto_session, ticket_provider,
                               batch=batch)
                cls._instances[key] = instance
                logger.info(f"Created new connection manager for {key}")
            else:
                # Same crypto session reconnecting: adopt a ticket
                # provider if none was set yet.
                existing = cls._instances[key]
                if ticket_provider and not existing._ticket_provider:
                    existing._ticket_provider = ticket_provider
            return cls._instances[key]

    @staticmethod
    def _registry_key(
        host: str,
        ws_port: int,
        crypto_session: Optional['CryptoSession'],
        batch: bool = False,
    ) -> str:
        crypto_id = (
            crypto_session.client_fingerprint[:16] if crypto_session else 'plain'
        )
        # Batched and unbatched streams are different server-side
        # connections; do not mix buses with different batch settings
        # on one WebSocket.
        mode = 'batch' if batch else 'single'
        return f"{host}:{ws_port}:{crypto_id}:{mode}"

    @classmethod
    def _remove_instance(cls, key: str) -> None:
        """Remove an instance from the registry (called when ref count hits 0)."""
        with cls._instances_lock:
            if key in cls._instances:
                del cls._instances[key]
                logger.info(f"Removed connection manager for {key}")

    def __init__(
        self,
        host: str,
        ws_port: int,
        crypto_session: Optional['CryptoSession'] = None,
        ticket_provider: Callable[[], str] | None = None,
        batch: bool = False,
    ):
        """
        Initialize connection manager. Use get_instance() instead.

        Args:
            host: Backend hostname or IP address
            ws_port: WebSocket port number
            crypto_session: Optional crypto session for encrypted communication
            ticket_provider: Callable returning a fresh single-use WS ticket;
                invoked once per WebSocket connect.
            batch: Request device-side CAN frame batching (see get_instance).
        """
        if websocket is None:
            raise ImportError(
                "websocket-client is required. "
                "Install with: pip install websocket-client"
            )

        self._host = host
        self._ws_port = ws_port
        self._crypto_session = crypto_session
        self._ticket_provider = ticket_provider
        self._batch = batch
        self._registry_key_value = self._registry_key(
            host, ws_port, crypto_session, batch
        )

        # Overflow accounting for rate-limited drop warnings
        self._dropped_messages = 0
        self._last_drop_log = 0.0

        # Identity URL used for logging only. The actual connect URL is
        # rebuilt in _connect() so each connection gets a fresh ticket.
        self._url = f"ws://{host}:{ws_port}"
        if crypto_session:
            logger.info("WebSocket will use encrypted communication")
        else:
            logger.warning(
                "WebSocket using unencrypted communication "
                "(handshake will also fail without authentication "
                "credentials — backend requires a WS ticket)"
            )

        # Subscribers: channel -> set of (queue, weak_ref_to_bus)
        # Using weak references so we don't prevent garbage collection
        self._subscribers: dict[int, set[tuple]] = {}
        self._subscribers_lock = threading.Lock()

        # Also support "all channels" subscribers (channel=-1)
        self._all_channel_subscribers: set[tuple] = set()

        # Reference count for connection management
        self._ref_count = 0
        self._ref_lock = threading.Lock()

        # WebSocket state
        self._ws: websocket.WebSocketApp | None = None
        self._ws_thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._shutdown = threading.Event()
        self._send_lock = threading.Lock()

        # Error tracking
        self._last_error: Exception | None = None

    @property
    def is_connected(self) -> bool:
        """Check if WebSocket is connected."""
        return self._connected.is_set()

    @property
    def url(self) -> str:
        """Get the WebSocket URL."""
        return self._url

    def acquire(self) -> None:
        """
        Acquire a reference to this connection manager.

        Call this when a new HelixBus starts using this manager.
        The WebSocket connection is established on first acquire.
        """
        with self._ref_lock:
            need_connect = self._ref_count == 0
            self._ref_count += 1
            logger.debug(f"Connection manager {self._url} ref_count: {self._ref_count}")

        if need_connect:
            try:
                self._connect()
            except Exception:
                with self._ref_lock:
                    self._ref_count -= 1
                    if self._ref_count <= 0:
                        HelixConnectionManager._remove_instance(self._registry_key_value)
                raise

    def release(self) -> None:
        """
        Release a reference to this connection manager.

        Call this when a HelixBus is shutting down.
        The WebSocket connection is closed when the last reference is released.
        """
        with self._ref_lock:
            self._ref_count -= 1
            logger.debug(f"Connection manager {self._url} ref_count: {self._ref_count}")

            if self._ref_count <= 0:
                # Last subscriber - close connection
                self._disconnect()
                HelixConnectionManager._remove_instance(self._registry_key_value)

    def subscribe(
        self,
        channel: int,
        recv_queue: queue.Queue,
        bus_ref: Any,
    ) -> None:
        """
        Subscribe a bus to receive messages for a specific channel.

        Args:
            channel: CAN channel number (0, 1, ...) or -1 for all channels
            recv_queue: Queue to put received messages into
            bus_ref: Reference to the HelixBus (for cleanup tracking)
        """
        subscription = (recv_queue, weakref.ref(bus_ref))

        with self._subscribers_lock:
            if channel < 0:
                self._all_channel_subscribers.add(subscription)
            else:
                if channel not in self._subscribers:
                    self._subscribers[channel] = set()
                self._subscribers[channel].add(subscription)

        logger.debug(f"Subscribed to channel {channel} on {self._url}")

    def unsubscribe(
        self,
        channel: int,
        recv_queue: queue.Queue,
    ) -> None:
        """
        Unsubscribe from a channel.

        Args:
            channel: CAN channel number or -1 for all channels
            recv_queue: The queue that was used in subscribe()
        """
        with self._subscribers_lock:
            if channel < 0:
                # Remove from all_channel subscribers
                self._all_channel_subscribers = {
                    s for s in self._all_channel_subscribers
                    if s[0] is not recv_queue
                }
            elif channel in self._subscribers:
                self._subscribers[channel] = {
                    s for s in self._subscribers[channel]
                    if s[0] is not recv_queue
                }
                # Clean up empty channel sets
                if not self._subscribers[channel]:
                    del self._subscribers[channel]

        logger.debug(f"Unsubscribed from channel {channel} on {self._url}")

    def send(self, data: bytes) -> None:
        """
        Send binary data over the WebSocket.

        If a crypto session is configured, data is encrypted with
        ChaCha20-Poly1305 before sending.

        Args:
            data: Binary data to send

        Raises:
            can.CanOperationError: If not connected or send fails
        """
        import can

        if not self._connected.is_set():
            raise can.CanOperationError(
                "Not connected to backend. "
                "Create a new Bus instance to reconnect."
            )

        # Encrypt data if crypto session is available
        if self._crypto_session:
            try:
                data = self._crypto_session.encrypt_ws(data)
            except Exception as e:
                logger.error(f"Encryption error: {e}")
                raise can.CanOperationError(f"Encryption failed: {e}") from e

        ws = self._ws
        if ws is None:
            raise can.CanOperationError(
                "Not connected to backend. "
                "Create a new Bus instance to reconnect."
            )

        with self._send_lock:
            try:
                ws.send(data, opcode=websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                logger.error(f"WebSocket send error: {e}")
                raise can.CanOperationError(f"Send failed: {e}") from e

    def _build_connect_url(self) -> str:
        """Build the WS connect URL. WS tickets are single-use, so this
        mints one *now* (right before the upgrade) rather than at
        __init__ time."""
        import can

        params = []
        if self._crypto_session:
            params.append(
                f"clientPubKey={urllib.parse.quote(self._crypto_session.client_public_key_base64)}"
            )
        if self._ticket_provider:
            try:
                ticket = self._ticket_provider()
            except Exception as e:
                raise can.CanInitializationError(
                    f"Failed to obtain WebSocket auth ticket: {e}"
                ) from e
            params.append(f"ticket={urllib.parse.quote(ticket)}")
        if self._batch:
            # Opt into device-side frame batching: several
            # [cmd][len][payload] records per sealed WS message
            # (_handle_binary_message walks them).
            params.append("batch=1")
        return self._url + (f"?{'&'.join(params)}" if params else "")

    def _connect(self) -> None:
        """Establish WebSocket connection."""
        import can

        logger.debug(f"Connecting to {self._url}")
        self._shutdown.clear()

        connect_url = self._build_connect_url()

        def on_message(ws, message):
            if isinstance(message, bytes):
                self._handle_binary_message(message)
            else:
                self._handle_text_message(message)

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
            self._last_error = error

        def on_close(ws, close_status_code, close_msg):
            logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")
            self._connected.clear()

        def on_open(ws):
            logger.info("Websocket connected")
            logger.info(f"WebSocket connected to {self._url}")
            self._connected.set()

        self._ws = websocket.WebSocketApp(
            connect_url,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )

        # Socket options for low latency
        import socket
        sockopt = [
            (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1),  # Disable Nagle's algorithm
            (socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1),  # Enable keepalive
        ]

        self._ws_thread = threading.Thread(
            target=self._ws.run_forever,
            kwargs={
                'ping_interval': 30,
                'ping_timeout': 10,
                'sockopt': sockopt,
            },
            daemon=True,
            name=f"HelixWS-{self._host}"
        )
        self._ws_thread.start()

        if not self._connected.wait(timeout=10.0):
            # Surface the underlying error if the WS thread captured one
            # (e.g. HTTP 401 when no/invalid auth ticket was supplied).
            detail = f": {self._last_error}" if self._last_error else ""
            raise can.CanInitializationError(
                f"Failed to connect to {self._url} within 10 seconds{detail}"
            )

    def _disconnect(self) -> None:
        """Close WebSocket connection."""
        logger.info(f"Disconnecting from {self._url}")
        self._shutdown.set()

        if self._ws:
            self._ws.close()
            self._ws = None

        if self._ws_thread and self._ws_thread.is_alive():
            self._ws_thread.join(timeout=2.0)
            self._ws_thread = None

        self._connected.clear()

    def _handle_binary_message(self, data: bytes) -> None:
        """Handle incoming binary WebSocket message and route to subscribers.

        One WS message may carry a BATCH of concatenated ``[cmd][len:2LE][payload]`` records
        (the device packs several CAN frames into one sealed message to amortize crypto/transport
        at high rates). We decrypt once, then walk the records — a single frame is simply a
        batch of one, so this stays compatible with unbatched (single-frame) messages.
        """
        try:
            # Decrypt the whole message ONCE (covers every record in the batch)
            if self._crypto_session:
                try:
                    data = self._crypto_session.decrypt_ws(data)
                except Exception as e:
                    logger.error(f"Decryption error: {e}")
                    return

            n = len(data)
            off = 0
            while off + 3 <= n:
                length = int.from_bytes(data[off + 1:off + 3], "little")
                end = off + 3 + length
                if end > n:
                    break  # truncated trailing record — stop
                record = data[off:end]
                off = end
                if not protocol.is_can_rx_message(record):
                    continue
                try:
                    rx_msg = protocol.decode_can_rx(record)
                except ValueError:
                    continue  # skip a malformed record, keep processing the rest
                channel = rx_msg.channel
                message = rx_msg.message
                with self._subscribers_lock:
                    if channel in self._subscribers:
                        self._dispatch_to_subscribers(self._subscribers[channel], message)
                    if self._all_channel_subscribers:
                        self._dispatch_to_subscribers(self._all_channel_subscribers, message)

        except Exception as e:
            logger.error(f"Error handling binary message: {e}")

    def _dispatch_to_subscribers(
        self,
        subscribers: set[tuple],
        message: Any,
    ) -> None:
        """Dispatch a message to a set of subscribers."""
        dead_refs = []

        for recv_queue, bus_ref in subscribers:
            # Check if bus is still alive
            if bus_ref() is None:
                dead_refs.append((recv_queue, bus_ref))
                continue

            try:
                recv_queue.put_nowait(message)
            except queue.Full:
                # Rate-limited: on a busy bus with a slow consumer this
                # fires thousands of times per second, and a per-frame
                # log line would starve the very thread that should be
                # draining the socket.
                self._dropped_messages += 1
                now = time.monotonic()
                if now - self._last_drop_log >= self.DROP_LOG_INTERVAL:
                    logger.warning(
                        f"Subscriber queue full: dropped "
                        f"{self._dropped_messages} message(s) so far; "
                        f"consumer is not keeping up with the bus"
                    )
                    self._last_drop_log = now

        # Clean up dead references
        for ref in dead_refs:
            subscribers.discard(ref)

    def _handle_text_message(self, data: str) -> None:
        """Handle text WebSocket messages."""
        logger.debug(f"Received text message: {data}")
