"""Shared server configuration preserves init and runtime behavior."""
import os

import pytest

from qmt_rpyc.server import config


@pytest.fixture
def clean_env(monkeypatch):
    names = config.env_defaults()
    monkeypatch.setattr(os, 'environ', {key: value for key, value in os.environ.items() if key not in names})


def test_init_host_default_and_runtime_fallback_remain_distinct(clean_env, tmp_path):
    path = tmp_path / 'config.env'
    assert config.load_config(path)['host'] == '0.0.0.0'
    config.write_env(path, {})
    loaded = config.load_config(path)
    assert loaded['host'] == '127.0.0.1'
    assert loaded['port'] == 18812
    assert loaded['heartbeat_interval'] == 30
    assert loaded['bigqmt_timeout'] == 30


def test_environment_overrides_file_without_rewriting_it(clean_env, tmp_path, monkeypatch):
    path = tmp_path / 'config.env'
    config.write_env(path, {'QMT_RPYC_PORT': '12345', 'QMT_RPYC_ADAPTER': 'bigqmt'})
    before = path.read_bytes()
    monkeypatch.setenv('QMT_RPYC_PORT', '23456')
    monkeypatch.setenv('QMT_RPYC_DEBUG', 'true')
    loaded = config.load_config(path)
    assert loaded['port'] == 23456 and loaded['debug']
    assert loaded['adapter'] == 'bigqmt'
    assert path.read_bytes() == before


@pytest.mark.parametrize('name,value', [
    ('QMT_RPYC_PORT', '0'),
    ('QMT_RPYC_BIGQMT_TIMEOUT', '121'),
    ('QMT_SESSION_ID', '-1'),
    ('QMT_RPYC_DEBUG', 'maybe'),
])
def test_invalid_environment_still_rejected(clean_env, tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        config.load_config(tmp_path / 'absent.env')


def test_missing_optional_credentials_remain_none(clean_env, tmp_path):
    loaded = config.load_config(tmp_path / 'absent.env')
    assert loaded['auth_key'] is None
    assert loaded['tls_keyfile'] is None and loaded['tls_certfile'] is None
