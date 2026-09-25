"""Assemble providers for the verified xtquant deployment."""
import logging
from pathlib import Path
import sys

from qmt_rpyc.adapters.interfaces import Providers
from qmt_rpyc.contracts.system import Capabilities, Capability

from .downloads import DownloadAdapter
from .financials import FinancialsAdapter
from .instruments import InstrumentsAdapter
from .market import MarketAdapter
from .options import OptionsAdapter
from .reference import ReferenceAdapter
from .source import SdkEnvironment, SdkSource
from .trading import TradingAdapter

logger = logging.getLogger(__name__)


def _log_sdk_origins():
    """Report loaded modules, including junction targets, without rediscovery."""
    logger.info("SDK Python executable: %s", sys.executable)
    required = {'xtquant', 'xtquant.xtdata', 'xtquant.xttrader', 'xtquant.xttype'}
    modules = dict(sys.modules)
    native = {name for name, module in modules.items()
              if name.startswith('xtquant.')
              and str(getattr(module, '__file__', '')).lower().endswith(('.pyd', '.so', '.dll'))}
    for name in sorted(required | native):
        module = modules.get(name)
        origin = getattr(module, '__file__', None)
        if not origin:
            logger.info("SDK module %s: path unavailable", name)
            continue
        try:
            resolved = str(Path(origin).resolve())
        except (OSError, RuntimeError) as exc:
            logger.warning("SDK module %s: file=%s; cannot resolve path: %s", name, origin, exc)
        else:
            logger.info("SDK module %s: file=%s; resolved=%s", name, origin, resolved)


def create_providers(connection=None, workers=8, environment=None):
    if environment is None:
        from xtquant import xtconstant, xtdata
        from xtquant.xttrader import XtQuantTrader
        environment = SdkEnvironment(xtdata, XtQuantTrader, xtconstant, connection)
        _log_sdk_origins()
    source = SdkSource(environment, workers)
    available = {name: Capability(**value) for name, value in source.capabilities().items()}
    return Providers(
        market=MarketAdapter(source),
        reference=ReferenceAdapter(source),
        instruments=InstrumentsAdapter(source),
        options=OptionsAdapter(source),
        financials=FinancialsAdapter(source),
        trading=TradingAdapter(source),
        downloads=DownloadAdapter(source), capabilities=Capabilities(available))
