"""Correlated, bounded service transport; no automatic call replay."""
import threading
import time
import uuid

from qmt_rpyc.adapters.errors import ProviderError
from .bridge_queue import BRIDGE_VERSION, MAX_WAIT_SECONDS, RESPONSE_LIMIT, wire_dump, wire_load
from .winpipe import DEFAULT_PIPE, MAX_CONNECTIONS, exchange, pipe_path


class BridgeCapacityError(ProviderError):
    def __init__(self):
        super().__init__('SOURCE_ERROR', '', 'BigQMT bridge capacity wait expired')


class PipeTransport:
    def __init__(self, name=DEFAULT_PIPE, timeout=30, exchange_fn=exchange):
        pipe_path(name)
        if not 0 < timeout <= MAX_WAIT_SECONDS:
            raise ValueError('bridge timeout is outside supported range')
        self.name, self.timeout, self.exchange = name, timeout, exchange_fn
        self.instance = None
        self.lock = threading.Lock()
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS - 1)
        self.heartbeat_slots = threading.BoundedSemaphore(1)
        self.active_business = self.active_heartbeat = 0

    def diagnostics(self):
        with self.lock:
            return dict(instance=self.instance, business_capacity=MAX_CONNECTIONS - 1,
                        heartbeat_capacity=1, active_business=self.active_business,
                        active_heartbeat=self.active_heartbeat, timeout_seconds=self.timeout)

    def _invalidate(self, observed_instance):
        with self.lock:
            if self.instance == observed_instance:
                self.instance = None

    def request(self, operation, arguments, timeout=None):
        mutation = operation in ('trade_submit', 'trade_cancel')
        wait = self.timeout if timeout is None else min(timeout, self.timeout)
        deadline = time.monotonic() + wait
        with self.lock:
            instance = self.instance
        if operation != 'ping' and instance is None:
            raise ProviderError('NOT_CONNECTED', '', 'BigQMT strategy bridge is not connected')
        heartbeat = operation == 'ping'
        slots = self.heartbeat_slots if heartbeat else self.slots
        if not slots.acquire(timeout=max(0, deadline - time.monotonic())):
            raise BridgeCapacityError()
        counter = 'active_heartbeat' if heartbeat else 'active_business'
        with self.lock:
            setattr(self, counter, getattr(self, counter) + 1)
        try:
            request_id = uuid.uuid4().hex
            request = dict(version=BRIDGE_VERSION, instance=None if operation == 'ping' else instance,
                id=request_id, operation=operation, arguments=dict(arguments),
                expires_at=time.time() + max(0, deadline - time.monotonic()))
            try:
                raw = self.exchange(self.name, wire_dump(request), deadline)
                reply = wire_load(raw, RESPONSE_LIMIT)
                expected = {'version', 'instance', 'id', 'error' if 'error' in reply else 'result'}
                if (type(reply) is not dict or set(reply) != expected or type(reply['version']) is not int
                        or reply['version'] != BRIDGE_VERSION or reply['id'] != request_id
                        or type(reply['instance']) is not str or len(reply['instance']) != 32
                        or any(char not in '0123456789abcdef' for char in reply['instance'])):
                    raise ValueError('invalid bridge response correlation')
                if operation != 'ping' and reply['instance'] != instance:
                    raise ValueError('bridge instance changed during request')
                if time.monotonic() >= deadline:
                    raise TimeoutError('late bridge response')
            except (OSError, ValueError, TypeError, RuntimeError, RecursionError) as exc:
                self._invalidate(instance)
                raise ProviderError('NOT_CONNECTED', '', 'BigQMT bridge exchange failed',
                                    'sdk_execution', 'unknown' if mutation else 'not_applicable') from exc
            if 'error' in reply:
                category = 'API_UNAVAILABLE' if reply['error'] == 'API_UNAVAILABLE' else 'SOURCE_ERROR'
                if reply['error'] in ('STALE_INSTANCE', 'STOPPED'):
                    category = 'NOT_CONNECTED'
                    self._invalidate(instance)
                raise ProviderError(category, '', 'BigQMT bridge operation failed: ' + str(reply['error']),
                                    'sdk_execution', 'unknown' if mutation else 'not_applicable')
            if operation == 'ping':
                if reply['result'] != {'runtime': 'bigqmt', 'read_only': False}:
                    raise ProviderError('NOT_CONNECTED', '', 'Unexpected BigQMT bridge runtime')
                with self.lock:
                    self.instance = reply['instance']
            return reply['result']
        finally:
            with self.lock:
                setattr(self, counter, getattr(self, counter) - 1)
            slots.release()
