from datetime import date, datetime
from typing import Tuple

from qmt_rpyc.adapters.errors import ItemFailure
from qmt_rpyc.contracts.common import BatchResult, CodesRequest
from qmt_rpyc.contracts.market import (
    DailyBarSeries,
    DailyBarsQuery,
    IntradayBarSeries,
    IntradayBarsQuery,
    MarketTicks,
    MarketTicksRequest,
    Tick,
    TradingDatesRequest,
)

from . import conversions as v
from .source import SdkSource

TICK_NAMES = {
    'lastPrice': 'last_price', 'open': 'open', 'high': 'high', 'low': 'low',
    'lastClose': 'previous_close', 'amount': 'turnover',
    'settlementPrice': 'settlement_price', 'lastSettlementPrice': 'previous_settlement_price',
    'volume': 'volume', 'pvolume': 'source_volume_detail', 'stockStatus': 'source_trading_status',
    'openInt': 'open_interest', 'askPrice': 'ask_prices', 'bidPrice': 'bid_prices',
    'askVol': 'ask_volumes', 'bidVol': 'bid_volumes',
}
FLOAT_TICKS = frozenset(('lastPrice', 'open', 'high', 'low', 'lastClose', 'amount',
                         'settlementPrice', 'lastSettlementPrice'))


BAR_FIELDS = ['open', 'high', 'low', 'close', 'volume', 'amount', 'settelementPrice', 'openInterest', 'preClose', 'suspendFlag', 'time']

class MarketAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def _ticks(self, selectors, market=False):
        if not selectors:
            return MarketTicks(()) if market else BatchResult(())
        source = self.b.call('get_full_tick', list(selectors))
        if not isinstance(source, dict) or any(not isinstance(k, str) or not k for k in source):
            raise ValueError('invalid tick result identities')
        codes = sorted(source) if market else selectors
        if not market and set(source) - set(selectors):
            raise ValueError('unexpected tick identities')
        def one(code):
            if code not in source:
                raise ItemFailure('MISSING_RESULT', 'source omitted requested instrument')
            row = source[code]
            v.timestamp_milliseconds(row['time'])
            result = {'observed_at': v.instant(row['time'], milliseconds=True)}
            for old, new in TICK_NAMES.items():
                value = row[old]
                if old in FLOAT_TICKS:
                    value = v.number(value)
                elif old in ('askPrice', 'bidPrice'):
                    value = [v.number(x) for x in value]
                result[new] = value
            return result
        result = self.b.batch(codes, Tick, one)
        return MarketTicks(result.items) if market else result

    def get_ticks(self, r: CodesRequest) -> BatchResult[Tick]:
        return self._ticks(r.codes)

    def get_market_ticks(self, r: MarketTicksRequest) -> MarketTicks:
        return self._ticks(r.markets, market=True)

    def get_daily_bars(self, r: DailyBarsQuery) -> BatchResult[DailyBarSeries]:
        return self._bars(r, '1d')

    def get_intraday_bars(self, r: IntradayBarsQuery) -> BatchResult[IntradayBarSeries]:
        return self._bars(r, r.period)

    def _bars(self, r, period):
        if not r.codes:
            return BatchResult(())
        start, end = v.sdk_range(r.start, r.end, period != '1d')
        fields = BAR_FIELDS
        source = self.b.call('get_market_data_ex', fields, list(r.codes), period,
                            start, end, r.count if r.count is not None else -1,
                            r.adjustment, r.fill_data)
        if not isinstance(source, dict) or set(source) - set(r.codes):
            raise ValueError('invalid bar result identities')
        def one(code):
            if code not in source:
                raise ItemFailure('MISSING_RESULT', 'source omitted requested series')
            table = source[code]
            records = []
            previous = None
            for index, row in v.rows(table):
                stamp = v.day(index) if period == '1d' else datetime.strptime(str(index), '%Y%m%d%H%M%S').replace(tzinfo=v.SHANGHAI).astimezone(v.UTC)
                if previous is not None and stamp <= previous:
                    raise ValueError('bars must have unique increasing timestamps')
                previous = stamp
                record = dict(source_time=v.instant(row['time'], True) if 'time' in row else None,
                              volume=row['volume'], turnover=v.number(row['amount']),
                              open_interest=row['openInterest'], source_suspension_flag=row['suspendFlag'],
                              settlement_price=v.number(row['settelementPrice']))
                for old, new in [('open', 'open'), ('high', 'high'), ('low', 'low'), ('close', 'close'), ('preClose', 'previous_close')]:
                    record[new] = None if row[old] is None else v.number(row[old])
                record['trade_date' if period == '1d' else 'bar_at'] = stamp
                records.append(record)
            result = dict(rows=records, adjustment=r.adjustment)
            if period != '1d':
                result['period'] = period
            return result
        return self.b.batch(r.codes, DailyBarSeries if period == '1d' else IntradayBarSeries, one)

    def get_trading_dates(self, r: TradingDatesRequest) -> Tuple[date, ...]:
        start, end = v.sdk_range(r.start, r.end)
        # The source does not supply a coverage boundary for future calendars.
        # Reject future queries explicitly, rather than calling empty authoritative.
        if (r.end is not None and r.end > v.market_date()) or (r.start is not None and r.start > v.market_date()):
            from qmt_rpyc.adapters.errors import ProviderError
            raise ProviderError('INVALID_ARGUMENTS', 'market.get_trading_dates', 'future calendar coverage is not guaranteed by this adapter')
        result = self.b.call('get_trading_dates', r.market, start, end, r.count if r.count is not None else -1)
        return tuple(sorted(set(v.instant(x, True).astimezone(v.SHANGHAI).date() for x in result)))
