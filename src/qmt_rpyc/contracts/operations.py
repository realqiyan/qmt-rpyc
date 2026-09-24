"""One authoritative operation registry and reproducible contract manifest."""
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

from qmt_rpyc.contracts.common import (
    MAX_CODES,
    BatchResult,
    CodesRequest,
    EmptyRequest,
    OperationError,
)
from qmt_rpyc.contracts.downloads import (
    DownloadStatus,
    FinancialDownloadRequest,
    HistoryDownloadRequest,
    TaskRef,
    TaskRequest,
)
from qmt_rpyc.contracts.financials import FinancialQuery, FinancialReports
from qmt_rpyc.contracts.instruments import Instrument, TradingReference
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
from qmt_rpyc.contracts.options import (
    ExpiryDates,
    ExpiryDatesRequest,
    OptionChain,
    OptionChainRequest,
    OptionContract,
)
from qmt_rpyc.contracts.reference import (
    DividendEvent,
    DividendQuery,
    IndexWeights,
    IndexWeightsRequest,
    SectorMembersRequest,
)
from qmt_rpyc.contracts.system import Capabilities, Health
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
)

from .schema import schema

CONTRACT_VERSION = 2


@dataclass(frozen=True)
class Operation:
    request_type: type
    response_type: object
    mutation: bool = False


OPERATIONS = {
    "reference.list_sectors": Operation(EmptyRequest, Tuple[str, ...]),
    "reference.get_sector_members": Operation(SectorMembersRequest, Tuple[str, ...]),
    "instruments.list_option_underlyings": Operation(EmptyRequest, Tuple[str, ...]),
    "instruments.get_details": Operation(CodesRequest, BatchResult[Instrument]),
    "instruments.get_trading_reference": Operation(CodesRequest, BatchResult[TradingReference]),
    "options.get_expiry_dates": Operation(ExpiryDatesRequest, ExpiryDates),
    "options.get_option_chain": Operation(OptionChainRequest, OptionChain),
    "options.get_contract_details": Operation(CodesRequest, BatchResult[OptionContract]),
    "market.get_ticks": Operation(CodesRequest, BatchResult[Tick]),
    "market.get_market_ticks": Operation(MarketTicksRequest, MarketTicks),
    "market.get_daily_bars": Operation(DailyBarsQuery, BatchResult[DailyBarSeries]),
    "market.get_intraday_bars": Operation(IntradayBarsQuery, BatchResult[IntradayBarSeries]),
    "market.get_trading_dates": Operation(TradingDatesRequest, Tuple[date, ...]),
    "reference.get_dividend_events": Operation(DividendQuery, Tuple[DividendEvent, ...]),
    "reference.get_index_weights": Operation(IndexWeightsRequest, IndexWeights),
    "financials.get_reports": Operation(FinancialQuery, BatchResult[FinancialReports]),
    "downloads.start_history": Operation(HistoryDownloadRequest, TaskRef, True),
    "downloads.start_financials": Operation(FinancialDownloadRequest, TaskRef, True),
    "downloads.start_sectors": Operation(EmptyRequest, TaskRef, True),
    "downloads.start_index_weights": Operation(EmptyRequest, TaskRef, True),
    "downloads.get_task": Operation(TaskRequest, DownloadStatus),
    "trading.get_asset": Operation(AccountRequest, Optional[Asset]),
    "trading.list_positions": Operation(AccountRequest, Tuple[Position, ...]),
    "trading.list_orders": Operation(OrdersRequest, Tuple[Order, ...]),
    "trading.submit_order": Operation(OrderRequest, OrderSubmission, True),
    "trading.cancel_order": Operation(CancelRequest, CancelSubmission, True),
    "system.get_health": Operation(EmptyRequest, Health),
    "system.get_capabilities": Operation(EmptyRequest, Capabilities),
}


def manifest():
    return {
        "contract_version": CONTRACT_VERSION,
        "codec": "strict-json-utc-microseconds",
        "behavior_revision": 1,
        "max_codes": MAX_CODES,
        "semantics": {
            "identity": "opaque; preserve broker suffix; no whitespace normalization",
            "date_range": "inclusive; daily/date; intraday/aware instant",
            "count": "positive; mutually exclusive with start; None means all in range",
            "expiry": "Shanghai market date, includes today; current discovery only",
            "batch": "one result per requested code, same order; no hidden retries",
            "unknown_outcome": "never retry mutation or task creation",
            "float": "finite, no bool, no application rounding",
            "completion": "SDK returned normally, not coverage or freshness",
        },
        "operations": {name: {"request": schema(op.request_type), "response": schema(op.response_type), "mutation": op.mutation}
                       for name, op in sorted(OPERATIONS.items())},
        "error": schema(OperationError),
    }


CONTRACT_HASH = hashlib.sha256(json.dumps(manifest(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
