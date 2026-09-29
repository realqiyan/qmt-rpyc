import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest

from qmt_rpyc.adapters.bigqmt.bridge_queue import BRIDGE_VERSION, wire_dump
from qmt_rpyc.adapters.bigqmt.connection import ConnectionManager
from qmt_rpyc.adapters.bigqmt.transport import BridgeCapacityError, PipeTransport


def test_busy_business_slots_do_not_block_heartbeat_and_total_is_bounded():
    busy = Barrier(4)
    release = Event()
    def exchange(name, raw, deadline):
        request = json.loads(raw)
        ping = request['operation'] == 'ping'
        if not ping:
            busy.wait(timeout=2)
            assert release.wait(2)
        return wire_dump(dict(version=BRIDGE_VERSION, id=request['id'], instance='1' * 32,
                              result={'runtime': 'bigqmt', 'read_only': False} if ping else {}))
    transport = PipeTransport(exchange_fn=exchange)
    transport.request('ping', {})
    with ThreadPoolExecutor(3) as pool:
        futures = [pool.submit(transport.request, 'instrument', {'code': str(i)}) for i in range(3)]
        busy.wait(timeout=2)
        try:
            assert transport.diagnostics()['active_business'] == 3
            assert not transport.request('ping', {}, timeout=.5)['read_only']
        finally:
            release.set()
        assert [future.result() for future in futures] == [{}, {}, {}]
    assert transport.diagnostics()['active_business'] == 0


def test_local_capacity_timeout_does_not_mark_connected_bridge_disconnected():
    manager = ConnectionManager()
    manager.state = 'connected'
    def full():
        manager.stopping.set()
        raise BridgeCapacityError()
    manager.probe = full
    manager._run()
    assert manager.get_health_status()['connected']
    assert manager.failures == 0


def test_health_preserves_actionable_protocol_failure():
    from qmt_rpyc.adapters.errors import ProviderError
    manager = ConnectionManager()
    def fail():
        manager.stopping.set()
        raise ProviderError('NOT_CONNECTED', '', 'BigQMT bridge protocol mismatch: service expects 7, strategy reports 4')
    manager.probe = fail
    manager._run()
    health = manager.get_health_status()
    assert not health['connected']
    assert 'service expects 7, strategy reports 4' in health['last_connection_error']


def test_waiting_and_restart_have_friendly_logs_and_restore_requests(monkeypatch, caplog):
    instances = [None, None, '1' * 32, None, '2' * 32]
    step = 0

    def exchange(name, raw, deadline):
        instance = instances[step]
        if instance is None:
            raise TimeoutError('bridge connect deadline')
        request = json.loads(raw)
        result = ({'runtime': 'bigqmt', 'read_only': False}
                  if request['operation'] == 'ping' else {'code': 'TEST'})
        return wire_dump(dict(version=BRIDGE_VERSION, id=request['id'],
                              instance=instance, result=result))

    transport = PipeTransport(exchange_fn=exchange)
    manager = ConnectionManager(transport=transport)
    states = []

    def advance(interval):
        nonlocal step
        states.append(manager.get_health_status()['connection_state'])
        assert transport.instance == instances[step]
        if instances[step] is not None:
            assert transport.request('instrument', {'code': 'TEST'}) == {'code': 'TEST'}
        step += 1
        if step == len(instances):
            manager.stopping.set()

    monkeypatch.setattr(manager.stopping, 'wait', advance)
    with caplog.at_level(logging.INFO):
        manager._run()
    assert states == ['disconnected', 'disconnected', 'connected', 'disconnected', 'connected']
    assert manager.failures == 0
    assert all(record.exc_info is None for record in caplog.records)
    assert caplog.text.count('QMT bridge connected;') == 2
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert 'connection lost' in warnings[0].message
    assert 'retrying in 30s' in caplog.text


@pytest.mark.parametrize('failure', [ValueError('bad response'), PermissionError(5, 'access denied')])
def test_unexpected_bridge_failure_keeps_traceback(failure, caplog):
    def exchange(*args):
        raise failure

    manager = ConnectionManager(transport=PipeTransport(exchange_fn=exchange), reconnect_max_attempts=1)
    manager._run()
    assert any(record.exc_info for record in caplog.records)
    assert manager.state == 'exhausted'
    assert 'automatic retries stopped' in caplog.text


def test_expected_failure_exhaustion_is_actionable_without_traceback(caplog):
    def exchange(*args):
        raise TimeoutError('bridge connect deadline')

    manager = ConnectionManager(transport=PipeTransport(exchange_fn=exchange), reconnect_max_attempts=1)
    with caplog.at_level(logging.INFO):
        manager._run()
    assert manager.state == 'exhausted'
    assert all(record.exc_info is None for record in caplog.records)
    assert 'automatic retries stopped' in caplog.text
    assert 'Restart the service' in caplog.text
    assert 'retrying in' not in caplog.text
