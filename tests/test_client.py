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

    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.server.connection import ConnectionManager
    from qmt_rpyc.server.download_manager import DownloadTaskManager
    from qmt_rpyc.server.api_surface import build_api_surface
    from qmt_rpyc.server.adapters import create_dispatcher
    from rpyc.utils.server import ThreadedServer

    cm = ConnectionManager(path="test", session_id=1, account_id="ACC1")
    cm._init_trader()
    cm.connect()
    dm = DownloadTaskManager(max_workers=1)

    XtquantService._require_auth = False
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._dispatcher = create_dispatcher(cm)
    XtquantService._api_surface = XtquantService._dispatcher.surface()

    srv = ThreadedServer(XtquantService, port=18899,
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


class TestQmtClientConnect:
    def test_connect_and_close(self, mock_server):
        from qmt_rpyc import QmtClient
        client = QmtClient.connect("127.0.0.1", port=18899)
        try:
            assert client is not None
        finally:
            client.close()

    def test_context_manager(self, mock_server):
        from qmt_rpyc import QmtClient
        from qmt_rpyc.exceptions import NotConnectedError
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert client is not None
        with pytest.raises(NotConnectedError):
            client.health()
        client.close()


class TestQmtClientCall:
    def test_call_xtdata(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            result = client.xtdata.get_market_data_ex([], ["600000.SH"], "1d")
            assert isinstance(result, dict)
            assert "600000.SH" in result

    def test_call_trader(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            order_id = client.trader.order_stock("ACC1", "600000.SH", 23, 100, 5, 10.0)
            assert isinstance(order_id, int)

    def test_uncontracted_sdk_method_is_hidden(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert "query_new_purchase_limit" not in client._surface["XtQuantTrader"]["methods"]
            with pytest.raises(AttributeError):
                client.trader.query_new_purchase_limit("ACC1")

    def test_xtconstant_inline(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert client.xtconstant.STOCK_BUY == 23

    def test_health(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            h = client.health()
            assert "connected" in h


class TestQmtClientDownload:
    def test_download_returns_handle(self, mock_server):
        from qmt_rpyc import QmtClient
        from qmt_rpyc.proxy import DownloadTaskHandle
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            task = client.xtdata.download_history_data(
                stock_code="600000.SH", period="1d")
            assert isinstance(task, DownloadTaskHandle)
            result = task.wait(timeout=10)
            assert result["status"] in ("completed", "failed")


class TestQmtClientEvents:
    def test_events_are_not_exported_in_v1(self, mock_server):
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            assert not hasattr(client, 'subscribe')
            assert not hasattr(client.trader, 'register_callback')


class TestQmtClientBatch:
    def test_batch_success(self, mock_server):
        """Batch call through client returns results in order."""
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            results = client.xtdata.get_instrument_detail.batch([
                (["000001.SZ"], {}),
                (["000002.SZ"], {}),
                (["000003.SZ"], {}),
            ])
            assert len(results) == 3
            for i, r in enumerate(results):
                assert r["status"] == "ok", f"call {i} failed: {r}"
                assert "InstrumentID" in r["data"]

    def test_batch_empty(self, mock_server):
        """Empty batch returns empty results list."""
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            results = client.xtdata.get_instrument_detail.batch([])
            assert len(results) == 0

    def test_batch_nonexistent_function(self, mock_server):
        """Non-existent function raises QmtError."""
        from qmt_rpyc import QmtClient
        from qmt_rpyc.exceptions import QmtError
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            with pytest.raises(AttributeError):
                client.xtdata.nonexistent_func.batch([([], {})])

    def test_batch_download_rejected(self, mock_server):
        """download_* rejected — overall batch fails, raises QmtError."""
        from qmt_rpyc import QmtClient
        from qmt_rpyc.exceptions import QmtError
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            with pytest.raises(QmtError):
                client.xtdata.download_history_data.batch([
                    (["600000.SH"], {"period": "1d"})])

    def test_batch_dir_discovers_batch(self, mock_server):
        """dir() on a remote callable includes 'batch'."""
        from qmt_rpyc import QmtClient
        with QmtClient.connect("127.0.0.1", port=18899) as client:
            names = dir(client.xtdata.get_instrument_detail)
            assert "batch" in names


def test_pre_protocol_authentication_preserves_obtain():
    import sys
    import threading

    from tests import _xtquant_mock
    sys.modules["xtquant"] = _xtquant_mock
    sys.modules["xtquant.xtdata"] = _xtquant_mock.xtdata
    sys.modules["xtquant.xttrader"] = _xtquant_mock
    sys.modules["xtquant.xttype"] = _xtquant_mock
    sys.modules["xtquant.xtconstant"] = _xtquant_mock.xtconstant

    from rpyc.utils.server import ThreadedServer
    from qmt_rpyc import QmtClient
    from qmt_rpyc.protocol import make_server_authenticator
    from qmt_rpyc.server.api_surface import build_api_surface
    from qmt_rpyc.server.adapters import create_dispatcher
    from qmt_rpyc.server.auth_limiter import AuthRateLimiter
    from qmt_rpyc.server.connection import ConnectionManager
    from qmt_rpyc.server.download_manager import DownloadTaskManager
    from qmt_rpyc.server.service import XtquantService

    manager = ConnectionManager(
        path="test", session_id=1, account_id="ACC1"
    )
    manager._init_trader()
    manager.connect()
    downloads = DownloadTaskManager(max_workers=1)
    XtquantService._require_auth = True
    XtquantService._connection_mgr = manager
    XtquantService._download_mgr = downloads
    XtquantService._dispatcher = create_dispatcher(manager)
    XtquantService._api_surface = XtquantService._dispatcher.surface()
    server = ThreadedServer(
        XtquantService,
        hostname="127.0.0.1",
        port=0,
        authenticator=make_server_authenticator(
            "test-secret", AuthRateLimiter()
        ),
        protocol_config={
            "allow_public_attrs": True,
            "allow_pickle": True,
        },
    )
    port = server.listener.getsockname()[1]
    thread = threading.Thread(target=server.start, daemon=True)
    thread.start()
    try:
        with pytest.raises(Exception):
            QmtClient.connect(
                "127.0.0.1", port=port, auth_key="wrong-secret"
            )
        with QmtClient.connect(
                "127.0.0.1", port=port,
                auth_key="test-secret") as client:
            health = client.health()
            assert isinstance(health, dict)
            assert health["connected"] is True
    finally:
        server.close()
        downloads.shutdown()
        manager.stop()
        thread.join(timeout=2)
        XtquantService._require_auth = False
