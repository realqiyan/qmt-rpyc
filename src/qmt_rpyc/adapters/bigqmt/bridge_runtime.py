"""Python 3.6 strategy-side business dispatcher; no xtquant or public model imports.

The packaging helper embeds financial_wire beside this module. All business
calls execute on the thread that created StrategyRuntime, never an I/O worker.
"""
import math
import logging
from datetime import datetime
import threading

from .financial_wire import encode_raw_financial
from .bridge_debug import StrategyDebug
from .bridge_trading import StrategyTrading

BAR_FIELDS = ('open', 'high', 'low', 'close', 'volume', 'amount', 'settelementPrice',
              'openInterest', 'preClose', 'suspendFlag', 'time')
ARGUMENTS = {
    'ping': (), 'ticks': ('selectors',), 'instrument': ('code',), 'option_detail': ('code',),
    'option_codes': ('underlying',), 'option_map': (), 'index_members': ('index',),
    'index_weight': ('index', 'code'),
    'dividends': ('code',), 'trading_dates': ('market', 'start', 'end', 'count'),
    'daily_bars': ('codes', 'start', 'end', 'count', 'adjustment', 'fill_data'),
    'financials': ('code', 'fields', 'start', 'end', 'date_basis'),
    'option_details': ('codes',), 'index_weights': ('index', 'codes'),
    'debug': ('request',),
    'trade_read': ('account', 'kind', 'cancelable_only'),
    'trade_submit': ('account', 'instrument', 'side', 'quantity', 'pricing', 'price', 'strategy_name', 'marker'),
    'trade_cancel': ('account', 'target'),
}


def plain(value):
    """Convert explicit native scalar containers, never serialize arbitrary objects."""
    if type(value).__module__.startswith('numpy') and getattr(value, 'ndim', None) == 0:
        value = value.item()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError('source infinity is not serializable')
        return value
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if isinstance(value, dict):
        if any(type(key) is not str for key in value):
            raise ValueError('non-string mapping key requires an explicit wire encoding')
        return {key: plain(item) for key, item in value.items()}
    raise ValueError('unsupported native value type')


def frame(value):
    if type(value).__name__ != 'DataFrame' or not type(value).__module__.startswith('pandas'):
        raise ValueError('native daily bars must be a DataFrame')
    return {'columns': plain(list(value.columns)), 'index': plain(list(value.index)),
            'data': [plain(row) for row in value.itertuples(index=False, name=None)]}


