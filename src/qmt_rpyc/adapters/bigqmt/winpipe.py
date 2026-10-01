"""Owned overlapped Win32 pipe I/O, shared by the strategy and service.

Python 3.6 compatible. Native requests retain their buffers through cancellation.
No QMT business call executes in this module.

Strategy-side reading and writing is driven by the QMT callback thread
(``PipeServer.poll``); the service keeps one persistent client connection per
concurrent slot instead of reconnecting per request.
"""
import ctypes
import logging
import re
import threading
import time
from ctypes import wintypes

from .bridge_queue import REQUEST_LIMIT, RESPONSE_LIMIT

logger = logging.getLogger(__name__)
ERROR_IO_PENDING = 997
ERROR_IO_INCOMPLETE = 996
ERROR_PIPE_CONNECTED = 535
MAX_CONNECTIONS = 4
IO_TIMEOUT = 5.0
DEFAULT_PIPE = "qmt_rpyc_bridge_v1"


class PipeWriteError(OSError):
    """WriteFile failed before anything could leave this process."""


def pipe_path(name):
    if type(name) is not str or re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name) is None:
        raise ValueError("invalid local pipe name")
    return "\\\\.\\pipe\\" + name


class Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32),
                ("hEvent", wintypes.HANDLE)]


def emit(event, **data):
    logger.warning("BigQMT pipe %s: %s", event, data)


def kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(Overlapped)
    dword_pointer = ctypes.POINTER(wintypes.DWORD)
    definitions = {
        "CreateFileW": (wintypes.HANDLE, [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]),
        "SetNamedPipeHandleState": (wintypes.BOOL, [wintypes.HANDLE, dword_pointer, dword_pointer, dword_pointer]),
        "CreateNamedPipeW": (wintypes.HANDLE, [wintypes.LPCWSTR] + [wintypes.DWORD] * 6 + [wintypes.LPVOID]),
        "CreateEventW": (wintypes.HANDLE, [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]),
        "CloseHandle": (wintypes.BOOL, [wintypes.HANDLE]),
        "ConnectNamedPipe": (wintypes.BOOL, [wintypes.HANDLE, pointer]),
        "ReadFile": (wintypes.BOOL, [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD, dword_pointer, pointer]),
        "WriteFile": (wintypes.BOOL, [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, dword_pointer, pointer]),
        "GetOverlappedResult": (wintypes.BOOL, [wintypes.HANDLE, pointer, dword_pointer, wintypes.BOOL]),
        "CancelIoEx": (wintypes.BOOL, [wintypes.HANDLE, pointer]),
    }
    for name, (result, arguments) in definitions.items():
        method = getattr(dll, name)
        method.restype, method.argtypes = result, arguments
    return dll


class Pending:
    """Own the native buffer and OVERLAPPED until completion, including cancel."""
    def __init__(self, dll, handle, kind, data=None, read_limit=REQUEST_LIMIT):
        self.dll, self.handle, self.kind = dll, handle, kind
        self.overlapped = Overlapped()
        self.overlapped.hEvent = dll.CreateEventW(None, True, False, None)
        if not self.overlapped.hEvent:
            raise OSError(ctypes.get_last_error(), "CreateEventW")
        self.buffer = ctypes.create_string_buffer(data) if data is not None else ctypes.create_string_buffer(read_limit if kind == "read" else 1)
        self.count = wintypes.DWORD()
        self.started = time.monotonic()
        self.done, self.error, self.cancel_requested = False, 0, False
        pointer = ctypes.byref(self.overlapped)
        if kind == "connect":
            ok = dll.ConnectNamedPipe(handle, pointer)
        elif kind == "read":
            ok = dll.ReadFile(handle, self.buffer, read_limit, ctypes.byref(self.count), pointer)
        else:
            ok = dll.WriteFile(handle, self.buffer, len(data), ctypes.byref(self.count), pointer)
        error = 0 if ok else ctypes.get_last_error()
        if kind == "connect" and error == ERROR_PIPE_CONNECTED:
            error = 0
        if error != ERROR_IO_PENDING:
            self.done, self.error = True, error

    def poll(self):
        if not self.done:
            ok = self.dll.GetOverlappedResult(self.handle, ctypes.byref(self.overlapped),
                                              ctypes.byref(self.count), False)
            error = 0 if ok else ctypes.get_last_error()
            if error != ERROR_IO_INCOMPLETE:
                self.done, self.error = True, error
        return self.done

    def cancel(self):
        if self.done or self.cancel_requested:
            return
        self.cancel_requested = True
        if not self.dll.CancelIoEx(self.handle, ctypes.byref(self.overlapped)):
            error = ctypes.get_last_error()
            # ERROR_NOT_FOUND also occurs when completion wins the race.
            if error != 1168:
                emit("cancel_error", winerror=error)

    def close_event(self):
        if not self.done:
            raise RuntimeError("Cannot release pending OVERLAPPED")
        if self.overlapped.hEvent:
            if not self.dll.CloseHandle(self.overlapped.hEvent):
                emit("event_close_error", winerror=ctypes.get_last_error())
            self.overlapped.hEvent = None


