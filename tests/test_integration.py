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

    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.server.connection import ConnectionManager
    from qmt_rpyc.server.download_manager import DownloadTaskManager
    from qmt_rpyc.server.api_surface import build_api_surface
    from qmt_rpyc.server.adapters import create_dispatcher
    from rpyc.utils.server import ThreadedServer

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=2)

    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._dispatcher = create_dispatcher(cm)
    XtquantService._api_surface = XtquantService._dispatcher.surface()

    srv = ThreadedServer(XtquantService, port=18900,
                         protocol_config={"allow_public_attrs": True,
                                          "allow_pickle": True,
                                          "sync_request_timeout": 300})
    t = threading.Thread(target=srv.start, daemon=True)
    t.start()
    time.sleep(0.5)
    yield srv
    srv.close()
    cm.stop()
    dm.shutdown()
    for mod in list(sys.modules.keys()):
        if mod.startswith("xtquant"):
            del sys.modules[mod]


class TestEndToEnd:
    def test_connect_and_call_xtdata(self, server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            result = client.xtdata.get_market_data_ex([], ["600000.SH"], "1d")
            assert "600000.SH" in result

    def test_connect_and_call_trader(self, server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            order_id = client.trader.order_stock("ACC1", "600000.SH", 23, 100, 5, 10.0)
            assert order_id >= 10000

    def test_constant_access(self, server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            assert client.xtconstant.STOCK_BUY == 23
            assert client.xtconstant.STOCK_SELL == 24

    def test_health(self, server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            h = client.health()
            assert h["connected"] is True

    def test_download_workflow(self, server):
        from qmt_rpyc import QmtClient
        from qmt_rpyc.proxy import DownloadTaskHandle
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            task = client.xtdata.download_history_data(
                stock_code="600000.SH", period="1d")
            assert isinstance(task, DownloadTaskHandle)
            result = task.wait(timeout=10)
            assert result["status"] in ("completed", "failed")

    def test_query_nonexistent_trader_method(self, server):
        from qmt_rpyc import QmtClient
        from qmt_rpyc.exceptions import RemoteCallError
        with QmtClient.connect("127.0.0.1", port=18900) as client:
            with pytest.raises(AttributeError):
                client.trader.nonexistent_method("ACC1")

    def test_multiple_clients(self, server):
        from qmt_rpyc import QmtClient
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
