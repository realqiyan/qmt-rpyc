"""Assemble independent BigQMT business providers."""
from qmt_rpyc.adapters.interfaces import Providers
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.contracts.system import Capabilities, Capability

from .downloads import COMPATIBILITY_DOWNLOAD_REASON, DownloadAdapter
from .financials import FinancialsAdapter
from .instruments import InstrumentsAdapter
from .market import MarketAdapter
from .options import OptionsAdapter
from .reader import StrategyReader
from .reference import ReferenceAdapter

from .trading import TradingAdapter


def create_providers(connection=None, workers=8, environment=None):
    if type(workers) is not int or workers < 1:
        raise ValueError('workers must be positive')
    if environment is not None:
        raise ValueError('BigQMT does not accept an external SDK environment')
    if connection is None:
        from .connection import ConnectionManager
        connection = ConnectionManager()
    reader = StrategyReader(connection.transport)
    operations = {}
    for name in OPERATIONS:
        reason = COMPATIBILITY_DOWNLOAD_REASON if name.startswith('downloads.start_') else None
        operations[name] = Capability(True, 'bigqmt', reason)
    options = OptionsAdapter(reader, workers)
    connection.discovery_cache = options.cache
    return Providers(MarketAdapter(reader, workers), ReferenceAdapter(reader),
        InstrumentsAdapter(reader, workers, options=options, underlying_cache=None), options,
        FinancialsAdapter(reader, workers), TradingAdapter(connection.transport), DownloadAdapter(), Capabilities(operations))


def storage_strategies(providers):
    from qmt_rpyc.adapters.storage_evidence import BigQmtCoverage
    from qmt_rpyc.storage.adjustment import SampledBigQmtAdjustment
    return dict(evidence=BigQmtCoverage(providers.market), adjustment=SampledBigQmtAdjustment())
