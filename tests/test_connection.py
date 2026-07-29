import sys
import os
import time
import threading
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
    def test_discovers_runtime_account_parameter(self, mock_xtquant):
        from qmt_rpyc.server.connection import _discover_account_parameters
        from xtquant.xttrader import XtQuantTrader

        discovered = _discover_account_parameters(XtQuantTrader)

        assert discovered["query_new_purchase_limit"].name == "account"
        assert discovered["query_new_purchase_limit"].position == 0
        assert "echo_account_id" not in discovered

    def test_init_and_connect(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.trader is not None
        assert cm.connect() is True
        assert cm.is_connected is True
        cm.stop()

    def test_connect_no_trader(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        cm._init_trader()
        assert cm.connect() is False
        cm.stop()

    def test_health_status(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
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
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm.HEARTBEAT_INTERVAL = 0.1
        cm._init_trader()
        cm.connect()
        cm.start_heartbeat()
        cm.mark_disconnected()
        assert cm.is_connected is False
        cm.stop()

    def test_call_trader_method_success(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "ok"
        assert isinstance(result["data"], int)
        cm.stop()

    def test_call_trader_method_not_connected(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "error"
        assert result["error_type"] == "NotConnected"

    def test_initialized_trader_rejects_calls_before_connect(
            self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        try:
            result = cm.call_trader_method(
                "query_stock_asset", ["ACC1"], {})
            assert result["status"] == "error"
            assert result["error_type"] == "NotConnected"
        finally:
            cm.stop()

    def test_reconnect_subscribes_once_and_preserves_attempt_count(
            self, mock_xtquant, monkeypatch):
        import qmt_rpyc.server.connection as connection

        cm = connection.ConnectionManager(
            path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.RECONNECT_BACKOFF = [0]
        subscriptions = []
        events = []
        monkeypatch.setattr(cm, "_reset_trader", lambda: None)
        monkeypatch.setattr(
            cm, "_subscribe_trader",
            lambda trader, native_lock, account_id:
                subscriptions.append(account_id) or True)
        monkeypatch.setattr(connection.event_bus, "publish", events.append)

        try:
            cm._reconnect_loop()
            assert subscriptions == ["ACC1"]
            assert events[0]["data"]["attempts"] == 1
            assert cm.get_health_status()["reconnect_attempts"] == 0
            assert cm._heartbeat_thread.is_alive()
        finally:
            cm.stop()

    def test_heartbeat_timeout_does_not_hold_state_lock(
            self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager

        release = threading.Event()
        cm = ConnectionManager(
            path="test",
            session_id=1,
            account_id="ACC1",
            heartbeat_timeout=0.01,
        )
        cm._init_trader()
        assert cm.connect() is True
        cm.trader.query_stock_asset = lambda account: release.wait(1)
        try:
            assert cm._do_heartbeat() is False
            assert cm._trader_lock.acquire(timeout=0.1) is True
            cm._trader_lock.release()
        finally:
            release.set()
            cm.stop()

    def test_reset_replaces_native_lock_generation(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager

        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        old_lock = cm._native_lock
        acquired = threading.Event()
        release = threading.Event()

        def hold_old_generation():
            with old_lock:
                acquired.set()
                release.wait(1)

        thread = threading.Thread(target=hold_old_generation, daemon=True)
        thread.start()
        assert acquired.wait(0.5)

        try:
            cm._reset_trader()
            assert cm._native_lock is not old_lock
            assert cm._native_lock.acquire(timeout=0.1)
            cm._native_lock.release()
        finally:
            release.set()
            thread.join(timeout=1)
            cm.stop()

    def test_account_wrapping(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("query_stock_asset", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_legacy_account_wrapping_fallback(
            self, mock_xtquant, monkeypatch):
        import qmt_rpyc.server.connection as connection
        monkeypatch.setattr(
            connection, "_discover_account_parameters", lambda trader_cls: {})
        cm = connection.ConnectionManager(
            path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "query_stock_asset", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_discovered_account_wrapping_positional(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "query_new_purchase_limit", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_discovered_account_wrapping_keyword(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "query_new_purchase_limit", [], {"account": "ACC1"})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_stock_account_passes_through(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        from xtquant.xttype import StockAccount
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        account = StockAccount("ACC2")
        result = cm.call_trader_method(
            "query_new_purchase_limit", [account], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC2"
        cm.stop()

    def test_account_id_parameter_is_not_wrapped(self, mock_xtquant):
        from qmt_rpyc.server.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "echo_account_id", [], {"account_id": "ACC1"})
        assert result == {"status": "ok", "data": "ACC1"}
        cm.stop()

    def test_account_construction_failure_is_structured(
            self, mock_xtquant, monkeypatch):
        from tests import _xtquant_mock
        from qmt_rpyc.server.connection import ConnectionManager

        class BrokenStockAccount:
            def __init__(self, account_id):
                raise ValueError("invalid account")

        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        monkeypatch.setattr(
            _xtquant_mock, "StockAccount", BrokenStockAccount)
        result = cm.call_trader_method(
            "query_new_purchase_limit", ["ACC1"], {})
        assert result["status"] == "error"
        assert result["error_type"] == "ValueError"
        assert result["error_message"] == "invalid account"
        cm.stop()
