"""Assemble providers for the verified xtquant deployment."""
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


def create_providers(connection=None, workers=8, environment=None):
    if environment is None:
        from xtquant import xtconstant, xtdata
        from xtquant.xttrader import XtQuantTrader
        environment = SdkEnvironment(xtdata, XtQuantTrader, xtconstant, connection)
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
