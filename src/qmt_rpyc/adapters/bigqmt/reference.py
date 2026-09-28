"""Reference reads use explicit strategy operations, without SDK fallbacks."""
from collections import deque
from typing import Mapping, Protocol, Sequence, Tuple

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.reference import (
    DividendEvent, DividendQuery, IndexWeights, IndexWeightsRequest, SectorMembersRequest,
)
from . import conversions as v


class ReferenceReader(Protocol):
    def get_sector_tree(self, node: str) -> Sequence: ...
    def get_sector_trees(self, nodes: Sequence[str]) -> Mapping: ...
    def get_sector_members(self, sector: str) -> Sequence[str]: ...
    def get_index_members(self, index: str) -> Sequence[str]: ...
    def get_index_weight(self, index: str, code: str) -> float: ...
    def get_index_weights(self, index: str, codes: Sequence[str]) -> Mapping: ...
    def get_dividend_factors(self, code: str) -> Sequence: ...


class ReferenceAdapter:
    def __init__(self, reader: ReferenceReader, max_tree_nodes: int = 4096):
        if type(max_tree_nodes) is not int or max_tree_nodes <= 0:
            raise ValueError('max_tree_nodes must be positive')
        self.reader, self.max_tree_nodes = reader, max_tree_nodes

    def list_sectors(self, request: EmptyRequest) -> Tuple[str, ...]:
        pending, visited, sectors = deque(['']), set(), set()
        while pending:
            nodes = []
            while pending and len(nodes) < 16:
                node = pending.popleft()
                if node in visited:
                    continue
                if len(visited) >= self.max_tree_nodes:
                    raise ValueError('sector tree exceeds traversal limit')
                visited.add(node)
                nodes.append(node)
            if not nodes:
                continue
            values = v.read(self.reader.get_sector_trees, nodes)
            if not isinstance(values, Mapping) or set(values) != set(nodes):
                raise ValueError('sector tree result identities do not match')
            for node in nodes:
                value = values[node]
                if not isinstance(value, (list, tuple)) or len(value) != 2:
                    raise ValueError('sector tree must contain sector and folder lists')
                sectors.update(v.identities(value[0]))
                # Node names are opaque. Never construct unverified slash paths.
                for folder in v.identities(value[1]):
                    if folder not in visited:
                        pending.append(folder)
            if len(pending) + len(visited) > self.max_tree_nodes * 2 or len(sectors) > 50000:
                raise ValueError('sector tree exceeds bounded result size')
        return tuple(sorted(sectors))

    def get_sector_members(self, request: SectorMembersRequest) -> Tuple[str, ...]:
        return v.identities(v.read(self.reader.get_sector_members, request.sector))

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
