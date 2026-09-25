"""Explicit SDK selection must never silently use an older installed package."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from qmt_rpyc.server.sdk_loader import configured_sdk_path


def sdk(root, value, body=None):
    package = root / 'xtquant'
    package.mkdir(parents=True)
    (package / '__init__.py').write_text(body or 'from .identity import VERSION\n')
    (package / 'identity.py').write_text('VERSION = {!r}\n'.format(value))
    return package


def run(code, *args, env=None):
    environment = {**os.environ, 'PYTHONPATH': str(Path(__file__).parents[1] / 'src'),
                   'QMT_XTQUANT_PATH': '', **(env or {})}
    return subprocess.run([sys.executable, '-c', code, *map(str, args)],
                          env=environment, capture_output=True, text=True, timeout=15)


def test_explicit_package_overrides_old_import_without_polluting_sys_path(tmp_path):
    old = sdk(tmp_path / 'old', 'old')
    new = sdk(tmp_path / 'new', 'new')
    result = run('''
import sys
from qmt_rpyc.server.sdk_loader import load_sdk
sys.path.insert(0, sys.argv[1])
before = list(sys.path)
assert load_sdk(sys.argv[2]).VERSION == 'new'
import xtquant.identity
assert xtquant.identity.VERSION == 'new'
assert sys.path == before
assert load_sdk(sys.argv[2]).VERSION == 'new'
''', old.parent, new)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('failure', ['missing', 'broken', 'already_loaded', 'relative'])
def test_wrong_path_or_loaded_sdk_cannot_fall_back(tmp_path, failure):
    old = sdk(tmp_path / 'old', 'old')
    new = sdk(tmp_path / 'new', 'new', 'raise ImportError("broken SDK")\n' if failure == 'broken' else None)
    target = tmp_path / 'absent' if failure == 'missing' else new
    if failure == 'relative':
        target = 'relative/xtquant'
    result = run('''
import sys
from qmt_rpyc.server.sdk_loader import load_sdk
sys.path.insert(0, sys.argv[1])
if sys.argv[3] == 'already_loaded':
    import xtquant
try:
    load_sdk(sys.argv[2])
except (ValueError, OSError, ImportError, RuntimeError):
    pass
else:
    raise AssertionError('SDK selection should fail')
if sys.argv[3] == 'broken':
    try:
        load_sdk(sys.argv[2])
    except RuntimeError:
        pass
    else:
        raise AssertionError('failed native import requires restart')
''', old.parent, target, failure)
    assert result.returncode == 0, result.stderr


def test_no_configuration_preserves_default_import(tmp_path):
    old = sdk(tmp_path / 'old', 'old')
    result = run('''
import sys
from qmt_rpyc.server.sdk_loader import load_sdk
sys.path.insert(0, sys.argv[1])
assert load_sdk().VERSION == 'old'
''', old.parent)
    assert result.returncode == 0, result.stderr


def test_environment_overrides_config(monkeypatch):
    monkeypatch.setenv('QMT_XTQUANT_PATH', 'environment')
    assert configured_sdk_path({'QMT_XTQUANT_PATH': 'file'}) == 'environment'


def test_cli_check_uses_sdk_from_selected_config(tmp_path):
    package = sdk(tmp_path / 'selected', 'new')
    config = tmp_path / 'config.env'
    config.write_text('QMT_XTQUANT_PATH=' + json.dumps(str(package)) + '\n')
    result = run('''
import os,sys
os.environ.pop('QMT_XTQUANT_PATH', None)
from qmt_rpyc.cli.server import main
raise SystemExit(main(['--config',sys.argv[1],'xtquant','check']))
''', config)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['path'] == str((package / '__init__.py').resolve())


def test_init_preserves_explicit_sdk_without_rewiring(tmp_path, monkeypatch):
    from qmt_rpyc.cli import server as cli
    package = sdk(tmp_path / 'selected', 'new')
    config = tmp_path / 'config.env'
    cli._write_env(config, {'QMT_XTQUANT_PATH': str(package), 'QMT_RPYC_AUTH_KEY': 'test-key'})
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('QMT_XTQUANT_PATH', raising=False)
    monkeypatch.setattr(cli, 'detect_environment', lambda: {'xtquant_site': 'old-sdk'})
    monkeypatch.setattr(cli, 'private_ipv4_addresses', lambda: [])
    monkeypatch.setattr(cli, 'wire_xtquant', lambda *args: pytest.fail('must not change old link'))
    args = cli.build_parser().parse_args(['--config', str(config), 'init', '--non-interactive', '--yes'])
    cli._cmd_init(args)
    assert cli._read_env(config)['QMT_XTQUANT_PATH'] == str(package)


def test_invalid_explicit_sdk_prevents_server_listener(tmp_path, monkeypatch):
    from qmt_rpyc.server.main import start_server
    from tests.test_main import _config
    import rpyc.utils.server
    monkeypatch.setattr(rpyc.utils.server, 'ThreadedServer',
                        lambda *a, **kw: pytest.fail('must not open listener'))
    with pytest.raises(FileNotFoundError):
        start_server(_config(xtquant_path=str(tmp_path / 'absent')))


def test_api_dump_uses_selected_sdk(tmp_path):
    package = sdk(tmp_path / 'selected', 'new')
    config = tmp_path / 'config.env'
    config.write_text('QMT_XTQUANT_PATH=' + json.dumps(str(package)) + '\n')
    output = tmp_path / 'surface.json'
    result = run('''
import os,sys
from types import SimpleNamespace
os.environ.pop('QMT_XTQUANT_PATH',None)
from qmt_rpyc.cli.server import main
from qmt_rpyc.adapters import registry
registry.select_adapter=lambda *a: SimpleNamespace(discover=lambda: {'chosen':sys.modules['xtquant'].VERSION})
raise SystemExit(main(['--config',sys.argv[1],'api','dump','--output',sys.argv[2]]))
''', config, output)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text())['api_surface']['chosen'] == 'new'
