"""Tests for the HelixBus class."""

from unittest.mock import MagicMock, patch

import can
import pytest

from can_helix import protocol


class TestHelixBusInit:
    """Test HelixBus initialization."""

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_init_basic(self, mock_ws, mock_conn_mgr_cls):
        """Test basic initialization with default channel."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(host='192.168.7.1')

        assert bus._can_channel == 1  # default channel
        assert bus._host == '192.168.7.1'
        assert 'helix' in bus.channel_info
        mock_conn_mgr_cls.get_instance.assert_called_once()
        mock_mgr.acquire.assert_called_once()
        mock_mgr.subscribe.assert_called_once()

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_init_with_channel(self, mock_ws, mock_conn_mgr_cls):
        """Test initialization with explicit channel."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=2, host='10.0.0.1')

        assert bus._can_channel == 2
        assert bus._host == '10.0.0.1'
        assert 'ch2' in bus.channel_info

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_init_fd_mode(self, mock_ws, mock_conn_mgr_cls):
        """Test initialization with FD mode."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1', fd=True)

        assert bus._fd is True
        assert bus.protocol == can.CanProtocol.CAN_FD

        bus.shutdown()


class TestHelixBusSend:
    """Test HelixBus send functionality."""

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_send_standard_message(self, mock_ws, mock_conn_mgr_cls):
        """Test sending a standard CAN message."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')

        msg = can.Message(
            arbitration_id=0x123,
            data=[0x01, 0x02, 0x03, 0x04],
        )
        bus.send(msg)

        # Verify send was called on the connection manager
        mock_mgr.send.assert_called_once()
        sent_data = mock_mgr.send.call_args[0][0]
        assert sent_data[0] == protocol.CMD_CAN_TX_NORMAL

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_send_fd_message(self, mock_ws, mock_conn_mgr_cls):
        """Test sending a CAN FD message."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1', fd=True)

        msg = can.Message(
            arbitration_id=0x100,
            data=bytes(64),
            is_fd=True,
            bitrate_switch=True,
        )
        bus.send(msg)

        mock_mgr.send.assert_called_once()
        sent_data = mock_mgr.send.call_args[0][0]
        assert sent_data[0] == protocol.CMD_CAN_TX_FD

        # Verify DLC is 15 (FD mapping for 64 bytes), not 64
        payload = sent_data[3:]  # skip header
        dlc = payload[6]
        assert dlc == 15, f"Expected FD DLC 15 for 64 bytes, got {dlc}"

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_send_after_shutdown_raises(self, mock_ws, mock_conn_mgr_cls):
        """Test that send raises error after shutdown."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')
        bus.shutdown()

        msg = can.Message(arbitration_id=0x123, data=[0x01])

        with pytest.raises(can.CanOperationError, match="shut down"):
            bus.send(msg)


class TestHelixBusReceive:
    """Test HelixBus receive functionality."""

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_recv_timeout(self, mock_ws, mock_conn_mgr_cls):
        """Test receive with timeout returns None."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')

        msg = bus.recv(timeout=0.01)
        assert msg is None

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_recv_message_from_queue(self, mock_ws, mock_conn_mgr_cls):
        """Test receiving a CAN message from the internal queue."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')

        # Simulate a message arriving in the receive queue
        expected_msg = can.Message(
            arbitration_id=0x123,
            data=[0x01, 0x02, 0x03, 0x04],
        )
        bus._recv_queue.put(expected_msg)

        msg = bus.recv(timeout=1.0)

        assert msg is not None
        assert msg.arbitration_id == 0x123
        assert list(msg.data) == [0x01, 0x02, 0x03, 0x04]

        bus.shutdown()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_recv_when_disconnected_raises(self, mock_ws, mock_conn_mgr_cls):
        """Test that recv raises error when disconnected."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = False
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')

        with pytest.raises(can.CanOperationError, match="Connection.*lost"):
            bus.recv(timeout=0.01)

        bus.shutdown()


class TestHelixBusShutdown:
    """Test HelixBus shutdown."""

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_shutdown_unsubscribes_and_releases(self, mock_ws, mock_conn_mgr_cls):
        """Test clean shutdown unsubscribes and releases connection."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        bus = HelixBus(channel=1, host='192.168.7.1')
        bus.shutdown()

        mock_mgr.unsubscribe.assert_called_once()
        mock_mgr.release.assert_called_once()

    @patch('can_helix.bus.HelixConnectionManager')
    @patch('can_helix.bus.websocket', new_callable=MagicMock)
    def test_context_manager(self, mock_ws, mock_conn_mgr_cls):
        """Test using bus as context manager."""
        mock_mgr = MagicMock()
        mock_mgr.is_connected = True
        mock_conn_mgr_cls.get_instance.return_value = mock_mgr

        from can_helix.bus import HelixBus
        with HelixBus(channel=1, host='192.168.7.1') as bus:
            assert bus._can_channel == 1

        # Shutdown should have been called
        mock_mgr.unsubscribe.assert_called()
        mock_mgr.release.assert_called()


class TestShutdownIdempotent:
    def test_double_shutdown_releases_manager_once(self):
        import queue as _queue
        import threading
        from unittest import mock

        from can_helix.bus import HelixBus
        bus = HelixBus.__new__(HelixBus)
        bus._shutdown_flag = threading.Event()
        bus._connection_manager = mock.MagicMock()
        bus._recv_queue = _queue.Queue()
        bus._can_channel = 1
        bus._rest_client = None
        bus._owns_rest_client = True
        bus._session = None
        bus._periodic_tasks = []
        bus._is_shutdown = False

        bus.shutdown()
        bus.shutdown()

        assert bus._connection_manager.release.call_count == 1
