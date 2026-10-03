"""Instrument records preserve missing source facts instead of deriving them."""
from datetime import date
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Mapping, Optional, Protocol, Sequence, Tuple

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import BatchResult, CachedCodesRequest, CodesRequest, RefreshRequest
from qmt_rpyc.contracts.instruments import Instrument, TradingReference
from qmt_rpyc.contracts.options import ExpiryDatesRequest
from . import conversions as v
from .options import OptionsAdapter


class InstrumentReader(Protocol):
    def get_instrument_detail(self, code: str) -> Optional[Mapping]: ...
    def get_option_detail(self, code: str) -> Optional[Mapping]: ...
    def get_option_codes(self, underlying: str) -> Sequence[str]: ...
    def get_option_details(self, codes: Sequence[str]) -> Mapping: ...
    def get_option_underlying_map(self) -> Mapping: ...


class InstrumentsAdapter:
    def __init__(self, reader: InstrumentReader, workers: int = 8,
                 market_date: Callable[[], date] = v.market_date, options=None):
        self.reader, self.workers, self.market_date = reader, workers, market_date
        self.options = options if options is not None else OptionsAdapter(reader, workers, market_date)

    def list_option_underlyings(self, request: RefreshRequest) -> Tuple[str, ...]:
        return self._load_underlyings()

    def _load_underlyings(self):
        token = self.options.cache_token()
        source = v.read(self.reader.get_option_underlying_map)
        if not isinstance(source, Mapping):
            raise ValueError('all-market option underlying source must be a mapping')
        codes = v.identities(source)
        if not codes:
            if self.options.cache_token() != token:
                raise ProviderError('NOT_CONNECTED', '', 'bridge changed during underlying discovery')
            return ()
        def current(code):
            return bool(self.options.get_expiry_dates(ExpiryDatesRequest(code, refresh=True)).dates)
        with ThreadPoolExecutor(max_workers=min(3, self.workers, len(codes))) as pool:
            available = tuple(pool.map(current, codes))
        if self.options.cache_token() != token:
            raise ProviderError('NOT_CONNECTED', '', 'bridge changed during underlying discovery')
        return tuple(code for code, active in zip(codes, available) if active)

    def get_details(self, request: CachedCodesRequest) -> BatchResult[Instrument]:
        def one(code):
            row = v.read(self.reader.get_instrument_detail, code)
            if row is None or row == {}:
                return None
            v.identity(code, row)
            delivery = None
            # The deployed general instrument view omits option extension fields.
            # Ask the native option view; do not infer option identity from names.
            option = v.read(self.reader.get_option_detail, code)
            if option is not None and option != {}:
                v.identity(code, option)
                if not option.get('OptUndlCode') or not option.get('OptUndlMarket'):
                    raise ValueError('incomplete option identity')
                delivery = v.day(option['EndDelivDate'])
            return dict(source_exchange=row['ExchangeID'], source_instrument_id=row['InstrumentID'],
                name=row['InstrumentName'], created_date=v.source_date(row['CreateDate']),
                listed_date=v.source_date(row['OpenDate']), expiry_date=v.source_date(row['ExpireDate']),
                float_volume=row['FloatVolume'], total_volume=row['TotalVolume'],
                source_volume_multiple=row['VolumeMultiple'], delivery_end_date=delivery)
        return v.batch(request.codes, Instrument, one, self.workers)

    def get_trading_reference(self, request: CodesRequest) -> BatchResult[TradingReference]:
        def one(code):
            row = v.read(self.reader.get_instrument_detail, code)
            if row is None or row == {}:
                return None
            v.identity(code, row)
            names = {'source_is_trading': 'IsTrading', 'previous_close': 'PreClose',
                'settlement_price': 'SettlementPrice', 'upper_limit': 'UpStopPrice',
                'lower_limit': 'DownStopPrice', 'price_tick': 'PriceTick'}
            if any(row.get(names[name]) is None for name in ('previous_close', 'upper_limit', 'lower_limit', 'price_tick')):
                raise ItemFailure('MISSING_RESULT', 'source lacks required trading-reference fields')
            return {name: row.get(native) for name, native in names.items()}
        return v.batch(request.codes, TradingReference, one, self.workers)
