"""
Binary protocol codec for Helix CAN backend communication.

Protocol format:
    [cmd_id (1 byte)][len (2 bytes LE)][data (variable)]

CAN RX message data (backend → client):
    [timestamp (8 bytes LE)][channel (1 byte)][id (4 bytes LE)]
    [flags (1 byte)][dlc (1 byte)][data (0-64 bytes)]

CAN TX message data (client → backend):
    [channel (1 byte)][id (4 bytes LE)][flags (1 byte)]
    [dlc (1 byte)][data (0-64 bytes)]

Flags:
    0x01: Extended ID (29-bit)
    0x02: Remote frame
    0x04: Error frame
    0x08: FD frame
    0x10: BRS (Bit Rate Switch for FD)
    0x20: ESI (Error State Indicator for FD)
"""

import struct
from dataclasses import dataclass

import can

# Command IDs
CMD_ACK = 0x04
CMD_CAN_RX_NORMAL = 0x05
CMD_CAN_RX_REMOTE = 0x06
CMD_CAN_RX_ERROR = 0x07
CMD_CAN_RX_FD = 0x08
CMD_CAN_TX_NORMAL = 0x09
CMD_CAN_TX_REMOTE = 0x0A
CMD_CAN_TX_ERROR = 0x0B
CMD_CAN_TX_FD = 0x0C

# Flag bits
FLAG_EXTENDED_ID = 0x01
FLAG_REMOTE_FRAME = 0x02
FLAG_ERROR_FRAME = 0x04
FLAG_FD_FRAME = 0x08
FLAG_BRS = 0x10
FLAG_ESI = 0x20

# DLC to length mapping for CAN FD
DLC_TO_LEN = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8,
    9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64
}

LEN_TO_DLC = {v: k for k, v in DLC_TO_LEN.items()}


@dataclass
class CanRxMessage:
    """Received CAN message with metadata."""
    channel: int
    timestamp_ns: int
    message: can.Message


def decode_message(buffer: bytes) -> tuple[int, bytes]:
    """
    Decode a generic message from the binary protocol.

    Args:
        buffer: Raw binary data

    Returns:
        Tuple of (cmd_id, data_payload)

    Raises:
        ValueError: If message is malformed
    """
    if len(buffer) < 3:
        raise ValueError(f"Message too short: {len(buffer)} bytes")

    cmd_id = buffer[0]
    length = struct.unpack('<H', buffer[1:3])[0]

    if len(buffer) < 3 + length:
        raise ValueError(f"Message length mismatch: expected {3 + length}, got {len(buffer)}")

    return cmd_id, buffer[3:3 + length]


def encode_message(cmd_id: int, data: bytes) -> bytes:
    """
    Encode a generic message to the binary protocol format.

    Args:
        cmd_id: Command identifier
        data: Payload data

    Returns:
        Encoded binary message
    """
    if len(data) > 65535:
        raise ValueError(f"Data too long: {len(data)} bytes")

    return bytes([cmd_id]) + struct.pack('<H', len(data)) + data


def _get_data_length(dlc: int, is_fd: bool) -> int:
    """Get actual data length from DLC."""
    if is_fd and dlc > 8:
        return DLC_TO_LEN.get(dlc, dlc)
    return min(dlc, 8)


