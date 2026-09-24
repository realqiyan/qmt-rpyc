import sys
import os
import time
import threading
import pytest


def test_cold_rpc_start_and_recovery_without_existing_server(mock_xtquant, monkeypatch):
    """Start a real listener during maintenance, then recover on the same RPC."""
    from qmt_rpyc import QmtClient
    from qmt_rpyc.server.main import start_server
    from qmt_rpyc.server.connection import ConnectionManager
    from tests.test_main import _config
    import qmt_rpyc.server.service as service_module
    import rpyc.utils.server

    sdk = sys.modules["xtquant.xttrader"]
    ready = threading.Event()
    maintenance = threading.Event()
    maintenance.set()
    failed_once = threading.Event()
    listeners = []
    errors = []
    original_connect = sdk.XtQuantTrader.connect
    real_server = rpyc.utils.server.ThreadedServer

    def connect(trader):
        if maintenance.is_set():
            failed_once.set()
            return -1
        return original_connect(trader)

    class CapturedServer(real_server):
        def _listen(self):
            super()._listen()
            listeners.append(self)
            ready.set()

    def run():
        try:
            start_server(_config(port=0))
        except Exception as e:
            errors.append(e)
            ready.set()

    monkeypatch.setattr(sdk.XtQuantTrader, "connect", connect)
    monkeypatch.setattr(ConnectionManager, "RECONNECT_BACKOFF", [0.01])
    monkeypatch.setattr(service_module, "xtdata", sys.modules["xtquant.xtdata"])
    monkeypatch.setattr(rpyc.utils.server, "ThreadedServer", CapturedServer)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(3)
        assert not errors
        assert failed_once.wait(1)
        with QmtClient.connect(
                "127.0.0.1", listeners[0].port,
                auth_key=_config()["auth_key"], timeout=2) as client:
            assert client.health()["connected"] is False
            assert "get_market_data_ex" in client._surface["xtdata"]["functions"]
            maintenance.clear()
            deadline = time.monotonic() + 3
            while not client.health()["connected"] and time.monotonic() < deadline:
                time.sleep(0.01)
            assert client.health()["connected"] is True
            assert client.health()["consecutive_failures"] == 0
            assert len(listeners) == 1
    finally:
        maintenance.clear()
        for listener in listeners:
            listener.close()
        thread.join(3)
        assert not thread.is_alive()
        assert not errors


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
        if mod.startswith("xtquant") or mod.startswith("server."):
            del sys.modules[mod]


@pytest.fixture
def service(mock_xtquant):
    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.server.connection import ConnectionManager
    from qmt_rpyc.server.download_manager import DownloadTaskManager
    from qmt_rpyc.server.api_surface import build_api_surface
    from qmt_rpyc.server.adapters import create_dispatcher

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=1)

    XtquantService._auth_key = None
    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._dispatcher = create_dispatcher(cm)
    XtquantService._api_surface = XtquantService._dispatcher.surface()

    svc = XtquantService()
    yield svc

    cm.stop()
    dm.shutdown()


class TestApiSurface:
    def test_get_api_surface(self, service):
        result = service.exposed_get_api_surface()
        assert "xtdata" in result
        assert "XtQuantTrader" in result
        assert "xtconstant" in result


class TestCallXtdata:
    def test_call_success(self, service):
        result = service.exposed_call_xtdata(
            "get_market_data_ex", [], {"stock_list": ["600000.SH"], "period": "1d"})
        assert result["status"] == "ok"
        assert "600000.SH" in result["data"]

    def test_call_nonexistent(self, service):
        result = service.exposed_call_xtdata("nonexistent_func", [], {})
        assert result["status"] == "error"
        assert result["error_type"] == "UnknownAPI"

    def test_call_with_error(self, service):
        result = service.exposed_call_xtdata("get_market_data_ex", [], {})
        assert result["status"] in ("ok", "error")


