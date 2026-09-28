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


def test_channel_does_not_close_before_client_can_read_response(monkeypatch):
    started = []
    monkeypatch.setattr(pipe, 'Pending', lambda *args: started.append(args) or 'awaiting_eof')
    channel = pipe.PipeChannel.__new__(pipe.PipeChannel)
    channel.closing, channel.ticket, channel.replied = False, None, False
    channel.dll, channel.handle, channel.reply = object(), 123, b'reply'
    channel.pending = SimpleNamespace(kind='write', poll=lambda: True, close_event=lambda: None,
                                     error=0, count=SimpleNamespace(value=5))
    assert not channel.tick()
    assert channel.replied and channel.pending == 'awaiting_eof'
    assert started[0][2] == 'read'


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