# Retain exceptional unfinished operations outside the unloadable QMT strategy.
_retained = getattr(ctypes, '_qmt_rpyc_bridge_retained', None)
if _retained is None:
    _retained = []
    ctypes._qmt_rpyc_bridge_retained = _retained
_retained_lock = threading.Lock()


class HandleOwner:
    def __init__(self, dll, handle):
        self.dll, self.handle, self.pending = dll, handle, None

    def close(self):
        if self.pending is not None:
            self.pending.cancel()
            if not self.pending.poll():
                return False
            self.pending.close_event()
            self.pending = None
        if self.handle is not None:
            if not self.dll.CloseHandle(self.handle):
                emit('pipe_close_error', winerror=ctypes.get_last_error())
                return False
            self.handle = None
        return True


def retain(owner):
    with _retained_lock:
        if owner not in _retained:
            _retained.append(owner)


def reap():
    with _retained_lock:
        for owner in list(_retained):
            if owner.close():
                _retained.remove(owner)
        return len(_retained)


class PipeChannel(HandleOwner):
    def __init__(self, dll, name, bridge, first=False):
        flags = 3 | 0x40000000 | (0x00080000 if first else 0)
        # Message mode + overlapped I/O + reject remote clients. Default DACL
        # requires the service and QMT to run under the same Windows identity.
        handle = dll.CreateNamedPipeW(pipe_path(name), flags, 4 | 2 | 8,
                                     MAX_CONNECTIONS, 4096, 4096, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), 'CreateNamedPipeW')
        super().__init__(dll, handle)
        self.bridge, self.ticket, self.closing = bridge, None, False
        self.reply = None
        try:
            self.pending = Pending(dll, handle, 'connect')
        except Exception:
            if not self.close():
                retain(self)
            raise

    def close(self):
        self.closing = True
        if self.ticket is not None:
            self.ticket.cancel()
        return super().close()

    def tick(self):
        if self.closing:
            return self.close()
        if self.ticket is not None and self.pending is None:
            self.reply = self.ticket.poll()
            if self.reply is not None:
                self.pending = Pending(self.dll, self.handle, 'write', self.reply)
            return False
        pending = self.pending
        if not pending.poll():
            # A persistent connection may legitimately wait here for the next
            # request; only the write phase is bounded.
            if pending.kind == 'write' and time.monotonic() - pending.started >= IO_TIMEOUT:
                return self.close()
            return False
        pending.close_event()
        self.pending = None
        if pending.error:
            return self.close()
        if pending.kind == 'connect':
            self.pending = Pending(self.dll, self.handle, 'read')
        elif pending.kind == 'read':
            # Zero bytes means the client closed; anything else is the next
            # request on the same persistent connection.
            if pending.count.value == 0:
                return self.close()
            try:
                self.ticket = self.bridge.submit(pending.buffer.raw[:pending.count.value])
            except (ValueError, TypeError, UnicodeError, RecursionError):
                return self.close()
        else:
            # Reuse the connection for the next request. Closing after one
            # response forces the strategy to rebuild a pipe instance from its
            # own callback before it can read again.
            if pending.count.value != len(self.reply):
                emit('short_write')
                return self.close()
            self.reply = None
            self.ticket = None
            self.pending = Pending(self.dll, self.handle, 'read')
        return False


