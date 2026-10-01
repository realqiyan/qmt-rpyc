"""Upgrades are prepared before stop, verified separately, and never start RPC."""
from argparse import Namespace
import json
import os
from pathlib import Path
import subprocess
import zipfile

from packaging.version import Version
import pytest

from qmt_rpyc.cli import update, processes


@pytest.fixture
def environment(tmp_path, monkeypatch):
    monkeypatch.setattr(processes, 'runtime_dir', lambda: tmp_path)
    monkeypatch.setattr(update, '_installed', lambda: Version('0.8.0'))
    monkeypatch.setattr(processes, 'read_state', lambda: {})
    monkeypatch.setattr(processes, 'running', lambda: False)
    monkeypatch.setattr(processes, 'launch', lambda *a, **k: pytest.fail('update must not start a service'))
    return tmp_path


def fake_packages(monkeypatch, version='0.9.0', fail=None):
    calls = []
    def pip(args):
        calls.append(args)
        if fail == args[0]:
            raise RuntimeError('simulated package failure')
        if args[0] == 'download' and '--no-deps' in args:
            dest = Path(args[args.index('--dest') + 1])
            with zipfile.ZipFile(dest / ('qmt_rpyc-' + version + '-py3-none-any.whl'), 'w') as wheel:
                wheel.writestr('qmt_rpyc.dist-info/METADATA', 'Name: qmt-rpyc\nVersion: ' + version + '\n')
    monkeypatch.setattr(update, '_pip', pip)
    monkeypatch.setattr(update, '_verify', lambda *args: calls.append(('verify', *args)))
    return calls


def args(version=None, pre=False):
    return Namespace(target_version=version, pre=pre)


def test_stable_update_installs_then_verifies_without_start(environment, monkeypatch):
    calls = fake_packages(monkeypatch)
    result = update.perform(args())
    assert result['status'] == 'updated'
    assert result['version'] == '0.9.0'
    assert not result['server_stopped']
    assert calls[0][calls[0].index('--index-url') + 1] == update.PYPI
    assert [call[0] for call in calls] == ['download', 'download', 'install', 'verify']
    assert '--no-index' in calls[2]


@pytest.mark.parametrize('version,pre', [(None, True), ('0.9.0rc3', False)])
def test_prerelease_source_does_not_supply_dependencies(environment, monkeypatch, version, pre):
    calls = fake_packages(monkeypatch, '0.9.0rc3')
    update.perform(args(version, pre))
    assert update.TESTPYPI in calls[0]
    assert update.PYPI in calls[1]
    assert update.TESTPYPI not in calls[1]


def test_explicit_stable_overrides_pre_source(environment, monkeypatch):
    calls = fake_packages(monkeypatch)
    update.perform(args('0.9.0', True))
    assert update.PYPI in calls[0]


@pytest.mark.parametrize('available', ['0.8.0', '0.7.0'])
def test_no_upgrade_leaves_running_service_untouched(environment, monkeypatch, available):
    calls = fake_packages(monkeypatch, available)
    monkeypatch.setattr(processes, 'read_state', lambda: {'config': 'test.env'})
    monkeypatch.setattr(processes, 'running', lambda: True)
    monkeypatch.setattr(processes, 'stop', lambda: pytest.fail('must not stop'))
    assert update.perform(args())['status'] == 'unchanged'
    assert len(calls) == 1


def test_client_update_coordinates_local_server_and_allows_old_downgrade(environment, monkeypatch):
    config = environment / 'config.env'
    config.write_text('configuration-preserved')
    calls = fake_packages(monkeypatch, '0.7.0')
    monkeypatch.setattr(processes, 'read_state', lambda: {'config': str(config)})
    monkeypatch.setattr(processes, 'running', lambda: True)
    monkeypatch.setattr(processes, 'stop', lambda: calls.append(('stop',)))
    result = update.perform(args('0.7.0'))
    assert result['server_stopped']
    assert [call[0] for call in calls] == ['download', 'download', 'stop', 'install', 'verify']
    assert calls[-1] == ('verify', Version('0.7.0'), True)
    assert str(config) in result['next_steps'][0]
    assert next(environment.glob('config.env.backup-*')).read_text() == config.read_text()


def test_download_failure_does_not_stop_service(environment, monkeypatch):
    fake_packages(monkeypatch, fail='download')
    monkeypatch.setattr(processes, 'stop', lambda: pytest.fail('must not stop'))
    with pytest.raises(RuntimeError, match='preparation'):
        update.perform(args(), server=True, config='config.env')


