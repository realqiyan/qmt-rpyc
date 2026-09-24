from typing import Sequence, Tuple

from qmt_rpyc.contracts.common import BatchResult, CodesRequest, EmptyRequest
from qmt_rpyc.contracts.instruments import Instrument, TradingReference

from .base import _API, _sequence


class InstrumentsAPI(_API):
    def list_option_underlyings(self) -> Tuple[str, ...]:
        return self._call("instruments.list_option_underlyings", EmptyRequest())

    def get_details(self, codes: Sequence[str]) -> BatchResult[Instrument]:
        return self._call("instruments.get_details", CodesRequest(_sequence(codes)))

    def get_trading_reference(self, codes: Sequence[str]) -> BatchResult[TradingReference]:
        return self._call("instruments.get_trading_reference", CodesRequest(_sequence(codes)))
