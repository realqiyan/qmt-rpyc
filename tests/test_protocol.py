import time
import pytest
from common.protocol import (
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


class TestConstants:
    def test_status_constants(self):
        assert STATUS_OK == "ok"
        assert STATUS_ERROR == "error"

    def test_event_types_mapping(self):
        assert "order" in EVENT_TYPES
        assert EVENT_TYPES["order"] == "on_stock_order"
        assert EVENT_TYPES["disconnect"] == "on_disconnected"
