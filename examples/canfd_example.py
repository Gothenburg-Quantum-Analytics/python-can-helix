#!/usr/bin/env python3
"""
Example: CAN FD communication with Helix CAN backend.

This example shows how to send and receive CAN FD messages
which support larger payloads (up to 64 bytes) and flexible data rates.
"""

import argparse
import time

import can

from can_helix import HelixSession


def main():
    parser = argparse.ArgumentParser(
        description='CAN FD example with Helix backend'
    )
    parser.add_argument(
        '--host',
        default='192.168.7.1',
        help='Backend host (default: 192.168.7.1)'
    )
    parser.add_argument(
        '--channel',
        type=int,
        default=1,
        help='CAN channel (default: 1)'
    )
    parser.add_argument(
        '--username', '-u',
        default=None,
        help='Username for authentication (enables encryption)'
    )
    parser.add_argument(
        '--password', '-p',
        default=None,
        help='Password for authentication'
    )
    args = parser.parse_args()

    print(f"Connecting to {args.host} in CAN FD mode...")

    # Connect with FD mode enabled
    session = None
    if args.username:
        session = HelixSession(args.host, username=args.username,
                               password=args.password)
    bus = can.Bus(
        interface='helix',
        host=args.host,
        channel=args.channel,
        fd=True,
        session=session,
    )

    print(f"Connected: {bus.channel_info}")
    print(f"Protocol: {bus.protocol}")

    try:
        # Send a CAN FD message with 64-byte payload
        large_data = bytes(range(64))  # 0x00 to 0x3F

        msg = can.Message(
            arbitration_id=0x1FFFFFFF,  # 29-bit ID
            data=large_data,
            is_extended_id=True,
            is_fd=True,
            bitrate_switch=True,  # Use higher data rate
        )

        print("\nSending CAN FD message:")
        print(f"  ID: 0x{msg.arbitration_id:08X}")
        print(f"  Length: {len(msg.data)} bytes")
        print(f"  BRS: {msg.bitrate_switch}")
        print(f"  Data (first 16): {msg.data[:16].hex()}")

        bus.send(msg)
        print("Message sent!")

        # Also send different FD payload sizes
        for data_len in [12, 16, 20, 24, 32, 48]:
            time.sleep(0.1)

            msg = can.Message(
                arbitration_id=0x100 + data_len,
                data=bytes(data_len),
                is_fd=True,
                bitrate_switch=True,
            )

            print(f"Sent FD message with {data_len} bytes (ID: 0x{msg.arbitration_id:03X})")
            bus.send(msg)

        # Receive messages
        print("\nListening for CAN FD messages (Ctrl+C to stop)...")

        while True:
            received = bus.recv(timeout=1.0)
            if received:
                print("\nReceived:")
                print(f"  ID: 0x{received.arbitration_id:08X}")
                print(f"  Extended: {received.is_extended_id}")
                print(f"  FD: {received.is_fd}")
                print(f"  BRS: {received.bitrate_switch}")
                print(f"  Length: {len(received.data)} bytes")
                print(f"  Data: {received.data.hex()}")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        bus.shutdown()
        print("Disconnected.")


if __name__ == '__main__':
    main()
