"""Tests for the protocol codec."""

import struct

import can
import pytest

from can_helix import protocol


class TestProtocolEncode:
    """Test message encoding."""

    def test_encode_message_basic(self):
        """Test basic message encoding."""
        result = protocol.encode_message(0x01, b'\x01\x02\x03')
        assert result[0] == 0x01  # cmd_id
        assert struct.unpack('<H', result[1:3])[0] == 3  # length
        assert result[3:] == b'\x01\x02\x03'  # data

    def test_encode_message_empty(self):
        """Test encoding empty message."""
        result = protocol.encode_message(0x05, b'')
        assert result[0] == 0x05
        assert struct.unpack('<H', result[1:3])[0] == 0
        assert len(result) == 3

    def test_encode_can_tx_standard(self):
        """Test encoding standard CAN TX message."""
        msg = can.Message(
            arbitration_id=0x123,
            data=[0x01, 0x02, 0x03, 0x04],
            is_extended_id=False,
        )

        result = protocol.encode_can_tx(msg, channel=0)

        # Check command ID
        assert result[0] == protocol.CMD_CAN_TX_NORMAL

        # Check length
        length = struct.unpack('<H', result[1:3])[0]
        assert length == 7 + 4  # header + data

        # Parse payload
        payload = result[3:]
        assert payload[0] == 0  # channel
        assert struct.unpack('<I', payload[1:5])[0] == 0x123  # arbitration_id
        assert payload[5] == 0  # flags (standard, not extended)
        assert payload[6] == 4  # dlc
        assert payload[7:11] == b'\x01\x02\x03\x04'  # data

    def test_encode_can_tx_extended(self):
        """Test encoding extended ID CAN TX message."""
        msg = can.Message(
            arbitration_id=0x12345678,
            data=[0xAA, 0xBB],
            is_extended_id=True,
        )

        result = protocol.encode_can_tx(msg, channel=1)

        assert result[0] == protocol.CMD_CAN_TX_NORMAL

        payload = result[3:]
        assert payload[0] == 1  # channel
        assert struct.unpack('<I', payload[1:5])[0] == 0x12345678
        assert payload[5] & protocol.FLAG_EXTENDED_ID  # extended flag set

    def test_encode_can_tx_fd(self):
        """Test encoding CAN FD TX message."""
        msg = can.Message(
            arbitration_id=0x100,
            data=bytes(64),
            is_fd=True,
            bitrate_switch=True,
        )

        result = protocol.encode_can_tx(msg, channel=0)

        assert result[0] == protocol.CMD_CAN_TX_FD

        payload = result[3:]
        flags = payload[5]
        assert flags & protocol.FLAG_FD_FRAME
        assert flags & protocol.FLAG_BRS

    def test_encode_can_tx_remote(self):
        """Test encoding remote frame."""
        msg = can.Message(
            arbitration_id=0x200,
            is_remote_frame=True,
            dlc=8,
        )

        result = protocol.encode_can_tx(msg, channel=0)

        assert result[0] == protocol.CMD_CAN_TX_REMOTE


class TestProtocolDecode:
    """Test message decoding."""

    def test_decode_message_basic(self):
        """Test basic message decoding."""
        data = bytes([0x05, 0x03, 0x00, 0x01, 0x02, 0x03])
        cmd_id, payload = protocol.decode_message(data)

        assert cmd_id == 0x05
        assert payload == b'\x01\x02\x03'

    def test_decode_message_too_short(self):
        """Test decoding message that's too short."""
        with pytest.raises(ValueError, match="too short"):
            protocol.decode_message(b'\x01\x02')

    def test_decode_message_length_mismatch(self):
        """Test decoding message with wrong length."""
        data = bytes([0x05, 0x10, 0x00, 0x01])  # Claims 16 bytes, has 1
        with pytest.raises(ValueError, match="length mismatch"):
            protocol.decode_message(data)

    def test_decode_can_rx_standard(self):
        """Test decoding standard CAN RX message."""
        # Build a valid CAN RX message
        timestamp_ns = 1234567890123456789
        channel = 0
        arb_id = 0x123
        flags = 0  # standard, normal frame
        dlc = 4
        frame_data = b'\x01\x02\x03\x04'

        payload = (
            struct.pack('<q', timestamp_ns) +
            bytes([channel]) +
            struct.pack('<I', arb_id) +
            bytes([flags, dlc]) +
            frame_data
        )

        # Wrap in message format
        buffer = protocol.encode_message(protocol.CMD_CAN_RX_NORMAL, payload)

        rx_msg = protocol.decode_can_rx(buffer)

        assert rx_msg.channel == 0
        assert rx_msg.timestamp_ns == timestamp_ns
        assert rx_msg.message.arbitration_id == 0x123
        assert not rx_msg.message.is_extended_id
        assert rx_msg.message.data == frame_data
        assert rx_msg.message.dlc == 4

    def test_decode_can_rx_extended(self):
        """Test decoding extended ID CAN RX message."""
        timestamp_ns = 999999999999999
        channel = 1
        arb_id = 0x12345678
        flags = protocol.FLAG_EXTENDED_ID
        dlc = 8
        frame_data = bytes(8)

        payload = (
            struct.pack('<q', timestamp_ns) +
            bytes([channel]) +
            struct.pack('<I', arb_id) +
            bytes([flags, dlc]) +
            frame_data
        )

        buffer = protocol.encode_message(protocol.CMD_CAN_RX_NORMAL, payload)
        rx_msg = protocol.decode_can_rx(buffer)

        assert rx_msg.message.is_extended_id
        assert rx_msg.message.arbitration_id == 0x12345678

    def test_decode_can_rx_fd(self):
        """Test decoding CAN FD RX message."""
        timestamp_ns = 1000000000
        channel = 0
        arb_id = 0x100
        flags = protocol.FLAG_FD_FRAME | protocol.FLAG_BRS
        dlc = 15  # 64 bytes in FD
        frame_data = bytes(64)

        payload = (
            struct.pack('<q', timestamp_ns) +
            bytes([channel]) +
            struct.pack('<I', arb_id) +
            bytes([flags, dlc]) +
            frame_data
        )

        buffer = protocol.encode_message(protocol.CMD_CAN_RX_FD, payload)
        rx_msg = protocol.decode_can_rx(buffer)

        assert rx_msg.message.is_fd
        assert rx_msg.message.bitrate_switch
        assert len(rx_msg.message.data) == 64

    def test_decode_can_rx_invalid_cmd(self):
        """Test decoding message with invalid command ID."""
        buffer = protocol.encode_message(0xFF, bytes(20))
        with pytest.raises(ValueError, match="Invalid command ID"):
            protocol.decode_can_rx(buffer)


