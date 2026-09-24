"""Selection uses explicit installed adapters, independent of the broker SDK."""
import os
from pathlib import Path
import subprocess
import sys
from dataclasses import replace

import pytest

from qmt_rpyc.adapters import registry
from qmt_rpyc.server.main import _load_config, _validate_config


def test_default_and_unknown_adapter():
    adapter = registry.select_adapter()
    assert adapter.name == 'xtquant_2.0.6.1'
    assert adapter.sdk_version == '2.0.6.1'
    assert adapter.package.endswith('xtquant_2_0_6_1')
    with pytest.raises(ValueError, match='Unknown QMT_RPYC_ADAPTER'):
        registry.select_adapter('os')


def test_selection_does_not_import_the_sdk():
    subprocess.run([sys.executable, '-c', '''
import sys
from qmt_rpyc.adapters.registry import select_adapter
select_adapter()
assert 'xtquant' not in sys.modules
assert 'qmt_rpyc.adapters.xtquant_2_0_6_1.connection' not in sys.modules
'''], check=True, env={**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src')})


def test_environment_and_config_validation(monkeypatch, tmp_path):
    monkeypatch.setenv('QMT_RPYC_ADAPTER', 'missing')
    cfg = _load_config(tmp_path / 'absent.env')
    assert cfg['adapter'] == 'missing'
    with pytest.raises(ValueError, match='Unknown QMT_RPYC_ADAPTER'):
        _validate_config(cfg)


def test_switch_uses_selected_package_for_every_component(monkeypatch):
    selected = replace(registry.select_adapter(), name='synthetic', package='test_backend')
    monkeypatch.setattr(registry, 'ADAPTERS', {'synthetic': selected})
    imports = []
    class Module:
        ConnectionManager = object
        def create_providers(self, *args): return ('providers', args)
        def DebugGateway(self, connection): return ('debug', connection)
        def build_api_surface(self): return 'surface'
    def load(name):
        imports.append(name)
        return Module()
    monkeypatch.setattr(registry, 'import_module', load)
    adapter = registry.select_adapter('synthetic')
    assert adapter.connection_type() is object
    assert adapter.create_providers('connection')[0] == 'providers'
    assert adapter.create_debug('connection') == ('debug', 'connection')
    assert adapter.discover() == 'surface'
    assert imports == ['test_backend.connection', 'test_backend.factory',
                       'test_backend.debug', 'test_backend.discovery']
