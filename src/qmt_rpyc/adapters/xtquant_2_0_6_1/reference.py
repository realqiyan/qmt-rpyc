from typing import Tuple

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.reference import (
    DividendEvent,
    DividendQuery,
    IndexWeights,
    IndexWeightsRequest,
    SectorMembersRequest,
)

from . import conversions as v
from .source import SdkSource


class ReferenceAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def list_sectors(self, r: EmptyRequest) -> Tuple[str, ...]:
        return tuple(v.identities(self.b.call('get_sector_list')))

    def get_sector_members(self, r: SectorMembersRequest) -> Tuple[str, ...]:
        return tuple(v.identities(self.b.call('get_stock_list_in_sector', r.sector)))

    def get_dividend_events(self, r: DividendQuery) -> Tuple[DividendEvent, ...]:
        start, end = v.sdk_range(r.start, r.end)
        source = self.b.call('get_divid_factors', r.code, start, end)
        result = []
        names = {'dr': 'dr', 'interest': 'interest', 'stockBonus': 'stock_bonus', 'stockGift': 'stock_gift',
                 'allotNum': 'allotment_quantity', 'allotPrice': 'allotment_price', 'gugai': 'source_gugai'}
        for index, row in v.rows(source):
            result.append(DividendEvent(event_date=v.day(index), source_event_at=v.instant(row['time'], True),
                               **{new: v.number(row[old]) for old, new in names.items()}))
        return tuple(sorted(result, key=lambda event: event.event_date))

    def get_index_weights(self, r: IndexWeightsRequest) -> IndexWeights:
        return IndexWeights({code: v.number(weight) for code, weight in self.b.call('get_index_weight', r.index).items()})
