"""
Pre-authenticated sessions for the Helix backend.

A :class:`HelixSession` performs authentication *before* any bus is
created, so credentials are never part of the python-can bus
configuration (python-can logs the full config dict at DEBUG level -
with a session object, that log line contains no secrets).

Example::

    import can
    from can_helix import HelixSession

    # Credentials from HELIX_USERNAME / HELIX_PASSWORD env vars:
    session = HelixSession("192.168.7.1")

    # First time: inspect and record the device fingerprint
    print(session.device_fingerprint)

    # From then on, pin it - refuses to talk to any other device:
    session = HelixSession("192.168.7.1",
                           device_fingerprint="3f9a...e2")

    bus = can.Bus(interface="helix", channel=1, session=session)
    ...
    bus.shutdown()
    session.close()      # or use HelixSession as a context manager
"""

import logging
import os

from .auth import AuthError
from .bus import HelixRestClient

logger = logging.getLogger(__name__)


class HelixSession:
    """
    An authenticated connection context for one Helix device.

    Owns the REST client, the encryption (ECDH) session, and the login
    token. Several :class:`~can_helix.bus.HelixBus` instances may share
    one session. The caller owns the lifecycle: shutting a bus down does
    not log the session out - call :meth:`close` (or use ``with``).

    Args:
        host: Helix device hostname or IP address.
        username: Login username. Falls back to the ``HELIX_USERNAME``
            environment variable.
        password: Login password. Falls back to the ``HELIX_PASSWORD``
            environment variable.
        rest_port: REST API port (default 9999).
        device_fingerprint: Expected device public-key fingerprint (hex,
            as printed by :attr:`device_fingerprint`). When set, the
            session verifies the device's key *before* sending
            credentials and raises :class:`~can_helix.auth.AuthError` on
            mismatch. When ``None`` (default), any device key is
            accepted (trust on first use) - read the fingerprint after
            connecting and pin it for subsequent runs.

    Raises:
        AuthError: Missing credentials, failed login, or fingerprint
            mismatch.
    """

    def __init__(
        self,
        host: str,
        username: str | None = None,
        password: str | None = None,
        rest_port: int = 9999,
        device_fingerprint: str | None = None,
    ):
        username = username or os.environ.get('HELIX_USERNAME')
        password = password or os.environ.get('HELIX_PASSWORD')
        if not username or not password:
            raise AuthError(
                "Credentials required: pass username=/password= or set "
                "the HELIX_USERNAME and HELIX_PASSWORD environment "
                "variables"
            )

        self._host = host
        self._rest_port = rest_port
        self._username = username
        self._pinned = device_fingerprint is not None
        # HelixRestClient authenticates in its constructor; with a
        # pinned fingerprint the device key is verified before the
        # credentials are transmitted.
        self._rest_client: HelixRestClient | None = HelixRestClient(
            f"http://{host}:{rest_port}",
            username=username,
            password=password,
            expected_device_fingerprint=device_fingerprint,
        )
        if not self._pinned:
            # Trust-on-first-use: the credentials above were sent to
            # whichever device answered. Give the user the exact value
            # needed to pin future sessions.
            logger.warning(
                f"Unpinned session to {host}: device identity was not "
                f"verified before login (trust-on-first-use). Pin it "
                f"with device_fingerprint="
                f"'{self._rest_client.device_fingerprint}'"
            )

    # -- introspection ------------------------------------------------------

    @property
    def host(self) -> str:
        return self._host

    @property
    def rest_port(self) -> int:
        return self._rest_port

    @property
    def username(self) -> str:
        return self._username

    @property
    def pinned(self) -> bool:
        """True if this session verifies the device fingerprint."""
        return self._pinned

    @property
    def device_fingerprint(self) -> str | None:
        """
        The device's public-key fingerprint (hex string).

        Print/store this after a first (unpinned) connection, then pass
        it as ``device_fingerprint=`` to lock the session to this
        device.
        """
        return self._require_client().device_fingerprint

    @property
    def rest_client(self) -> HelixRestClient:
        """The underlying authenticated REST client."""
        return self._require_client()

    @property
    def crypto_session(self):
        """The crypto session used for WebSocket encryption."""
        return self._require_client().crypto_session

    def get_ws_ticket(self) -> str:
        """Mint a single-use WebSocket authentication ticket."""
        return self._require_client().get_ws_ticket()

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Log out and release the session. Idempotent."""
        if self._rest_client is not None:
            self._rest_client.close()
            self._rest_client = None

    def _require_client(self) -> HelixRestClient:
        if self._rest_client is None:
            raise AuthError("HelixSession is closed")
        return self._rest_client

    def __enter__(self) -> "HelixSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __repr__(self) -> str:
        # Deliberately excludes password and session token: this repr
        # ends up in python-can's DEBUG log of the bus config.
        try:
            fp = self.device_fingerprint
            fp_str = f"{fp[:16]}..." if fp else "?"
        except AuthError:
            fp_str = "closed"
        pin = "pinned" if self._pinned else "tofu"
        return (f"<HelixSession host={self._host} user={self._username} "
                f"device={fp_str} ({pin})>")
