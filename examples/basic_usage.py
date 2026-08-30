#!/usr/bin/env python3
"""
Example: Basic send and receive with Helix CAN backend.

This example shows how to connect to the Helix CAN backend,
send CAN messages, and receive them using python-can.

With authentication (recommended):
    python basic_usage.py --username admin --password yourpassword

Legacy mode (no encryption):
    python basic_usage.py
"""

import argparse

import can

from can_helix import HelixSession


def main():
    parser = argparse.ArgumentParser(
        description='Send and receive CAN messages via Helix backend'
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

    if args.username and not args.password:
        parser.error("--password is required when --username is provided")

    print(f"Connecting to {args.host} on CAN channel {args.channel}...")
    if args.username:
        print(f"Using authenticated/encrypted connection as '{args.username}'")
    else:
        print("WARNING: Using unencrypted connection (legacy mode)")

    # Connect to the backend
    session = None
    if args.username:
        session = HelixSession(args.host, username=args.username,
                               password=args.password)
    bus = can.Bus(
        interface='helix',
        host=args.host,
        channel=args.channel,
        session=session,
    )

    print(f"Connected: {bus.channel_info}")

    try:
        # Send a test message
        msg = can.Message(
            arbitration_id=0x123,
            data=[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08],
            is_extended_id=False,
        )

        print(f"\nSending: {msg}")
        bus.send(msg)
        print("Message sent!")

        # Receive messages
        print("\nListening for messages (Ctrl+C to stop)...")

        while True:
            received = bus.recv(timeout=1.0)
            if received:
                print(f"Received: {received}")

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        bus.shutdown()
        print("Disconnected.")


if __name__ == '__main__':
    main()
