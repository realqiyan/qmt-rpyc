"""Private SDK invocation, capability probing and bounded batch conversion."""
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.contracts.common import BatchResult, Failure, ItemError, Success
from qmt_rpyc.transport.codec import decode, encode

from . import conversions as v
from .probe import SIGNATURES, probe
from .serializer import serialize

logger = logging.getLogger(__name__)

DEPENDENCIES = {
    'reference.list_sectors': ('get_sector_list',),
    'reference.get_sector_members': ('get_stock_list_in_sector',),
    'instruments.list_option_underlyings': ('get_option_undl_data', 'get_option_detail_data'),
    'instruments.get_details': ('get_instrument_detail', 'get_option_detail_data'),
    'instruments.get_trading_reference': ('get_instrument_detail',),
    'options.get_expiry_dates': ('get_option_undl_data', 'get_option_detail_data'),
    'options.get_option_chain': ('get_option_undl_data', 'get_option_detail_data'),
    'options.get_contract_details': ('get_option_detail_data', 'get_instrument_detail'),
    'market.get_ticks': ('get_full_tick',),
    'market.get_market_ticks': ('get_full_tick',),
    'market.get_daily_bars': ('get_market_data_ex',),
    'market.get_intraday_bars': ('get_market_data_ex',),
    'market.get_trading_dates': ('get_trading_dates',),
    'reference.get_dividend_events': ('get_divid_factors',),
    'reference.get_index_weights': ('get_index_weight',),
    'financials.get_reports': ('get_financial_data',),
    'downloads.start_history': ('download_history_data',),
    'downloads.start_financials': ('download_financial_data',),
    'downloads.start_sectors': ('download_sector_data',),
    'downloads.start_index_weights': ('download_index_weight',),
    'trading.get_asset': ('trader.query_stock_asset',),
    'trading.list_positions': ('trader.query_stock_positions',),
    'trading.list_orders': ('trader.query_stock_orders',),
    'trading.submit_order': ('trader.order_stock',),
    'trading.cancel_order': ('trader.cancel_order_stock', 'trader.cancel_order_stock_sysid'),
    'downloads.get_task': (), 'system.get_health': (), 'system.get_capabilities': (),
}

@dataclass(frozen=True)
class SdkEnvironment:
    xtdata: object
    trader_type: object
    constants: object
    connection: object


class SdkSource:
    adapter_id = 'xtquant_2.0.6.1'

    def __init__(self, environment, workers=8):
        if type(workers) is not int or workers < 1:
            raise ValueError("batch workers must be a positive integer")
        self.environment = environment
        self.workers = workers
        self._problems = {api: probe(api, environment) for api in SIGNATURES}

    def capabilities(self):
        result = {}
        for operation, dependencies in DEPENDENCIES.items():
            problems = [self._problems[dep if dep.startswith('trader.') else 'xtdata.' + dep]
                        for dep in dependencies]
            reason = '; '.join(problem for problem in problems if problem) or None
            result[operation] = dict(available=reason is None, adapter_id=self.adapter_id if reason is None else None, reason=reason)
        return result

    def call(self, name, *args, **kwargs):
        api = name if name.startswith('trader.') else 'xtdata.' + name
        if self._problems[api]:
            raise ProviderError('API_UNAVAILABLE', '', self._problems[api])
        bound = SIGNATURES[api].bind(*args, **kwargs)
        bound.apply_defaults()
        mutation = name.startswith(('download_', 'trader.order_', 'trader.cancel_'))
        if name.startswith('trader.'):
            connection = self.environment.connection
            if connection is None:
                raise ProviderError('NOT_CONNECTED', '', 'trader not configured')
            response = connection.call_trader_method(name.split('.')[1], [], dict(bound.arguments))
            if response['status'] != 'ok':
                category = 'NOT_CONNECTED' if response.get('error_type') == 'NotConnected' else 'SOURCE_ERROR'
                outcome = response.get('outcome', 'unknown') if mutation else 'not_applicable'
                if response.get('phase') == 'pre_execution':
                    outcome = 'not_executed'
                raise ProviderError(category, '', 'trader invocation failed', response.get('phase', 'sdk_execution'), outcome)
            return response['data']
        try:
            value = getattr(self.environment.xtdata, name)(**bound.arguments)
            if name.startswith('download_'):
                return None
            if name == 'get_option_detail_data' and value == {}:
                return None
            return serialize(value)
        except ProviderError:
            raise
        except Exception as exc:
            logger.exception('SDK invocation failed for %s', api)
            raise ProviderError('SOURCE_ERROR', '', 'source invocation failed', 'sdk_execution',
                                'unknown' if mutation else 'not_applicable') from exc

    def batch(self, codes, model, getter):
        def one(code):
            try:
                value = getter(code)
                if value is None:
                    raise ItemFailure('NOT_FOUND', 'source returned no object')
                return Success(code, decode(model, encode(value)))
            except ProviderError as exc:
                if exc.category in ('NOT_CONNECTED', 'API_UNAVAILABLE'):
                    raise
                logger.warning('Per-item source failure', exc_info=True)
                return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
            except (ValueError, KeyError, TypeError, OverflowError) as exc:
                logger.warning('Per-item validation failure', exc_info=True)
                return Failure(code, ItemError(exc.code if isinstance(exc, ItemFailure) else 'INVALID_RESULT',
                                              str(exc) if isinstance(exc, ItemFailure) else 'source item does not satisfy the contract'))
        if not codes:
            return BatchResult(())
        with ThreadPoolExecutor(max_workers=min(self.workers, len(codes))) as pool:
            return BatchResult(tuple(pool.map(one, codes)))

    def source_option(self, code):
        return self.call('get_option_detail_data', code)

    def candidates(self, underlying, today):
        codes = self.call('get_option_undl_data', underlying)
        v.identities(codes)
        if len(set(codes)) != len(codes):
            raise ValueError('duplicate option discovery identity')
        # Discovery must not invoke the full detail/name adapter. Its only
        # required fields are ownership and expiry, not price/unit/name.
        def read(code):
            row = self.source_option(code)
            if not isinstance(row, dict) or not row:
                raise ValueError('candidate has no usable discovery metadata')
            if 'InstrumentID' in row:
                v.verify_identity(code, row)
            owner, expiry = v.underlying(row), v.day(row['ExpireDate'])
            if owner != underlying:
                raise ValueError('candidate underlying mismatch')
            return code, expiry
        if not codes:
            return ()
        with ThreadPoolExecutor(max_workers=min(self.workers, len(codes))) as pool:
            records = tuple(pool.map(read, codes))
        return tuple((code, expiry) for code, expiry in records if expiry >= today)
