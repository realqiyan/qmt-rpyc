"""Typed provider interfaces; implementations need no RPC dependencies."""
from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, Tuple

from qmt_rpyc.contracts.common import BatchResult, CodesRequest, EmptyRequest
from qmt_rpyc.contracts.downloads import (
    FinancialDownloadRequest,
    HistoryDownloadRequest,
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
from qmt_rpyc.contracts.system import Capabilities
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


class MarketProvider(Protocol):
    def get_ticks(self, r: CodesRequest) -> BatchResult[Tick]:
        ...

    def get_market_ticks(self, r: MarketTicksRequest) -> MarketTicks:
        ...

    def get_daily_bars(self, r: DailyBarsQuery) -> BatchResult[DailyBarSeries]:
        ...

    def get_intraday_bars(self, r: IntradayBarsQuery) -> BatchResult[IntradayBarSeries]:
        ...

    def get_trading_dates(self, r: TradingDatesRequest) -> Tuple[date, ...]:
        ...


class ReferenceProvider(Protocol):
    def list_sectors(self, r: EmptyRequest) -> Tuple[str, ...]:
        ...

    def get_sector_members(self, r: SectorMembersRequest) -> Tuple[str, ...]:
        ...

    def get_dividend_events(self, r: DividendQuery) -> Tuple[DividendEvent, ...]:
        ...

    def get_index_weights(self, r: IndexWeightsRequest) -> IndexWeights:
        ...


class InstrumentsProvider(Protocol):
    def list_option_underlyings(self, r: EmptyRequest) -> Tuple[str, ...]:
        ...

    def get_details(self, r: CodesRequest) -> BatchResult[Instrument]:
        ...

    def get_trading_reference(self, r: CodesRequest) -> BatchResult[TradingReference]:
        ...


class OptionsProvider(Protocol):
    def get_expiry_dates(self, r: ExpiryDatesRequest) -> ExpiryDates:
        ...

    def get_option_chain(self, r: OptionChainRequest) -> OptionChain:
        ...

    def get_contract_details(self, r: CodesRequest) -> BatchResult[OptionContract]:
        ...


class FinancialsProvider(Protocol):
    def get_reports(self, r: FinancialQuery) -> BatchResult[FinancialReports]:
        ...


class TradingProvider(Protocol):
    def get_asset(self, r: AccountRequest) -> Optional[Asset]:
        ...

    def list_positions(self, r: AccountRequest) -> Tuple[Position, ...]:
        ...

    def list_orders(self, r: OrdersRequest) -> Tuple[Order, ...]:
        ...

    def submit_order(self, r: OrderRequest) -> OrderSubmission:
        ...

    def cancel_order(self, r: CancelRequest) -> CancelSubmission:
        ...


class DownloadProvider(Protocol):
    def validate_history(self, request: HistoryDownloadRequest) -> None: ...
    def history(self, request: HistoryDownloadRequest) -> None: ...
    def financials(self, request: FinancialDownloadRequest) -> None: ...
    def sectors(self, request: EmptyRequest) -> None: ...
    def index_weights(self, request: EmptyRequest) -> None: ...


@dataclass(frozen=True)
class Providers:
    market: MarketProvider
    reference: ReferenceProvider
    instruments: InstrumentsProvider
    options: OptionsProvider
    financials: FinancialsProvider
    trading: TradingProvider
    downloads: DownloadProvider
    capabilities: Capabilities
