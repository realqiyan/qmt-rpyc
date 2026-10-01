"""Portable fault injection for production pipe lifetimes and one-shot calls."""
from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt import winpipe as pipe


def test_pending_cancel_keeps_native_buffer_event_and_pipe_until_completion():
    actions = []
    class Pending:
        done = False
        def cancel(self): actions.append('cancel')
        def poll(self): return self.done
        def close_event(self):
            assert self.done
            actions.append('event')
    owner = pipe.HandleOwner(SimpleNamespace(CloseHandle=lambda handle: actions.append(handle) or True), 2**40 + 7)
    pending = owner.pending = Pending()
    assert not owner.close() and owner.pending is pending
    assert actions == ['cancel']
    pending.done = True
    assert owner.close()
    assert actions[-2:] == ['event', 2**40 + 7]
    assert owner.handle is None


def test_channel_reuses_connection_for_next_request_after_response(monkeypatch):
    started = []
    monkeypatch.setattr(pipe, 'Pending', lambda *args: started.append(args) or 'awaiting_request')
    channel = pipe.PipeChannel.__new__(pipe.PipeChannel)
    channel.closing, channel.ticket, channel.reply = False, None, b'reply'
    channel.dll, channel.handle = object(), 123
    channel.pending = SimpleNamespace(kind='write', poll=lambda: True, close_event=lambda: None,
                                     error=0, count=SimpleNamespace(value=5))
    assert not channel.tick()
    assert channel.pending == 'awaiting_request' and channel.ticket is None and channel.reply is None
    assert started[0][2] == 'read'


def test_channel_closes_when_client_disconnects_instead_of_replying():
    closed = []
    channel = pipe.PipeChannel.__new__(pipe.PipeChannel)
    channel.closing, channel.ticket, channel.reply = False, None, None
    channel.dll, channel.handle = object(), 123
    channel.bridge = object()
    channel.close = lambda: closed.append(True) or True
    channel.pending = SimpleNamespace(kind='read', poll=lambda: True, close_event=lambda: None,
                                      error=0, count=SimpleNamespace(value=0))
    assert channel.tick()
    assert closed == [True]


def test_handle_owner_releases_handle_when_collected():
    closed = []
    owner = pipe.HandleOwner(SimpleNamespace(CloseHandle=lambda handle: closed.append(handle) or True), 7)
    owner.__del__()
    assert closed == [7] and owner.handle is None


def test_handle_owner_del_retains_when_close_cannot_finish(monkeypatch):
    retained = []
    monkeypatch.setattr(pipe, 'retain', lambda owner: retained.append(owner))
    class Pending:
        def cancel(self): pass
        def poll(self): return False
        def close_event(self): raise AssertionError('event must not be released before completion')
    owner = pipe.HandleOwner(SimpleNamespace(CloseHandle=lambda handle: True), 9)
    owner.pending = Pending()
    owner.__del__()
    assert retained == [owner] and owner.handle == 9


def test_poll_advances_each_channel_repeatedly(monkeypatch):
    ticks = []
    class Channel:
        def tick(self):
            ticks.append(1)
            return False
    server = pipe.PipeServer.__new__(pipe.PipeServer)
    server.stopping = pipe.threading.Event()
    server.lock = pipe.threading.RLock()
    server.channels = [Channel()]
    monkeypatch.setattr(pipe, 'MAX_CONNECTIONS', 1)
    server.poll()
    assert len(ticks) == pipe.POLL_STEPS


def test_poll_swallows_channel_creation_failure(monkeypatch):
    server = pipe.PipeServer.__new__(pipe.PipeServer)
    server.stopping = pipe.threading.Event()
    server.lock = pipe.threading.RLock()
    server.channels = []
    server.dll, server.name, server.bridge = object(), 'test', object()
    def fail(*args, **kwargs):
        raise OSError(5, 'access denied')
    monkeypatch.setattr(pipe, 'PipeChannel', fail)
    monkeypatch.setattr(pipe, 'MAX_CONNECTIONS', 1)
    server.poll()
    assert server.channels == []


def test_client_resends_once_only_after_a_proved_unsent_write(monkeypatch):
    events = []
    dll = SimpleNamespace(CreateFileW=lambda *args: events.append('open') or 123,
        SetNamedPipeHandleState=lambda *args: True,
        CloseHandle=lambda handle: events.append('close') or True)
    monkeypatch.setattr(pipe, 'kernel32', lambda: dll)
    writes = [0]
    class Pending:
        def __init__(self, dll, handle, kind, data=None, read_limit=None):
            events.append(kind)
            self.kind = kind
            if kind == 'write':
                writes[0] += 1
                self.error = 232 if writes[0] == 1 else 0
                self.count = SimpleNamespace(value=len(data))
            else:
                self.error = 0
                self.buffer = SimpleNamespace(raw=b'{"ok":true}')
                self.count = SimpleNamespace(value=len(self.buffer.raw))
        def poll(self): return True
        def cancel(self): pass
        def close_event(self): pass
    monkeypatch.setattr(pipe, 'Pending', Pending)
    client = pipe.PipeClient('test')
    deadline = pipe.time.monotonic() + 5
    try:
        assert client.exchange(b'abc', deadline) == b'{"ok":true}'
    finally:
        client.close()
    assert events.count('open') == 2 and events.count('write') == 2


@pytest.mark.parametrize('fault', ['short_write', 'read_error', 'read_timeout'])
def test_native_exchange_does_not_reopen_or_rewrite_after_failure(monkeypatch, fault):
    calls = []
    now = [1.0]
    monkeypatch.setattr(pipe.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(pipe.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    dll = SimpleNamespace(CreateFileW=lambda *args: calls.append('open') or 123,
        SetNamedPipeHandleState=lambda *args: True, CloseHandle=lambda h: calls.append('close') or True)
    monkeypatch.setattr(pipe, 'kernel32', lambda: dll)
    class Pending:
        def __init__(self, dll, handle, kind, data=None, read_limit=None):
            calls.append(kind)
            self.kind, self.cancelled = kind, False
            self.error = 109 if kind == 'read' and fault == 'read_error' else 0
            self.count = SimpleNamespace(value=1 if fault == 'short_write' else 3)
        def poll(self): return self.cancelled or self.kind != 'read' or fault != 'read_timeout'
        def cancel(self): self.cancelled = True
        def close_event(self): pass
    monkeypatch.setattr(pipe, 'Pending', Pending)
    with pytest.raises((OSError, TimeoutError)):
        pipe.exchange('test', b'abc', 1.02)
    assert calls.count('open') == 1 and calls.count('write') == 1
    assert calls[-1] == 'close'