class TestCallTrader:
    def test_call_order_stock(self, service):
        result = service.exposed_call_trader(
            "order_stock",
            ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "ok"
        assert isinstance(result["data"], int)

    def test_call_not_connected(self, mock_xtquant):
        from qmt_rpyc.server.service import XtquantService
        from qmt_rpyc.server.connection import ConnectionManager
        from qmt_rpyc.server.download_manager import DownloadTaskManager
        from qmt_rpyc.server.api_surface import build_api_surface
        from qmt_rpyc.server.adapters import create_dispatcher

        cm = ConnectionManager(path="", session_id=1, account_id="")
        dm = DownloadTaskManager(max_workers=1)
        XtquantService._auth_key = None
        XtquantService._require_auth = False
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._dispatcher = create_dispatcher(cm)
        XtquantService._api_surface = XtquantService._dispatcher.surface()
        svc = XtquantService()

        result = svc.exposed_call_trader("order_stock", ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "error"
        assert result["error_type"] == "NotConnected"
        dm.shutdown()


class TestHealth:
    def test_health(self, service):
        result = service.exposed_health()
        assert "connected" in result
        assert "trader_available" in result


class TestDownload:
    def test_download_returns_task_id(self, service):
        result = service.exposed_call_xtdata(
            "download_history_data", [],
            {"stock_code": "600000.SH", "period": "1d"})
        assert result["status"] == "ok"
        assert "task_id" in result["data"]

        task_result = service.exposed_query_download(result["data"]["task_id"])
        assert task_result["status"] == "ok"
        assert "task_id" in task_result["data"]

    def test_query_download_not_found(self, service):
        result = service.exposed_query_download("nonexistent")
        assert result["status"] == "error"
        assert result["error_type"] == "KeyError"


class TestEvents:
    def test_events_are_not_exposed(self, service):
        assert not hasattr(service, 'exposed_subscribe_event')
        assert not hasattr(service, 'exposed_poll_events')


class TestAuth:
    def test_auth_required_blocks(self, mock_xtquant):
        from qmt_rpyc.server.service import XtquantService
        from qmt_rpyc.server.connection import ConnectionManager
        from qmt_rpyc.server.download_manager import DownloadTaskManager
        from qmt_rpyc.server.api_surface import build_api_surface
        from qmt_rpyc.server.adapters import create_dispatcher

        cm = ConnectionManager(path="", session_id=1, account_id="")
        dm = DownloadTaskManager(max_workers=1)
        XtquantService._require_auth = True
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._dispatcher = create_dispatcher(cm)
        XtquantService._api_surface = XtquantService._dispatcher.surface()
        svc = XtquantService()

        with pytest.raises(Exception):
            svc.exposed_get_api_surface()
        dm.shutdown()

class TestBatchCallXtdata:
    def test_batch_success(self, service):
        """All calls succeed — results in order with status=ok."""
        calls = [
            (["000001.SZ"], {}),
            (["000002.SZ"], {}),
            (["000003.SZ"], {}),
        ]
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", calls)
        assert result["status"] == "ok"
        assert len(result["results"]) == 3
        for i, r in enumerate(result["results"]):
            assert r["status"] == "ok", f"call {i} failed: {r}"
            assert "InstrumentID" in r["data"]

    def test_batch_with_partial_failure(self, service):
        """Some calls fail — each result carries its own status."""
        from tests._xtquant_mock import _BATCH_FAIL_SENTINEL
        calls = [
            (["000001.SZ"], {}),
            ([_BATCH_FAIL_SENTINEL], {}),
            (["000003.SZ"], {}),
        ]
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", calls)
        assert result["status"] == "ok"
        assert len(result["results"]) == 3
        # First and third calls succeed
        assert result["results"][0]["status"] == "ok"
        assert result["results"][2]["status"] == "ok"
        # Second call fails
        assert result["results"][1]["status"] == "error"
        assert result["results"][1]["error_type"] == "SDKError"
        assert "SDK call failed" in result["results"][1]["error_message"]

    def test_batch_nonexistent_function(self, service):
        """Calling a non-existent function returns top-level error."""
        result = service.exposed_batch_call_xtdata(
            "nonexistent_func", [([], {})])
        assert result["status"] == "error"
        assert result["error_type"] == "UnknownAPI"

    def test_batch_download_rejected(self, service):
        """download_* functions are rejected at the batch level."""
        result = service.exposed_batch_call_xtdata(
            "download_history_data",
            [(["600000.SH"], {"period": "1d"})])
        assert result["status"] == "error"
        assert result["error_type"] == "BatchRejected"

    def test_empty_batch_download_rejected(self, service):
        result = service.exposed_batch_call_xtdata("download_history_data", [])
        assert result["status"] == "error"
        assert result["error_type"] == "BatchRejected"

    def test_batch_too_large(self, service):
        """Exceeding _BATCH_MAX_CALLS (500) returns BatchTooLarge."""
        from qmt_rpyc.server.service import _BATCH_MAX_CALLS
        calls = [(["test"], {})] * (_BATCH_MAX_CALLS + 1)
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", calls)
        assert result["status"] == "error"
        assert result["error_type"] == "BatchTooLarge"

    def test_batch_empty_list(self, service):
        """Zero calls should still return ok with empty results."""
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", [])
        assert result["status"] == "ok"
        assert result["results"] == []

    def test_batch_malformed_json_is_structured_error(self, service):
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", "{bad")
        assert result["status"] == "error"
        assert result["error_type"] == "InvalidBatch"

    @pytest.mark.parametrize("calls", [
        {},
        [["not-paired"]],
        [[{}, {}]],
        [[[], []]],
    ])
    def test_batch_invalid_shape_is_structured_error(self, service, calls):
        result = service.exposed_batch_call_xtdata(
            "get_instrument_detail", calls)
        assert result["status"] == "error"
        assert result["error_type"] == "TypeError"
