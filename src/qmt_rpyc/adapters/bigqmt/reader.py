"""Explicit service-side methods for the private read-only strategy protocol."""
from typing import Mapping, Protocol


class BridgeTransport(Protocol):
    def request(self, operation: str, arguments: Mapping): ...


class StrategyReader:
    def __init__(self, transport: BridgeTransport):
        self.transport = transport

    def cache_token(self):
        from qmt_rpyc.adapters.errors import ProviderError
        with self.transport.lock:
            token = self.transport.instance
        if token is None:
            raise ProviderError('NOT_CONNECTED', '', 'BigQMT strategy bridge is not connected')
        return token

    def get_full_ticks(self, selectors):
        return self.transport.request('ticks', {'selectors': list(selectors)})

    def get_daily_bars(self, codes, start, end, count, adjustment, fill_data):
        return self.transport.request('daily_bars', dict(codes=list(codes), start=start, end=end,
            count=count, adjustment=adjustment, fill_data=fill_data))

    def get_trading_dates(self, market, start, end, count):
        return self.transport.request('trading_dates', dict(market=market, start=start, end=end, count=count))

    def get_instrument_detail(self, code):
        return self.transport.request('instrument', {'code': code})

    def get_option_detail(self, code):
        return self.transport.request('option_detail', {'code': code})

    def _read_groups(self, operation, key, identities, **arguments):
        # Amortize the measured pipe round-trip without unbounded native loops.
        identities = list(identities)
        if len(identities) != len(set(identities)):
            raise ValueError('duplicate read identities')
        result = {}
        for offset in range(0, len(identities), 16):
            group = identities[offset:offset + 16]
            value = self.transport.request(operation, dict(arguments, **{key: group}))
            if not isinstance(value, Mapping) or set(value) != set(group):
                raise ValueError('grouped source result identities do not match')
            result.update(value)
        return result

    def get_option_details(self, codes):
        return self._read_groups('option_details', 'codes', codes)

    def get_contract_records(self, codes):
        return self.get_option_details(codes)

    def get_index_weights(self, index, codes):
        return self._read_groups('index_weights', 'codes', codes, index=index)

    def get_index_members(self, index):
        return self.transport.request('index_members', {'index': index})

    def get_index_weight(self, index, code):
        return self.transport.request('index_weight', dict(index=index, code=code))

    def get_dividend_factors(self, code):
        return self.transport.request('dividends', {'code': code})

    def get_scalar_financials(self, code, fields, start, end, date_basis):
        return self.transport.request('financials', dict(code=code, fields=list(fields), start=start, end=end, date_basis=date_basis))
