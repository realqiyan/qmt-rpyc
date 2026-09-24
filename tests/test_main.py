from tests.fixtures import mock_xtquant
import ssl
import threading

import pytest

from qmt_rpyc.server.main import _validate_config, start_server


def _config(**overrides):
    config = {
        "host": "127.0.0.1",
        "port": 18812,
        "auth_key": "secret-key-123456",
        "allow_insecure": False,
        "qmt_path": "test",
        "qmt_session_id": 1,
        "qmt_account_id": "",
        "heartbeat_interval": 30,
        "heartbeat_timeout": 5,
        "heartbeat_max_failures": 3,
        "reconnect_max_attempts": 0,
        "log_dir": "logs",
        "tls_keyfile": None,
        "tls_certfile": None,
        "tls_ca_certs": None,
    }
    config.update(overrides)
    return config


def test_config_requires_nonempty_auth_unless_explicitly_insecure():
    with pytest.raises(ValueError, match="QMT_RPYC_AUTH_KEY"):
        _validate_config(_config(auth_key=None))

    _validate_config(_config(auth_key=None, allow_insecure=True))
    _validate_config(_config(auth_key="short"))

    with pytest.raises(ValueError, match="must be non-empty"):
        _validate_config(_config(auth_key=""))


def test_config_requires_complete_tls_pair():
    with pytest.raises(ValueError, match="configured together"):
        _validate_config(_config(tls_keyfile="server.key"))


def test_server_serves_health_and_discovery_during_blocked_qmt_init(mock_xtquant, monkeypatch):
    import qmt_rpyc.adapters.xtquant_2_0_6_1.factory as adapters
    from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
    from qmt_rpyc.server.service import XtquantService
    import rpyc.utils.server

    entered = threading.Event()
    release = threading.Event()
    calls = []

    def blocked_init(self):
        entered.set()
        assert release.wait(2)

    class FakeServer:
        def __init__(self, *args, **kwargs):
            calls.append("listener created")
            assert not entered.is_set()

        def start(self):
            try:
                assert entered.wait(1)
                service = XtquantService()
                from qmt_rpyc.transport.codec import loads, dumps
                from qmt_rpyc.contracts.operations import CONTRACT_HASH
                from qmt_rpyc.contracts.common import EmptyRequest
                negotiated = loads(service.exposed_negotiate(CONTRACT_HASH))
                health = loads(service.exposed_call(dumps(dict(contract_version=2, request_id="test", operation="system.get_health", payload={}))))["data"]
                assert health["connected"] is False
                assert health["connection_state"] == "connecting"
                assert "market.get_ticks" in negotiated["capabilities"]["operations"]
                calls.append("rpc available")
            finally:
                release.set()

        def close(self):
            release.set()

    monkeypatch.setattr(ConnectionManager, "_init_trader", blocked_init)
    from types import SimpleNamespace
    monkeypatch.setattr(rpyc.utils.server, "ThreadedServer", FakeServer)
    try:
        assert start_server(_config(auth_key=None, allow_insecure=True)) == 0
        assert calls == ["listener created", "rpc available"]
    finally:
        release.set()


def test_missing_sdk_fails_before_listener_or_background_attempt(monkeypatch):
    import qmt_rpyc.adapters.xtquant_2_0_6_1.factory as adapters
    from qmt_rpyc.adapters.xtquant_2_0_6_1.connection import ConnectionManager
    import rpyc.utils.server

    def missing_sdk(*args):
        raise ImportError("missing SDK")

    def unexpected(*args, **kwargs):
        pytest.fail("listener/connection must not start with a broken SDK")

    monkeypatch.setattr(adapters, "create_providers", missing_sdk)
    monkeypatch.setattr(ConnectionManager, "start", unexpected)
    monkeypatch.setattr(rpyc.utils.server, "ThreadedServer", unexpected)
    with pytest.raises(ImportError, match="missing SDK"):
        start_server(_config())


def test_tls_uses_server_context_and_cleanup_order(mock_xtquant, monkeypatch):
    import socket
    import qmt_rpyc.adapters.xtquant_2_0_6_1.factory as adapters
    import qmt_rpyc.adapters.xtquant_2_0_6_1.connection as connection
    import qmt_rpyc.server.downloads as download_manager
    import rpyc.utils.server

    calls = []

    class FakeConnectionManager:
        def __init__(self, **kwargs):
            self.is_connected = False

        def start(self):
            calls.append("cm.start")
            self.is_connected = True
            return True

        def stop(self):
            calls.append("cm.stop")

        def get_health_status(self):
            return {"connection_state": "connected", "consecutive_failures": 0,
                    "next_retry_at": None, "last_connection_error": ""}

    class FakeDownloadManager:
        def __init__(self, max_workers):
            pass

        def shutdown(self):
            calls.append("dm.shutdown")

    class FakeSocket:
        def setsockopt(self, *args):
            pass

        def bind(self, address):
            calls.append(("bind", address))

        def listen(self, backlog):
            calls.append(("listen", backlog))

        def close(self):
            calls.append("socket.close")

    class FakeContext:
        def __init__(self, protocol):
            calls.append(("context", protocol))

        def load_cert_chain(self, certfile, keyfile):
            calls.append(("certs", certfile, keyfile))

        def wrap_socket(self, listener, server_side):
            calls.append(("wrap", server_side))
            return listener

    class FakeServer:
        def __init__(self, *args, **kwargs):
            assert isinstance(kwargs["listener"], FakeSocket)

        def start(self):
            calls.append("server.start")

        def close(self):
            calls.append("server.close")

    monkeypatch.setattr(
        connection, "ConnectionManager", FakeConnectionManager)
    monkeypatch.setattr(
        download_manager, "DownloadTaskManager", FakeDownloadManager)
    from types import SimpleNamespace
    providers = adapters.create_providers()
    monkeypatch.setattr(adapters, "create_providers", lambda *args: providers)
    monkeypatch.setattr(rpyc.utils.server, "ThreadedServer", FakeServer)
    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    monkeypatch.setattr(ssl, "SSLContext", FakeContext)

    start_server(
        _config(),
        tls={"keyfile": "server.key", "certfile": "server.crt"},
    )

    assert ("context", ssl.PROTOCOL_TLS_SERVER) in calls
    assert ("certs", "server.crt", "server.key") in calls
    assert ("wrap", True) in calls
    assert calls.index("server.close") < calls.index("dm.shutdown")
    assert calls.index("dm.shutdown") < calls.index("cm.stop")
