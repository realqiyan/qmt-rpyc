import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt.bridge_queue import BRIDGE_VERSION, BridgeQueue, wire_dump, wire_load
from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime


def setup_bridge(capacity=32, context=None):
    now = [100.0]
    runtime = StrategyRuntime(context or SimpleNamespace(), {})
    bridge = BridgeQueue(runtime, capacity, clock=lambda: now[0], wall_clock=lambda: now[0])
    return bridge, now


def request(bridge, operation='ping', arguments=None, **overrides):
    value = dict(version=BRIDGE_VERSION, instance=bridge.instance, id='1' * 32, expires_at=110,
                 operation=operation, arguments=arguments or {})
    value.update(overrides)
    return wire_dump(value)


def result(ticket):
    raw = ticket.poll()
    return None if raw is None else json.loads(raw)


def test_io_worker_handoff_runs_only_when_strategy_thread_pumps():
    threads = []
    bridge, _ = setup_bridge(context=SimpleNamespace(get_full_tick=lambda codes: threads.append(threading.get_ident()) or {}))
    with ThreadPoolExecutor(1) as pool:
        ticket = pool.submit(bridge.submit, request(bridge, 'ticks', {'selectors': ['000001.SZ']})).result()
    assert threads == [] and result(ticket) is None
    bridge.pump()
    assert threads == [threading.get_ident()]
    assert result(ticket)['result'] == {}


@pytest.mark.parametrize('action', ['cancel', 'expire', 'stop'])
def test_abandoned_queue_entries_never_call_source(action):
    calls = []
    bridge, now = setup_bridge(context=SimpleNamespace(get_full_tick=lambda codes: calls.append(codes)))
    ticket = bridge.submit(request(bridge, 'ticks', {'selectors': ['000001.SZ']}))
    if action == 'cancel':
        ticket.cancel()
    elif action == 'expire':
        now[0] = 111
    else:
        bridge.stop()
    bridge.pump()
    assert calls == []
    if action == 'expire':
        assert result(ticket)['error'] == 'EXPIRED'


def test_stop_between_dequeue_and_claim_prevents_execution():
    bridge, _ = setup_bridge()
    ticket = bridge.submit(request(bridge))
    assert bridge.pending.get_nowait() is ticket
    bridge.stop()
    assert not ticket.claim()


def test_cancel_running_call_discards_late_result_without_replay():
    calls = []
    bridge, _ = setup_bridge()
    ticket = bridge.submit(request(bridge))
    def dispatch(operation, arguments):
        calls.append(operation)
        with ThreadPoolExecutor(1) as pool:
            pool.submit(ticket.cancel).result()
        return {'late': True}
    bridge.runtime.dispatch = dispatch
    bridge.pump()
    bridge.pump()
    assert calls == ['ping'] and ticket.poll() is None and ticket.state == 'cancelled'


def test_capacity_and_epoch_rejection_are_explicit_and_have_no_replay():
    bridge, _ = setup_bridge(capacity=1)
    first = bridge.submit(request(bridge))
    assert result(bridge.submit(request(bridge)))['error'] == 'OVERLOADED'
    assert result(bridge.submit(request(bridge, instance='old')))['error'] == 'STALE_INSTANCE'
    bridge.pump()
    assert not result(first)['result']['read_only']
    reconnect = bridge.submit(request(bridge, instance=None))
    bridge.pump()
    assert result(reconnect)['instance'] == bridge.instance


def test_deadline_during_execution_reports_expired_not_late_success():
    bridge, now = setup_bridge()
    ticket = bridge.submit(request(bridge))
    bridge.runtime.dispatch = lambda *args: now.__setitem__(0, 111) or 'late'
    bridge.pump()
    assert result(ticket)['error'] == 'EXPIRED'


@pytest.mark.parametrize('raw', [b'{"x":1,"x":2}', b'{"x":NaN}', b'null', b'[]', b'{'])
def test_malformed_frames_cannot_be_enqueued(raw):
    bridge, _ = setup_bridge()
    with pytest.raises((ValueError, TypeError)):
        bridge.submit(raw)
    assert bridge.pending.empty()


def test_response_size_failure_does_not_truncate_success(monkeypatch):
    import qmt_rpyc.adapters.bigqmt.bridge_queue as module
    monkeypatch.setattr(module, 'RESPONSE_LIMIT', 200)
    bridge, _ = setup_bridge()
    response = wire_load(bridge.reply('1' * 32, result='x' * 1000), 200)
    assert response['error'] == 'RESULT_TOO_LARGE' and 'result' not in response


def test_protocol_mismatch_replies_without_pump_or_native_execution():
    bridge, _ = setup_bridge(context=SimpleNamespace(
        get_full_tick=lambda *args: pytest.fail('mismatched request executed')))
    ticket = bridge.submit(request(bridge, 'ticks', {'selectors': ['000001.SZ']}, version=4))
    assert result(ticket)['error'] == 'PROTOCOL_MISMATCH'
    assert result(ticket)['version'] == BRIDGE_VERSION
    assert bridge.pending.empty()
