"""Conversions verified for the baseline SDK; never exposed as public models."""
import math
from datetime import date, datetime, timedelta, timezone

SHANGHAI = timezone(timedelta(hours=8))
UTC = timezone.utc


def market_date():
    return datetime.now(SHANGHAI).date()


def day(value):
    if type(value) not in (str, int):
        raise ValueError('source date must be an eight-digit date')
    text = str(value)
    if len(text) != 8 or not text.isascii() or not text.isdecimal():
        raise ValueError('source date must be YYYYMMDD')
    return datetime.strptime(text, '%Y%m%d').date()


def instant(value, milliseconds=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid source timestamp')
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(
        milliseconds=value) if milliseconds else datetime(1970, 1, 1, tzinfo=UTC) + timedelta(seconds=value)


def local_instant(value):
    """Task/health wall clocks originate on the server, use its local timezone."""
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def source_date(value):
    if str(value) in ('', '0', '99999999'):
        return {'kind': 'placeholder', 'raw': str(value)}
    return {'kind': 'known', 'value': day(value)}


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError('source number must be finite')
    return float(value)


def integer(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('source quantity must be an integer')
    if not math.isfinite(value) or int(value) != value or (positive and value <= 0):
        raise ValueError('invalid source quantity')
    return int(value)


def underlying(row):
    code, market = row['OptUndlCode'], row['OptUndlMarket']
    if not isinstance(code, str) or not code or not isinstance(market, str) or not market:
        raise ValueError('missing underlying identity')
    return code if '.' in code else code + '.' + market


def sdk_range(start, end, intraday=False):
    def encode(value, is_end):
        if value is None:
            return ''
        if isinstance(value, datetime):
            if value.tzinfo is None or value.microsecond:
                raise ValueError('baseline requires aware boundaries with whole-second precision')
            return value.astimezone(SHANGHAI).strftime('%Y%m%d%H%M%S')
        if not isinstance(value, date):
            raise ValueError('date boundary required')
        return value.strftime('%Y%m%d') + (('235959' if is_end else '000000') if intraday else '')
    return encode(start, False), encode(end, True)


def rows(table):
    columns, indexes, data = table['columns'], table['index'], table['data']
    if len(indexes) != len(data) or len(set(columns)) != len(columns):
        raise ValueError('invalid source table shape')
    for index, row in zip(indexes, data):
        if len(row) != len(columns):
            raise ValueError('invalid source row shape')
        yield index, dict(zip(columns, row))


def identities(items):
    if any(type(item) is not str or not item or item.strip() != item for item in items):
        raise ValueError('invalid source identity')
    return sorted(set(items))


def verify_identity(requested, row):
    # Verified sources return either a bare identifier or the requested full ID.
    # ExchangeID is not a reliable suffix (IF/CFFEX is one known counterexample).
    if row.get('InstrumentID') not in (requested, requested.rsplit('.', 1)[0]):
        raise ValueError('source instrument does not match requested identity')


def timestamp_milliseconds(value):
    if type(value) is not int or not 1000000000000 <= value <= 9999999999999:
        raise ValueError("source timestamp must be Unix milliseconds")
    return value
