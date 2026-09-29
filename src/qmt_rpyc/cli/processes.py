"""Per-environment local process control, independent of business RPC and SDKs."""
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time

from platformdirs import user_state_dir


class FileLock:
    """An OS-owned lock; crashes release it without trusting a recorded PID."""
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.file = open(self.path, 'a+b')
        try:
            if os.name == 'nt':
                import msvcrt
                if self.path.stat().st_size == 0:
                    self.file.write(b'0')
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.file.close()
            self.file = None
            raise RuntimeError('Another server or management command owns {}'
                               .format(self.path)) from exc
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()
            self.file = None


def runtime_dir():
    identity = os.path.normcase(os.path.realpath(sys.prefix))
    key = hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    local = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else None
    base = Path(local) / 'qmt-rpyc' / 'state' if local else Path(user_state_dir('qmt-rpyc', appauthor=False))
    path = base / key
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != 'nt':
        path.chmod(0o700)
    return path


def command_lock():
    return FileLock(runtime_dir() / 'command.lock')


def instance_lock():
    return FileLock(runtime_dir() / 'instance.lock')


def running():
    try:
        with instance_lock():
            return False
    except RuntimeError:
        return True


def read_state():
    try:
        return json.loads((runtime_dir() / 'process.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def write_state(value):
    path = runtime_dir() / 'process.json'
    temporary = path.with_suffix('.tmp')
    fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(value, output)
    os.replace(str(temporary), str(path))


def _receive(connection):
    with connection.makefile('rb') as stream:
        line = stream.readline(65537)
    if len(line) > 65536 or not line.endswith(b'\n'):
        raise ValueError('Invalid local control response')
    return json.loads(line)


def request(command, timeout=60):
    state = read_state()
    if not running():
        return {'status': 'stopped', 'rpc_ready': False}
    if 'port' not in state:
        raise RuntimeError('Server is starting; local control is not ready')
    with socket.create_connection(('127.0.0.1', state['port']), timeout=3) as conn:
        conn.settimeout(timeout + 10)
        payload = dict(token=state['token'], command=command, timeout=timeout)
        conn.sendall(json.dumps(payload).encode('utf-8') + b'\n')
        return _receive(conn)


def status():
    if not running():
        return {'status': 'stopped', 'rpc_ready': False}
    try:
        result = request('status', timeout=3)
        result['log'] = str(runtime_dir() / 'server.log')
        result['config'] = read_state().get('config')
        return result
    except (OSError, ValueError, RuntimeError) as exc:
        return {'status': 'running', 'rpc_ready': False,
                'control_error': str(exc), 'log': str(runtime_dir() / 'server.log')}


def stop(timeout=60):
    if not 0 < timeout <= 3600:
        raise ValueError("Stop timeout must be between 0 and 3600 seconds")
    if not running():
        return {'status': 'stopped', 'rpc_ready': False}
    response = request('stop', timeout)
    if response.get('status') != 'stopping':
        raise RuntimeError(response.get('message', 'Unable to stop server'))
    deadline = time.monotonic() + 15
    while running():
        if time.monotonic() >= deadline:
            raise RuntimeError('Requests drained but server cleanup has not exited; '
                               'installation cancelled. Inspect server.log and status.')
        time.sleep(.1)
    return {'status': 'stopped', 'rpc_ready': False}


def launch(config, verbose=False, timeout=30, environment=None):
    if running():
        raise RuntimeError('This Python environment already has a running server')
    config = str(Path(config).expanduser().resolve())
    command = [sys.executable, '-m', 'qmt_rpyc.server.managed', '--config', config]
    if verbose:
        command.append('--verbose')
    options = {}
    if os.name == 'nt':
        # A detached venv launcher can give its interpreter a new visible
        # console. A windowless console is inherited through that redirector.
        options['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        options['start_new_session'] = True
    log = runtime_dir() / 'server.log'
    # Windows venv python.exe may be a redirector whose PID differs from the
    # interpreter's. Correlate readiness with this launch, not the launcher PID.
    launch_id = secrets.token_hex(16)
    child_environment = dict(os.environ if environment is None else environment)
    child_environment['QMT_RPYC_LAUNCH_ID'] = launch_id
    with log.open('ab') as output:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=output,
                                 stderr=output, env=child_environment, **options)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('Server exited during startup; inspect {}'.format(log))
        result = status()
        if result.get('rpc_ready') and result.get('launch_id') == launch_id:
            return result
        time.sleep(.1)
    raise RuntimeError('Server startup not ready within {} seconds; process may still '
                       'be running. Use status/stop; inspect {}'.format(timeout, log))


class ControlServer:
    """Bounded JSON over loopback, authenticated by a private per-process token."""
    def __init__(self, handler, state):
        token = secrets.token_hex(32)

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(3)
                try:
                    value = _receive(self.request)
                    if not secrets.compare_digest(str(value.get('token', '')), token):
                        result = {'status': 'error', 'message': 'Invalid control token'}
                    else:
                        timeout = float(value.get('timeout', 60))
                        if not 0 < timeout <= 3600:
                            raise ValueError('Invalid timeout')
                        result = handler(value.get('command'), timeout)
                    self.request.sendall(json.dumps(result).encode('utf-8') + b'\n')
                except (OSError, ValueError, TypeError):
                    # Malformed/abandoned connections cannot affect server lifetime.
                    import logging
                    logging.getLogger(__name__).warning('Local control request failed', exc_info=True)

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
            block_on_close = False

        self.server = Server(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        write_state(dict(state, pid=os.getpid(), token=token,
                         port=self.server.server_address[1]))
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
