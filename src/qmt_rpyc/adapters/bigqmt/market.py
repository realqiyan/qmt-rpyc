"""Typed tick, daily bar and calendar reads through the owned strategy reader."""
from datetime import date
from typing import Callable, Mapping, Protocol, Sequence, Tuple

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import BatchResult, CodesRequest
from qmt_rpyc.contracts.market import (
    DailyBarSeries, DailyBarsQuery, MarketTicks, MarketTicksRequest, Tick, TradingDatesRequest,
)
from . import conversions as v


class MarketReader(Protocol):
    def get_full_ticks(self, selectors: Sequence[str]) -> Mapping: ...
    def get_daily_bars(self, codes: Sequence[str], start: str, end: str, count: int,
                       adjustment: str, fill_data: bool) -> Mapping: ...
    def get_trading_dates(self, market: str, start: str, end: str, count: int) -> Sequence: ...


TICK_FIELDS = {
    'last_price': 'lastPrice', 'open': 'open', 'high': 'high', 'low': 'low',
    'previous_close': 'lastClose', 'turnover': 'amount', 'settlement_price': 'settlementPrice',
    'previous_settlement_price': 'lastSettlementPrice', 'volume': 'volume',
    'source_volume_detail': 'pvolume', 'source_trading_status': 'stockStatus',
    'open_interest': 'openInt', 'ask_prices': 'askPrice', 'bid_prices': 'bidPrice',
    'ask_volumes': 'askVol', 'bid_volumes': 'bidVol',
}


class MarketAdapter:
    def __init__(self, reader: MarketReader, workers: int = 8,
                 market_date: Callable[[], date] = v.market_date):
        self.reader, self.workers, self.market_date = reader, workers, market_date

    def _ticks(self, selectors, all_market=False):
        if not selectors:
            return BatchResult(())
        source = v.envelope(v.read(self.reader.get_full_ticks, selectors), None if all_market else selectors)
        codes = v.identities(source) if all_market else selectors
        def one(code):
            if code not in source:
                raise ItemFailure('MISSING_RESULT', 'source omitted requested tick')
            row = source[code]
            return dict(observed_at=v.instant(row['time']), **{key: row[native] for key, native in TICK_FIELDS.items()})
        return v.batch(codes, Tick, one, self.workers)

    def get_ticks(self, request: CodesRequest) -> BatchResult[Tick]:
        return self._ticks(request.codes)

    def get_market_ticks(self, request: MarketTicksRequest) -> MarketTicks:
        # Deployment returns over 50,000 instruments for SH+SZ. Each market
        # fits the bounded pipe frame; combining native responses does not.
        items = {}
        for market in request.markets:
            for item in self._ticks((market,), True).items:
                if item.code in items:
                    raise ValueError('duplicate source identity across requested markets')
                items[item.code] = item
        return MarketTicks(tuple(items[code] for code in sorted(items)))

    def get_daily_bars(self, request: DailyBarsQuery) -> BatchResult[DailyBarSeries]:
        if not request.codes:
            return BatchResult(())
        start, end = v.sdk_range(request.start, request.end)
        source = v.envelope(v.read(self.reader.get_daily_bars, request.codes, start, end,
            request.count if request.count is not None else -1, request.adjustment, request.fill_data), request.codes)
        def one(code):
            if code not in source:
                raise ItemFailure('MISSING_RESULT', 'source omitted requested daily bars')
            records = []
            for index, row in v.table_rows(source[code]):
                stamp = v.day(index)
                if (request.start and stamp < request.start) or (request.end and stamp > request.end):
                    raise ValueError('source returned bars outside requested date window')
                record = dict(trade_date=stamp, source_time=v.instant(row['time']) if row.get('time') is not None else None,
                    volume=row['volume'], turnover=row['amount'], open_interest=row['openInterest'],
                    source_suspension_flag=row['suspendFlag'], settlement_price=row['settelementPrice'],
                    previous_close=row['preClose'], **{key: row[key] for key in ('open', 'high', 'low', 'close')})
                records.append(record)
            if request.count is not None and len(records) > request.count:
                raise ValueError('source returned more bars than requested')
            return dict(rows=records, adjustment=request.adjustment)
        return v.batch(request.codes, DailyBarSeries, one, self.workers)

    def get_trading_dates(self, request: TradingDatesRequest) -> Tuple[date, ...]:
        today = self.market_date()
        if any(day and day > today for day in (request.start, request.end)):
            raise ProviderError('INVALID_ARGUMENTS', 'market.get_trading_dates', 'future calendar coverage is not verified')
        start, end = v.sdk_range(request.start, request.end)
        raw = v.read(self.reader.get_trading_dates, request.market, start, end,
                     request.count if request.count is not None else -1)
        if not isinstance(raw, (list, tuple)):
            raise ValueError('trading dates must be a sequence')
        days = tuple(sorted(set(v.day(value) for value in raw)))
        if any((request.start and day < request.start) or (request.end and day > request.end) or day > today for day in days):
            raise ValueError('source returned dates outside requested window')
        if request.count is not None and len(days) > request.count:
            raise ValueError('source returned more dates than requested')
        return days
