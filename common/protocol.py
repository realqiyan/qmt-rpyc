import hashlib
import hmac
import time

STATUS_OK = "ok"
STATUS_ERROR = "error"

AUTH_NONCE_LEN = 16
AUTH_TIMESTAMP_WINDOW = 60

# Documentation-only mapping of event type names to callback method names.
# The server/connection.py _Callback class uses this same mapping implicitly
# via its method names (on_stock_order, on_stock_trade, etc.).
EVENT_TYPES = {
    "order": "on_stock_order",
    "trade": "on_stock_trade",
    "disconnect": "on_disconnected",
    "order_error": "on_order_error",
    "cancel_error": "on_cancel_error",
    "account_status": "on_account_status",
    "async_response": "on_order_stock_async_response",
    "reconnect": None,
}


def make_auth_token(auth_key: str, nonce: str, timestamp: int) -> str:
    msg = f"{nonce}|{timestamp}".encode()
    return hmac.new(auth_key.encode(), msg, hashlib.sha256).hexdigest()


def verify_auth_token(auth_key: str, nonce: str, timestamp: int, token: str) -> bool:
    if not isinstance(auth_key, str) or not auth_key:
        return False
    if not isinstance(nonce, str) or len(nonce) != AUTH_NONCE_LEN:
        return False
    if not isinstance(timestamp, int) or isinstance(timestamp, bool):
        return False
    if not isinstance(token, str) or len(token) != 64:
        return False
    now = int(time.time())
    if abs(now - timestamp) > AUTH_TIMESTAMP_WINDOW:
        return False
    expected = make_auth_token(auth_key, nonce, timestamp)
    return hmac.compare_digest(expected, token)
