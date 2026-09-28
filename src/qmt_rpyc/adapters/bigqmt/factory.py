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
from .underlying_cache import UnderlyingCache
import os
import hashlib
from pathlib import Path


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
    cache_root = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache')
    namespace = hashlib.sha256(str(getattr(connection.transport, 'name', 'default')).encode()).hexdigest()[:16]
    underlyings = UnderlyingCache(cache_root / 'qmt-rpyc' / ('underlyings-' + namespace + '.json'))
    connection.underlying_cache = underlyings
    return Providers(MarketAdapter(reader, workers), ReferenceAdapter(reader),
        InstrumentsAdapter(reader, workers, options=options, underlying_cache=underlyings), options,
        FinancialsAdapter(reader, workers), TradingAdapter(connection.transport), DownloadAdapter(), Capabilities(operations))
