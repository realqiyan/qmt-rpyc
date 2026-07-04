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
        if mod.startswith("xtquant") or mod.startswith("server."):
            del sys.modules[mod]


@pytest.fixture
def service(mock_xtquant):
    from server.service import XtquantService
    from server.connection import ConnectionManager
    from server.download_manager import DownloadTaskManager
    from server.api_surface import build_api_surface

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=1)

    XtquantService._auth_key = None
    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._api_surface = build_api_surface()

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
            "get_market_data", [], {"stock_list": ["600000.SH"], "period": "1d"})
        assert result["status"] == "ok"
        assert "600000.SH" in result["data"]

    def test_call_nonexistent(self, service):
        result = service.exposed_call_xtdata("nonexistent_func", [], {})
        assert result["status"] == "error"
        assert result["error_type"] == "AttributeError"

    def test_call_with_error(self, service):
        result = service.exposed_call_xtdata("get_market_data", [], {})
        assert result["status"] in ("ok", "error")


class TestCallTrader:
    def test_call_order_stock(self, service):
        result = service.exposed_call_trader(
            "order_stock",
            ["ACC1", "600000.SH", 23, 100, 5, 10.0], {})
        assert result["status"] == "ok"
        assert isinstance(result["data"], int)

    def test_call_not_connected(self, mock_xtquant):
        from server.service import XtquantService
        from server.connection import ConnectionManager
        from server.download_manager import DownloadTaskManager
        from server.api_surface import build_api_surface

        cm = ConnectionManager(path="", session_id=1, account_id="")
        dm = DownloadTaskManager(max_workers=1)
        XtquantService._auth_key = None
        XtquantService._require_auth = False
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._api_surface = build_api_surface()
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
    def test_subscribe_unsubscribe(self, service):
        sub_id = service.exposed_subscribe_event(["order"])
        assert isinstance(sub_id, str)
        assert service.exposed_unsubscribe_event(sub_id) is True

    def test_poll_empty(self, service):
        sub_id = service.exposed_subscribe_event(["order"])
        events, dropped = service.exposed_poll_events(sub_id)
        assert events == []
        assert dropped == 0
        service.exposed_unsubscribe_event(sub_id)


class TestAuth:
    def test_auth_required_blocks(self, mock_xtquant):
        from server.service import XtquantService
        from server.connection import ConnectionManager
        from server.download_manager import DownloadTaskManager
        from server.api_surface import build_api_surface

        cm = ConnectionManager(path="", session_id=1, account_id="")
        dm = DownloadTaskManager(max_workers=1)
        XtquantService._auth_key = "secret"
        XtquantService._require_auth = True
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._api_surface = build_api_surface()
        svc = XtquantService()

        with pytest.raises(Exception):
            svc.exposed_get_api_surface()
        dm.shutdown()

    def test_auth_success(self, mock_xtquant):
        from server.service import XtquantService
        from server.connection import ConnectionManager
        from server.download_manager import DownloadTaskManager
        from server.api_surface import build_api_surface
        from common.protocol import make_auth_token
        import time

        cm = ConnectionManager(path="", session_id=1, account_id="")
        dm = DownloadTaskManager(max_workers=1)
        key = "test-secret"
        XtquantService._auth_key = key
        XtquantService._require_auth = True
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._api_surface = build_api_surface()
        svc = XtquantService()
        svc._peer = ("127.0.0.1", 12345)

        nonce = "testnonce1234567890"
        ts = int(time.time())
        token = make_auth_token(key, nonce, ts)
        assert svc.exposed_authenticate(nonce, ts, token) is True
        assert svc.exposed_get_api_surface() is not None
        dm.shutdown()
