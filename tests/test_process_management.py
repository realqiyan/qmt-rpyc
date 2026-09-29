"""Real OS locks, detached processes and local control without a broker SDK."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from qmt_rpyc.cli import processes
from qmt_rpyc.server.draining import DrainingError, RequestGate


@pytest.fixture
def local_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    monkeypatch.setenv('PYTHONPATH', str(Path(__file__).resolve().parents[1] / 'src'))
    return processes.runtime_dir()


def test_lock_is_exclusive_and_stale_pid_is_not_authority(local_runtime):
    processes.write_state({'pid': os.getpid(), 'port': 1234, 'token': 'stale'})
    assert not processes.running()
    with processes.instance_lock():
        assert processes.running()
        with pytest.raises(RuntimeError):
            with processes.instance_lock():
                pass
    assert not processes.running()


@pytest.mark.parametrize('launcher', ['direct', 'redirector', pytest.param(
    'windows_venv', marks=pytest.mark.skipif(os.name != 'nt', reason='Windows venv redirector'))])
def test_real_background_control_and_duplicate_start(local_runtime, tmp_path, monkeypatch, launcher):
    config = tmp_path / 'a config.env'
    config.write_text('')
    original = subprocess.Popen
    executable = sys.executable
    if launcher == 'windows_venv':
        import venv
        environment_path = tmp_path / 'venv'
        venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment_path)
        executable = str(environment_path / 'Scripts' / 'python.exe')
        monkeypatch.setattr(processes.sys, 'executable', executable)
        monkeypatch.setattr(processes.sys, 'prefix', str(environment_path))
    script = '''
import os, sys, threading
from qmt_rpyc.server import main as app
from qmt_rpyc.server.managed import run
if os.name == 'nt':
    import ctypes
    from ctypes import wintypes
    ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
    ctypes.windll.user32.IsWindowVisible.argtypes = [wintypes.HWND]
    assert not ctypes.windll.user32.IsWindowVisible(ctypes.windll.kernel32.GetConsoleWindow())
class Server:
    active = True
    def __init__(self): self.done = threading.Event()
    def close(self):
        self.active = False
        self.done.set()
def fake(config, verbose=False, runtime=None):
    server = Server()
    runtime.attach(server, None, None)
    server.done.wait(20)
    return 0
app.main = fake
run(sys.argv[1])
'''
    children = []
    def spawn(command, **kwargs):
        command = [executable, '-c', script, str(config)]
        if launcher == 'redirector':
            command = [executable, '-c',
                       'import subprocess, sys; sys.exit(subprocess.call(sys.argv[1:]))'] + command
        child = original(command, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(processes.subprocess, 'Popen', spawn)
    try:
        with processes.command_lock():
            result = processes.launch(config, timeout=10)
        assert result['rpc_ready']
        assert result['pid'] != os.getpid()
        if launcher in ('redirector', 'windows_venv'):
            assert result['pid'] != children[0].pid
        assert processes.read_state()['config'] == str(config)
        with pytest.raises(RuntimeError, match='already has'):
            processes.launch(config)
        assert processes.status()['rpc_ready']
        with processes.command_lock():
            assert processes.stop()['status'] == 'stopped'
        assert processes.status() == {'status': 'stopped', 'rpc_ready': False}
    finally:
        if processes.running():
            processes.stop()
        for child in children:
            child.wait(timeout=10)


@pytest.mark.skipif(os.name != 'nt', reason='Windows console launcher lifecycle')
def test_windows_background_survives_start_command_exit(local_runtime, tmp_path, monkeypatch):
    import venv
    from pip._vendor.distlib.scripts import ScriptMaker

    environment_path = tmp_path / 'venv'
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment_path)
    executable = str(environment_path / 'Scripts' / 'python.exe')
    monkeypatch.setattr(processes.sys, 'prefix', str(environment_path))
    stub = tmp_path / 'stub_server.py'
    stub.write_text('''
import threading
from qmt_rpyc.server import main as app
from qmt_rpyc.server.managed import main
class Server:
    active = True
    def __init__(self): self.done = threading.Event()
    def close(self):
        self.active = False
        self.done.set()
def fake(config, verbose=False, runtime=None):
    server = Server()
    runtime.attach(server, None, None)
    server.done.wait(30)
    return 0
app.main = fake
main()
''', encoding='utf-8')
    entry = tmp_path / 'start_entry.py'
    entry.write_text('''
def main():
    import json, subprocess, sys
    from qmt_rpyc.cli import processes
    original = subprocess.Popen
    def spawn(command, **kwargs):
        return original([sys.executable, sys.argv[1]] + command[3:], **kwargs)
    processes.subprocess.Popen = spawn
    with processes.command_lock():
        print(json.dumps(processes.launch(sys.argv[2], timeout=10)))
''', encoding='utf-8')
    maker = ScriptMaker(None, str(tmp_path))
    maker.executable = executable
    maker.variants = {''}
    maker.make('qmt-rpyc-server = start_entry:main')
    environment = dict(os.environ)
    environment['PYTHONPATH'] = str(tmp_path) + os.pathsep + environment['PYTHONPATH']
    try:
        result = subprocess.run([str(tmp_path / 'qmt-rpyc-server.exe'), str(stub),
                                 str(tmp_path / 'config.env')], env=environment,
                                capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        started = json.loads(result.stdout)
        assert started['rpc_ready']
        # The console executable and its interpreter have both exited. The
        # background interpreter must still answer authenticated local control.
        time.sleep(.2)
        status = processes.status()
        assert status['rpc_ready']
        assert status['pid'] == started['pid']
    finally:
        if processes.running():
            processes.stop()


def test_launch_rejects_readiness_from_another_launch(local_runtime, monkeypatch):
    class Child:
        pid = 123
        def poll(self):
            return None
    environment = {'EXAMPLE': 'preserved'}
    captured = []
    monkeypatch.setattr(processes, 'running', lambda: False)
    monkeypatch.setattr(processes.subprocess, 'Popen',
                        lambda *a, **kw: captured.append(kw['env']) or Child())
    monkeypatch.setattr(processes, 'status', lambda: dict(
        rpc_ready=True, pid=123, launch_id='an-earlier-launch'))
    with pytest.raises(RuntimeError, match='startup not ready'):
        processes.launch('config.env', timeout=.01, environment=environment)
    assert environment == {'EXAMPLE': 'preserved'}
    assert captured[0]['EXAMPLE'] == 'preserved'
    assert captured[0]['QMT_RPYC_LAUNCH_ID'] != 'an-earlier-launch'


def test_drain_waits_for_requests_and_downloads_and_rejects_new_work():
    from qmt_rpyc.server.downloads import DownloadTaskManager
    gate = RequestGate()
    manager = DownloadTaskManager()
    release = threading.Event()
    begun = threading.Event()
    finished = threading.Event()
    def download():
        begun.set()
        release.wait(3)
    manager.submit(download, 'test')
    assert begun.wait(1)
    context = gate.admit()
    context.__enter__()
    thread = threading.Thread(target=lambda: (gate.drain(manager, 3), finished.set()))
    thread.start()
    deadline = time.monotonic() + 1
    while not gate._draining and time.monotonic() < deadline:
        time.sleep(.01)
    with pytest.raises(DrainingError):
        with gate.admit():
            pytest.fail('must not execute')
    context.__exit__(None, None, None)
    assert not finished.wait(.05)
    release.set()
    assert finished.wait(2)
    thread.join()
    manager.shutdown()


def test_drain_timeout_restores_admission_without_cancelling_downloads():
    gate = RequestGate()
    class Downloads:
        def get_stats(self):
            return {'pending': 1, 'running': 1}
    with pytest.raises(TimeoutError):
        gate.drain(Downloads(), .02)
    with gate.admit():
        assert gate._active == 1


def test_active_request_timeout_restores_admission():
    gate = RequestGate()
    with gate.admit():
        with pytest.raises(TimeoutError):
            gate.drain(None, .01)
        with gate.admit():
            assert gate._active == 2


def test_control_token_rejects_unauthorized_commands(local_runtime):
    called = []
    with processes.instance_lock():
        control = processes.ControlServer(lambda *args: called.append(args) or {}, {})
        state = processes.read_state()
        processes.write_state(dict(state, token='wrong'))
        try:
            assert processes.request('stop')['status'] == 'error'
            assert called == []
        finally:
            control.close()


def test_restart_reuses_config_and_original_overrides(local_runtime, monkeypatch):
    from qmt_rpyc.cli import server
    processes.write_state(dict(config='saved.env', verbose=True,
                               overrides={'QMT_RPYC_ADAPTER': 'bigqmt'}))
    monkeypatch.setenv('QMT_RPYC_ADAPTER', 'wrong')
    events = []
    monkeypatch.setattr(processes, 'stop', lambda timeout: events.append('stop'))
    def launch(config, verbose, environment):
        events.append((config, verbose, environment['QMT_RPYC_ADAPTER']))
        return {}
    monkeypatch.setattr(processes, 'launch', launch)
    assert server.main(['restart']) == 0
    assert events == ['stop', ('saved.env', True, 'bigqmt')]


def test_drain_waits_for_reply_even_after_sdk_handler_returns():
    gate = RequestGate()
    sent = threading.Event()
    draining = threading.Event()
    with gate.reply():
        with gate.admit():
            pass  # SDK handler is done, but response has not been sent yet.
        thread = threading.Thread(target=lambda: (draining.set(), gate.drain(None, 2), sent.set()))
        thread.start()
        assert draining.wait(1)
        assert not sent.wait(.05)
    assert sent.wait(1)
    thread.join()


def test_stop_delivers_inflight_rpc_reply_before_closing_clients(monkeypatch):
    import rpyc
    from rpyc.utils.server import ThreadedServer
    from qmt_rpyc.server.managed import Runtime
    from qmt_rpyc.server.managed_protocol import ManagedConnection
    gate_runtime = Runtime()
    entered = threading.Event()
    release = threading.Event()
    class Service(rpyc.Service):
        _protocol = ManagedConnection
        _request_gate = gate_runtime.gate
        def exposed_work(self):
            with self._request_gate.admit():
                entered.set()
                assert release.wait(2)
                return 'completed-once'
    server = ThreadedServer(Service, hostname='127.0.0.1', port=0, auto_register=False)
    gate_runtime.attach(server, None, None)
    server_thread = threading.Thread(target=server.start, daemon=True)
    server_thread.start()
    deadline = time.monotonic() + 2
    while not server.active and time.monotonic() < deadline:
        time.sleep(.01)
    result = []
    failures = []
    client = rpyc.connect('127.0.0.1', server.port)
    def invoke():
        try:
            result.append(client.root.work())
        except Exception as exc:
            failures.append(exc)
    call_thread = threading.Thread(target=invoke)
    call_thread.start()
    assert entered.wait(1)
    stopped = []
    stop_thread = threading.Thread(target=lambda: stopped.append(gate_runtime.handle('stop', 2)))
    stop_thread.start()
    try:
        deadline = time.monotonic() + 1
        while not gate_runtime.gate._draining and time.monotonic() < deadline:
            time.sleep(.01)
        assert not stopped
        release.set()
        call_thread.join(3)
        stop_thread.join(3)
        assert not failures
        assert result == ['completed-once']
        assert stopped == [{'status': 'stopping'}]
    finally:
        release.set()
        client.close()
        server.close()
        server_thread.join(3)
