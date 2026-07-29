import ssl

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


def test_config_requires_auth_unless_explicitly_insecure():
    with pytest.raises(ValueError, match="QMT_RPYC_AUTH_KEY"):
        _validate_config(_config(auth_key=None))

    _validate_config(_config(auth_key=None, allow_insecure=True))

    with pytest.raises(ValueError, match="at least 16 bytes"):
        _validate_config(_config(auth_key="too-short"))


def test_config_requires_complete_tls_pair():
    with pytest.raises(ValueError, match="configured together"):
        _validate_config(_config(tls_keyfile="server.key"))


def test_tls_uses_server_context_and_cleanup_order(monkeypatch):
    import socket
    import qmt_rpyc.server.api_surface as api_surface
    import qmt_rpyc.server.connection as connection
    import qmt_rpyc.server.download_manager as download_manager
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
    monkeypatch.setattr(api_surface, "build_api_surface", lambda: {})
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
