"""Replay deployed empty full-tick + real option DataFrames across both seams."""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.market import MarketAdapter
from qmt_rpyc.contracts.common import CodesRequest, Failure
from qmt_rpyc.transport.codec import dumps, loads
from tests.test_bigqmt_reads import tick

SAMPLE = json.loads((Path(__file__).parent / 'fixtures/bigqmt/option_ticks.json').read_text())


def table(code):
    value = SAMPLE['frames'][code]
    return pd.DataFrame(value['data'], columns=value['columns'], index=value['index'])


def test_deployed_missing_option_quotes_recover_without_losing_book_or_timestamp():
    calls = []
    stock = tick()
    def full(codes):
        return dict(SAMPLE['get_full_tick'], **{'510300.SH': stock})
    def fallback(**kwargs):
        calls.append(kwargs)
        return {code: table(code) for code in kwargs['stock_code']}
    runtime = StrategyRuntime(SimpleNamespace(get_full_tick=full, get_market_data_ex=fallback), {})
    provider = MarketAdapter(SimpleNamespace(get_full_ticks=lambda codes:
        loads(dumps(runtime.dispatch('ticks', {'selectors':list(codes)})))))
    codes = ('510300.SH', '10012327.SHO', '90007971.SZO')
    result = provider.get_ticks(CodesRequest(codes)).require_all()
    assert calls == [dict(fields=[], stock_code=list(codes[1:]), period='tick', count=1,
                          dividend_type='none', fill_data=False, subscribe=True)]
    assert result['510300.SH'].last_price == stock['lastPrice']
    for code in codes[1:]:
        expected = table(code).iloc[0]
        value = result[code]
        assert value.last_price == expected['lastPrice']
        assert value.previous_settlement_price == expected['lastSettlementPrice']
        assert value.bid_prices == tuple(expected['bidPrice'])
        assert value.ask_volumes == tuple(expected['askVol'])
        assert int(value.observed_at.timestamp()*1000) == expected['time']


def test_answered_options_and_missing_stocks_do_not_trigger_fallback():
    runtime = StrategyRuntime(SimpleNamespace(get_full_tick=lambda codes: {'10012327.SHO':tick()},
        get_market_data_ex=lambda **kwargs: pytest.fail('unnecessary fallback')), {})
    value = runtime.dispatch('ticks', {'selectors':['10012327.SHO','MISSING.SH']})
    assert set(value) == {'10012327.SHO'}


@pytest.mark.parametrize('failure', ['empty', 'exception', 'invalid'])
def test_unrecoverable_option_preserves_good_stock_and_explicit_failure(failure):
    def fallback(**kwargs):
        if failure == 'exception': raise RuntimeError('native failure')
        return {'10012327.SHO': pd.DataFrame() if failure == 'empty' else {'bad':'shape'}}
    runtime = StrategyRuntime(SimpleNamespace(get_full_tick=lambda codes: {'510300.SH':tick()},
                                               get_market_data_ex=fallback), {})
    provider = MarketAdapter(SimpleNamespace(get_full_ticks=lambda codes: runtime.dispatch('ticks', {'selectors':list(codes)})))
    result = provider.get_ticks(CodesRequest(('510300.SH','10012327.SHO')))
    assert result.items[0].value.last_price == tick()['lastPrice']
    assert isinstance(result.items[1], Failure)
    assert result.items[1].error.error_type == 'MISSING_RESULT'


def test_option_recovery_calls_are_bounded_and_do_not_retry():
    groups = []
    def fallback(**kwargs):
        groups.append(kwargs['stock_code']); return {}
    runtime = StrategyRuntime(SimpleNamespace(get_full_tick=lambda codes: {}, get_market_data_ex=fallback), {})
    codes = ['%08d.SHO' % n for n in range(33)]
    assert runtime.dispatch('ticks', {'selectors':codes}) == {}
    assert list(map(len,groups)) == [16,16,1]
    assert sum(groups,[]) == codes
