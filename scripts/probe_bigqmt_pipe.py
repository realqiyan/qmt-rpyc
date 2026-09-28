# coding: utf-8
"""QMT strategy: bounded asynchronous pipe echo probe, no business APIs.

Paste into an independent strategy. Run the companion PowerShell client on
the same Windows host, then stop this strategy. Python 3.6, standard library.
This is an investigation tool, NOT the production bridge or its protocol.
"""
import ctypes
import json
import time
from ctypes import wintypes


PIPE_NAME = "qmt_rpyc_probe_v1"
MAX_FRAME = 256 * 1024
MAX_CONNECTIONS = 4
IO_TIMEOUT = 5.0
PROBE_LIFETIME = 180.0
ERROR_IO_PENDING = 997
ERROR_IO_INCOMPLETE = 996
ERROR_PIPE_CONNECTED = 535


class Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                ("Offset", ctypes.c_uint32), ("OffsetHigh", ctypes.c_uint32),
                ("hEvent", wintypes.HANDLE)]


def emit(event, **data):
    print("QMT_RPYC_PIPE " + json.dumps(dict(event=event, data=data), sort_keys=True))


def kernel32():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer = ctypes.POINTER(Overlapped)
    dword_pointer = ctypes.POINTER(wintypes.DWORD)
    definitions = {
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
    def __init__(self, dll, handle, kind, data=None):
        self.dll, self.handle, self.kind = dll, handle, kind
        self.overlapped = Overlapped()
        self.overlapped.hEvent = dll.CreateEventW(None, True, False, None)
        if not self.overlapped.hEvent:
            raise OSError(ctypes.get_last_error(), "CreateEventW")
        self.buffer = ctypes.create_string_buffer(data) if data is not None else ctypes.create_string_buffer(MAX_FRAME)
        self.count = wintypes.DWORD()
        self.started = time.monotonic()
        self.done, self.error, self.cancel_requested = False, 0, False
        pointer = ctypes.byref(self.overlapped)
        if kind == "connect":
            ok = dll.ConnectNamedPipe(handle, pointer)
        elif kind == "read":
            ok = dll.ReadFile(handle, self.buffer, MAX_FRAME, ctypes.byref(self.count), pointer)
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


def response_for(raw):
    """Only synthetic echo/delay requests; no dispatch to QMT functions."""
    try:
        def unique_pairs(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_pairs)
        if (type(request) is not dict or set(request) != {"id", "payload", "delay"}
                or type(request["id"]) is not str or not 0 < len(request["id"]) <= 128
                or type(request["payload"]) is not str or type(request["delay"]) is not bool):
            raise ValueError("invalid probe request")
        response = dict(id=request["id"], payload=request["payload"], status="ok")
        delay = 2.0 if request["delay"] else 0.0
    except (ValueError, UnicodeError, TypeError):
        response, delay = dict(status="invalid_request"), 0.0
    wire = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(wire) > MAX_FRAME:
        wire, delay = b'{"status":"response_too_large"}', 0.0
    return wire, delay


class Channel:
    def __init__(self, dll, first=False):
        self.dll = dll
        flags = 3 | 0x40000000 | (0x00080000 if first else 0)
        self.handle = dll.CreateNamedPipeW("\\\\.\\pipe\\" + PIPE_NAME, flags,
                                          4 | 2 | 8, MAX_CONNECTIONS, 4096, 4096, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_last_error(), "CreateNamedPipeW")
        self.pending = None
        self.closing = False
        self.reply = None
        self.send_at = 0.0
        try:
            self.pending = Pending(dll, self.handle, "connect")
        except Exception:
            self.close()
            raise

    def close(self):
        self.closing = True
        if self.pending is not None:
            self.pending.cancel()
            if not self.pending.poll():
                return False
            self.pending.close_event()
            self.pending = None
        if self.handle is not None:
            if not self.dll.CloseHandle(self.handle):
                emit("pipe_close_error", winerror=ctypes.get_last_error())
            self.handle = None
        return True

    def tick(self):
        if self.closing:
            return self.close()
        now = time.monotonic()
        if self.pending is None:
            if now >= self.send_at:
                self.pending = Pending(self.dll, self.handle, "write", self.reply)
            return False
        pending = self.pending
        if not pending.poll():
            if pending.kind != "connect" and now - pending.started >= IO_TIMEOUT:
                emit("io_timeout", kind=pending.kind)
                self.close()
            return False
        pending.close_event()
        self.pending = None
        if pending.error:
            # Broken clients, cancellation and oversized messages are closed.
            if pending.error not in (109, 232, 233, 995):
                emit("io_error", kind=pending.kind, winerror=pending.error)
            return self.close()
        if pending.kind == "read":
            if pending.count.value == 0:
                return self.close()
            self.reply, delay = response_for(pending.buffer.raw[:pending.count.value])
            self.send_at = now + delay
        else:
            if pending.kind == "write" and pending.count.value != len(self.reply):
                emit("short_write", actual=pending.count.value, expected=len(self.reply))
                return self.close()
            self.pending = Pending(self.dll, self.handle, "read")
        return False


_CHANNELS = []
# Pin exceptional unfinished I/O outside strategy globals: the strategy may
# be unloaded while its Python process and the kernel requests still exist.
_RETAINED = getattr(ctypes, "_qmt_rpyc_pipe_probe_retained", None)
if _RETAINED is None:
    _RETAINED = []
    ctypes._qmt_rpyc_pipe_probe_retained = _RETAINED
_DLL = None
_STARTED = None
_STOPPING = False
_TICKS = 0


def pipe_probe_pump(ContextInfo):
    global _STOPPING, _TICKS, _STARTED
    if _STARTED is None:
        return
    _TICKS += 1
    if _TICKS == 1:
        emit("pump_observed")
    if time.monotonic() - _STARTED >= PROBE_LIFETIME:
        _STOPPING = True
    for channel in list(_CHANNELS):
        try:
            closed = channel.close() if _STOPPING else channel.tick()
            if closed:
                _CHANNELS.remove(channel)
        except Exception as exc:
            emit("channel_error", error_type=type(exc).__name__)
            channel.close()
    if not _STOPPING:
        while len(_CHANNELS) < MAX_CONNECTIONS:
            try:
                _CHANNELS.append(Channel(_DLL))
            except Exception as exc:
                emit("accept_error", error_type=type(exc).__name__)
                _STOPPING = True
                break
    if _STOPPING and not _CHANNELS:
        emit("probe_closed")
        # The QMT timer may still fire; no further native operations occur.
        _STARTED = None


def init(ContextInfo):
    global _DLL, _STARTED, _STOPPING, _TICKS
    from datetime import datetime, timedelta
    if _CHANNELS or _RETAINED:
        raise RuntimeError("Use a fresh strategy instance for this probe")
    _DLL = kernel32()
    _STOPPING, _TICKS = False, 0
    _STARTED = time.monotonic()
    try:
        for index in range(MAX_CONNECTIONS):
            _CHANNELS.append(Channel(_DLL, first=index == 0))
        start = (datetime.now() + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        ContextInfo.run_time("pipe_probe_pump", "100nMilliSecond", start)
        emit("probe_ready", pipe=PIPE_NAME, connections=MAX_CONNECTIONS,
             frame_limit=MAX_FRAME, lifetime_seconds=PROBE_LIFETIME)
    except Exception:
        stop(ContextInfo)
        raise


def handlebar(ContextInfo):
    # Timer scheduling must actually work; bars do not mask a failed timer.
    return None


def stop(ContextInfo):
    global _STOPPING, _STARTED
    _STOPPING = True
    deadline = time.monotonic() + 1.0
    while _CHANNELS:
        for channel in list(_CHANNELS):
            if channel.close():
                _CHANNELS.remove(channel)
        if time.monotonic() >= deadline:
            break
        time.sleep(0.005)
    # Never free buffers referenced by native I/O that has not completed.
    # Report failed cleanup; retain objects for the remaining process lifetime.
    if _CHANNELS:
        _RETAINED.extend(_CHANNELS)
        _CHANNELS[:] = []
    emit("stopped", pending_handles_retained=len(_RETAINED))
    _STARTED = None
