import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

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
