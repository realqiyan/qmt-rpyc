"""Option providers for deployed BigQMT records, independent of xtquant.

The injected reader is implemented by the strategy bridge. These methods run
in the external service; the bridge owns scheduling calls on the QMT thread.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Mapping, Optional, Protocol, Sequence

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import (
    BatchResult, CodesRequest, Failure, ItemError, Success, validate_identity,
)
from qmt_rpyc.contracts.options import (
    ExpiryDates, ExpiryDatesRequest, OptionChain, OptionChainRequest, OptionContract,
)
from qmt_rpyc.transport.codec import decode, encode
from .cache import DiscoveryCache

logger = logging.getLogger(__name__)
_SHANGHAI = timezone(timedelta(hours=8))


class OptionReader(Protocol):
    """Explicit, read-only bridge methods; no arbitrary SDK invocation."""

    def get_option_codes(self, underlying: str) -> Sequence[str]: ...
    def get_option_detail(self, code: str) -> Optional[Mapping[str, object]]: ...
    def get_option_details(self, codes: Sequence[str]) -> Mapping: ...
    def get_instrument_detail(self, code: str) -> Optional[Mapping[str, object]]: ...


def _market_date():
    return datetime.now(_SHANGHAI).date()


def _day(value):
    if type(value) not in (str, int):
        raise ValueError('source date must be YYYYMMDD')
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdecimal():
        raise ValueError('source date must be YYYYMMDD')
    return datetime.strptime(text, '%Y%m%d').date()


def _identity(code, row):
    if not isinstance(row, Mapping):
        raise ValueError('source detail must be a mapping')
    # Preserve the requested broker suffix, including SHO/SZO. ExchangeID is
    # not a general suffix mapping for all QMT instrument classes.
    if row.get('InstrumentID') not in (code, code.rsplit('.', 1)[0]):
        raise ValueError('source instrument identity mismatch')


def _underlying(row):
    code, market = row['OptUndlCode'], row['OptUndlMarket']
    validate_identity(code, 'source underlying')
    validate_identity(market, 'source underlying market')
    return code if '.' in code else code + '.' + market


def _unit(value):
    # The deployed strategy returns OptUnit as float (e.g. 10000.0).
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0 or int(value) != value:
        raise ValueError('option unit must be a positive integer')
    return int(value)


def _read(method, *args):
    try:
        return method(*args)
    except ProviderError:
        raise
    except Exception as exc:
        logger.exception('BigQMT option source read failed')
        raise ProviderError('SOURCE_ERROR', '', 'source read failed', 'sdk_execution', 'not_applicable') from exc


class OptionsAdapter:
    def __init__(self, reader: OptionReader, workers: int = 8, market_date: Callable[[], date] = _market_date,
                 cache=None):
        if type(workers) is not int or workers < 1:
            raise ValueError('workers must be a positive integer')
        self.reader = reader
        self.workers = workers
        self.market_date = market_date
        self.cache = cache if cache is not None else DiscoveryCache()

    def cache_token(self):
        token = getattr(self.reader, 'cache_token', None)
        return token() if token is not None else None

    def _map(self, codes, getter):
        if not codes:
            return ()
        with ThreadPoolExecutor(max_workers=min(self.workers, len(codes))) as pool:
            return tuple(pool.map(getter, codes))

    def get_contract_details(self, r: CodesRequest) -> BatchResult[OptionContract]:
        def one(code):
            try:
                row = _read(self.reader.get_option_detail, code)
                if row is None or row == {}:
                    raise ItemFailure('NOT_FOUND', 'source returned no option')
                _identity(code, row)
                instrument = _read(self.reader.get_instrument_detail, code)
                if instrument is None or instrument == {}:
                    raise ItemFailure('MISSING_RESULT', 'option name unavailable')
                _identity(code, instrument)
                value = dict(underlying=_underlying(row), name=instrument['InstrumentName'],
                             option_type=row['optType'], expiry_date=_day(row['ExpireDate']),
                             strike_price=row['OptExercisePrice'], contract_unit=_unit(row['OptUnit']))
                return Success(code, decode(OptionContract, encode(value)))
            except ProviderError as exc:
                if exc.category in ('NOT_CONNECTED', 'API_UNAVAILABLE'):
                    raise
                logger.warning('BigQMT option read failed for one item', exc_info=True)
                return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                logger.warning('BigQMT option record failed validation', exc_info=True)
                return Failure(code, ItemError(exc.code if isinstance(exc, ItemFailure) else 'INVALID_RESULT',
                    str(exc) if isinstance(exc, ItemFailure) else 'source item does not satisfy the contract'))
        return BatchResult(self._map(r.codes, one))

    def _candidates(self, underlying, today):
        token = self.cache_token()
        value = self.cache.get(('options', token, today, underlying),
                               lambda: self._load_candidates(underlying, today))
        if self.cache_token() != token:
            raise ProviderError('NOT_CONNECTED', '', 'bridge changed during option discovery')
        return value

    def _load_candidates(self, underlying, today):
        codes = _read(self.reader.get_option_codes, underlying)
        if not isinstance(codes, (list, tuple)):
            raise ValueError('source option codes must be a sequence')
        for code in codes:
            validate_identity(code, 'source option code')
        if len(set(codes)) != len(codes):
            raise ValueError('duplicate option discovery identity')
        if len(codes) > 10000:
            raise ValueError('option discovery exceeds bounded source size')
        rows = _read(self.reader.get_option_details, codes) if codes else {}
        if not isinstance(rows, Mapping) or set(rows) != set(codes):
            raise ValueError('incomplete option discovery result')
        def one(code):
            row = rows[code]
            _identity(code, row)
            if _underlying(row) != underlying:
                raise ValueError('source option underlying mismatch')
            return code, _day(row['ExpireDate'])
        return tuple((code, expiry) for code, expiry in self._map(codes, one) if expiry >= today)

    def get_expiry_dates(self, r: ExpiryDatesRequest) -> ExpiryDates:
        today = self.market_date()
        return ExpiryDates(today, tuple(sorted({expiry for _, expiry in self._candidates(r.underlying, today)})))

    def get_option_chain(self, r: OptionChainRequest) -> OptionChain:
        today = self.market_date()
        if r.expiry_date < today:
            raise ProviderError('INVALID_ARGUMENTS', 'options.get_option_chain', 'past expiry is outside current discovery')
        return OptionChain(today, tuple(sorted(code for code, expiry in self._candidates(r.underlying, today)
                                              if expiry == r.expiry_date)))
