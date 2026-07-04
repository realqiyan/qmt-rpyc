import sys
import os
import time
import threading
import pytest


@pytest.fixture(scope="module")
def server():
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
    dm = DownloadTaskManager(max_workers=2)

    XtquantService._auth_key = None
    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._api_surface = build_api_surface()

    srv = ThreadedServer(XtquantService, port=18900,
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


class TestEndToEnd:
    def test_connect_and_call_xtdata(self, server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            result = client.xtdata.get_market_data([], ["600000.SH"], "1d")
            assert "600000.SH" in result

    def test_connect_and_call_trader(self, server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            order_id = client.trader.order_stock("ACC1", "600000.SH", 23, 100, 5, 10.0)
            assert order_id >= 10000

    def test_constant_access(self, server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            assert client.xtconstant.STOCK_BUY == 23
            assert client.xtconstant.STOCK_SELL == 24

    def test_health(self, server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            h = client.health()
            assert h["connected"] is True

    def test_download_workflow(self, server):
        from client import QmtClient
        from client.proxy import DownloadTaskHandle
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            task = client.xtdata.download_history_data(
                stock_code="600000.SH", period="1d")
            assert isinstance(task, DownloadTaskHandle)
            result = task.wait(timeout=10)
            assert result["status"] in ("completed", "failed")

    def test_query_nonexistent_trader_method(self, server):
        from client import QmtClient
        from client.exceptions import RemoteCallError
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            with pytest.raises(RemoteCallError):
                client.trader.nonexistent_method("ACC1")

    def test_event_subscribe_unsubscribe(self, server):
        from client import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            sub_id = client.subscribe(["order", "disconnect"])
            client.unsubscribe(sub_id)

    def test_multiple_clients(self, server):
        from client import QmtClient
        c1 = QmtClient.connect("127.0.0.1", port=18900)
        c2 = QmtClient.connect("127.0.0.1", port=18900)
        try:
            r1 = c1.xtdata.get_full_tick(["600000.SH"])
            r2 = c2.xtdata.get_full_tick(["600000.SH"])
            assert "600000.SH" in r1
            assert "600000.SH" in r2
        finally:
            c1.close()
            c2.close()
