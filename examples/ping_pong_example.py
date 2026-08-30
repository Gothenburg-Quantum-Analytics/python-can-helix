#!/usr/bin/env python3
"""
Example: Ping-pong latency measurement between two CAN channels.

This example opens CAN channel 1 and channel 2, sends ping messages
from channel 1 to channel 2, and measures the round-trip time when
channel 2 echoes back the message.

Both channels share the same WebSocket connection via HelixConnectionManager.

With authentication (recommended):
    python ping_pong_example.py --username admin --password yourpassword

Legacy mode (no encryption):
    python ping_pong_example.py
"""

import argparse
import statistics
import time

import can

from can_helix import HelixSession


def main():
    parser = argparse.ArgumentParser(
        description='Measure CAN ping-pong latency between two channels'
    )
    parser.add_argument(
        '--host',
        default='192.168.7.1',
        help='Backend host (default: 192.168.7.1)'
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
        '--count',
        type=int,
        default=1000,
        help='Number of ping-pong iterations (default: 1000)'
    )
    parser.add_argument(
        '--ping-id',
        type=lambda x: int(x, 0),
        default=0x100,
        help='CAN ID for ping messages (default: 0x100)'
    )
    parser.add_argument(
        '--pong-id',
        type=lambda x: int(x, 0),
        default=0x101,
        help='CAN ID for pong messages (default: 0x101)'
    )
    args = parser.parse_args()

    print(f"Connecting to {args.host}...")
    if args.username:
        print(f"Using authenticated/encrypted connection as '{args.username}'")
    else:
        print("WARNING: Using unencrypted connection (legacy mode)")
    print(f"  Channel 1: Ping sender (ID: 0x{args.ping_id:03X})")
    print(f"  Channel 2: Pong responder (ID: 0x{args.pong_id:03X})")
    print(f"  Iterations: {args.count}")
    print()

    session = None
    if args.username:
        session = HelixSession(args.host, username=args.username,
                               password=args.password)

    # Open both channels (they share the same WebSocket connection)
    bus1 = can.Bus(
        interface='helix',
        host=args.host,
        channel=1,
        session=session,
    )

    bus2 = can.Bus(
        interface='helix',
        host=args.host,
        channel=2,
        session=session,  # both buses share one authenticated session
    )

    print(f"Channel 1: {bus1.channel_info}")
    print(f"Channel 2: {bus2.channel_info}")
    print()

    latencies = []
    timeouts = 0
    errors = 0

    try:
        print(f"Starting ping-pong test ({args.count} iterations)...")
        print()

        for i in range(args.count):
            # Create ping message with sequence number in data
            seq_bytes = i.to_bytes(4, 'little')
            ping_msg = can.Message(
                arbitration_id=args.ping_id,
                data=seq_bytes + bytes([0xAA, 0xBB, 0xCC, 0xDD]),
                is_extended_id=False,
            )

            # Record start time and send ping from channel 1
            t_start = time.perf_counter()
            bus1.send(ping_msg)

            # Wait for ping on channel 2 (filter by ID, skip other messages)
            received_ping = None
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                msg = bus2.recv(timeout=0.005)  # 5ms timeout for faster polling
                if msg and msg.arbitration_id == args.ping_id:
                    received_ping = msg
                    break

            if received_ping is None:
                timeouts += 1
                continue

            # Send pong response from channel 2
            pong_msg = can.Message(
                arbitration_id=args.pong_id,
                data=received_ping.data,  # Echo back the same data
                is_extended_id=False,
            )
            bus2.send(pong_msg)

            # Wait for pong on channel 1 (filter by ID, skip other messages)
            received_pong = None
            deadline = time.perf_counter() + 1.0
            while time.perf_counter() < deadline:
                msg = bus1.recv(timeout=0.005)  # 5ms timeout for faster polling
                if msg and msg.arbitration_id == args.pong_id:
                    received_pong = msg
                    break

            t_end = time.perf_counter()

            if received_pong is None:
                timeouts += 1
                continue

            # Verify sequence number
            recv_seq = int.from_bytes(received_pong.data[:4], 'little')
            if recv_seq != i:
                errors += 1
                continue

            # Record latency
            latency_us = (t_end - t_start) * 1_000_000
            latencies.append(latency_us)

            # Progress indicator
            if (i + 1) % 100 == 0:
                print(f"  Progress: {i + 1}/{args.count}")

    except KeyboardInterrupt:
        print("\nInterrupted by user.")

    finally:
        bus1.shutdown()
        bus2.shutdown()

    # Print results
    print()
    print("=" * 50)
    print("RESULTS")
    print("=" * 50)
    print(f"Total iterations:  {args.count}")
    print(f"Successful:        {len(latencies)}")
    print(f"Timeouts:          {timeouts}")
    print(f"Errors:            {errors}")
    print()

    if latencies:
        avg_rtt = statistics.mean(latencies)
        avg_one_way = avg_rtt / 2

        print("Latency Statistics:")
        print("  Round-trip (ping→pong→ping):")
        print(f"    Min:      {min(latencies):>10.2f} µs")
        print(f"    Max:      {max(latencies):>10.2f} µs")
        print(f"    Average:  {avg_rtt:>10.2f} µs")
        print(f"    Median:   {statistics.median(latencies):>10.2f} µs")
        if len(latencies) > 1:
            print(f"    Std Dev:  {statistics.stdev(latencies):>10.2f} µs")
        print()
        print("  One-way estimate (ping→pong):")
        print(f"    Average:  {avg_one_way:>10.2f} µs")
        print()
        print(f"  Throughput: {len(latencies) / (sum(latencies) / 1_000_000):.1f} ping-pongs/sec")
    else:
        print("No successful ping-pong cycles completed.")

    print()


if __name__ == '__main__':
    main()