class StrategyRuntime:
    def __init__(self, context, global_api):
        self.context, self.global_api = context, global_api
        self.owner_thread = threading.get_ident()
        self.trading = StrategyTrading(context, global_api)

    def _context(self, name, *args, **kwargs):
        method = getattr(self.context, name, None)
        if not callable(method):
            raise NotImplementedError('required strategy method is absent')
        return method(*args, **kwargs)

    def _ticks(self, selectors):
        source = self._context('get_full_tick', selectors)
        if not isinstance(source, dict):
            raise ValueError('native ticks must be code-keyed')
        result = dict(source)
        missing = [code for code in selectors
                   if code.endswith(('.SHO', '.SZO')) and code not in source]
        # Deployed QMT omits explicit options from get_full_tick. Its tick
        # DataFrame supplies the original timestamp and full five-level book.
        # subscribe=False returns empty frames for a cold option on this build.
        for offset in range(0, len(missing), 16):
            codes = missing[offset:offset + 16]
            try:
                tables = self._context('get_market_data_ex', fields=[], stock_code=codes,
                    period='tick', count=1, dividend_type='none', fill_data=False, subscribe=True)
                if not isinstance(tables, dict):
                    raise ValueError('native option ticks must be code-keyed')
            except Exception:
                logging.getLogger(__name__).warning('Option tick read failed for %s', codes, exc_info=True)
                continue
            for code in codes:
                if code not in tables:
                    continue
                try:
                    table = frame(tables[code])
                    if not table['data']:
                        continue
                    if len(table['data']) != 1 or len(set(table['columns'])) != len(table['columns']):
                        raise ValueError('ambiguous latest option tick')
                    result[code] = dict(zip(table['columns'], table['data'][0]))
                except Exception:
                    logging.getLogger(__name__).warning('Invalid option tick frame for %s', code, exc_info=True)
        return result

    def _option_record(self, code):
        try:
            row = self._context('get_option_detail_data', code)
            if isinstance(row, dict) and row:
                row = dict(row)
                # Bundle the supplemental identity/name in the existing bounded
                # read. Old service builds ignore this private extra field.
                row['_instrument'] = self._context('get_instrumentdetail', code)
            return row
        except Exception:
            logging.getLogger(__name__).warning('Option record read failed for %s', code, exc_info=True)
            return {'_read_error': True}

    def dispatch(self, operation, args):
        if threading.get_ident() != self.owner_thread:
            raise RuntimeError('QMT reads must execute on the strategy thread')
        if operation not in ARGUMENTS:
            raise ValueError('operation is outside the bridge allowlist')
        if not isinstance(args, dict) or set(args) != set(ARGUMENTS[operation]):
            raise ValueError('invalid bridge operation arguments')
        for name in ('code', 'underlying', 'index'):
            if name in args and (type(args[name]) is not str or len(args[name]) > 256
                                 or not args[name]):
                raise ValueError('invalid bridge identity')
        for name in ('codes', 'selectors'):
            if name in args and (not isinstance(args[name], list) or not 0 < len(args[name]) <= 500
                or any(type(code) is not str or not code or len(code) > 256 for code in args[name])
                or len(set(args[name])) != len(args[name])):
                raise ValueError('invalid bridge identity list')
        for name in ('start', 'end'):
            if name in args:
                value = args[name]
                if type(value) is not str:
                    raise ValueError('date boundary must be text')
                if value:
                    if len(value) != 8 or any(char < '0' or char > '9' for char in value):
                        raise ValueError('date boundary must be YYYYMMDD')
                    datetime.strptime(value, '%Y%m%d')
        if 'count' in args and (type(args['count']) is not int or args['count'] < -1 or args['count'] == 0):
            raise ValueError('invalid count')
        if operation == 'ping':
            return {'runtime': 'bigqmt', 'read_only': False}
        if operation.startswith('trade_'):
            return plain(self.trading.execute(operation, args, getattr(self, 'request_deadline', None)))
        if operation == 'debug':
            return StrategyDebug(self.context, self.global_api).execute(args['request'])
        if operation in ('option_details', 'index_weights'):
            if len(args['codes']) > 16:
                raise ValueError('native read group exceeds 16 items')
            if operation == 'option_details':
                result = {code: self._option_record(code) for code in args['codes']}
            else:
                result = {code: self._context('get_weight_in_index', args['index'], code) for code in args['codes']}
        elif operation == 'ticks':
            selectors = args['selectors']
            if not isinstance(selectors, list) or not selectors or len(selectors) > 500:
                raise ValueError('tick selectors must be bounded and nonempty')
            result = self._ticks(selectors)
        elif operation == 'daily_bars':
            if not isinstance(args['codes'], list) or not args['codes'] or len(args['codes']) > 500:
                raise ValueError('daily codes must be bounded and nonempty')
            if args['adjustment'] not in ('none', 'front', 'back', 'front_ratio', 'back_ratio') or type(args['fill_data']) is not bool:
                raise ValueError('invalid daily adjustment or fill policy')
            raw = self._context('get_market_data_ex', list(BAR_FIELDS), args['codes'], '1d',
                args['start'], args['end'], args['count'], args['adjustment'], args['fill_data'], False)
            if not isinstance(raw, dict):
                raise ValueError('daily result must be code-keyed')
            result = {code: frame(table) for code, table in raw.items()}
        elif operation == 'instrument':
            result = self._context('get_instrumentdetail', args['code'])
        elif operation == 'option_detail':
            result = self._context('get_option_detail_data', args['code'])
        elif operation == 'option_codes':
            result = self._context('get_option_undl_data', args['underlying'])
        elif operation == 'option_map':
            result = self._context('get_option_undl_data', '')
        elif operation == 'index_members':
            result = self._context('get_sector', args['index'])
        elif operation == 'index_weight':
            result = self._context('get_weight_in_index', args['index'], args['code'])
        elif operation == 'dividends':
            raw = self._context('get_divid_factors', args['code'])
            if not isinstance(raw, dict):
                raise ValueError('dividend result must be a time-keyed mapping')
            result = [[plain(key), plain(values)] for key, values in raw.items()]
        elif operation == 'trading_dates':
            # ContextInfo expects a security, not a market token. Keep the
            # representative-index choice private and explicit for validation.
            code = {'SH': '000001.SH', 'SZ': '399001.SZ'}[args['market']]
            result = self._context('get_trading_dates', code, args['start'], args['end'], args['count'], '1d')
        elif operation == 'financials':
            if args['date_basis'] not in ('report_time', 'announce_time'):
                raise ValueError('invalid financial date basis')
            allowed_tables = ('ASHAREBALANCESHEET', 'ASHAREINCOME', 'ASHARECASHFLOW', 'CAPITALSTRUCTURE', 'PERSHAREINDEX')
            if not isinstance(args['fields'], list) or not args['fields'] or len(args['fields']) > 64:
                raise ValueError('financial field selection must be bounded')
            if any(type(field) is not str or field.split('.', 1)[0] not in allowed_tables for field in args['fields']):
                raise ValueError('financial table is outside scope')
            result = encode_raw_financial(self._context('get_raw_financial_data', args['fields'],
                [args['code']], args['start'], args['end'], args['date_basis']))
        return plain(result)
