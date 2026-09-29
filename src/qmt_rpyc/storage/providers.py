"""Explicit decorators keep the public provider seam typed and discoverable."""
from dataclasses import replace
from datetime import date
from typing import Tuple

from qmt_rpyc.adapters.interfaces import Providers
from qmt_rpyc.contracts.common import BatchResult, CodesRequest, CachedCodesRequest, RefreshRequest
from qmt_rpyc.contracts.financials import FinancialQuery, FinancialReports
from qmt_rpyc.contracts.instruments import Instrument, TradingReference
from qmt_rpyc.contracts.market import DailyBarsQuery, DailyBarSeries, TradingDatesRequest, MarketTicksRequest, MarketTicks, Tick
from qmt_rpyc.contracts.options import ExpiryDatesRequest, ExpiryDates, OptionChainRequest, OptionChain, OptionContract
from qmt_rpyc.contracts.reference import DividendQuery, DividendEvent, IndexWeightsRequest, IndexWeights
from qmt_rpyc.contracts.system import Capabilities, Capability
from .service import PersistentData

CACHED_OPERATIONS = (
    'market.get_daily_bars', 'market.get_trading_dates', 'reference.get_dividend_events',
    'financials.get_reports', 'instruments.get_details', 'options.get_contract_details',
    'instruments.list_option_underlyings', 'options.get_expiry_dates',
)


class Market:
    def __init__(self, source, data):
        self.source, self.data = source, data

    def get_ticks(self, request: CodesRequest) -> BatchResult[Tick]:
        return self.source.get_ticks(request)

    def get_market_ticks(self, request: MarketTicksRequest) -> MarketTicks:
        return self.source.get_market_ticks(request)

    def get_daily_bars(self, request: DailyBarsQuery) -> BatchResult[DailyBarSeries]:
        return self.data.daily_bars(request)

    def get_trading_dates(self, request: TradingDatesRequest) -> Tuple[date, ...]:
        return self.data.trading_dates(request)


class Instruments:
    def __init__(self, source, data):
        self.source, self.data = source, data

    def get_details(self, request: CachedCodesRequest) -> BatchResult[Instrument]:
        return self.data.details(request)

    def get_trading_reference(self, request: CodesRequest) -> BatchResult[TradingReference]:
        return self.source.get_trading_reference(request)

    def list_option_underlyings(self, request: RefreshRequest) -> Tuple[str, ...]:
        return self.data.underlyings(request)


class Options:
    def __init__(self, source, data):
        self.source, self.data = source, data

    def get_contract_details(self, request: CachedCodesRequest) -> BatchResult[OptionContract]:
        return self.data.details(request, contracts=True)

    def get_expiry_dates(self, request: ExpiryDatesRequest) -> ExpiryDates:
        return self.data.expiry_dates(request)

    def get_option_chain(self, request: OptionChainRequest) -> OptionChain:
        return self.source.get_option_chain(request)


class Reference:
    def __init__(self, source, data):
        self.source, self.data = source, data

    def get_dividend_events(self, request: DividendQuery) -> Tuple[DividendEvent, ...]:
        return self.data.dividends(request)

    def get_index_weights(self, request: IndexWeightsRequest) -> IndexWeights:
        return self.source.get_index_weights(request)


class Financials:
    def __init__(self, data):
        self.data = data

    def get_reports(self, request: FinancialQuery) -> BatchResult[FinancialReports]:
        return self.data.financials(request)


def decorate(source: Providers, repository, policies, **kwargs) -> Providers:
    data = PersistentData(source, repository, policies, **kwargs)
    capabilities = dict(source.capabilities.operations)
    for name in CACHED_OPERATIONS:
        capability = capabilities[name]
        capabilities[name] = Capability(True, capability.adapter_id,
            'persistent data; cache miss requires available source' if not capability.available else capability.reason)
    return replace(source, market=Market(source.market,data), instruments=Instruments(source.instruments,data),
                   options=Options(source.options,data), reference=Reference(source.reference,data),
                   financials=Financials(data), capabilities=Capabilities(capabilities))
