"""Options requests and results."""
from dataclasses import dataclass
from datetime import date
from typing import Literal, Tuple

from .common import validate_identity


@dataclass(frozen=True)
class ExpiryDates:
    market_date: date
    dates: Tuple[date, ...]

    def __post_init__(self):
        if self.dates != tuple(sorted(set(self.dates))) or any(d < self.market_date for d in self.dates):
            raise ValueError("expiry dates must be unique, sorted and current")


@dataclass(frozen=True)
class OptionChain:
    market_date: date
    contract_codes: Tuple[str, ...]

    def __post_init__(self):
        for code in self.contract_codes:
            validate_identity(code, "contract code")
        if self.contract_codes != tuple(sorted(set(self.contract_codes))):
            raise ValueError("contract codes must be unique and sorted")


@dataclass(frozen=True)
class OptionContract:
    underlying: str
    name: str
    option_type: Literal["CALL", "PUT"]
    expiry_date: date
    strike_price: float
    contract_unit: int

    def __post_init__(self):
        validate_identity(self.underlying, "underlying")
        if self.strike_price <= 0 or self.contract_unit <= 0:
            raise ValueError("option strike and unit must be positive")


@dataclass(frozen=True)
class ExpiryDatesRequest:
    underlying: str

    def __post_init__(self):
        validate_identity(self.underlying, "underlying")


@dataclass(frozen=True)
class OptionChainRequest:
    underlying: str
    expiry_date: date

    def __post_init__(self):
        validate_identity(self.underlying, "underlying")