class TestIsCanRxMessage:
    """Test is_can_rx_message function."""

    def test_is_can_rx_normal(self):
        """Test recognizing normal CAN RX."""
        assert protocol.is_can_rx_message(bytes([protocol.CMD_CAN_RX_NORMAL]))

    def test_is_can_rx_fd(self):
        """Test recognizing FD CAN RX."""
        assert protocol.is_can_rx_message(bytes([protocol.CMD_CAN_RX_FD]))

    def test_is_not_can_rx(self):
        """Test rejecting non-CAN-RX messages."""
        assert not protocol.is_can_rx_message(bytes([protocol.CMD_CAN_TX_NORMAL]))
        assert not protocol.is_can_rx_message(bytes([protocol.CMD_ACK]))

    def test_empty_buffer(self):
        """Test handling empty buffer."""
        assert not protocol.is_can_rx_message(b'')


class TestRoundTrip:
    """Test encode/decode round trips."""

    def test_roundtrip_standard_frame(self):
        """Test encoding then decoding standard frame."""
        original = can.Message(
            arbitration_id=0x7FF,
            data=[0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88],
            is_extended_id=False,
        )

        # Encode as TX, then create RX message to decode
        # (simulating what the backend would do)
        tx_data = protocol.encode_can_tx(original, channel=0)

        # Extract the relevant parts to create RX message
        # TX format: cmd_id, len, channel, id, flags, dlc, data
        tx_payload = tx_data[3:]

        timestamp_ns = 1234567890000000000
        rx_payload = (
            struct.pack('<q', timestamp_ns) +
            tx_payload  # channel, id, flags, dlc, data are same
        )

        rx_buffer = protocol.encode_message(protocol.CMD_CAN_RX_NORMAL, rx_payload)
        decoded = protocol.decode_can_rx(rx_buffer)

        assert decoded.message.arbitration_id == original.arbitration_id
        assert list(decoded.message.data) == list(original.data)
        assert decoded.message.is_extended_id == original.is_extended_id


class TestCanFdDlcMapping:
    """Test CAN FD DLC encoding uses correct DLC codes."""

    @pytest.mark.parametrize("data_len,expected_dlc", [
        (12, 9),
        (16, 10),
        (20, 11),
        (24, 12),
        (32, 13),
        (48, 14),
        (64, 15),
    ])
    def test_fd_dlc_mapping(self, data_len, expected_dlc):
        """Test that FD frames encode the correct DLC for each payload size."""
        msg = can.Message(
            arbitration_id=0x100,
            data=bytes(data_len),
            is_fd=True,
            bitrate_switch=True,
        )

        result = protocol.encode_can_tx(msg, channel=0)

        # Parse payload: [channel(1)][id(4)][flags(1)][dlc(1)][data(...)]
        payload = result[3:]
        dlc = payload[6]
        assert dlc == expected_dlc, (
            f"Data length {data_len} should map to DLC {expected_dlc}, got {dlc}"
        )

    @pytest.mark.parametrize("data_len", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_fd_dlc_small_payloads(self, data_len):
        """Test that FD frames with <=8 bytes use raw length as DLC."""
        msg = can.Message(
            arbitration_id=0x100,
            data=bytes(data_len),
            is_fd=True,
        )

        result = protocol.encode_can_tx(msg, channel=0)
        payload = result[3:]
        dlc = payload[6]
        assert dlc == data_len

    def test_non_fd_uses_raw_dlc(self):
        """Test that standard CAN frames use raw DLC (not FD mapping)."""
        msg = can.Message(
            arbitration_id=0x100,
            data=[0x01, 0x02, 0x03, 0x04, 0x05],
        )

        result = protocol.encode_can_tx(msg, channel=0)
        payload = result[3:]
        dlc = payload[6]
        assert dlc == 5


class TestFdPayloadPadding:
    """The wire payload must match the DLC the encoder commits to."""

    def test_fd_odd_length_is_padded_to_dlc_length(self):
        import can as _can

        from can_helix.protocol import DLC_TO_LEN, encode_can_tx
        msg = _can.Message(arbitration_id=0x123, data=bytes(range(9)),
                           is_fd=True, is_extended_id=False)
        encoded = encode_can_tx(msg, channel=1)
        # message payload = [channel][id:4][flags][dlc][data...]
        body = encoded[encoded.index(1, 0):]  # from channel byte
        dlc = body[6]
        data = body[7:]
        assert DLC_TO_LEN[dlc] == 12, "9 bytes must round up to 12-byte FD frame"
        assert len(data) == 12, "payload must be padded to the DLC length"
        assert data[:9] == bytes(range(9))
        assert data[9:] == b'\x00\x00\x00'
