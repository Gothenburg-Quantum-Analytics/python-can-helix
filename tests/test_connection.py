"""Tests for HelixConnectionManager internals."""

import logging
import queue
import weakref

from can_helix.connection import HelixConnectionManager


class _FakeBus:
    pass


def test_queue_full_warning_is_rate_limited(caplog):
    manager = HelixConnectionManager('127.0.0.1', 8080)
    bus = _FakeBus()
    full_queue = queue.Queue(maxsize=1)
    full_queue.put_nowait('occupied')
    subscribers = {(full_queue, weakref.ref(bus))}

    with caplog.at_level(logging.WARNING, logger='can_helix.connection'):
        for _ in range(1000):
            manager._dispatch_to_subscribers(subscribers, 'msg')

    warnings = [r for r in caplog.records if 'queue full' in r.message]
    assert len(warnings) == 1, "expected exactly one rate-limited warning"
    assert '1 message(s)' in warnings[0].message
    assert manager._dropped_messages == 1000


class _FakeCrypto:
    def __init__(self, fp):
        self.client_fingerprint = fp


def test_distinct_crypto_sessions_get_distinct_managers():
    from can_helix.connection import HelixConnectionManager as M
    a = M.get_instance('unittest-host', 9, crypto_session=_FakeCrypto('a' * 64))
    b = M.get_instance('unittest-host', 9, crypto_session=_FakeCrypto('b' * 64))
    same_a = M.get_instance('unittest-host', 9, crypto_session=_FakeCrypto('a' * 64))
    try:
        assert a is not b, "different sessions must not share a WebSocket"
        assert a is same_a, "same session identity must share the manager"
    finally:
        for inst in {a, b}:
            M._remove_instance(inst._registry_key_value)


class TestBatchOption:
    def test_batch_param_in_connect_url(self):
        from can_helix.connection import HelixConnectionManager
        m = HelixConnectionManager('h', 8080, batch=True)
        assert 'batch=1' in m._build_connect_url()
        m_off = HelixConnectionManager('h', 8080)
        assert 'batch=' not in m_off._build_connect_url()

    def test_batch_isolated_in_registry(self):
        from can_helix.connection import HelixConnectionManager as M
        a = M.get_instance('unittest-batch-host', 9)
        b = M.get_instance('unittest-batch-host', 9, batch=True)
        try:
            assert a is not b, "batched and unbatched must not share a WebSocket"
        finally:
            for inst in {a, b}:
                M._remove_instance(inst._registry_key_value)

    def test_batched_message_dispatches_all_records(self):
        import struct
        import weakref

        from can_helix import protocol
        from can_helix.connection import HelixConnectionManager

        manager = HelixConnectionManager('h', 8080, batch=True)
        q = queue.Queue()
        bus = _FakeBus()
        manager._subscribers[1] = {(q, weakref.ref(bus))}

        def rx_record(can_id):
            payload = (struct.pack('<q', 123456789) + bytes([1]) +
                       struct.pack('<I', can_id) + bytes([0]) + bytes([4]) +
                       bytes([1, 2, 3, 4]))
            return protocol.encode_message(protocol.CMD_CAN_RX_NORMAL, payload)

        batch = rx_record(0x100) + rx_record(0x200) + rx_record(0x300)
        manager._handle_binary_message(batch)

        ids = [q.get_nowait().arbitration_id for _ in range(3)]
        assert ids == [0x100, 0x200, 0x300]
        assert q.empty()
