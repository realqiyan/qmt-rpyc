"""Market requests and results."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional, Tuple, Union

from .common import Failure, Success, validate_codes, validate_window

Period = Literal["1m", "5m", "15m", "30m", "1h", "1d"]


IntradayPeriod = Literal["1m", "5m", "15m", "30m", "1h"]


Adjustment = Literal["none", "front", "back", "front_ratio", "back_ratio"]


@dataclass(frozen=True)
class Tick:
    observed_at: datetime
    last_price: float
    open: float
    high: float
    low: float
    previous_close: float
    turnover: float
    settlement_price: float
    previous_settlement_price: float
    volume: int
    source_volume_detail: int
    source_trading_status: int
    open_interest: int
    ask_prices: Tuple[float, ...]
    bid_prices: Tuple[float, ...]
    ask_volumes: Tuple[int, ...]
    bid_volumes: Tuple[int, ...]


@dataclass(frozen=True)
class MarketTicks:
    items: Tuple[Union[Success[Tick], Failure], ...]

    def __post_init__(self):
        codes = [item.code for item in self.items]
        if codes != sorted(set(codes)):
            raise ValueError("market result identities must be sorted and unique")


@dataclass(frozen=True)
class DailyBar:
    trade_date: date
    source_time: Optional[datetime]
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    previous_close: Optional[float]
    volume: int
    turnover: float
    open_interest: int
    source_suspension_flag: int
    settlement_price: float


@dataclass(frozen=True)
class IntradayBar:
    bar_at: datetime
    source_time: Optional[datetime]
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    previous_close: Optional[float]
    volume: int
    turnover: float
    open_interest: int
    source_suspension_flag: int
    settlement_price: float


@dataclass(frozen=True)
class DailyBarSeries:
    rows: Tuple[DailyBar, ...]
    adjustment: Adjustment

    def __post_init__(self):
        dates = [row.trade_date for row in self.rows]
        if dates != sorted(set(dates)):
            raise ValueError("bar times must be strictly increasing")


@dataclass(frozen=True)
class IntradayBarSeries:
    rows: Tuple[IntradayBar, ...]
    period: IntradayPeriod
    adjustment: Adjustment

    def __post_init__(self):
        dates = [row.bar_at for row in self.rows]
        if dates != sorted(set(dates)):
            raise ValueError("bar times must be strictly increasing")


@dataclass(frozen=True)
class MarketTicksRequest:
    markets: Tuple[Literal["SH", "SZ"], ...]

    def __post_init__(self):
        if not self.markets or len(set(self.markets)) != len(self.markets):
            raise ValueError("markets must be nonempty and unique")


@dataclass(frozen=True)
class DailyBarsQuery:
    codes: Tuple[str, ...]
    start: Optional[date] = None
    end: Optional[date] = None
    count: Optional[int] = None
    adjustment: Adjustment = "none"
    fill_data: bool = True

    def __post_init__(self):
        validate_codes(self.codes)
        validate_window(self.start, self.end, self.count)


@dataclass(frozen=True)
class IntradayBarsQuery:
    codes: Tuple[str, ...]
    period: IntradayPeriod
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    count: Optional[int] = None
    adjustment: Adjustment = "none"
    fill_data: bool = True

    def __post_init__(self):
        validate_codes(self.codes)
        validate_window(self.start, self.end, self.count)


@dataclass(frozen=True)
class TradingDatesRequest:
    market: Literal["SH", "SZ"]
    start: Optional[date] = None
    end: Optional[date] = None
    count: Optional[int] = None

    def __post_init__(self):
        validate_window(self.start, self.end, self.count)