class PipeServer:
    def __init__(self, name, bridge):
        pipe_path(name)
        if reap():
            raise RuntimeError('previous bridge I/O is still pending')
        self.name, self.bridge = name, bridge
        self.dll = kernel32()
        self.channels = []
        self.stopping = threading.Event()
        self.stopping.set()
        try:
            for index in range(MAX_CONNECTIONS):
                self.channels.append(PipeChannel(self.dll, name, bridge, first=index == 0))
        except Exception:
            for channel in self.channels:
                if not channel.close():
                    retain(channel)
            self.channels = []
            raise

    def start(self):
        # All pipe I/O runs on the QMT strategy callback thread. A background
        # Python thread is scheduled too rarely by QMT to carry the request
        # path; every GIL acquisition it needs costs about one callback
        # window (~100ms) of the round trip.
        self.stopping.clear()

    def poll(self):
        """Advance every channel one non-blocking step; strategy thread only."""
        if self.stopping.is_set():
            return
        for channel in list(self.channels):
            try:
                closed = channel.tick()
            except Exception:
                logger.exception('BigQMT pipe channel failed')
                closed = channel.close()
            if closed:
                self.channels.remove(channel)
        while len(self.channels) < MAX_CONNECTIONS:
            self.channels.append(PipeChannel(self.dll, self.name, self.bridge,
                                             first=not self.channels))

    def stop(self):
        self.stopping.set()
        self.bridge.stop()
        for channel in self.channels:
            if not channel.close():
                retain(channel)
        self.channels = []
        reap()


class PipeClient:
    """Persistent client connection; one request in flight, handle reused.

    Opening a connection per request forces the strategy to rebuild a pipe
    instance from its callback before it can read the next request.
    """
    def __init__(self, name):
        pipe_path(name)
        self.name = name
        self.dll = kernel32()
        self.owner = None

    def _connect(self, deadline):
        while self.owner is None and time.monotonic() < deadline:
            handle = self.dll.CreateFileW(pipe_path(self.name), 0xC0000000, 0, None,
                                          3, 0x40000000, None)
            if handle != ctypes.c_void_p(-1).value:
                owner = HandleOwner(self.dll, handle)
                mode = wintypes.DWORD(2)
                if not self.dll.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None):
                    error = ctypes.get_last_error()
                    if not owner.close():
                        retain(owner)
                    raise OSError(error, 'SetNamedPipeHandleState')
                self.owner = owner
                return
            error = ctypes.get_last_error()
            if error not in (2, 231):
                raise OSError(error, 'CreateFileW')
            # Only opening an unused connection may wait; a connect retry sends
            # no request. Only a proved-unsent write is ever resent.
            time.sleep(min(.01, max(0, deadline - time.monotonic())))
        if self.owner is None:
            raise TimeoutError('bridge connect deadline')

    def _step(self, kind, data, deadline):
        owner = self.owner
        pending = Pending(self.dll, owner.handle, kind, data, read_limit=RESPONSE_LIMIT)
        owner.pending = pending
        while not pending.poll():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('bridge I/O deadline')
            time.sleep(min(.005, remaining))
        if pending.error:
            # A failed WriteFile is provable: nothing left this process, so it
            # is the only failure the caller may resend. A failed read has an
            # unknown outcome and is never retried.
            if kind == 'write':
                raise PipeWriteError(pending.error, 'overlapped write')
            raise OSError(pending.error, 'overlapped read')
        return pending

    def _write(self, raw, deadline):
        pending = self._step('write', raw, deadline)
        if pending.count.value != len(raw):
            # A short write may have reached the peer; never resend it.
            raise OSError('incomplete pipe write')
        pending.close_event()
        self.owner.pending = None

    def exchange(self, raw, deadline):
        """One request, one response over the persistent handle; no replay."""
        if len(raw) > REQUEST_LIMIT:
            raise ValueError('request too large')
        if reap() >= MAX_CONNECTIONS:
            raise RuntimeError('too many unfinished native I/O operations')
        self._connect(deadline)
        try:
            try:
                self._write(raw, deadline)
            except PipeWriteError:
                # One reconnect-and-resend after a proved-unsent write. Read
                # failures take the plain except path below.
                self.close()
                self._connect(deadline)
                self._write(raw, deadline)
            pending = self._step('read', None, deadline)
            if pending.count.value == 0:
                raise OSError(109, 'bridge peer closed')
            result = pending.buffer.raw[:pending.count.value]
            pending.close_event()
            self.owner.pending = None
            return result
        except Exception:
            self.close()
            raise

    def close(self):
        if self.owner is not None:
            owner, self.owner = self.owner, None
            if not owner.close():
                retain(owner)


def exchange(name, raw, deadline):
    """One request over a fresh connection; the injected/test client seam."""
    client = PipeClient(name)
    try:
        return client.exchange(raw, deadline)
    finally:
        client.close()
