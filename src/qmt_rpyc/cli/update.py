"""Local package updates shared by the client and server entry points."""
import importlib.metadata as metadata
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from email.parser import BytesParser

from packaging.version import Version

from qmt_rpyc.cli import processes

PYPI = 'https://pypi.org/simple'
TESTPYPI = 'https://test.pypi.org/simple'


def add_arguments(parser):
    parser.add_argument('--pre', action='store_true', help='select prereleases from official TestPyPI')
    parser.add_argument('--version', dest='target_version', help='install this exact version, including downgrades')


def _pip(arguments):
    # A user's extra-index-url/find-links must not mix the two official sources.
    env = {key: value for key, value in os.environ.items() if not key.startswith('PIP_')}
    env['PIP_CONFIG_FILE'] = os.devnull
    result = subprocess.run(
        [sys.executable, '-m', 'pip', '--disable-pip-version-check', *arguments],
        env=env, capture_output=True, text=True,
    )
    if result.returncode:
        raise RuntimeError('pip failed:\n' + (result.stderr or result.stdout).strip())


def _installed():
    distribution = metadata.distribution('qmt-rpyc')
    direct = distribution.read_text('direct_url.json')
    if direct and json.loads(direct).get('dir_info', {}).get('editable'):
        raise RuntimeError('Editable installation: update the source with Git and rerun scripts/setup instead.')
    # Legacy editable installs can predate direct_url.json.
    if any(Path(entry, 'qmt-rpyc.egg-link').exists() or
           Path(entry, 'qmt_rpyc.egg-link').exists() for entry in sys.path):
        raise RuntimeError('Editable installation: update the source with Git instead.')
    return Version(distribution.version)


