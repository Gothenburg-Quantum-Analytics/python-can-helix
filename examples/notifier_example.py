#!/usr/bin/env python3
"""
Example: Using python-can Notifier with Helix CAN backend.

This example shows how to use the Notifier pattern to handle
incoming CAN messages with listeners.
"""

import argparse
import signal
import sys
import time

import can
from can import Logger, Notifier, Printer

from can_helix import HelixSession


class MessageCounter(can.Listener):
    """Custom listener that counts messages by ID."""

    def __init__(self):
        self.counts = {}
        self.total = 0

    def on_message_received(self, msg: can.Message):
        arb_id = msg.arbitration_id
        self.counts[arb_id] = self.counts.get(arb_id, 0) + 1
        self.total += 1

    def print_stats(self):
        print("\n--- Message Statistics ---")
        print(f"Total messages: {self.total}")
        print(f"Unique IDs: {len(self.counts)}")
        if self.counts:
            print("\nTop 10 by count:")
            sorted_ids = sorted(self.counts.items(), key=lambda x: x[1], reverse=True)
            for arb_id, count in sorted_ids[:10]:
                print(f"  0x{arb_id:03X}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description='Monitor CAN traffic with statistics'
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
    parser.add_argument(
        '--log-file',
        type=str,
        default=None,
        help='Optional log file (e.g., log.asc, log.blf)'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='Don\'t print messages to console'
    )
    args = parser.parse_args()

    print(f"Connecting to {args.host}...")

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

    # Build list of listeners
    listeners = []

    # Message counter
    counter = MessageCounter()
    listeners.append(counter)

    # Console printer (unless quiet mode)
    if not args.quiet:
        listeners.append(Printer())

    # File logger (if specified)
    if args.log_file:
        listeners.append(Logger(args.log_file))
        print(f"Logging to: {args.log_file}")

    # Create notifier
    notifier = Notifier(bus, listeners)

    # Setup signal handler for clean shutdown
    def signal_handler(sig, frame):
        print("\nStopping...")
        notifier.stop()
        counter.print_stats()
        bus.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    print("\nMonitoring CAN traffic (Ctrl+C to stop)...")

    # Keep running
    while True:
        time.sleep(1)


if __name__ == '__main__':
    main()
