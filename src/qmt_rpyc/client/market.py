from datetime import date, datetime
from typing import Optional, Sequence, Tuple

from qmt_rpyc.contracts.common import BatchResult, CodesRequest
from qmt_rpyc.contracts.market import (
    Adjustment,
    DailyBarSeries,
    DailyBarsQuery,
    IntradayBarSeries,
    IntradayBarsQuery,
    IntradayPeriod,
    MarketTicks,
    MarketTicksRequest,
    Tick,
    TradingDatesRequest,
)

from .base import _API, _sequence


class MarketAPI(_API):
    def get_ticks(self, codes: Sequence[str]) -> BatchResult[Tick]:
        return self._call("market.get_ticks", CodesRequest(_sequence(codes)))

    def get_market_ticks(self, markets: Sequence[str]) -> MarketTicks:
        return self._call("market.get_market_ticks", MarketTicksRequest(_sequence(markets)))

    def get_daily_bars(self, codes: Sequence[str], start: Optional[date] = None, end: Optional[date] = None,
                       count: Optional[int] = None, adjustment: Adjustment = "none", fill_data: bool = True) -> BatchResult[DailyBarSeries]:
        return self._call("market.get_daily_bars", DailyBarsQuery(_sequence(codes), start, end, count, adjustment, fill_data))

    def get_intraday_bars(self, codes: Sequence[str], period: IntradayPeriod, start: Optional[datetime] = None,
                          end: Optional[datetime] = None, count: Optional[int] = None,
                          adjustment: Adjustment = "none", fill_data: bool = True) -> BatchResult[IntradayBarSeries]:
        return self._call("market.get_intraday_bars", IntradayBarsQuery(_sequence(codes), period, start, end, count, adjustment, fill_data))

    def get_trading_dates(self, market: str, start: Optional[date] = None, end: Optional[date] = None,
                          count: Optional[int] = None) -> Tuple[date, ...]:
        return self._call("market.get_trading_dates", TradingDatesRequest(market, start, end, count))
