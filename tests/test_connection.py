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
    def test_start_returns_while_native_constructor_is_blocked(
            self, mock_xtquant, monkeypatch):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        sdk = sys.modules["xtquant.xttrader"]

        entered = threading.Event()
        release = threading.Event()
        original = sdk.XtQuantTrader

        def blocked_constructor(*args):
            entered.set()
            assert release.wait(2)
            return original(*args)

        monkeypatch.setattr(sdk, "XtQuantTrader", blocked_constructor)
        cm = ConnectionManager("test", 1, "ACC1")
        try:
            assert cm.start() is True
            assert entered.wait(1)
            assert not cm.is_connected
            assert cm.get_health_status()["connection_state"] == "connecting"
            release.set()
            worker = cm._reconnect_thread
            if worker is not None:
                worker.join(1)
            assert cm.is_connected
            assert cm.get_health_status()["consecutive_failures"] == 0
        finally:
            release.set()
            cm.stop()

    @pytest.mark.parametrize("connect_result, expected", [(0, True), (-1, False)])
    def test_probe_reports_one_attempt_without_scheduling_retries(
            self, mock_xtquant, monkeypatch, connect_result, expected):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        sdk = sys.modules["xtquant.xttrader"]

        attempts = []

        def connect(trader):
            attempts.append(1)
            return connect_result

        monkeypatch.setattr(sdk.XtQuantTrader, "connect", connect)
        cm = ConnectionManager("test", 1, "ACC1")
        try:
            assert cm.probe() is expected
            assert len(attempts) == 1
            assert cm.is_connected is expected
            assert cm._reconnect_thread is None
            health = cm.get_health_status()
            assert health["last_connection_error"] == (
                "" if expected else "Trader.connect returned -1")
            assert health["next_retry_at"] is None
        finally:
            cm.stop()

    def test_subscribe_log_does_not_contain_the_raw_account(
            self, mock_xtquant, caplog):
        import logging

        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

        cm = ConnectionManager("test", 1, "123456789012")
        try:
            with caplog.at_level(logging.INFO,
                                 logger="qmt_rpyc.adapters.xtquant_2_0_6_1.connection"):
                assert cm.probe()
            assert "Subscribed to account 12********12" in caplog.text
            assert "123456789012" not in caplog.text
        finally:
            cm.stop()

    @pytest.mark.parametrize("stage", ["init", "connect", "subscribe"])
    def test_failed_initial_attempt_recovers_with_heartbeat(
            self, mock_xtquant, monkeypatch, stage):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        sdk = sys.modules["xtquant.xttrader"]

        original = sdk.XtQuantTrader
        attempts = []

        def factory(*args):
            attempts.append(1)
            if len(attempts) == 1 and stage == "init":
                raise RuntimeError("maintenance")
            trader = original(*args)
            if len(attempts) == 1:
                setattr(trader, stage, lambda *args: -1)
            return trader

        monkeypatch.setattr(sdk, "XtQuantTrader", factory)
        cm = ConnectionManager("test", 1, "ACC1")
        cm.RECONNECT_BACKOFF = [0.001]
        try:
            cm.start()
            worker = cm._reconnect_thread
            if worker is not None:
                worker.join(2)
            assert cm.is_connected
            assert len(attempts) == 2
            assert cm._heartbeat_thread.is_alive()
            health = cm.get_health_status()
            assert health["connection_state"] == "connected"
            assert health["last_connection_error"] == ""
            assert health["next_retry_at"] is None
            assert health["consecutive_failures"] == 0
        finally:
            cm.stop()

    @pytest.mark.parametrize("immediate, expected", [
        (True, [0, 10, 30, 60, 600, 600]),
        (False, [10, 30, 60, 600, 600, 600]),
    ])
    def test_retry_delays_and_health(self, mock_xtquant, monkeypatch,
                                   immediate, expected):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

        cm = ConnectionManager("test", 1, "")
        waits = []
        snapshots = []

        class Clock:
            def is_set(self):
                return False

            def wait(self, delay):
                waits.append(delay)
                snapshots.append(cm.get_health_status())
                return len(waits) == len(expected)

        cm._stop_event = Clock()
        monkeypatch.setattr(cm, "_reset_trader", lambda: None)
        monkeypatch.setattr(cm, "connect", lambda: False)
        cm._reconnect_loop(immediate=immediate)
        assert waits == expected
        assert snapshots[-1]["connection_state"] == "waiting_retry"
        assert snapshots[-1]["next_retry_at"] is not None
        assert snapshots[-1]["consecutive_failures"] == len(expected) - 1

    def test_stop_interrupts_long_retry_wait(self, mock_xtquant, monkeypatch):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

        cm = ConnectionManager("test", 1, "")
        cm.RECONNECT_BACKOFF = [600]
        entered = threading.Event()
        monkeypatch.setattr(cm, "_reset_trader", lambda: entered.set())
        cm.start()
        assert entered.wait(1)
        worker = cm._reconnect_thread
        cm.stop()
        assert not worker.is_alive()
        assert cm.get_health_status()["connection_state"] == "stopped"
        assert cm.start() is False

    def test_stale_disconnect_does_not_publish_event(
            self, mock_xtquant, monkeypatch):
        import qmt_rpyc.adapters.xtquant_2_0_6_1.connection as connection

        cm = connection.ConnectionManager("test", 1, "")
        cm._init_trader()
        stale_callback = cm._callback
        cm._reset_trader()
        assert cm.connect()
        events = []
        monkeypatch.setattr(connection.event_bus, "publish", events.append)
        try:
            stale_callback.on_disconnected()
            assert cm.is_connected
            assert events == []
        finally:
            cm.stop()

    def test_disconnect_after_connect_does_not_publish_false_recovery(
            self, mock_xtquant, monkeypatch):
        import qmt_rpyc.adapters.xtquant_2_0_6_1.connection as connection

        cm = connection.ConnectionManager("test", 1, "")
        cm.RECONNECT_BACKOFF = [0]
        real_connect = cm.connect
        events = []

        def connect_then_disconnect():
            assert real_connect()
            cm.mark_disconnected(cm.trader)
            return True

        monkeypatch.setattr(cm, "connect", connect_then_disconnect)
        monkeypatch.setattr(cm, "schedule_reconnect", lambda: None)
        monkeypatch.setattr(connection.event_bus, "publish", events.append)
        try:
            cm._reconnect_loop(immediate=True)
            assert not cm.is_connected
            assert cm.get_health_status()["last_connection_error"] == "QMT connection lost"
            assert events == []
        finally:
            cm.stop()

    @pytest.mark.parametrize("probe_result", [True, False])
    def test_old_heartbeat_result_cannot_update_new_trader(
            self, mock_xtquant, monkeypatch, probe_result):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

        cm = ConnectionManager("test", 1, "", heartbeat_max_failures=1)
        cm._init_trader()
        assert cm.connect()
        original_event = cm._stop_event

        class OneHeartbeat:
            calls = 0

            def is_set(self):
                return False

            def wait(self, timeout):
                self.calls += 1
                return self.calls > 1

        def replace_during_probe():
            cm._reset_trader()
            assert cm.connect()
            cm._last_heartbeat = None
            return probe_result

        cm._stop_event = OneHeartbeat()
        monkeypatch.setattr(cm, "_do_heartbeat", replace_during_probe)
        try:
            cm._heartbeat_loop()
            health = cm.get_health_status()
            assert health["connected"]
            assert health["last_heartbeat"] == ""
            assert health["heartbeat_failures"] == 0
        finally:
            cm._stop_event = original_event
            cm.stop()

    def test_discovers_runtime_account_parameter(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import _discover_account_parameters
        from xtquant.xttrader import XtQuantTrader

        discovered = _discover_account_parameters(XtQuantTrader)

        assert discovered["query_new_purchase_limit"].name == "account"
        assert discovered["query_new_purchase_limit"].position == 0
        assert "echo_account_id" not in discovered

    def test_init_and_connect(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.trader is not None
        assert cm.connect() is True
        assert cm.is_connected is True
        cm.stop()

    def test_connect_no_trader(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        cm._init_trader()
        assert cm.connect() is False
        cm.stop()

    def test_health_status(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm.HEARTBEAT_INTERVAL = 0.1
        cm._init_trader()
        cm.connect()
        cm.start_heartbeat()
        cm.mark_disconnected()
        assert cm.is_connected is False
        cm.stop()

    def test_call_trader_method_success(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "ok"
        assert isinstance(result["data"], int)
        cm.stop()

    def test_call_trader_method_not_connected(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="", session_id=1, account_id="")
        result = cm.call_trader_method("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "error"
        assert result["error_type"] == "NotConnected"

    def test_initialized_trader_rejects_calls_before_connect(
            self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
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
        import qmt_rpyc.adapters.xtquant_2_0_6_1.connection as connection

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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        cm.connect()
        result = cm.call_trader_method("query_stock_asset", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_legacy_account_wrapping_fallback(
            self, mock_xtquant, monkeypatch):
        import qmt_rpyc.adapters.xtquant_2_0_6_1.connection as connection
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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "query_new_purchase_limit", ["ACC1"], {})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_discovered_account_wrapping_keyword(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
        cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
        cm._init_trader()
        assert cm.connect() is True
        result = cm.call_trader_method(
            "query_new_purchase_limit", [], {"account": "ACC1"})
        assert result["status"] == "ok"
        assert result["data"]["account_id"] == "ACC1"
        cm.stop()

    def test_stock_account_passes_through(self, mock_xtquant):
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
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
        from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager

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
