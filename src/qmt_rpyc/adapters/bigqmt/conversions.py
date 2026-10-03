"""BigQMT boundary conversions; independent of any external native SDK."""
import logging
import math
from datetime import date, datetime, timedelta, timezone
from typing import Mapping

from qmt_rpyc.adapters.batch import read_batch
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import validate_identity
from qmt_rpyc.contracts.instruments import DatePlaceholder, KnownDate

SHANGHAI = timezone(timedelta(hours=8))
UTC = timezone.utc
logger = logging.getLogger(__name__)


def market_date():
    return datetime.now(SHANGHAI).date()


def day(value):
    if type(value) not in (str, int):
        raise ValueError('source date must be YYYYMMDD')
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdecimal():
        raise ValueError('source date must be YYYYMMDD')
    return datetime.strptime(text, '%Y%m%d').date()


def instant(value):
    if type(value) is not int or value < 0:
        raise ValueError('source timestamp must be Unix milliseconds')
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=value)


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('source number must be finite')
    return float(value)


def source_date(value):
    if type(value) not in (str, int):
        raise ValueError('source date must be text or integer')
    return DatePlaceholder(str(value)) if str(value) in ('', '0', '99999999') else KnownDate(day(value))


def sdk_range(start, end):
    def one(value):
        if value is None:
            return ''
        if type(value) is not date:
            raise ValueError('date boundary required')
        return value.strftime('%Y%m%d')
    return one(start), one(end)


def identities(value):
    if not isinstance(value, (list, tuple, Mapping)):
        raise ValueError('source identities must be a sequence or mapping')
    for code in value:
        validate_identity(code, 'source identity')
    return tuple(sorted(set(value)))


def identity(code, row):
    if not isinstance(row, Mapping) or row.get('InstrumentID') not in (code, code.rsplit('.', 1)[0]):
        raise ValueError('source instrument identity mismatch')


def envelope(source, codes=None):
    if not isinstance(source, Mapping):
        raise ValueError('source envelope must be a mapping')
    identities(source)
    if codes is not None and set(source) - set(codes):
        raise ValueError('unexpected source identities')
    return source


def table_rows(table):
    if not isinstance(table, Mapping) or set(table) != {'columns', 'index', 'data'}:
        raise ValueError('invalid source table envelope')
    columns, index, data = (table[key] for key in ('columns', 'index', 'data'))
    if any(not isinstance(value, (list, tuple)) for value in (columns, index, data)):
        raise ValueError('invalid source table dimensions')
    if any(type(column) is not str for column in columns) or len(set(columns)) != len(columns) or len(index) != len(data):
        raise ValueError('invalid source table columns or length')
    for stamp, row in zip(index, data):
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise ValueError('invalid source table row')
        yield stamp, dict(zip(columns, row))


def read(method, *args):
    try:
        return method(*args)
    except ProviderError:
        raise
    except Exception as exc:
        logger.exception('BigQMT strategy read failed')
        raise ProviderError('SOURCE_ERROR', '', 'strategy read failed', 'sdk_execution', 'not_applicable') from exc


def batch(codes, model, getter, workers=8):
    return read_batch(codes, model, getter, workers, logger=logger,
                      missing_message='source returned no record')
