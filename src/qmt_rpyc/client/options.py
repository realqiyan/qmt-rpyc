from datetime import date
from typing import Sequence

from qmt_rpyc.contracts.common import BatchResult, CachedCodesRequest
from qmt_rpyc.contracts.options import (
    ExpiryDates,
    ExpiryDatesRequest,
    OptionChain,
    OptionChainRequest,
    OptionContract,
)

from .base import _API, _sequence


class OptionsAPI(_API):
    def get_expiry_dates(self, underlying: str, refresh: bool = False) -> ExpiryDates:
        return self._call("options.get_expiry_dates", ExpiryDatesRequest(underlying, refresh))

    def get_option_chain(self, underlying: str, expiry_date: date) -> OptionChain:
        return self._call("options.get_option_chain", OptionChainRequest(underlying, expiry_date))

    def get_contract_details(self, codes: Sequence[str], refresh: bool = False) -> BatchResult[OptionContract]:
        return self._call("options.get_contract_details", CachedCodesRequest(_sequence(codes), refresh))
