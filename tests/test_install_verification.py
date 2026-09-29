"""Reject same-version stale deployments without requiring a live RPC server."""
import importlib.util
from pathlib import Path

import pytest


def checker():
    spec = importlib.util.spec_from_file_location('install_verifier', Path('verify-install.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_install_reports_paths_and_both_compatibility_ids():
    check = checker()
    result = check.verify(check.version.__version__)
    assert result['contract'] == result['bridge'] == 8
    assert Path(result['python']).is_file()
    assert Path(result['package']).is_dir()


@pytest.mark.parametrize('name,value', [('CONTRACT_VERSION', 5), ('BRIDGE_VERSION', 4)])
def test_same_package_version_does_not_hide_stale_protocol(monkeypatch, capsys, name, value):
    check = checker()
    monkeypatch.setattr(check, name, value)
    assert check.main(['--expected-version', check.version.__version__]) == 1
    assert 'Mixed installation' in capsys.readouterr().err


def test_wrong_wheel_version_fails_before_configuration(monkeypatch, capsys):
    check = checker()
    wrong_version = check.version.__version__ + '+mismatch'
    assert check.main(['--expected-version', wrong_version]) == 1
    assert 'Expected qmt-rpyc ' + wrong_version in capsys.readouterr().err


def test_bundle_strategy_is_checked_without_executing_it(tmp_path):
    import runpy
    check = checker()
    source = runpy.run_path('scripts/build_bigqmt_strategy.py')['build']()
    path = tmp_path / 'bigqmt_strategy.py'
    path.write_bytes(source.encode('gbk'))
    result = check.verify_strategy(path, check.verify())
    assert len(result['build']) == 16
    path.write_bytes((source + '\n# edited after packaging\n').encode('gbk'))
    with pytest.raises(ValueError, match='fingerprint mismatch'):
        check.verify_strategy(path, check.verify())
    path.write_bytes(source.replace('BRIDGE_VERSION = 8', 'BRIDGE_VERSION = 4').encode('gbk'))
    with pytest.raises(ValueError, match='does not match'):
        check.verify_strategy(path, check.verify())


@pytest.mark.skipif(__import__('sys').platform != 'win32', reason='requires Windows cmd.exe')
@pytest.mark.parametrize('verification_exit', [0, 1])
def test_managed_launcher_checks_install_before_starting_server(tmp_path, verification_exit):
    import os
    import shutil
    import subprocess
    import venv
    bundle = tmp_path / 'bundle with spaces'
    bundle.mkdir()
    shutil.copy2('start-rpyc.bat', bundle / 'start-rpyc.bat')
    # Simulate the verifier discovering stale files. Exercise the actual BAT
    # branching/error propagation independently of the verifier unit tests.
    (bundle / 'verify-install.py').write_text('raise SystemExit(%d)\n' % verification_exit)
    local = tmp_path / 'local data'
    managed = local / 'qmt-rpyc'
    venv.EnvBuilder(with_pip=False).create(managed / 'venv')
    marker = tmp_path / 'server-started'
    (managed / 'qmt-rpyc-server.bat').write_bytes(
        ('@echo off\r\necho started > "' + str(marker) + '"\r\n').encode())
    env = dict(os.environ, LOCALAPPDATA=str(local))
    result = subprocess.run(['cmd.exe', '/c', str(bundle / 'start-rpyc.bat')],
                            env=env, input='\n', text=True, capture_output=True, timeout=30)
    assert result.returncode == verification_exit
    assert marker.exists() == (verification_exit == 0)
    assert 'Environment: managed' in result.stdout
    assert ('Incomplete or outdated installation' in result.stdout) == (verification_exit != 0)


def test_legacy_install_without_bigqmt_has_actionable_error(monkeypatch, capsys):
    import builtins
    import runpy
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == 'qmt_rpyc.adapters.bigqmt.bridge_queue':
            raise ImportError('legacy package')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', missing)
    with pytest.raises(SystemExit) as error:
        runpy.run_path('verify-install.py')
    assert error.value.code == 1
    assert 'current official bundle' in capsys.readouterr().err
