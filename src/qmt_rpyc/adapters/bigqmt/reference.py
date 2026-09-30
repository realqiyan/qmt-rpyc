"""Reference reads use explicit strategy operations, without SDK fallbacks."""
from typing import Mapping, Protocol, Sequence, Tuple

from qmt_rpyc.contracts.reference import (
    DividendEvent, DividendQuery, IndexWeights, IndexWeightsRequest,
)
from . import conversions as v


class ReferenceReader(Protocol):
    def get_index_members(self, index: str) -> Sequence[str]: ...
    def get_index_weight(self, index: str, code: str) -> float: ...
    def get_index_weights(self, index: str, codes: Sequence[str]) -> Mapping: ...
    def get_dividend_factors(self, code: str) -> Sequence: ...


class ReferenceAdapter:
    def __init__(self, reader: ReferenceReader):
        self.reader = reader

    def get_index_weights(self, request: IndexWeightsRequest) -> IndexWeights:
        members = v.identities(v.read(self.reader.get_index_members, request.index))
        if len(members) > 10000:
            raise ValueError('index member count exceeds limit')
        # Keep source percent values; a zero weight is still a returned weight.
        source = v.read(self.reader.get_index_weights, request.index, members) if members else {}
        if not isinstance(source, Mapping) or set(source) != set(members):
            raise ValueError('index weight result identities do not match')
        values = {code: v.number(source[code]) for code in members}
        return IndexWeights(values)

    def get_dividend_events(self, request: DividendQuery) -> Tuple[DividendEvent, ...]:
        pairs = v.read(self.reader.get_dividend_factors, request.code)
        if not isinstance(pairs, (list, tuple)):
            raise ValueError('dividend source must preserve explicit time/value pairs')
        records, seen = [], set()
        names = ('interest', 'stock_bonus', 'stock_gift', 'allotment_quantity', 'allotment_price', 'source_gugai', 'dr')
        for pair in pairs:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError('invalid dividend pair')
            stamp, fields = pair
            event_at = v.instant(stamp)
            if stamp in seen or not isinstance(fields, (list, tuple)) or len(fields) != 7:
                raise ValueError('invalid dividend event or duplicate timestamp')
            seen.add(stamp)
            day = event_at.astimezone(v.SHANGHAI).date()
            # Validate even out-of-window values; do not conceal corrupt data.
            values = {name: v.number(value) for name, value in zip(names, fields)}
            if (request.start and day < request.start) or (request.end and day > request.end):
                continue
            records.append(DividendEvent(day, event_at, **values))
        return tuple(sorted(records, key=lambda row: row.source_event_at))
