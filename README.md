# python-can-helix

A [python-can](https://python-can.readthedocs.io/) interface for connecting to
the Helix CAN backend via WebSocket.

This allows you to use standard python-can APIs to send and receive CAN messages
through the Helix CAN backend server.

## Features

- Full python-can `BusABC` compatibility
- Support for standard CAN and CAN FD frames
- Multiple channel support
- Optional device-side frame batching for high-rate buses (`batch=True`):
  the device coalesces frames for up to ~2 ms and packs several into one
  sealed WebSocket message, cutting per-frame crypto/transport overhead
  (roughly 2x lower client CPU under load). Leave off for
  request/response traffic where per-frame latency matters.
- Proper exception handling following python-can conventions
- **Bitrate configuration** via REST API
- **Bus on/off control** via REST API
- **Bus state monitoring** via `state` property

## Installation

The package is distributed as source on GitHub (it is not on PyPI yet):

```bash
# Tagged release, straight from GitHub
pip install "git+https://github.com/Gothenburg-Quantum-Analytics/python-can-helix.git@v0.3.0"

# Or from a checkout
git clone https://github.com/Gothenburg-Quantum-Analytics/python-can-helix.git
cd python-can-helix
pip install -e .
```

Each release also has a built wheel and sdist attached on the
[GitHub Releases](https://github.com/Gothenburg-Quantum-Analytics/python-can-helix/releases)
page.

## Quick Start

```python
import can
from can_helix import HelixSession

# Authenticate first (enables encryption); credentials can also come
# from the HELIX_USERNAME / HELIX_PASSWORD environment variables.
session = HelixSession('192.168.7.1', username='admin', password='secret')

# Connect to the Helix backend
bus = can.Bus(
    interface='helix',
    channel=1,           # CAN channel (1, 2, etc.)
    session=session,
)

# Send a CAN message
msg = can.Message(
    arbitration_id=0x123,
    data=[0x01, 0x02, 0x03, 0x04],
    is_extended_id=False,
)
bus.send(msg)

# Receive CAN messages
while True:
    msg = bus.recv(timeout=1.0)
    if msg:
        print(f"Received: {msg}")

# Cleanup
bus.shutdown()
session.close()
```

## Bitrate Configuration

Configure bitrate at initialization or change it at runtime:

```python
import can
from can_helix import HelixSession

session = HelixSession('192.168.7.1', username='admin', password='secret')

# Configure bitrate at initialization (via REST API on port 9999)
bus = can.Bus(
    interface='helix',
    channel=1,
    bitrate=500000,      # 500 kbit/s
    session=session,
)

# Change bitrate at runtime (recommended: take bus off first)
bus.bus_off()
bus.set_bitrate(250000)  # Change to 250 kbit/s
bus.bus_on()
```

### CAN FD with Dual Bitrate

```python
bus = can.Bus(
    interface='helix',
    channel=1,
    bitrate=500000,        # 500 kbit/s arbitration phase
    data_bitrate=2000000,  # 2 Mbit/s data phase
    fd=True,
    session=session,
)
```

## Bus State Control

Control and monitor the CAN bus state:

```python
import can
from can_helix import HelixSession

session = HelixSession('192.168.7.1', username='admin', password='secret')
bus = can.Bus(interface='helix', channel=1, session=session)

# Check current bus state
print(f"State: {bus.state}")  # BusState.ACTIVE, PASSIVE, or ERROR

# Take bus off (stops TX/RX on physical bus)
bus.bus_off()

# Put bus back on
bus.bus_on()

# Get detailed channel configuration
config = bus.get_channel_config()
print(f"Bitrate: {config['baud']}")
print(f"Bus on: {config['bus_on']}")
print(f"FD mode: {config['fd']}")

# Open/close channel
bus.close_channel()
bus.open_channel(fd=True)
```

## Advanced Usage

### CAN FD Support

```python
bus = can.Bus(
    interface='helix',
    channel=1,
    fd=True,           # Enable CAN FD mode
    session=session,
)

# Send CAN FD message
msg = can.Message(
    arbitration_id=0x123,
    data=bytes(64),  # Up to 64 bytes for FD
    is_fd=True,
    bitrate_switch=True,
)
bus.send(msg)
```

### Using with python-can Notifier

```python
import can
import time
from can import Notifier, Printer
from can_helix import HelixSession

session = HelixSession('192.168.7.1', username='admin', password='secret')
bus = can.Bus(interface='helix', channel=1, session=session)

# Create notifier with listeners
notifier = Notifier(bus, [Printer()])

# Messages will be printed automatically
try:
    while True:
        time.sleep(1)
finally:
    notifier.stop()
    bus.shutdown()
    session.close()
```

### Message Filtering

```python
bus = can.Bus(
    interface='helix',
    channel=1,
    session=session,
    can_filters=[
        {"can_id": 0x100, "can_mask": 0x7FF, "extended": False},
        {"can_id": 0x200, "can_mask": 0x7FF, "extended": False},
    ],
)
```

## Error Handling & Reconnection

Like other python-can interfaces (socketcand, udp_multicast), this interface
**does NOT automatically reconnect** when the connection is lost. When the
backend disconnects, a `can.CanOperationError` will be raised on the next
`send()` or `recv()` call.

Your application is responsible for handling reconnection:

```python
import can
import time
from can_helix import HelixSession

# One session outlives any number of bus connections
session = HelixSession('192.168.7.1', username='admin', password='secret')

def connect():
    return can.Bus(interface='helix', channel=1, session=session)

bus = connect()

while True:
    try:
        msg = bus.recv(timeout=1.0)
        if msg:
            print(msg)

    except can.CanOperationError as e:
        # Connection lost - reconnect
        print(f"Connection lost: {e}")
        bus.shutdown()
        time.sleep(2.0)  # Wait before retry
        bus = connect()

    except KeyboardInterrupt:
        break

bus.shutdown()
session.close()
```

See `examples/reconnect_example.py` for a complete example.

## Configuration

### Using can.rc Configuration

python-can's configuration system can supply the interface, channel and
host. Keys are read from the `[default]` section (or from a named
section selected with `config_context=`), and every key is passed to the
bus as a keyword argument:

```ini
# ~/.canrc, ~/.can or /etc/can.conf
[default]
interface = helix
channel = 1
host = 192.168.7.1
```

Credentials never come from the config file: authenticate with a
`HelixSession` and pass it explicitly; everything else is taken from
the file:

```python
import can
from can_helix import HelixSession

session = HelixSession("192.168.7.1")   # HELIX_USERNAME / HELIX_PASSWORD
bus = can.Bus(session=session)          # interface, channel, host from can.rc
```

### Environment Variables

python-can reads `CAN_INTERFACE`, `CAN_CHANNEL` and `CAN_BITRATE`; any
other bus argument goes into `CAN_CONFIG` as JSON. `HelixSession` reads
its credentials from `HELIX_USERNAME` / `HELIX_PASSWORD`:

```bash
export CAN_INTERFACE=helix
export CAN_CHANNEL=1
export CAN_CONFIG='{"host": "192.168.7.1"}'
export HELIX_USERNAME=admin
export HELIX_PASSWORD=secret
```

## Security

### Recommended: pre-authenticated sessions

Authenticate *before* creating the bus, so credentials never appear in
the python-can bus configuration. This matters because **python-can
logs the full bus config — including a `password=` argument — at DEBUG
level** on the `can` logger. With a `HelixSession`, that log line
contains no secrets:

```python
import can
from can_helix import HelixSession

# Credentials from HELIX_USERNAME / HELIX_PASSWORD env vars
# (or pass username= / password= explicitly):
session = HelixSession("192.168.7.1")

bus = can.Bus(interface="helix", channel=1, session=session)
...
bus.shutdown()      # the session survives bus shutdown
session.close()     # caller owns the session lifecycle
```

Several buses can share one session. `HelixSession` also works as a
context manager.

### Device identity: trust on first use, then pin

The device's public key is fetched over plain HTTP, so the encrypted
channel is *trust-on-first-use*: fine on a trusted LAN, but a
man-in-the-middle at first contact could impersonate the device. To
lock a deployment down, read the fingerprint once and pin it:

```python
session = HelixSession("192.168.7.1")
print(session.device_fingerprint)   # record this value
# e.g. 365d3bf394785e465c26de3b66cfc43bd99f2bb304dbd37cca369da86b112bb3
```

```python
session = HelixSession(
    "192.168.7.1",
    device_fingerprint="365d3bf394785e46...",  # full value
)
```

A pinned session verifies the device's key **before sending
credentials** and raises `AuthError` on mismatch, so a wrong or
impersonated device never sees your password. The fingerprint is
computed locally as SHA-256 over the device's raw public key.

### Why there are no username/password bus arguments

`HelixSession` is the only way to authenticate — deliberately. python-can
logs the full bus configuration at DEBUG level, so credentials passed as
bus arguments would end up in logs. With a session object, that log line
contains no secrets. (An unauthenticated `can.Bus(interface='helix', ...)`
without a session remains available for legacy backends without
authentication.)

## Protocol Details

This interface uses a binary WebSocket protocol to communicate with
the Helix CAN backend. The protocol format:

### Message Header
```
[cmd_id: u8][length: u16 LE][payload: variable]
```

### CAN RX Payload (backend → client)
```
[timestamp: i64 LE][channel: u8][can_id: u32 LE][flags: u8][dlc: u8][data: 0-64 bytes]
```

### CAN TX Payload (client → backend)
```
[channel: u8][can_id: u32 LE][flags: u8][dlc: u8][data: 0-64 bytes]
```

### Flags
| Bit | Meaning |
|-----|---------|
| 0   | Extended ID (29-bit) |
| 1   | Remote frame |
| 2   | Error frame |
| 3   | FD frame |
| 4   | BRS (Bit Rate Switch) |
| 5   | ESI (Error State Indicator) |

## Development

### Setup Development Environment

```bash
cd python-can-helix
pip install -e .[dev]
```

### Run Tests

```bash
pytest
```

### Code Quality

```bash
black .
ruff check .
mypy .
```

## Requirements

- Python >= 3.10
- python-can >= 4.0.0
- websocket-client >= 1.0.0
- requests >= 2.25.0
- cryptography >= 41.0.0

## License

MIT License - see [LICENSE](LICENSE) file for details.

## Related Projects

- [python-can](https://python-can.readthedocs.io/) - CAN bus abstraction library
