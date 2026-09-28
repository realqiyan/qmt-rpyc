from threading import Event
import pytest
from qmt_rpyc.adapters.bigqmt.underlying_cache import UnderlyingCache
from qmt_rpyc.adapters.errors import ProviderError


def test_success_survives_restart_and_refresh_failure(tmp_path):
    path = tmp_path / 'cache.json'
    now = [100]
    cache = UnderlyingCache(path, ttl=10, clock=lambda: now[0])
    assert cache.get(lambda: ('510300.SH',)) == ('510300.SH',)
    again = UnderlyingCache(path, ttl=10, clock=lambda: now[0])
    assert again.get(lambda: pytest.fail('must use disk cache')) == ('510300.SH',)
    now[0] = 111
    def failure(): raise TimeoutError('test')
    assert again.get(failure) == ('510300.SH',)
    assert again.done.wait(2)
    assert again.info()['stale'] and again.info()['last_error'] == 'TimeoutError'
    assert UnderlyingCache(path).value == ('510300.SH',)


def test_cold_wait_is_bounded_and_late_success_is_usable():
    release, entered = Event(), Event()
    cache = UnderlyingCache(wait=.01)
    def load():
        entered.set()
        assert release.wait(2)
        return ('510300.SH',)
    with pytest.raises(ProviderError): cache.get(load)
    assert entered.is_set()
    release.set()
    assert cache.done.wait(2)
    assert cache.get(load) == ('510300.SH',)


def test_refresh_single_flight_and_stale_return_is_immediate():
    clock = [100]
    cache = UnderlyingCache(ttl=1, retry=1, clock=lambda: clock[0])
    assert cache.get(lambda: ('510300.SH',))
    clock[0] += 2
    release = Event()
    calls = []
    def refresh():
        calls.append(1)
        assert release.wait(2)
        return ('510050.SH', '510300.SH')
    for _ in range(10): assert cache.get(refresh) == ('510300.SH',)
    release.set(); assert cache.done.wait(2)
    assert calls == [1]
    assert cache.get(refresh) == ('510050.SH', '510300.SH')


def test_corrupt_cache_does_not_fabricate_success(tmp_path):
    p = tmp_path / 'cache.json';p.write_text('{}')
    cache = UnderlyingCache(p, wait=.05)
    with pytest.raises(ProviderError): cache.get(lambda: ('bad identity ',))
    assert cache.value is None
