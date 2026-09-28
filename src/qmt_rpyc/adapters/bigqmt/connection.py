"""Independent bridge health; RPC startup never waits for a running strategy."""
import logging
import threading
import time
from datetime import datetime, timezone

from qmt_rpyc.adapters.errors import ProviderError

from .transport import BridgeCapacityError, PipeTransport
from .winpipe import DEFAULT_PIPE

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self, path='', session_id=1, account_id='', heartbeat_interval=30,
                 heartbeat_timeout=5, heartbeat_max_failures=3, reconnect_max_attempts=0,
                 pipe_name=DEFAULT_PIPE, request_timeout=30, transport=None):
        # Common constructor arguments do not imply dependence on QMT userdata,
        # an SDK session or an account. The owned read-only bridge needs none.
        self.transport = transport or PipeTransport(pipe_name, request_timeout)
        self.interval, self.timeout = heartbeat_interval, heartbeat_timeout
        self.max_attempts = reconnect_max_attempts
        self.lock, self.stopping = threading.Lock(), threading.Event()
        self.thread = None
        self.started = time.monotonic()
        self.state, self.error, self.heartbeat = 'uninitialized', '', ''
        self.failures = 0

    def start(self):
        if self.thread is not None:
            raise RuntimeError('connection manager already started')
        self.thread = threading.Thread(target=self._run, name='bigqmt-bridge-health', daemon=True)
        self.thread.start()

    def probe(self):
        self.transport.request('ping', {}, timeout=self.timeout)
        with self.lock:
            self.failures = 0
            self.state, self.error = 'connected', ''
            self.heartbeat = datetime.now(timezone.utc).isoformat()
        return True

    def _run(self):
        while not self.stopping.is_set():
            try:
                self.probe()
            except BridgeCapacityError:
                # A local wait did not reach QMT. It is not evidence of a lost
                # strategy and must not consume the reconnection failure budget.
                logger.info('BigQMT heartbeat deferred: local capacity is busy')
            except Exception as exc:
                logger.warning('BigQMT bridge heartbeat failed', exc_info=True)
                with self.lock:
                    self.failures += 1
                    self.state = 'disconnected'
                    self.error = str(exc) if isinstance(exc, ProviderError) else 'BigQMT strategy bridge is unavailable'
                    if self.max_attempts and self.failures >= self.max_attempts:
                        self.state = 'exhausted'
                        return
            self.stopping.wait(self.interval)

    def stop(self):
        self.stopping.set()
        if self.thread is not None:
            self.thread.join(min(self.timeout, self.transport.timeout) + 1)
        with self.lock:
            self.state = 'stopped'

    def get_health_status(self):
        with self.lock:
            return dict(connected=self.state == 'connected', trader_available=self.state == 'connected',
                connection_state=self.state, last_connection_error=self.error,
                last_heartbeat=self.heartbeat, heartbeat_failures=self.failures,
                consecutive_failures=self.failures, reconnect_attempts=None,
                uptime_seconds=time.monotonic() - self.started, next_retry_at=None)
