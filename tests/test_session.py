"""Tests for HelixSession (pre-authenticated sessions + fingerprint pinning)."""

import base64
import hashlib
from unittest import mock

import pytest

from can_helix.auth import AuthError, HelixAuthClient
from can_helix.session import HelixSession

DEVICE_KEY_B64 = base64.b64encode(b'\x04' + b'\x11' * 64).decode('ascii')
DEVICE_FP = hashlib.sha256(base64.b64decode(DEVICE_KEY_B64)).hexdigest()


def _mock_rest_client(*args, **kwargs):
    client = mock.MagicMock()
    client.device_fingerprint = DEVICE_FP
    return client


@mock.patch('can_helix.session.HelixRestClient', side_effect=_mock_rest_client)
class TestHelixSession:
    def test_requires_credentials(self, _rc, monkeypatch):
        monkeypatch.delenv('HELIX_USERNAME', raising=False)
        monkeypatch.delenv('HELIX_PASSWORD', raising=False)
        with pytest.raises(AuthError, match='Credentials required'):
            HelixSession('1.2.3.4')

    def test_env_credentials(self, rc, monkeypatch):
        monkeypatch.setenv('HELIX_USERNAME', 'envuser')
        monkeypatch.setenv('HELIX_PASSWORD', 'envpass')
        s = HelixSession('1.2.3.4')
        assert s.username == 'envuser'
        _, kwargs = rc.call_args
        assert kwargs['username'] == 'envuser'
        assert kwargs['password'] == 'envpass'

    def test_repr_contains_no_secrets(self, _rc):
        s = HelixSession('1.2.3.4', username='admin', password='hunter2')
        assert 'hunter2' not in repr(s)
        assert 'admin' in repr(s)
        assert DEVICE_FP[:16] in repr(s)

    def test_fingerprint_passed_to_client(self, rc):
        HelixSession('1.2.3.4', username='u', password='p',
                     device_fingerprint='abc123')
        _, kwargs = rc.call_args
        assert kwargs['expected_device_fingerprint'] == 'abc123'

    def test_close_is_idempotent_and_invalidates(self, _rc):
        s = HelixSession('1.2.3.4', username='u', password='p')
        s.close()
        s.close()
        with pytest.raises(AuthError, match='closed'):
            _ = s.device_fingerprint

    def test_context_manager_closes(self, _rc):
        with HelixSession('1.2.3.4', username='u', password='p') as s:
            client = s.rest_client
        client.close.assert_called_once()


class TestFingerprintVerification:
    """HelixAuthClient._init_crypto pinning behavior."""

    def _client_with_device_key(self, expected_fp=None, claimed_fp=None):
        client = HelixAuthClient('http://1.2.3.4:9999',
                                 expected_device_fingerprint=expected_fp)
        response = mock.MagicMock()
        response.json.return_value = {
            'public_key': DEVICE_KEY_B64,
            'fingerprint': claimed_fp or DEVICE_FP,
        }
        response.raise_for_status.return_value = None
        client._session = mock.MagicMock()
        client._session.get.return_value = response
        return client

    def test_tofu_accepts_and_computes_fingerprint(self):
        client = self._client_with_device_key()
        with mock.patch('can_helix.auth.CryptoSession'):
            client._init_crypto()
        assert client.device_fingerprint == DEVICE_FP

    def test_pin_match_accepts(self):
        client = self._client_with_device_key(expected_fp=DEVICE_FP.upper())
        with mock.patch('can_helix.auth.CryptoSession'):
            client._init_crypto()
        assert client.device_fingerprint == DEVICE_FP

    def test_pin_mismatch_rejects_before_crypto(self):
        client = self._client_with_device_key(expected_fp='0' * 64)
        with mock.patch('can_helix.auth.CryptoSession') as crypto:
            with pytest.raises(AuthError, match='fingerprint mismatch'):
                client._init_crypto()
            crypto.assert_not_called()

    def test_computed_fingerprint_wins_over_claimed(self):
        client = self._client_with_device_key(claimed_fp='not-the-real-one')
        with mock.patch('can_helix.auth.CryptoSession'):
            client._init_crypto()
        assert client.device_fingerprint == DEVICE_FP


def test_unpinned_login_warns_with_fingerprint(caplog):
    import logging as _logging
    with mock.patch('can_helix.session.HelixRestClient',
                    side_effect=_mock_rest_client):
        with caplog.at_level(_logging.WARNING, logger='can_helix.session'):
            HelixSession('h', username='u', password='p')
    warnings = [r for r in caplog.records if 'trust-on-first-use' in r.message]
    assert len(warnings) == 1
    assert DEVICE_FP in warnings[0].message


def test_pinned_login_does_not_warn(caplog):
    import logging as _logging
    with mock.patch('can_helix.session.HelixRestClient',
                    side_effect=_mock_rest_client):
        with caplog.at_level(_logging.WARNING, logger='can_helix.session'):
            HelixSession('h', username='u', password='p',
                         device_fingerprint=DEVICE_FP)
    assert not [r for r in caplog.records if 'trust-on-first-use' in r.message]
