"""Five core financial tables backed by the dedicated BigQMT strategy bridge.

Shareholder tables are outside this adapter's agreed scope. Missing numeric
values follow the public nullable contract.
"""
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields as model_fields
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol, Sequence

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import BatchResult, Failure, ItemError, Success
from qmt_rpyc.contracts.financials import (
    FINANCIAL_TABLES, FinancialQuery, FinancialReports,
    BalanceRecord, CapitalRecord, CashFlowRecord, IncomeRecord,
    PershareIndexRecord,
)
from qmt_rpyc.transport.codec import decode, encode

_SHANGHAI = timezone(timedelta(hours=8))
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
SCALAR_TABLES = {
    'Balance': ('ASHAREBALANCESHEET', BalanceRecord),
    'Income': ('ASHAREINCOME', IncomeRecord),
    'CashFlow': ('ASHARECASHFLOW', CashFlowRecord),
    'Capital': ('CAPITALSTRUCTURE', CapitalRecord),
    'PershareIndex': ('PERSHAREINDEX', PershareIndexRecord),
}
# Reference local field documentation explicitly names freeFloatCapital as the
# old spelling of free_float_capital; the latter is observed in this deployment.
_FIELD_ALIASES = {('Capital', 'freeFloatCapital'): 'free_float_capital'}
logger = logging.getLogger(__name__)


class FinancialReader(Protocol):
    def get_scalar_financials(self, code: str, fields: Sequence[str], start: str,
                             end: str, date_basis: str) -> Mapping[str, object]:
        """Return a code-keyed envelope with encoded field/time/value pairs."""
        ...


class FinancialsAdapter:
    def __init__(self, reader: FinancialReader, workers: int = 4):
        if type(workers) is not int or workers < 1:
            raise ValueError('workers must be a positive integer')
        self.reader, self.workers = reader, workers

    def get_reports(self, request: FinancialQuery) -> BatchResult[FinancialReports]:
        if not request.codes:
            return BatchResult(())
        fields = tuple(field for table in request.tables for field in scalar_fields(table))
        start = request.start.strftime('%Y%m%d') if request.start else ''
        end = request.end.strftime('%Y%m%d') if request.end else ''

        def one(code):
            try:
                envelope = self.reader.get_scalar_financials(code, fields, start, end, request.date_basis)
            except ProviderError as exc:
                if exc.category in ('NOT_CONNECTED', 'API_UNAVAILABLE'):
                    raise
                logger.warning('BigQMT financial source failed for one item', exc_info=True)
                return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
            except Exception:
                logger.exception('BigQMT financial source read failed')
                return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
            try:
                if not isinstance(envelope, Mapping) or set(envelope) - {code}:
                    raise ValueError('financial source identity mismatch')
                if code not in envelope:
                    raise ItemFailure('MISSING_RESULT', 'source omitted financial reports')
                values = {table: None for table in FINANCIAL_TABLES}
                for table in request.tables:
                    values[table] = convert_scalar_table(table, envelope[code], request.date_basis)
                return Success(code, FinancialReports(**values))
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                logger.warning('BigQMT financial record failed validation', exc_info=True)
                return Failure(code, ItemError(exc.code if isinstance(exc, ItemFailure) else 'INVALID_RESULT',
                    str(exc) if isinstance(exc, ItemFailure) else 'source item does not satisfy the contract'))

        with ThreadPoolExecutor(max_workers=min(self.workers, len(request.codes))) as pool:
            return BatchResult(tuple(pool.map(one, request.codes)))


def scalar_fields(table):
    native, model = SCALAR_TABLES[table]
    return tuple(native + '.' + _FIELD_ALIASES.get((table, field.name), field.name)
                 for field in model_fields(model))


def _milliseconds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or int(value) != value:
        raise ValueError('financial date must be integral Unix milliseconds')
    return int(value)


def _day(value):
    return (_EPOCH + timedelta(milliseconds=_milliseconds(value))).astimezone(_SHANGHAI).date()


def _points(value):
    if not isinstance(value, (list, tuple)):
        raise ValueError('financial points must be explicit time/value pairs')
    result = {}
    for pair in value:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2 or type(pair[0]) is not int:
            raise ValueError('invalid financial time/value pair')
        key, item = pair
        if key in result:
            raise ValueError('duplicate financial time key')
        result[key] = item
    return result


def convert_scalar_table(table, source_fields, date_basis='report_time'):
    """Return public records from one stock's JSON-safe field series.

Join only by identical source keys, preserving separate report and announcement
metadata. Both dates must be supplied. No joining by row position, values, or
inferred reporting calendars is permitted.
"""
    if date_basis not in ('report_time', 'announce_time'):
        raise ValueError('unknown financial date basis')
    if not isinstance(source_fields, Mapping):
        raise ValueError('financial fields must be a mapping')
    _, model = SCALAR_TABLES[table]
    report_field, announcement_field = 'm_timetag', 'm_anntime'
    date_fields = (report_field, announcement_field)
    public_names = tuple(field.name for field in model_fields(model))
    columns = {}
    for name, native in zip(public_names, scalar_fields(table)):
        if name in date_fields:
            columns[name] = _points(source_fields[native])
        else:
            columns[name] = _points(source_fields[native]) if native in source_fields else {}
    keys = set(columns[report_field])
    if set(columns[announcement_field]) != keys:
        raise ValueError('financial dates have different record keys')
    if any(set(points) - keys for points in columns.values()):
        raise ValueError('financial value has no matching date metadata')
    primary = report_field if date_basis == 'report_time' else announcement_field
    result = []
    for key in sorted(keys):
        row = {name: points.get(key) for name, points in columns.items()}
        if _milliseconds(row[primary]) != key:
            raise ValueError('financial key does not match requested date basis')
        for name in date_fields:
            row[name] = _day(row[name])
        result.append(decode(model, encode(row)))
    return tuple(result)
