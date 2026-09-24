"""Socket challenge authentication, separate from the business contract."""
import pytest
from qmt_rpyc.transport.auth import (
    PROTOCOL_VERSION, SocketAuthError, _auth_digest, _recv_exact,
    authenticate_client_socket,
)


class Socket:
    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.timeout = 10
    def recv(self, size):
        return next(self.chunks, b'')
    def gettimeout(self):
        return self.timeout
    def settimeout(self, value):
        self.timeout = value


def test_challenge_digest_binds_key_versions_and_nonce():
    expected = _auth_digest('key', 1, 1, b'challenge')
    assert expected != _auth_digest('other', 1, 1, b'challenge')
    assert expected != _auth_digest('key', 2, 1, b'challenge')
    assert expected != _auth_digest('key', 1, 2, b'challenge')
    assert expected != _auth_digest('key', 1, 1, b'new challenge')
    assert PROTOCOL_VERSION == 1


def test_auth_receives_fragmented_bytes_and_rejects_early_close():
    assert _recv_exact(Socket([b'a', b'bc']), 3) == b'abc'
    with pytest.raises(SocketAuthError, match='closed'):
        _recv_exact(Socket([b'a']), 3)


def test_wrong_magic_is_rejected_and_original_timeout_restored():
    sock = Socket([b'X' * 42])
    with pytest.raises(SocketAuthError, match='challenge'):
        authenticate_client_socket(sock, 'key')
    assert sock.timeout == 10
