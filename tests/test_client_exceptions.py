import pytest
from qmt_rpyc.exceptions import (
    QmtError, NotConnectedError, RemoteCallError, QmtAuthError, _map_error,
)


class TestErrorMapping:
    def test_map_not_connected(self):
        resp = {"status": "error", "error_type": "NotConnected", "error_message": "trader not connected"}
        err = _map_error(resp)
        assert isinstance(err, NotConnectedError)
        assert "trader not connected" in str(err)

    def test_map_remote_call_error(self):
        resp = {"status": "error", "error_type": "ValueError", "error_message": "bad value"}
        err = _map_error(resp)
        assert isinstance(err, RemoteCallError)
        assert err.error_type == "ValueError"

    def test_map_unknown_error_type(self):
        resp = {"status": "error", "error_type": "", "error_message": "something"}
        err = _map_error(resp)
        assert isinstance(err, RemoteCallError)

    def test_all_errors_inherit_qmt_error(self):
        assert issubclass(NotConnectedError, QmtError)
        assert issubclass(RemoteCallError, QmtError)
        assert issubclass(QmtAuthError, QmtError)

    def test_error_str_format(self):
        err = QmtError("ValueError", "bad thing")
        assert "[ValueError]" in str(err)
        assert "bad thing" in str(err)
