from datetime import date
from typing import Optional, Tuple

from qmt_rpyc.contracts.reference import (
    DividendEvent,
    DividendQuery,
    IndexWeights,
    IndexWeightsRequest,
)

from .base import _API


class ReferenceAPI(_API):
    def get_dividend_events(self, code: str, start: Optional[date] = None, end: Optional[date] = None, refresh: bool = False) -> Tuple[DividendEvent, ...]:
        return self._call("reference.get_dividend_events", DividendQuery(code, start, end, refresh))

    def get_index_weights(self, index: str) -> IndexWeights:
        return self._call("reference.get_index_weights", IndexWeightsRequest(index))
