"""Server process owner and independent local management channel."""
import argparse
from contextlib import ExitStack, nullcontext
import os
import sys
import threading

from qmt_rpyc.cli import processes
from qmt_rpyc.server.draining import RequestGate


class Runtime:
    def __init__(self):
        self.launch_id = os.environ.pop('QMT_RPYC_LAUNCH_ID', None)
        self.gate = RequestGate()
        self.server = None
        self.downloads = None
        self.connection = None
        self._stop_lock = threading.Lock()
        self._closing = False

    def attach(self, server, downloads, connection):
        self.server = server
        self.downloads = downloads
        self.connection = connection

    def handle(self, command, timeout):
        if command == 'status':
            ready = bool(self.server and self.server.active and not self._closing)
            return dict(status='stopping' if self._closing else 'running',
                        pid=os.getpid(), rpc_ready=ready, launch_id=self.launch_id)
        if command != 'stop':
            return dict(status='error', message='Unknown local control command')
        if not self._stop_lock.acquire(blocking=False):
            return dict(status='error', message='Server is already stopping')
        try:
            if not self.server or not self.server.active:
                return dict(status='error', message='Server is not ready for orderly stop; inspect server.log')
            self.gate.drain(self.downloads, timeout)
            self._closing = True
            threading.Thread(target=self.server.close, daemon=True).start()
            return dict(status='stopping')
        except (TimeoutError, RuntimeError) as exc:
            return dict(status='error', message=str(exc) + '; new requests are accepted again')
        finally:
            self._stop_lock.release()


def run(config, verbose=False, foreground=False):
    from qmt_rpyc.server.main import main
    with ExitStack() as stack:
        with processes.command_lock() if foreground else nullcontext():
            stack.enter_context(processes.instance_lock())
            runtime = Runtime()
            overrides = {key: value for key, value in os.environ.items()
                         if key.startswith(('QMT_', 'QMT_RPYC_'))}
            control = processes.ControlServer(runtime.handle, dict(
                config=str(config), verbose=verbose, overrides=overrides))
        try:
            return main(config, verbose=verbose, runtime=runtime)
        finally:
            control.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    return run(args.config, args.verbose)


if __name__ == '__main__':
    sys.exit(main())