def decode_can_rx(buffer: bytes) -> CanRxMessage:
    """
    Decode a CAN RX message from the binary protocol.

    Args:
        buffer: Raw binary message (including header)

    Returns:
        CanRxMessage with channel, timestamp, and can.Message

    Raises:
        ValueError: If message is malformed or has invalid command ID
    """
    cmd_id, data = decode_message(buffer)

    if cmd_id not in (CMD_CAN_RX_NORMAL, CMD_CAN_RX_REMOTE, CMD_CAN_RX_ERROR, CMD_CAN_RX_FD):
        raise ValueError(f"Invalid command ID for CAN RX: 0x{cmd_id:02X}")

    if len(data) < 15:
        raise ValueError(f"CAN RX data too short: {len(data)} bytes")

    # Parse header
    timestamp_ns = struct.unpack('<q', data[0:8])[0]
    channel = data[8]
    arbitration_id = struct.unpack('<I', data[9:13])[0]
    flags = data[13]
    dlc = data[14]

    # Determine frame type and data length
    is_extended = bool(flags & FLAG_EXTENDED_ID)
    is_remote = bool(flags & FLAG_REMOTE_FRAME)
    is_error = bool(flags & FLAG_ERROR_FRAME)
    is_fd = bool(flags & FLAG_FD_FRAME)
    is_brs = bool(flags & FLAG_BRS)
    is_esi = bool(flags & FLAG_ESI)

    data_len = _get_data_length(dlc, is_fd)

    expected_len = 15 + data_len
    if len(data) != expected_len:
        raise ValueError(f"CAN RX data length mismatch: expected {expected_len}, got {len(data)}")

    frame_data = data[15:15 + data_len]

    # Convert timestamp from nanoseconds to seconds
    timestamp_sec = timestamp_ns / 1e9

    # Create can.Message
    msg = can.Message(
        timestamp=timestamp_sec,
        arbitration_id=arbitration_id,
        is_extended_id=is_extended,
        is_remote_frame=is_remote,
        is_error_frame=is_error,
        is_fd=is_fd,
        bitrate_switch=is_brs,
        error_state_indicator=is_esi,
        dlc=dlc,
        data=bytes(frame_data),
        channel=channel,
    )

    return CanRxMessage(
        channel=channel,
        timestamp_ns=timestamp_ns,
        message=msg,
    )


def encode_can_tx(msg: can.Message, channel: int = 1) -> bytes:
    """
    Encode a CAN TX message to the binary protocol.

    Args:
        msg: can.Message to send
        channel: CAN channel number (1-255)

    Returns:
        Encoded binary message ready to send over WebSocket
    """
    # Determine command ID
    if msg.is_fd:
        cmd_id = CMD_CAN_TX_FD
    elif msg.is_remote_frame:
        cmd_id = CMD_CAN_TX_REMOTE
    elif msg.is_error_frame:
        cmd_id = CMD_CAN_TX_ERROR
    else:
        cmd_id = CMD_CAN_TX_NORMAL

    # Build flags
    flags = 0
    if msg.is_extended_id:
        flags |= FLAG_EXTENDED_ID
    if msg.is_remote_frame:
        flags |= FLAG_REMOTE_FRAME
    if msg.is_error_frame:
        flags |= FLAG_ERROR_FRAME
    if msg.is_fd:
        flags |= FLAG_FD_FRAME
        if msg.bitrate_switch:
            flags |= FLAG_BRS
        if msg.error_state_indicator:
            flags |= FLAG_ESI

    # Get DLC - for CAN FD, map payload length to FD DLC code
    data = bytes(msg.data)
    data_len = len(data)
    if msg.is_fd and data_len > 8:
        dlc = LEN_TO_DLC.get(data_len)
        if dlc is None:
            # Find the next valid FD length that fits the data
            for valid_len in sorted(LEN_TO_DLC.keys()):
                if valid_len >= data_len:
                    dlc = LEN_TO_DLC[valid_len]
                    break
            else:
                raise ValueError(f"Data length {data_len} exceeds maximum CAN FD payload (64 bytes)")
            # The wire frame carries DLC_TO_LEN[dlc] bytes: pad the
            # payload so it matches the DLC we just committed to.
            data = data.ljust(DLC_TO_LEN[dlc], b'\x00')
    else:
        dlc = msg.dlc if msg.dlc is not None else data_len

    # Build data payload
    # Format: [channel][id (4 bytes)][flags][dlc][data]
    payload = (
        bytes([channel]) +
        struct.pack('<I', msg.arbitration_id) +
        bytes([flags]) +
        bytes([dlc]) +
        data
    )

    return encode_message(cmd_id, payload)


def is_can_rx_message(buffer: bytes) -> bool:
    """Check if buffer contains a CAN RX message."""
    if len(buffer) < 1:
        return False
    cmd_id = buffer[0]
    return cmd_id in (CMD_CAN_RX_NORMAL, CMD_CAN_RX_REMOTE, CMD_CAN_RX_ERROR, CMD_CAN_RX_FD)
