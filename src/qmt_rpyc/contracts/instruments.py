"""Instruments requests and results."""
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional, Union


@dataclass(frozen=True)
class KnownDate:
    value: date
    kind: Literal["known"] = "known"


@dataclass(frozen=True)
class DatePlaceholder:
    raw: str
    kind: Literal["placeholder"] = "placeholder"


SourceDate = Union[KnownDate, DatePlaceholder]


@dataclass(frozen=True)
class Instrument:
    source_exchange: str
    source_instrument_id: str
    name: str
    created_date: SourceDate
    listed_date: SourceDate
    expiry_date: SourceDate
    float_volume: float
    total_volume: float
    source_volume_multiple: int
    delivery_end_date: Optional[date]


@dataclass(frozen=True)
class TradingReference:
    source_is_trading: bool
    previous_close: float
    settlement_price: float
    upper_limit: float
    lower_limit: float
    price_tick: float
