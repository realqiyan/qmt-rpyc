from typing import Sequence, Tuple

from qmt_rpyc.contracts.common import BatchResult, CachedCodesRequest, CodesRequest, RefreshRequest
from qmt_rpyc.contracts.instruments import Instrument, TradingReference

from .base import _API, _sequence


class InstrumentsAPI(_API):
    def list_option_underlyings(self, refresh: bool = False) -> Tuple[str, ...]:
        return self._call("instruments.list_option_underlyings", RefreshRequest(refresh))

    def get_details(self, codes: Sequence[str], refresh: bool = False) -> BatchResult[Instrument]:
        return self._call("instruments.get_details", CachedCodesRequest(_sequence(codes), refresh))

    def get_trading_reference(self, codes: Sequence[str]) -> BatchResult[TradingReference]:
        return self._call("instruments.get_trading_reference", CodesRequest(_sequence(codes)))