def test_stop_failure_does_not_install(environment, monkeypatch):
    calls = fake_packages(monkeypatch)
    monkeypatch.setattr(processes, 'running', lambda: True)
    def stop():
        raise RuntimeError('timed out')
    monkeypatch.setattr(processes, 'stop', stop)
    with pytest.raises(RuntimeError, match='stopping'):
        update.perform(args(), server=True, config='config.env')
    assert all(call[0] != 'install' for call in calls)


@pytest.mark.parametrize('phase', ['installation', 'verification'])
def test_failure_reports_phase_and_does_not_restart(environment, monkeypatch, phase):
    calls = fake_packages(monkeypatch, fail='install' if phase == 'installation' else None)
    if phase == 'verification':
        def verify(*args):
            raise RuntimeError('broken installed command')
        monkeypatch.setattr(update, '_verify', verify)
    with pytest.raises(RuntimeError, match=phase):
        update.perform(args())


def test_editable_rejected_before_any_download(monkeypatch, tmp_path):
    class Distribution:
        version = '0.8.0'
        def read_text(self, name):
            return json.dumps({'dir_info': {'editable': True}})
    monkeypatch.setattr(update.metadata, 'distribution', lambda name: Distribution())
    monkeypatch.setattr(processes, 'runtime_dir', lambda: tmp_path)
    monkeypatch.setattr(update, '_pip', lambda *args: pytest.fail('must not run pip'))
    with pytest.raises(RuntimeError, match='Git'):
        update.perform(args())


def test_pip_ignores_user_package_sources(monkeypatch):
    monkeypatch.setenv('PIP_EXTRA_INDEX_URL', 'https://invalid.test/simple')
    captured = []
    def run(command, **kwargs):
        captured.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '', '')
    monkeypatch.setattr(update.subprocess, 'run', run)
    update._pip(['download', '--index-url', update.PYPI, 'qmt-rpyc'])
    assert captured[0][1]['env']['PIP_CONFIG_FILE'] == os.devnull
    assert 'PIP_EXTRA_INDEX_URL' not in captured[0][1]['env']


def test_fresh_process_verification_calls_both_clis(monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, '0.8.0\n', '')
    monkeypatch.setattr(update.subprocess, 'run', run)
    update._verify(Version('0.8.0'), True)
    assert len(calls) == 5
    assert all('-c' in call for call in calls)
    assert sum('qmt_rpyc.cli.client' in call[-1] for call in calls) == 2
    assert sum('qmt_rpyc.cli.server' in call[-1] for call in calls) == 2


@pytest.mark.skipif(os.name != 'nt', reason='Windows executable handoff')
def test_windows_update_worker_waits_for_original_command_and_writes_result(tmp_path):
    """Exercise the real helper handoff; mock only the package installation."""
    import sys
    import time
    source = str(Path(__file__).resolve().parents[1] / 'src')
    worker_code = '''
import sys
from pathlib import Path
from qmt_rpyc.cli import update
update.perform = lambda *a, **k: {'status': 'updated', 'next_steps': ['example start']}
sys.exit(update._worker(Path(sys.argv[1])))
'''
    parent_code = '''
import json, subprocess, sys
from argparse import Namespace
from packaging.version import Version
from qmt_rpyc.cli import update
update._installed = lambda: Version('0.8.0')
original = subprocess.Popen
worker = sys.argv[1]
def spawn(command, **kwargs):
    return original([sys.executable, '-u', '-c', worker, command[-1]], **kwargs)
update.subprocess.Popen = spawn
result = update.execute(Namespace(pre=False, target_version=None))
print(json.dumps(result), flush=True)
'''
    from pip._vendor.distlib.scripts import ScriptMaker
    (tmp_path / 'handoff.py').write_text('def main():\n' + ''.join(
        '    ' + line + '\n' for line in parent_code.splitlines()), encoding='utf-8')
    maker = ScriptMaker(None, str(tmp_path))
    maker.executable = sys.executable
    maker.variants = {''}
    maker.make('qmt-rpyc-client = handoff:main')
    result = subprocess.run([str(tmp_path / 'qmt-rpyc-client.exe'), worker_code],
                            env={**os.environ, 'LOCALAPPDATA': str(tmp_path),
                                 'PYTHONPATH': str(tmp_path) + os.pathsep + source},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    scheduled = json.loads(result.stdout)
    assert scheduled['status'] == 'scheduled'
    log = Path(scheduled['log'])
    assert len(json.loads((log.parent / 'job.json').read_text())['wait']) == 2
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        content = log.read_text(encoding='utf-8')
        if '"updated"' in content:
            break
        time.sleep(.05)
    assert json.loads(log.read_text(encoding='utf-8')) == {
        'status': 'updated', 'next_steps': ['example start']}
