import hashlib
import hmac
import os
import socket
import struct
import time

STATUS_OK = "ok"
STATUS_ERROR = "error"

PROTOCOL_VERSION = 1
API_SURFACE_SCHEMA_VERSION = 1

AUTH_NONCE_LEN = 16
AUTH_TIMESTAMP_WINDOW = 60

_AUTH_MAGIC = b"QMTRPYC1"
_AUTH_CONTEXT = b"qmt-rpyc/socket-auth/v1\0"
_AUTH_CHALLENGE_BYTES = 32
_AUTH_DIGEST_BYTES = hashlib.sha256().digest_size
_AUTH_TIMEOUT_SECONDS = 5.0
_AUTH_CHALLENGE_SIZE = len(_AUTH_MAGIC) + 2 + _AUTH_CHALLENGE_BYTES
_AUTH_RESPONSE_SIZE = len(_AUTH_MAGIC) + 2 + _AUTH_DIGEST_BYTES
_AUTH_OK = b"\x01"
_AUTH_FAILED = b"\x00"

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


class SocketAuthError(Exception):
    """Raised when the pre-RPyC socket authentication fails."""


class ProtocolVersionError(SocketAuthError):
    """Raised when client and server protocol versions are incompatible."""


def _recv_exact(sock, size):
    chunks = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise SocketAuthError("connection closed during authentication")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _auth_digest(auth_key, server_version, client_version, challenge):
    if not isinstance(auth_key, str) or not auth_key:
        raise SocketAuthError("authentication key is required")
    message = (
        _AUTH_CONTEXT
        + struct.pack("!HH", server_version, client_version)
        + challenge
    )
    return hmac.new(
        auth_key.encode("utf-8"), message, hashlib.sha256
    ).digest()


def authenticate_client_socket(sock, auth_key, timeout=_AUTH_TIMEOUT_SECONDS):
    """Authenticate an already-connected socket before starting RPyC."""
    previous_timeout = sock.gettimeout()
    sock.settimeout(timeout)
    try:
        payload = _recv_exact(sock, _AUTH_CHALLENGE_SIZE)
        magic = payload[:len(_AUTH_MAGIC)]
        if magic != _AUTH_MAGIC:
            raise SocketAuthError("server did not provide qmt-rpyc auth challenge")
        server_version = struct.unpack(
            "!H", payload[len(_AUTH_MAGIC):len(_AUTH_MAGIC) + 2]
        )[0]
        if server_version != PROTOCOL_VERSION:
            raise ProtocolVersionError(
                "protocol mismatch: client={}, server={}".format(
                    PROTOCOL_VERSION, server_version
                )
            )
        challenge = payload[-_AUTH_CHALLENGE_BYTES:]
        digest = _auth_digest(
            auth_key, server_version, PROTOCOL_VERSION, challenge
        )
        response = (
            _AUTH_MAGIC
            + struct.pack("!H", PROTOCOL_VERSION)
            + digest
        )
        sock.sendall(response)
        if _recv_exact(sock, 1) != _AUTH_OK:
            raise SocketAuthError("authentication failed")
    except socket.timeout as e:
        raise SocketAuthError("authentication timed out") from e
    finally:
        sock.settimeout(previous_timeout)
    return sock


def make_server_authenticator(
        auth_key, rate_limiter, timeout=_AUTH_TIMEOUT_SECONDS):
    """Build an RPyC server authenticator using a server nonce challenge."""
    from rpyc.utils.authenticators import AuthenticationError

    def authenticate(sock):
        try:
            peer = sock.getpeername()
            ip = peer[0] if peer else "unknown"
        except OSError:
            ip = "unknown"
        if rate_limiter.is_locked(ip):
            raise AuthenticationError("client is temporarily locked out")

        previous_timeout = sock.gettimeout()
        sock.settimeout(timeout)
        try:
            challenge = os.urandom(_AUTH_CHALLENGE_BYTES)
            sock.sendall(
                _AUTH_MAGIC
                + struct.pack("!H", PROTOCOL_VERSION)
                + challenge
            )
            payload = _recv_exact(sock, _AUTH_RESPONSE_SIZE)
            magic = payload[:len(_AUTH_MAGIC)]
            client_version = struct.unpack(
                "!H", payload[len(_AUTH_MAGIC):len(_AUTH_MAGIC) + 2]
            )[0]
            digest = payload[-_AUTH_DIGEST_BYTES:]
            expected = _auth_digest(
                auth_key, PROTOCOL_VERSION, client_version, challenge
            )
            ok = (
                magic == _AUTH_MAGIC
                and client_version == PROTOCOL_VERSION
                and hmac.compare_digest(expected, digest)
            )
            sock.sendall(_AUTH_OK if ok else _AUTH_FAILED)
            if not ok:
                rate_limiter.record_failure(ip)
                raise AuthenticationError("invalid credentials or protocol")
            rate_limiter.record_success(ip)
            return sock, {
                "authenticated": True,
                "protocol_version": client_version,
                "peer_ip": ip,
            }
        except (OSError, SocketAuthError, socket.timeout) as e:
            rate_limiter.record_failure(ip)
            raise AuthenticationError(str(e)) from e
        finally:
            sock.settimeout(previous_timeout)

    return authenticate
