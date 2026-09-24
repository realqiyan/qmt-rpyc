"""Signature and constant probes for the deployed broker SDK. No SDK calls."""
import inspect


def _get_full_tick(code_list):
    pass

def _get_trading_dates(market, start_time='', end_time='', count=-1):
    pass

def _download_history_data(stock_code, period, start_time='', end_time=''):
    pass

def _get_market_data_ex(field_list=[], stock_list=[], period='1d', start_time='', end_time='', count=-1, dividend_type='none', fill_data=True):
    pass

def _get_divid_factors(stock_code, start_time='', end_time=''):
    pass

def _get_stock_list_in_sector(sector_name):
    pass

def _get_instrument_detail(stock_code, iscomplete=False):
    pass

def _get_option_undl_data(undl_code_ref):
    pass

def _get_option_detail_data(optioncode):
    pass

def _get_sector_list():
    pass

def _download_sector_data():
    pass

def _get_financial_data(stock_list, table_list=[], start_time='', end_time='', report_type='report_time'):
    pass

def _download_financial_data(stock_list, table_list=[], start_time='', end_time=''):
    pass

def _get_index_weight(index_code):
    pass

def _download_index_weight():
    pass

def _query_stock_asset(account):
    pass

def _query_stock_orders(account, cancelable_only=False):
    pass

def _query_stock_positions(account):
    pass

def _order_stock(account, stock_code, order_type, order_volume, price_type, price, strategy_name='', order_remark=''):
    pass

def _cancel_order_stock(account, order_id):
    pass

def _cancel_order_stock_sysid(account, market, sysid):
    pass

SIGNATURES = {
    'xtdata.get_full_tick': inspect.signature(_get_full_tick),
    'xtdata.get_trading_dates': inspect.signature(_get_trading_dates),
    'xtdata.download_history_data': inspect.signature(_download_history_data),
    'xtdata.get_market_data_ex': inspect.signature(_get_market_data_ex),
    'xtdata.get_divid_factors': inspect.signature(_get_divid_factors),
    'xtdata.get_stock_list_in_sector': inspect.signature(_get_stock_list_in_sector),
    'xtdata.get_instrument_detail': inspect.signature(_get_instrument_detail),
    'xtdata.get_option_undl_data': inspect.signature(_get_option_undl_data),
    'xtdata.get_option_detail_data': inspect.signature(_get_option_detail_data),
    'xtdata.get_sector_list': inspect.signature(_get_sector_list),
    'xtdata.download_sector_data': inspect.signature(_download_sector_data),
    'xtdata.get_financial_data': inspect.signature(_get_financial_data),
    'xtdata.download_financial_data': inspect.signature(_download_financial_data),
    'xtdata.get_index_weight': inspect.signature(_get_index_weight),
    'xtdata.download_index_weight': inspect.signature(_download_index_weight),
    'trader.query_stock_asset': inspect.signature(_query_stock_asset),
    'trader.query_stock_orders': inspect.signature(_query_stock_orders),
    'trader.query_stock_positions': inspect.signature(_query_stock_positions),
    'trader.order_stock': inspect.signature(_order_stock),
    'trader.cancel_order_stock': inspect.signature(_cancel_order_stock),
    'trader.cancel_order_stock_sysid': inspect.signature(_cancel_order_stock_sysid),
}

CONSTANTS = {'STOCK_BUY': 23, 'STOCK_SELL': 24, 'LATEST_PRICE': 5, 'FIX_PRICE': 11, 'ORDER_SUCCEEDED': 56, 'ORDER_PART_CANCEL': 53, 'ORDER_CANCELED': 54, 'ORDER_JUNK': 57, 'ORDER_PART_SUCC': 55, 'ORDER_PARTSUCC_CANCEL': 52, 'ORDER_REPORTED_CANCEL': 51, 'SH_MARKET': 0, 'SZ_MARKET': 1, 'ORDER_UNREPORTED': 48, 'ORDER_WAIT_REPORTING': 49, 'ORDER_REPORTED': 50, 'ORDER_UNKNOWN': 255}

def signature_problem(fn, expected, unbound=False):
    """Compare structure and defaults, ignoring annotations and formatting."""
    if not callable(fn):
        return 'SDK dependency is missing or not callable'
    try:
        actual = inspect.signature(fn)
    except (TypeError, ValueError):
        return 'SDK signature cannot be inspected'
    params = list(actual.parameters.values())
    if unbound and params and params[0].name == 'self':
        params = params[1:]
    def shape(items):
        return [(p.name, p.kind, type(p.default), p.default) for p in items]
    if shape(params) != shape(expected.parameters.values()):
        return 'SDK signature mismatch: expected {}, found {}'.format(expected, actual)
    return None

def probe(api, environment):
    group, name = api.split('.')
    owner = environment.xtdata if group == 'xtdata' else environment.trader_type
    problem = signature_problem(getattr(owner, name, None), SIGNATURES[api], group == 'trader')
    if problem:
        return problem
    required = []
    if name in ('order_stock', 'query_stock_orders'):
        required = [key for key in CONSTANTS if key.startswith(('STOCK_', 'ORDER_')) or key in ('LATEST_PRICE', 'FIX_PRICE')]
    elif name == 'cancel_order_stock_sysid':
        required = ['SH_MARKET', 'SZ_MARKET']
    for key in required:
        value = getattr(environment.constants, key, None)
        if type(value) is not int or value != CONSTANTS[key]:
            return 'SDK constant mismatch: ' + key
    return None
