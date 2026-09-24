"""Trading requests and results."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Optional, Union

from .common import validate_identity


@dataclass(frozen=True)
class Asset:
    account: str
    source_account_type: int
    cash: float
    frozen_cash: float
    market_value: float
    total_asset: float

    def __post_init__(self):
        validate_identity(self.account, "account")


@dataclass(frozen=True)
class Position:
    account: str
    source_account_type: int
    instrument: str
    quantity: int
    available_quantity: int
    frozen_volume: int
    on_road_volume: int
    yesterday_volume: int
    open_price: float
    market_value: float

    def __post_init__(self):
        validate_identity(self.account, "account")
        validate_identity(self.instrument, "instrument")
        if not 0 <= self.available_quantity <= self.quantity:
            raise ValueError("invalid position quantities")


OrderStatus = Literal["UNREPORTED", "WAIT_REPORTING", "REPORTED", "CANCEL_PENDING", "PARTIAL_CANCEL_PENDING", "PARTIAL_CANCELLED", "CANCELLED", "PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN"]


@dataclass(frozen=True)
class Order:
    account: str
    source_account_type: int
    instrument: str
    order_id: str
    exchange_order_id: Optional[str]
    submitted_at: datetime
    side: Literal["BUY", "SELL", "UNKNOWN"]
    source_order_type: int
    pricing: Literal["LATEST_PRICE", "UNKNOWN"]
    source_price_type: int
    submitted_price: float
    requested_quantity: int
    filled_quantity: int
    average_fill_price: float
    status: OrderStatus
    source_status: int
    source_status_message: str
    strategy_name: str
    correlation_ref: str

    def __post_init__(self):
        validate_identity(self.account, "account")
        validate_identity(self.instrument, "instrument")
        validate_identity(self.order_id, "order_id")
        if not 0 <= self.filled_quantity <= self.requested_quantity:
            raise ValueError("invalid order quantities")
        if self.average_fill_price < 0 or (self.filled_quantity and self.average_fill_price <= 0):
            raise ValueError("invalid average fill price")
        if self.status == "FILLED" and self.filled_quantity != self.requested_quantity:
            raise ValueError("FILLED quantity mismatch")
        if self.status in ("CANCELLED", "REJECTED") and self.filled_quantity:
            raise ValueError("terminal status loses existing fills")
        if self.status == "PARTIAL_CANCELLED" and self.filled_quantity >= self.requested_quantity:
            raise ValueError("partial cancellation quantity mismatch")


@dataclass(frozen=True)
class Submitted:
    order_id: str
    status: Literal["submitted"] = "submitted"

    def __post_init__(self):
        validate_identity(self.order_id, "order_id")


@dataclass(frozen=True)
class Rejected:
    reason: str
    source_code: int
    status: Literal["rejected"] = "rejected"


OrderSubmission = Union[Submitted, Rejected]


@dataclass(frozen=True)
class RequestSucceeded:
    source_code: int
    status: Literal["succeeded"] = "succeeded"


@dataclass(frozen=True)
class RequestRejected:
    reason: str
    source_code: int
    status: Literal["rejected"] = "rejected"


CancelSubmission = Union[RequestSucceeded, RequestRejected]


@dataclass(frozen=True)
class AccountRequest:
    account: str

    def __post_init__(self):
        validate_identity(self.account, "account")


@dataclass(frozen=True)
class OrdersRequest:
    account: str
    cancelable_only: bool = False

    def __post_init__(self):
        validate_identity(self.account, "account")


@dataclass(frozen=True)
class OrderRequest:
    account: str
    instrument: str
    side: Literal["BUY", "SELL"]
    quantity: int
    price: float
    strategy_name: str = ""
    correlation_ref: str = ""
    pricing: Literal["LATEST_PRICE"] = "LATEST_PRICE"

    def __post_init__(self):
        validate_identity(self.account, "account")
        validate_identity(self.instrument, "instrument")
        if type(self.quantity) is not int or self.quantity <= 0:
            raise ValueError("quantity must be a positive integer")
        if self.price < 0:
            raise ValueError("price cannot be negative")
        if len(self.correlation_ref.encode("ascii")) > 24:
            raise ValueError("correlation_ref exceeds 24 ASCII bytes")


@dataclass(frozen=True)
class ByOrderId:
    order_id: str
    kind: Literal["order_id"] = "order_id"

    def __post_init__(self):
        validate_identity(self.order_id, "order_id")


@dataclass(frozen=True)
class ByExchangeOrderId:
    market: Literal["SH", "SZ"]
    exchange_order_id: str
    kind: Literal["exchange_order_id"] = "exchange_order_id"

    def __post_init__(self):
        validate_identity(self.exchange_order_id, "exchange_order_id")


@dataclass(frozen=True)
class CancelRequest:
    account: str
    target: Union[ByOrderId, ByExchangeOrderId]

    def __post_init__(self):
        validate_identity(self.account, "account")
