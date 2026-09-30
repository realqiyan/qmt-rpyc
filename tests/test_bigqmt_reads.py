"""Read conversions use deployed samples; unobserved cases are synthetic."""
from datetime import date, timezone
from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt.market import MarketAdapter, TICK_FIELDS
from qmt_rpyc.adapters.bigqmt.instruments import InstrumentsAdapter
from qmt_rpyc.adapters.bigqmt.reference import ReferenceAdapter
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import CodesRequest
from qmt_rpyc.contracts.market import DailyBarsQuery, MarketTicksRequest, TradingDatesRequest
from qmt_rpyc.contracts.reference import DividendQuery, IndexWeightsRequest


def tick():
    value = {native: 1.0 for native in TICK_FIELDS.values()}
    value.update(time=1790524800000, volume=715341, pvolume=71534100,
                 stockStatus=0, openInt=15, askPrice=[11.31] * 5,
                 bidPrice=[11.30] * 5, askVol=[100] * 5, bidVol=[200] * 5)
    return value


def bars(indices=('20260928',)):
    values = dict(amount=810575796.0, close=11.3, high=11.41, low=11.27,
                  open=11.28, openInterest=15, preClose=11.3,
                  settelementPrice=0.0, suspendFlag=0, time=1790524800000,
                  volume=715341)
    return dict(columns=list(values), index=list(indices),
                data=[list(values.values()) for _ in indices])


def test_tick_units_order_and_missing_result_are_preserved():
    source = {'000001.SZ': tick(), 'BAD.SZ': tick()}
    source['BAD.SZ']['volume'] = 1.5
    provider = MarketAdapter(SimpleNamespace(get_full_ticks=lambda codes: source))
    result = provider.get_ticks(CodesRequest(('BAD.SZ', '000001.SZ', 'MISSING.SZ')))
    assert [item.code for item in result.items] == ['BAD.SZ', '000001.SZ', 'MISSING.SZ']
    assert result.items[0].error.error_type == 'INVALID_RESULT'
    assert result.items[2].error.error_type == 'MISSING_RESULT'
    value = result.items[1].value
    assert value.volume == 715341 and value.source_volume_detail == 71534100
    assert value.observed_at.tzinfo == timezone.utc


def test_whole_market_keeps_non_stock_codes_without_stock_filter():
    calls = []
    def read(selectors):
        calls.append(selectors)
        codes = ['510050.SH', '10000001.SHO'] if selectors == ('SH',) else ['000001.SZ']
        return {code: tick() for code in codes}
    result = MarketAdapter(SimpleNamespace(get_full_ticks=read)).get_market_ticks(MarketTicksRequest(('SH', 'SZ')))
    assert [item.code for item in result.items] == ['000001.SZ', '10000001.SHO', '510050.SH']
    assert calls == [('SH',), ('SZ',)]


def test_whole_market_does_not_overwrite_conflicting_source_identities():
    provider = MarketAdapter(SimpleNamespace(get_full_ticks=lambda selectors: {'same': tick()}))
    with pytest.raises(ValueError, match='duplicate'):
        provider.get_market_ticks(MarketTicksRequest(('SH', 'SZ')))


@pytest.mark.parametrize('adjustment', ['none', 'front', 'back', 'front_ratio', 'back_ratio'])
def test_daily_query_preserves_adjustment_fill_and_source_units(adjustment):
    def read(*args):
        assert args == (('000001.SZ',), '', '20260928', 1, adjustment, False)
        return {'000001.SZ': bars()}
    value = MarketAdapter(SimpleNamespace(get_daily_bars=read)).get_daily_bars(
        DailyBarsQuery(('000001.SZ',), None, date(2026, 9, 28), 1, adjustment, False)
    ).require_all()['000001.SZ']
    assert value.adjustment == adjustment and value.rows[0].volume == 715341
    assert value.rows[0].settlement_price == 0.0


