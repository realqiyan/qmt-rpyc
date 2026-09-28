"""Python 3.6 bounded handoff from pipe I/O to the owning strategy thread."""
import json
import logging
import math
import queue
import threading
import time
import uuid

from .bridge_runtime import ARGUMENTS

BRIDGE_VERSION = 7
REQUEST_LIMIT = 64 * 1024
RESPONSE_LIMIT = 16 * 1024 * 1024
MAX_WAIT_SECONDS = 120
logger = logging.getLogger(__name__)


def wire_load(raw, limit):
    if not isinstance(raw, bytes) or not 0 < len(raw) <= limit:
        raise ValueError('invalid bridge frame length')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('nonfinite JSON number')
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('nonfinite JSON number')
        return number
    return json.loads(raw.decode('utf-8'), object_pairs_hook=pairs, parse_constant=reject,
                      parse_float=finite_float)


def wire_dump(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


class Ticket:
    def __init__(self, owner, request, deadline):
        self.owner, self.request, self.deadline = owner, request, deadline
        self.lock = threading.Lock()
        self.state, self.response = 'queued', None

    def _error(self, code):
        return self.owner.reply(self.request['id'], error=code)

    def cancel(self):
        # One lock linearizes cancellation and the queued -> running transition.
        # Running native calls cannot be interrupted; discard their late result.
        with self.lock:
            if self.state in ('queued', 'running'):
                self.state = 'cancelled'
                self.response = None

    def claim(self):
        with self.owner.lock, self.lock:
            if self.state != 'queued':
                return False
            if self.owner.closed:
                self.state = 'cancelled'
                return False
            if self.owner.clock() >= self.deadline:
                self.state, self.response = 'done', self._error('EXPIRED')
                return False
            self.state = 'running'
            return True

    def finish(self, response):
        with self.lock:
            if self.state == 'running':
                self.state = 'done'
                self.response = self._error('EXPIRED') if self.owner.clock() >= self.deadline else response

    def poll(self):
        with self.lock:
            if self.state in ('queued', 'running') and self.owner.clock() >= self.deadline:
                self.state, self.response = 'done', self._error('EXPIRED')
            return self.response


class BridgeQueue:
    def __init__(self, runtime, capacity=32, clock=time.monotonic, wall_clock=time.time):
        if type(capacity) is not int or capacity < 1:
            raise ValueError('capacity must be positive')
        self.runtime, self.clock, self.wall_clock = runtime, clock, wall_clock
        self.instance = uuid.uuid4().hex
        self.pending = queue.Queue(capacity)
        self.lock, self.closed = threading.Lock(), False

    def reply(self, request_id, result=None, error=None):
        envelope = dict(version=BRIDGE_VERSION, instance=self.instance, id=request_id)
        envelope.update(dict(error=error) if error else dict(result=result))
        try:
            raw = wire_dump(envelope)
            if len(raw) <= RESPONSE_LIMIT:
                return raw
        except (TypeError, ValueError, OverflowError, RecursionError):
            logger.exception('Bridge result serialization failed')
            error = 'INVALID_RESULT'
        return wire_dump(dict(version=BRIDGE_VERSION, instance=self.instance, id=request_id,
                              error=error or 'RESULT_TOO_LARGE'))

    def submit(self, raw):
        request = wire_load(raw, REQUEST_LIMIT)
        if (type(request) is not dict or set(request) != {'version', 'instance', 'id', 'expires_at', 'operation', 'arguments'}
                or type(request['version']) is not int
                or type(request['id']) is not str or len(request['id']) != 32
                or any(char not in '0123456789abcdef' for char in request['id'])
                or type(request['operation']) is not str or request['operation'] not in ARGUMENTS
                or type(request['arguments']) is not dict
                or set(request['arguments']) != set(ARGUMENTS[request['operation']])
                or type(request['expires_at']) not in (int, float) or not math.isfinite(request['expires_at'])):
            raise ValueError('invalid bridge request')
        remaining = request['expires_at'] - self.wall_clock()
        ticket = Ticket(self, request, self.clock() + max(0, min(MAX_WAIT_SECONDS, remaining)))
        error = None
        if request['version'] != BRIDGE_VERSION:
            ticket.state, ticket.response = 'done', self.reply(request['id'], error='PROTOCOL_MISMATCH')
            return ticket
        if request['instance'] != self.instance and not (request['instance'] is None and request['operation'] == 'ping'):
            error = 'STALE_INSTANCE'
        elif remaining <= 0 or remaining > MAX_WAIT_SECONDS + 1:
            error = 'EXPIRED'
        with self.lock:
            if self.closed:
                error = 'STOPPED'
            if error is None:
                try:
                    self.pending.put_nowait(ticket)
                except queue.Full:
                    error = 'OVERLOADED'
            if error:
                ticket.state, ticket.response = 'done', self.reply(request['id'], error=error)
        return ticket

    def pump(self, max_calls=4, budget_seconds=.02):
        if threading.get_ident() != self.runtime.owner_thread:
            raise RuntimeError('bridge pump must run on the strategy thread')
        end = self.clock() + budget_seconds
        for _ in range(max_calls):
            if self.closed or self.clock() >= end:
                return
            try:
                ticket = self.pending.get_nowait()
            except queue.Empty:
                return
            if not ticket.claim():
                continue
            try:
                self.runtime.request_deadline = ticket.deadline
                result = self.runtime.dispatch(ticket.request['operation'], ticket.request['arguments'])
                response = self.reply(ticket.request['id'], result=result)
            except NotImplementedError:
                response = self.reply(ticket.request['id'], error='API_UNAVAILABLE')
            except Exception:
                logger.exception('BigQMT read failed: %s', ticket.request['operation'])
                response = self.reply(ticket.request['id'], error='SOURCE_ERROR')
            ticket.finish(response)

    def stop(self):
        with self.lock:
            self.closed = True
            while True:
                try:
                    self.pending.get_nowait().cancel()
                except queue.Empty:
                    return