def _wheel_version(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(names) != 1:
            raise RuntimeError('Invalid package wheel metadata')
        info = BytesParser().parsebytes(archive.read(names[0]))
    if info['Name'].lower().replace('_', '-') != 'qmt-rpyc':
        raise RuntimeError('Unexpected package in update wheel')
    return Version(info['Version'])


def _verify(target, server):
    # Use only commands that also exist in releases predating process management.
    code = ('import importlib.metadata as m; '
            'from qmt_rpyc.version import __version__; '
            'assert m.version("qmt-rpyc") == __version__; print(__version__)')
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    options = dict(capture_output=True, text=True, cwd=processes.runtime_dir(),
                   env=environment, timeout=30)
    result = subprocess.run([sys.executable, '-c', code], **options)
    if result.returncode or Version(result.stdout.strip()) != target:
        raise RuntimeError('Installed version verification failed: ' + result.stderr.strip())
    modules = ['qmt_rpyc.cli.client']
    if server:
        modules.append('qmt_rpyc.cli.server')
    for module in modules:
        for flag in ('--help', '--version'):
            command = 'from {} import main; raise SystemExit(main([{}]))'.format(module, repr(flag))
            result = subprocess.run([sys.executable, '-c', command], **options)
            if result.returncode:
                raise RuntimeError('{} {} failed: {}'.format(module, flag, result.stderr.strip()))


def _steps(server, config):
    if not server:
        return ['Restart applications using qmt-rpyc to load the installed version.']
    executable = str(Path(sys.executable).parent / (
        'qmt-rpyc-server.exe' if os.name == 'nt' else 'qmt-rpyc-server'))
    return ['"{}" --config "{}" start'.format(executable, config)]


def perform(args, server=False, config=None, locked=False):
    phase = 'preparation'
    stopped = False
    with nullcontext() if locked else processes.command_lock():
        current = _installed()
        explicit = getattr(args, 'target_version', None)
        target = Version(explicit) if explicit else None
        pre = target.is_prerelease or target.is_devrelease if target else args.pre
        state = processes.read_state()
        server = server or bool(state.get('config')) or processes.running()
        config = state.get('config') or config
        if server and not config:
            raise RuntimeError('Server configuration is unknown; use qmt-rpyc-server --config PATH update')
        steps = _steps(server, config)
        recovery = []
        try:
            with tempfile.TemporaryDirectory(prefix='qmt-rpyc-update-') as directory:
                root = Path(directory)
                package_dir = root / 'package'
                package_dir.mkdir()
                requirement = 'qmt-rpyc' + ('==' + str(target) if target else '')
                options = ['download', '--no-deps', '--only-binary=:all:',
                           '--index-url', TESTPYPI if pre else PYPI,
                           '--dest', str(package_dir), requirement]
                if pre:
                    options.append('--pre')
                _pip(options)
                wheels = list(package_dir.glob('*.whl'))
                if len(wheels) != 1:
                    raise RuntimeError('Expected exactly one qmt-rpyc wheel')
                wheel = wheels[0]
                selected = _wheel_version(wheel)
                if target and selected != target:
                    raise RuntimeError('Downloaded version differs from requested version')
                if selected == current or (target is None and selected < current):
                    return dict(status='unchanged', version=str(current), server_stopped=False)
                target = selected
                recovery_dir = processes.runtime_dir() / 'recovery'
                recovery = [
                    subprocess.list2cmdline([sys.executable, '-m', 'pip', 'download', '--no-deps',
                                            '--only-binary=:all:', '--index-url', TESTPYPI if pre else PYPI,
                                            '--dest', str(recovery_dir), 'qmt-rpyc==' + str(target)]),
                    subprocess.list2cmdline([sys.executable, '-m', 'pip', 'install', '--index-url', PYPI,
                                            str(recovery_dir / wheel.name) + ('[server]' if server else '')]),
                ]
                # Resolve and fetch dependencies before interrupting a running server.
                artifact = str(wheel) + ('[server]' if server else '')
                dependencies = root / 'dependencies'
                _pip(['download', '--only-binary=:all:', '--index-url', PYPI,
                      '--dest', str(dependencies), artifact])
                if config and Path(config).exists():
                    stamp = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
                    shutil.copy2(config, str(config) + '.backup-' + stamp)
                phase = 'stopping'
                if processes.running():
                    processes.stop()
                    stopped = True
                phase = 'installation'
                _pip(['install', '--no-index', '--find-links', str(dependencies),
                      '--upgrade', '--upgrade-strategy', 'only-if-needed', artifact])
                phase = 'verification'
                _verify(target, server)
        except Exception as exc:
            raise RuntimeError('Update failed during {} (server stopped: {}). {} '
                               'No automatic rollback/start. Repair commands (run only after checking the failure): {}. '
                               'After successful repair: {}'
                               .format(phase, stopped, exc, '; '.join(recovery) or 'retry update', '; '.join(steps))) from exc
        return dict(status='updated', previous_version=str(current), version=str(target),
                    server_stopped=stopped, next_steps=steps,
                    compatibility='Check matching client/server and BigQMT strategy versions before starting.')


def _windows_parent_wait():
    """Wait for both Python and its console-script launcher to release files."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    pids = [os.getpid()]
    handle = kernel.OpenProcess(0x1000, False, os.getppid())
    if handle:
        try:
            length = wintypes.DWORD(32768)
            path = ctypes.create_unicode_buffer(length.value)
            if kernel.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)):
                if Path(path.value).name.lower() in ('qmt-rpyc-client.exe', 'qmt-rpyc-server.exe'):
                    pids.append(os.getppid())
        finally:
            kernel.CloseHandle(handle)
    return pids


def _wait_windows_processes(pids):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    for pid in pids:
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle and ctypes.get_last_error() != 87:
            raise OSError(ctypes.get_last_error(), 'Cannot wait for the old command process')
        if handle:
            try:
                if kernel.WaitForSingleObject(handle, 30000) != 0:
                    raise RuntimeError('Updater could not wait for the old command to exit')
            finally:
                kernel.CloseHandle(handle)


def execute(args, server=False, config=None):
    if os.name != 'nt':
        return perform(args, server, config)
    _installed()  # Reject source installs before scheduling any work.
    if args.target_version:
        Version(args.target_version)
    directory = Path(tempfile.mkdtemp(prefix='update-', dir=processes.runtime_dir()))
    job = directory / 'job.json'
    job.write_text(json.dumps(dict(pre=args.pre, target_version=args.target_version,
                                  server=server, config=config, wait=_windows_parent_wait())), encoding='utf-8')
    log = directory / 'result.log'
    with log.open('wb') as output:
        child = subprocess.Popen([sys.executable, '-u', '-m', 'qmt_rpyc.cli.update', str(job)],
                                 stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
    deadline = time.monotonic() + 10
    while not job.with_suffix('.ready').exists():
        if child.poll() is not None or time.monotonic() >= deadline:
            raise RuntimeError('Update worker did not start; inspect {}'.format(log))
        time.sleep(.05)
    return dict(status='scheduled', log=str(log),
                message='The worker will update after this command exits and releases the Windows launcher. '
                        'Read the log for the final result and recommended next commands; the service is not started.',
                next_steps=['Get-Content -LiteralPath "{}" -Wait'.format(log)])


def _worker(job):
    import argparse
    value = json.loads(job.read_text(encoding='utf-8'))
    try:
        with processes.command_lock():
            job.with_suffix('.ready').touch()
            _wait_windows_processes(value['wait'])
            result = perform(argparse.Namespace(pre=value['pre'], target_version=value['target_version']),
                             value['server'], value['config'], locked=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps(dict(status='error', message=str(exc)), ensure_ascii=False, indent=2))
        return 1


if __name__ == '__main__':
    sys.exit(_worker(Path(sys.argv[1])))
