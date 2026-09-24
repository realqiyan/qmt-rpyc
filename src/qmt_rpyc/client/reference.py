from datetime import date
from typing import Optional, Tuple

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.reference import (
    DividendEvent,
    DividendQuery,
    IndexWeights,
    IndexWeightsRequest,
    SectorMembersRequest,
)

from .base import _API


class ReferenceAPI(_API):
    def list_sectors(self) -> Tuple[str, ...]:
        return self._call("reference.list_sectors", EmptyRequest())

    def get_sector_members(self, sector: str) -> Tuple[str, ...]:
        return self._call("reference.get_sector_members", SectorMembersRequest(sector))

    def get_dividend_events(self, code: str, start: Optional[date] = None, end: Optional[date] = None) -> Tuple[DividendEvent, ...]:
        return self._call("reference.get_dividend_events", DividendQuery(code, start, end))

    def get_index_weights(self, index: str) -> IndexWeights:
        return self._call("reference.get_index_weights", IndexWeightsRequest(index))
