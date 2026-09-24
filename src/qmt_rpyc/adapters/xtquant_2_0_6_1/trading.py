"""Trading translation preserves submission uncertainty and broker identities."""
from typing import Optional, Tuple

from qmt_rpyc.contracts.trading import (
    AccountRequest,
    Asset,
    CancelRequest,
    CancelSubmission,
    Order,
    OrderRequest,
    OrdersRequest,
    OrderSubmission,
    Position,
    Rejected,
    RequestRejected,
    RequestSucceeded,
    Submitted,
)

from . import conversions as v
from .source import SdkSource

PRICE_TYPES = {"LIMIT": 11, "LATEST_PRICE": 5}
PRICING_BY_SOURCE = {value: key for key, value in PRICE_TYPES.items()}

STATUSES = {48: 'UNREPORTED', 49: 'WAIT_REPORTING', 50: 'REPORTED', 51: 'CANCEL_PENDING',
            52: 'PARTIAL_CANCEL_PENDING', 53: 'PARTIAL_CANCELLED', 54: 'CANCELLED',
            55: 'PARTIALLY_FILLED', 56: 'FILLED', 57: 'REJECTED', 255: 'UNKNOWN'}


class TradingAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def get_asset(self, r: AccountRequest) -> Optional[Asset]:
        row = self.b.call('trader.query_stock_asset', r.account)
        if row is None:
            return None
        if row['account_id'] != r.account:
            raise ValueError('asset account mismatch')
        return Asset(account=row['account_id'], source_account_type=row['account_type'],
                    **{key: v.number(row[key]) for key in ('cash', 'frozen_cash', 'market_value', 'total_asset')})

    def list_positions(self, r: AccountRequest) -> Tuple[Position, ...]:
        rows = self.b.call('trader.query_stock_positions', r.account)
        result, seen = [], set()
        for row in rows:
            if row['account_id'] != r.account or row['stock_code'] in seen:
                raise ValueError('position account or identity mismatch')
            seen.add(row['stock_code'])
            result.append(Position(account=row['account_id'], source_account_type=row['account_type'],
                               instrument=row['stock_code'], quantity=row['volume'], available_quantity=row['can_use_volume'],
                               frozen_volume=row['frozen_volume'], on_road_volume=row['on_road_volume'],
                               yesterday_volume=row['yesterday_volume'], open_price=v.number(row['open_price']),
                               market_value=v.number(row['market_value'])))
        return tuple(sorted(result, key=lambda item: item.instrument))

    def list_orders(self, r: OrdersRequest) -> Tuple[Order, ...]:
        rows = self.b.call('trader.query_stock_orders', r.account, r.cancelable_only)
        result, seen = [], set()
        for row in rows:
            order_id = str(v.integer(row['order_id'], positive=True))
            if row['account_id'] != r.account or order_id in seen:
                raise ValueError('order account or identity mismatch')
            seen.add(order_id)
            result.append(Order(account=row['account_id'], source_account_type=row['account_type'],
                               instrument=row['stock_code'], order_id=order_id,
                               exchange_order_id=row['order_sysid'] or None,
                               submitted_at=v.instant(row['order_time']),
                               side={23: 'BUY', 24: 'SELL'}.get(row['order_type'], 'UNKNOWN'),
                               source_order_type=row['order_type'],
                               pricing=PRICING_BY_SOURCE.get(row['price_type'], 'UNKNOWN'),
                               source_price_type=row['price_type'], submitted_price=v.number(row['price']),
                               requested_quantity=row['order_volume'], filled_quantity=row['traded_volume'],
                               average_fill_price=v.number(row['traded_price']),
                               status=STATUSES.get(row['order_status'], 'UNKNOWN'), source_status=row['order_status'],
                               source_status_message=row['status_msg'], strategy_name=row['strategy_name'],
                               correlation_ref=row['order_remark']))
        return tuple(sorted(result, key=lambda item: (item.submitted_at, item.order_id)))

    def submit_order(self, r: OrderRequest) -> OrderSubmission:
        result = self.b.call('trader.order_stock', r.account, r.instrument,
                             {'BUY': 23, 'SELL': 24}[r.side], r.quantity, PRICE_TYPES[r.pricing],
                             r.price if r.price is not None else 0,
                             r.strategy_name, r.correlation_ref)
        if type(result) is not int:
            raise ValueError("source submission result must be an integer")
        if result == -1:
            return Rejected('broker rejected submission', -1)
        return Submitted(str(v.integer(result, positive=True)))

    def cancel_order(self, r: CancelRequest) -> CancelSubmission:
        target = r.target
        if target.kind == 'order_id':
            # Public identity remains opaque; this particular SDK requires a
            # positive decimal integer. Validate before crossing the SDK seam.
            if not target.order_id.isascii() or not target.order_id.isdecimal() or int(target.order_id) <= 0:
                from qmt_rpyc.adapters.errors import ProviderError
                raise ProviderError('INVALID_ARGUMENTS', 'trading.cancel_order', 'this adapter requires a positive decimal order ID')
            result = self.b.call('trader.cancel_order_stock', r.account, int(target.order_id))
        else:
            result = self.b.call('trader.cancel_order_stock_sysid', r.account,
                                 {'SH': 0, 'SZ': 1}[target.market], target.exchange_order_id)
        if type(result) is not int:
            raise ValueError("source cancellation result must be an integer")
        if result == 0:
            return RequestSucceeded(result)
        return RequestRejected('broker rejected cancellation request', result)
