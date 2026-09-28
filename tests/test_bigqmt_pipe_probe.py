"""Portable checks for probe framing and native-operation lifetime rules."""
import ctypes
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_bigqmt_pipe.py"
    spec = importlib.util.spec_from_file_location("bigqmt_pipe_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("raw", [b"{", b"[]", b'{"id":"a","id":"b","payload":"x","delay":false}',
                                 b'{"id":"a","payload":"x","delay":0}',
                                 b'{"id":"a","payload":"x","delay":false,"operation":"passorder"}'])
def test_probe_rejects_non_protocol_messages(probe, raw):
    wire, delay = probe.response_for(raw)
    assert json.loads(wire) == {"status": "invalid_request"}
    assert delay == 0


def test_echo_preserves_large_utf8_payload_and_correlation(probe):
    payload = "中" * 30000
    raw = json.dumps(dict(id="correlation", payload=payload, delay=False)).encode()
    wire, delay = probe.response_for(raw)
    assert json.loads(wire) == dict(id="correlation", payload=payload, status="ok")
    assert len(wire) > 8192
    assert delay == 0


def test_oversized_response_is_bounded(probe):
    raw = json.dumps(dict(id="a", payload="x" * probe.MAX_FRAME, delay=True)).encode()
    wire, delay = probe.response_for(raw)
    assert json.loads(wire)["status"] == "response_too_large"
    assert delay == 0


def test_overlapped_layout_matches_64_bit_windows(probe):
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit ABI check")
    assert ctypes.sizeof(probe.Overlapped) == 32
    assert probe.Overlapped.hEvent.offset == 24


def test_cancel_does_not_release_pending_native_buffers(probe):
    actions = []

    class Operation:
        done = False

        def cancel(self):
            actions.append("cancel")

        def poll(self):
            return self.done

        def close_event(self):
            assert self.done
            actions.append("close_event")

    channel = probe.Channel.__new__(probe.Channel)
    channel.dll = SimpleNamespace(CloseHandle=lambda handle: actions.append(("close_handle", handle)) or True)
    channel.handle = 2**40 + 3
    operation = channel.pending = Operation()
    assert channel.close() is False
    assert channel.pending is operation
    assert channel.handle == 2**40 + 3
    assert actions == ["cancel"]
    operation.done = True
    assert channel.close() is True
    assert actions[-2:] == ["close_event", ("close_handle", 2**40 + 3)]
    channel.close()
    assert actions.count(("close_handle", 2**40 + 3)) == 1


def test_idle_read_timeout_closes_instead_of_replaying(probe, monkeypatch):
    actions = []
    pending = SimpleNamespace(kind="read", started=0.0, poll=lambda: False)
    channel = probe.Channel.__new__(probe.Channel)
    channel.closing, channel.pending = False, pending
    channel.close = lambda: actions.append("close") or False
    monkeypatch.setattr(probe.time, "monotonic", lambda: probe.IO_TIMEOUT + 1)
    assert channel.tick() is False
    assert actions == ["close"]


def test_partial_native_write_is_not_retried(probe):
    actions = []
    channel = probe.Channel.__new__(probe.Channel)
    channel.closing = False
    channel.reply = b"12345"
    channel.pending = SimpleNamespace(kind="write", error=0, count=SimpleNamespace(value=3),
                                      poll=lambda: True, close_event=lambda: actions.append("close_event"))
    channel.close = lambda: actions.append("close") or True
    assert channel.tick() is True
    assert channel.pending is None
    assert actions == ["close_event", "close"]
