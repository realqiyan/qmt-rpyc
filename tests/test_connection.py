import sys
import os
import time
import pytest


@pytest.fixture
def mock_xtquant():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from tests import _xtquant_mock
    sys.modules["xtquant"] = _xtquant_mock
    sys.modules["xtquant.xtdata"] = _xtquant_mock.xtdata
    sys.modules["xtquant.xttrader"] = _xtquant_mock
    sys.modules["xtquant.xttype"] = _xtquant_mock
    sys.modules["xtquant.xtconstant"] = _xtquant_mock.xtconstant
    yield
    for mod in list(sys.modules.keys()):
        if mod.startswith("xtquant") or mod == "server.connection":
            del sys.modules[mod]


class TestConnectionManager:
    def test_init_and_connect(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.trader is not None
        assert cm.connect() is True
        assert cm.is_connected is True
        cm.stop()

    def test_connect_no_trader(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        cm._init_trader()
        assert cm.connect() is False
        cm.stop()

    def test_health_status(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        status = cm.get_health_status()
        assert "connected" in status
        assert "last_heartbeat" in status
        assert "reconnect_attempts" in status
        assert "uptime_seconds" in status
        assert "trader_available" in status
        cm.stop()

    def test_mark_disconnected(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm.HEARTBEAT_INTERVAL = 0.1
        cm._init_trader()
        cm.connect()
        cm.start_heartbeat()
        cm.mark_disconnected()
        assert cm.is_connected is False
        cm.stop()

    def test_call_trader_method_success(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "ok"
        assert isinstance(result["data"], int)
        cm.stop()

    def test_call_trader_method_not_connected(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "error"
        assert result["error_type"] == "NotConnected"

    def test_account_wrapping(self, mock_xtquant):
        from server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("query_stock_asset", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()