@pytest.mark.parametrize('indices', [('20260928', '20260928'), ('20260928', '20260924'), ('20250928',)])
def test_duplicate_unsorted_or_out_of_window_bars_fail_per_code(indices):
    reader = SimpleNamespace(get_daily_bars=lambda *args: {'000001.SZ': bars(indices)})
    result = MarketAdapter(reader).get_daily_bars(DailyBarsQuery(('000001.SZ',), start=date(2026, 1, 1)))
    assert result.items[0].error.error_type == 'INVALID_RESULT'


def test_calendar_does_not_invent_future_dates():
    calls = []
    def read(*args):
        calls.append(args)
        return ['20260928', '20260924']
    provider = MarketAdapter(SimpleNamespace(get_trading_dates=read), market_date=lambda: date(2026, 9, 28))
    assert provider.get_trading_dates(TradingDatesRequest('SH')) == (date(2026, 9, 24), date(2026, 9, 28))
    with pytest.raises(ProviderError):
        provider.get_trading_dates(TradingDatesRequest('SH', end=date(2026, 9, 29)))
    assert len(calls) == 1


def instrument(code):
    return dict(InstrumentID=code.split('.')[0], ExchangeID=code.split('.')[1],
                InstrumentName='source name', CreateDate=20200101, OpenDate=20200102,
                ExpireDate=0, FloatVolume=100, TotalVolume=200, VolumeMultiple=1,
                IsTrading=None, SettlementPrice=None, PreClose=11.3,
                UpStopPrice=12.43, DownStopPrice=10.17, PriceTick=.01)


def test_instruments_keep_placeholder_and_supplement_option_delivery():
    reader = SimpleNamespace(get_instrument_detail=instrument,
        get_option_detail=lambda code: dict(InstrumentID='10000001', OptUndlCode='510050',
            OptUndlMarket='SH', EndDelivDate=20261223) if code.endswith('.SHO') else {})
    values = InstrumentsAdapter(reader).get_details(CodesRequest(('000001.SZ', '10000001.SHO'))).require_all()
    assert values['000001.SZ'].expiry_date.raw == '0'
    assert values['000001.SZ'].delivery_end_date is None
    assert values['10000001.SHO'].delivery_end_date == date(2026, 12, 23)
    reference = InstrumentsAdapter(reader).get_trading_reference(CodesRequest(('000001.SZ',)))
    assert reference.require_all()['000001.SZ'].source_is_trading is None
    assert reference.require_all()['000001.SZ'].settlement_price is None


def test_weights_preserve_percentage_and_zero():
    reader = SimpleNamespace(get_index_members=lambda index: ['000002.SZ', '000001.SZ'],
        get_index_weights=lambda index, codes: {code: .433 if code == '000001.SZ' else 0 for code in codes})
    assert ReferenceAdapter(reader).get_index_weights(IndexWeightsRequest('000300.SH')).weights == {
        '000001.SZ': .433, '000002.SZ': 0.0}


def test_dividends_use_explicit_pairs_and_shanghai_event_date():
    # Synthetic vector; field ordering is from local reference fixtures, still
    # needs deployment validation. The milliseconds fall on the previous UTC day.
    reader = SimpleNamespace(get_dividend_factors=lambda code: [[1782748800000, [.5, .1, .2, .3, 4, 5, .99]]])
    provider = ReferenceAdapter(reader)
    result = provider.get_dividend_events(DividendQuery('000001.SZ', date(2026, 6, 30), date(2026, 6, 30)))
    assert len(result) == 1 and result[0].interest == .5 and result[0].dr == .99
    assert result[0].event_date == date(2026, 6, 30)
    assert result[0].source_event_at.date() == date(2026, 6, 29)


def test_trading_reference_preserves_false_zero_and_other_missing_fields_fail():
    def native(code):
        return dict(instrument(code), IsTrading=False, SettlementPrice=0.0)
    provider = InstrumentsAdapter(SimpleNamespace(get_instrument_detail=native))
    result = provider.get_trading_reference(CodesRequest(('000001.SZ',))).require_all()['000001.SZ']
    assert result.source_is_trading is False and result.settlement_price == 0.0
    provider.reader.get_instrument_detail = lambda code: dict(native(code), PriceTick=None)
    result = provider.get_trading_reference(CodesRequest(('000001.SZ',)))
    assert result.items[0].error.error_type == 'MISSING_RESULT'
