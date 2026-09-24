"""Reference requests and results."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Mapping, Optional

from .common import validate_identity, validate_window


@dataclass(frozen=True)
class DividendEvent:
    event_date: date
    source_event_at: datetime
    dr: float
    interest: float
    stock_bonus: float
    stock_gift: float
    allotment_quantity: float
    allotment_price: float
    source_gugai: float


@dataclass(frozen=True)
class IndexWeights:
    weights: Mapping[str, float]


@dataclass(frozen=True)
class SectorMembersRequest:
    sector: str

    def __post_init__(self):
        validate_identity(self.sector, "sector")


@dataclass(frozen=True)
class DividendQuery:
    code: str
    start: Optional[date] = None
    end: Optional[date] = None

    def __post_init__(self):
        validate_identity(self.code, "code")
        validate_window(self.start, self.end)


@dataclass(frozen=True)
class IndexWeightsRequest:
    index: str

    def __post_init__(self):
        validate_identity(self.index, "index")
