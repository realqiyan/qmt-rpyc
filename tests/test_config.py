import os

from qmt_rpyc import config


def test_profile_round_trip_with_plaintext_fallback(tmp_path, monkeypatch):
    path = tmp_path / "client.toml"
    monkeypatch.setenv("QMT_RPYC_CLIENT_CONFIG", str(path))
    monkeypatch.delenv("QMT_RPYC_AUTH_KEY", raising=False)

    saved = config.save_profile(
        "office",
        {"host": "192.168.1.20", "port": 18812, "timeout": 10},
        secret="secret-value",
        store_plaintext=True,
    )

    assert saved == path
    profile = config.resolve_profile("office")
    assert profile["host"] == "192.168.1.20"
    assert profile["port"] == 18812
    assert profile["auth_key"] == "secret-value"


def test_environment_overrides_profile(tmp_path, monkeypatch):
    path = tmp_path / "client.toml"
    monkeypatch.setenv("QMT_RPYC_CLIENT_CONFIG", str(path))
    config.save_profile(
        "default",
        {"host": "127.0.0.1", "port": 18812},
        secret="stored",
        store_plaintext=True,
    )
    monkeypatch.setenv("QMT_RPYC_HOST", "10.0.0.8")
    monkeypatch.setenv("QMT_RPYC_PORT", "19999")
    monkeypatch.setenv("QMT_RPYC_AUTH_KEY", "environment")

    profile = config.resolve_profile()

    assert profile["host"] == "10.0.0.8"
    assert profile["port"] == 19999
    assert profile["auth_key"] == "environment"


def test_delete_profile_removes_entry(tmp_path, monkeypatch):
    path = tmp_path / "client.toml"
    monkeypatch.setenv("QMT_RPYC_CLIENT_CONFIG", str(path))
    config.save_profile(
        "test", {"host": "localhost"}, secret="x" * 16,
        store_plaintext=True,
    )
    monkeypatch.setattr(config, "delete_profile_secret", lambda name: None)

    assert config.delete_profile("test") is True
    assert config.load_profiles() == {}
