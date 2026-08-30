#!/usr/bin/env python3
"""
Example: Handling reconnection with python-can-helix.

This example demonstrates how to handle connection loss and reconnection
with the Helix CAN interface. Like other python-can interfaces (socketcand,
udp_multicast), the HelixBus does NOT auto-reconnect - the user application
is responsible for detecting disconnection and creating a new Bus instance.

Usage:
    python reconnect_example.py

    # Then try stopping and restarting the backend to see reconnection
"""

import logging
import time

import can

from can_helix import HelixSession

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_bus(
    host: str = "192.168.7.1",
    channel: int = 1,
    username: str = None,
    password: str = None,
) -> can.Bus:
    """Create a new bus connection."""
    logger.info(f"Connecting to {host}...")
    try:
        session = None
        if username:
            session = HelixSession(host, username=username,
                                   password=password)
        bus = can.Bus(
            interface='helix',
            host=host,
            channel=channel,
            session=session,
        )
        logger.info("Connected successfully!")
        return bus
    except can.CanInitializationError as e:
        logger.error(f"Failed to connect: {e}")
        raise


def main():
    """Main loop with reconnection handling."""
    host = "192.168.7.1"
    channel = 1
    username = None   # Set for authenticated/encrypted mode
    password = None
    reconnect_delay = 2.0  # seconds to wait before reconnecting

    bus = None

    while True:
        # Connect if not connected
        if bus is None:
            try:
                bus = create_bus(host, channel, username, password)
            except can.CanInitializationError:
                logger.info(f"Retrying in {reconnect_delay} seconds...")
                time.sleep(reconnect_delay)
                continue

        # Try to receive messages
        try:
            msg = bus.recv(timeout=1.0)
            if msg is not None:
                logger.info(f"Received: {msg}")

        except can.CanOperationError as e:
            # Connection lost - need to reconnect
            logger.warning(f"Connection lost: {e}")

            # Clean up old bus
            try:
                bus.shutdown()
            except Exception:
                pass
            bus = None

            logger.info(f"Reconnecting in {reconnect_delay} seconds...")
            time.sleep(reconnect_delay)

        except KeyboardInterrupt:
            logger.info("Shutting down...")
            break

    # Cleanup
    if bus is not None:
        bus.shutdown()


if __name__ == "__main__":
    main()
