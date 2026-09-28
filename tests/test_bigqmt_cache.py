from concurrent.futures import ThreadPoolExecutor
from datetime import date
from threading import Event

import pytest

from qmt_rpyc.adapters.bigqmt.cache import DiscoveryCache
from qmt_rpyc.adapters.bigqmt.options import OptionsAdapter
from qmt_rpyc.adapters.bigqmt.instruments import InstrumentsAdapter
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.options import ExpiryDatesRequest, OptionChainRequest
from tests.test_bigqmt_options import Reader


def test_expiry_and_chain_share_discovery_until_ttl_date_or_epoch_changes():
    now = [0]
    day = [date(2026, 9, 28)]
    epoch = ['first']
    reader = Reader()
    reader.cache_token = lambda: epoch[0]
    calls = []
    original = reader.get_option_details
    reader.get_option_details = lambda codes: calls.append(tuple(codes)) or original(codes)
    cache = DiscoveryCache(ttl=300, clock=lambda: now[0])
    provider = OptionsAdapter(reader, market_date=lambda: day[0], cache=cache)
    query = ExpiryDatesRequest('510050.SH')
    provider.get_expiry_dates(query)
    provider.get_option_chain(OptionChainRequest('510050.SH', date(2026, 12, 23)))
    assert len(calls) == 1
    now[0] = 300
    provider.get_expiry_dates(query)
    assert len(calls) == 2
    day[0] = date(2026, 9, 29)
    provider.get_expiry_dates(query)
    epoch[0] = 'new_bridge'
    provider.get_expiry_dates(query)
    assert len(calls) == 4


def test_concurrent_refresh_is_single_flight_and_clear_does_not_repopulate_old_generation():
    cache = DiscoveryCache()
    entered, release = Event(), Event()
    calls = []
    def load():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return ('value',)
    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(cache.get, 'key', load)
        assert entered.wait(1)
        second = pool.submit(cache.get, 'key', load)
        release.set()
        assert first.result() == second.result() == ('value',)
    assert len(calls) == 1
    cache.clear()
    entered.clear()
    release.clear()
    with ThreadPoolExecutor(1) as pool:
        active = pool.submit(cache.get, 'key', load)
        assert entered.wait(1)
        cache.clear()
        release.set()
        active.result()
    assert cache.info()['entries'] == 0


def test_failed_refresh_is_not_cached_or_replaced_by_stale_success():
    now = [0]
    cache = DiscoveryCache(ttl=1, capacity=2, clock=lambda: now[0])
    assert cache.get('key', lambda: 'old') == 'old'
    now[0] = 1
    def failure(): raise ValueError('native failure')
    with pytest.raises(ValueError): cache.get('key', failure)
    assert cache.info()['entries'] == 0
    assert cache.get('key', lambda: 'new') == 'new'
    cache.get('second', lambda: 2)
    cache.get('third', lambda: 3)
    assert cache.info()['entries'] == 2


def test_underlying_list_retains_success_across_disconnection_but_contract_cache_does_not():
    reader = Reader()
    reads = []
    reader.get_option_underlying_map = lambda: reads.append('map') or {'510050.SH': reader.codes}
    original = reader.get_option_details
    reader.get_option_details = lambda codes: reads.append('details') or original(codes)
    reader.cache_token = lambda: 'bridge'
    options = OptionsAdapter(reader, market_date=lambda: date(2026, 9, 28))
    instruments = InstrumentsAdapter(reader, market_date=options.market_date, options=options)
    for _ in range(2):
        assert instruments.list_option_underlyings(EmptyRequest()) == ('510050.SH',)
        options.get_expiry_dates(ExpiryDatesRequest('510050.SH'))
    assert reads == ['map', 'details']
    def disconnected():
        raise ProviderError('NOT_CONNECTED', '', 'unavailable')
    reader.cache_token = disconnected
    assert instruments.list_option_underlyings(EmptyRequest()) == ('510050.SH',)
    with pytest.raises(ProviderError):
        options.get_expiry_dates(ExpiryDatesRequest('510050.SH'))
