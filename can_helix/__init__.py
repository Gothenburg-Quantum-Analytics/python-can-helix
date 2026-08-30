"""
python-can-helix: A python-can interface for Helix CAN backend.

This package provides a python-can compatible interface for communicating
with CAN devices through the Helix CAN backend via WebSocket.

Supports authenticated and encrypted communication with the Helix backend.

Example usage::

    import can
    from can_helix import HelixSession

    # Authenticate first (recommended; enables encryption)
    session = HelixSession('192.168.7.1', username='admin',
                           password='password123')
    bus = can.Bus(interface='helix', channel=1, session=session)

    # Legacy mode (no encryption, unauthenticated backends only)
    bus = can.Bus(interface='helix', host='192.168.7.1', channel=1)

    # Send and receive
    bus.send(can.Message(arbitration_id=0x123, data=[1, 2, 3]))
    msg = bus.recv(timeout=1.0)

    # Control bitrate and bus state via REST API
    bus.bus_off()
    bus.set_bitrate(500000)
    bus.bus_on()

    # Cleanup - WebSocket closes when last bus shuts down
    bus.shutdown()
"""

from .auth import AuthError, HelixAuthClient
from .bus import HelixBus, HelixRestClient
from .connection import HelixConnectionManager
from .crypto import CryptoError, CryptoSession
from .session import HelixSession

__all__ = [
    'HelixBus',
    'HelixRestClient',
    'HelixConnectionManager',
    'HelixAuthClient',
    'AuthError',
    'CryptoSession',
    'CryptoError',
    'HelixSession',
]
__version__ = '0.3.0'
