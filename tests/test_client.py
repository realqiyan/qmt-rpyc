import sys
import os
import time
import threading
import pytest


@pytest.fixture(scope="module")
def mock_server():
    tests_dir = os.path.dirname(os.path.abspath(__file__))
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from tests import _xtquant_mock
    sys.modules["xtquant"] = _xtquant_mock
    sys.modules["xtquant.xtdata"] = _xtquant_mock.xtdata
    sys.modules["xtquant.xttrader"] = _xtquant_mock
    sys.modules["xtquant.xttype"] = _xtquant_mock
    sys.modules["xtquant.xtconstant"] = _xtquant_mock.xtconstant

    from server.service import XtquantService
    from server.connection import ConnectionManager
    from server.download_manager import DownloadTaskManager
    from server.api_surface import build_api_surface
    from rpyc.utils.server import ThreadedServer

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=1)

    XtquantService._auth_key = None
    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._api_surface = build_api_surface()

    srv = ThreadedServer(XtquantService, port=18899,
                         protocol_config={"allow_public_attrs": True,
                                          "sync_request_timeout": 300})
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    time.sleep(0.5)
    yield srv
    srv.close()
    cm.stop()
    dm.shutdown()
    for mod in list(sys.modules.keys()):
        if mod.startswith("xtquant") or mod.startswith("server."):
            del sys.modules[mod]


class TestQmtClientConnect:
    def test_connect_and_close(self, mock_server):
        from client import QmtClient
        client = QmtClient.connect("127.0.0.1", port=18899)
        try:
            assert client is not None
        finally:
            client.close()

    def test_context_manager(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert client is not None


class TestQmtClientCall:
    def test_call_xtdata(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            result = client.xtdata.get_market_data([], ["600000.SH"], "1d")
            assert isinstance(result, dict)
            assert "600000.SH" in result

    def test_call_trader(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            order_id = client.trader.order_stock("ACC1", "600000.SH", 23, 100, 5, 10.0)
            assert isinstance(order_id, int)

    def test_xtconstant_inline(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert client.xtconstant.STOCK_BUY == 23

    def test_health(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            h = client.health()
            assert "connected" in h


class TestQmtClientDownload:
    def test_download_returns_handle(self, mock_server):
        from client import QmtClient
        from client.proxy import DownloadTaskHandle
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            task = client.xtdata.download_history_data(
                stock_code="600000.SH", period="1d")
            assert isinstance(task, DownloadTaskHandle)
            result = task.wait(timeout=10)
            assert result["status"] in ("completed", "failed")


class TestQmtClientEvents:
    def test_subscribe_and_drain(self, mock_server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            sub_id = client.subscribe(["order"])
            events, dropped = client.drain_events(sub_id)
            assert isinstance(events, list)
            client.unsubscribe(sub_id)
