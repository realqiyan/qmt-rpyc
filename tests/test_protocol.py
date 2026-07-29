import time
import pytest
from qmt_rpyc.protocol import (
    API_SURFACE_SCHEMA_VERSION, PROTOCOL_VERSION,
    make_auth_token, verify_auth_token,
    STATUS_OK, STATUS_ERROR, AUTH_TIMESTAMP_WINDOW, EVENT_TYPES,
)


class TestAuthProtocol:
    def test_make_and_verify_token_success(self):
        key = "secret-key"
        nonce = "abcdef1234567890"
        ts = int(time.time())
        token = make_auth_token(key, nonce, ts)
        assert verify_auth_token(key, nonce, ts, token) is True

    def test_verify_token_wrong_key(self):
        nonce = "abcdef1234567890"
        ts = int(time.time())
        token = make_auth_token("correct-key", nonce, ts)
        assert verify_auth_token("wrong-key", nonce, ts, token) is False

    def test_verify_token_expired_timestamp(self):
        key = "secret-key"
        nonce = "abcdef1234567890"
        ts = int(time.time()) - AUTH_TIMESTAMP_WINDOW - 1
        token = make_auth_token(key, nonce, ts)
        assert verify_auth_token(key, nonce, ts, token) is False

    def test_verify_token_tampered_nonce(self):
        key = "secret-key"
        ts = int(time.time())
        token = make_auth_token(key, "original-nonce", ts)
        assert verify_auth_token(key, "tampered-nonce", ts, token) is False

    @pytest.mark.parametrize("nonce,timestamp,token", [
        ("short", 1, "0" * 64),
        ("abcdef1234567890", "1", "0" * 64),
        ("abcdef1234567890", 1, "short"),
    ])
    def test_invalid_auth_fields_are_rejected(
            self, nonce, timestamp, token):
        assert verify_auth_token(
            "secret-key", nonce, timestamp, token) is False

class TestConstants:
    def test_protocol_versions_are_explicit(self):
        assert PROTOCOL_VERSION == 1
        assert API_SURFACE_SCHEMA_VERSION == 1

    def test_status_constants(self):
        assert STATUS_OK == "ok"
        assert STATUS_ERROR == "error"

    def test_event_types_mapping(self):
        assert "order" in EVENT_TYPES
        assert EVENT_TYPES["order"] == "on_stock_order"
        assert EVENT_TYPES["disconnect"] == "on_disconnected"
        assert "reconnect" in EVENT_TYPES
