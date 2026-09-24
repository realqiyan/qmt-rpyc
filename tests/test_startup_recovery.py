import sys
import time
import threading
from tests.fixtures import mock_xtquant

def test_cold_rpc_start_and_recovery_without_existing_server(mock_xtquant, monkeypatch):
    """Start a real listener during maintenance, then recover on the same RPC."""
    from qmt_rpyc import QmtClient
    from qmt_rpyc.server.main import start_server
    from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
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
            assert client.health().connected is False
            assert "market.get_daily_bars" in client.capabilities().operations
            maintenance.clear()
            deadline = time.monotonic() + 3
            while not client.health().connected and time.monotonic() < deadline:
                time.sleep(0.01)
            assert client.health().connected is True
            assert client.health().consecutive_failures == 0
            assert len(listeners) == 1
    finally:
        maintenance.clear()
        for listener in listeners:
            listener.close()
        thread.join(3)
        assert not thread.is_alive()
        assert not errors
