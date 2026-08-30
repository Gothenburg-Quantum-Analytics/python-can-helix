#!/usr/bin/env python3
"""
Example: Bitrate and Bus State Control with python-can-helix.

This example demonstrates how to configure bitrate and control bus
state (on/off) using the Helix CAN interface's REST API integration.

Usage (with authentication - recommended):
    python bus_control_example.py --username admin --password yourpassword

Usage (legacy - no encryption):
    python bus_control_example.py
"""

import argparse
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


# Default credentials (change for your setup)
DEFAULT_HOST = '192.168.7.1'
DEFAULT_USERNAME = None  # Set to enable encryption by default
DEFAULT_PASSWORD = None


def main():
    """Demonstrate bitrate and bus control features."""
    parser = argparse.ArgumentParser(description='Bus control example')
    parser.add_argument('--host', default=DEFAULT_HOST)
    parser.add_argument('--username', '-u', default=DEFAULT_USERNAME)
    parser.add_argument('--password', '-p', default=DEFAULT_PASSWORD)
    args = parser.parse_args()

    # Connect to backend with bitrate configuration
    # This will configure 500kbit/s via REST API before connecting WebSocket
    logger.info("Creating bus with 500kbit/s bitrate...")
    session = None
    if args.username:
        session = HelixSession(args.host, username=args.username,
                               password=args.password)
    bus = can.Bus(
        interface='helix',
        host=args.host,
        channel=1,
        bitrate=500000,  # 500 kbit/s
        session=session,
    )

    # Check the current bus state
    logger.info(f"Bus state: {bus.state}")

    # Get detailed channel configuration
    try:
        config = bus.get_channel_config()
        logger.info(f"Channel config: baud={config.get('baud')}, "
                   f"bus_on={config.get('bus_on')}, fd={config.get('fd')}")
    except Exception as e:
        logger.warning(f"Could not get config: {e}")

    # Send a test message
    msg = can.Message(
        arbitration_id=0x123,
        data=[0x01, 0x02, 0x03, 0x04],
        is_extended_id=False,
    )
    logger.info(f"Sending message: {msg}")
    bus.send(msg)

    # Demonstrate taking bus off
    logger.info("Taking bus OFF...")
    bus.bus_off()
    logger.info(f"Bus state after off: {bus.state}")

    # Change bitrate while off-bus (recommended practice)
    logger.info("Changing bitrate to 250kbit/s...")
    bus.set_bitrate(250000)

    # Go back on bus
    logger.info("Putting bus back ON...")
    bus.bus_on()
    logger.info(f"Bus state after on: {bus.state}")

    # Send another message at new bitrate
    msg2 = can.Message(
        arbitration_id=0x456,
        data=[0xAA, 0xBB, 0xCC, 0xDD],
        is_extended_id=False,
    )
    logger.info(f"Sending message at new bitrate: {msg2}")
    bus.send(msg2)

    # Receive messages for a bit
    logger.info("Listening for messages (5 seconds)...")
    end_time = time.time() + 5
    while time.time() < end_time:
        msg = bus.recv(timeout=1.0)
        if msg:
            logger.info(f"Received: {msg}")

    # Cleanup
    logger.info("Shutting down...")
    bus.shutdown()


def can_fd_example(username=None, password=None):
    """Demonstrate CAN FD with dual bitrate."""

    logger.info("Creating CAN FD bus with 500k/2M bitrates...")
    session = None
    if username:
        session = HelixSession(DEFAULT_HOST, username=username,
                               password=password)
    bus = can.Bus(
        interface='helix',
        host=DEFAULT_HOST,
        channel=1,
        bitrate=500000,       # 500 kbit/s arbitration
        data_bitrate=2000000, # 2 Mbit/s data phase
        fd=True,
        session=session,
    )

    # Send CAN FD message with 64 bytes
    data = bytes(range(64))
    msg = can.Message(
        arbitration_id=0x789,
        data=data,
        is_fd=True,
        bitrate_switch=True,
    )
    logger.info(f"Sending CAN FD message: {msg}")
    bus.send(msg)

    bus.shutdown()


if __name__ == "__main__":
    main()
    # Uncomment to test CAN FD:
    # can_fd_example()
