from typing import Literal, Optional, Tuple

from qmt_rpyc.contracts.trading import (
    AccountRequest,
    Asset,
    ByExchangeOrderId,
    ByOrderId,
    CancelRequest,
    CancelSubmission,
    Order,
    OrderRequest,
    OrdersRequest,
    OrderSubmission,
    Position,
)

from .base import _API


class TradingAPI(_API):
    def get_asset(self, account: str) -> Optional[Asset]:
        return self._call("trading.get_asset", AccountRequest(account))

    def list_positions(self, account: str) -> Tuple[Position, ...]:
        return self._call("trading.list_positions", AccountRequest(account))

    def list_orders(self, account: str, cancelable_only: bool = False) -> Tuple[Order, ...]:
        return self._call("trading.list_orders", OrdersRequest(account, cancelable_only))

    def submit_order(self, account: str, instrument: str, side: Literal["BUY", "SELL"], quantity: int, price: float,
                      strategy_name: str = "", correlation_ref: str = "") -> OrderSubmission:
        return self._call("trading.submit_order", OrderRequest(account, instrument, side, quantity, price, strategy_name, correlation_ref))

    def cancel_order(self, account: str, order_id: Optional[str] = None, market: Optional[str] = None,
                      exchange_order_id: Optional[str] = None) -> CancelSubmission:
        if order_id is not None:
            if market is not None or exchange_order_id is not None:
                raise ValueError("cancel targets are mutually exclusive")
            target = ByOrderId(order_id)
        else:
            if market is None or exchange_order_id is None:
                raise ValueError("provide order_id or market and exchange_order_id")
            target = ByExchangeOrderId(market, exchange_order_id)
        return self._call("trading.cancel_order", CancelRequest(account, target))
