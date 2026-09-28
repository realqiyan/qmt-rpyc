"""Python 3.6 native read diagnostics, executed only by StrategyRuntime."""
import datetime as debug_datetime
import inspect
import logging
import math

DEBUG_READS = {
    'context': ('get_full_tick', 'get_market_data', 'get_market_data_ex', 'get_local_data',
                'get_trading_dates', 'get_instrumentdetail', 'get_instrument_detail',
                'get_option_detail_data', 'get_option_list', 'get_option_undl_data',
                'get_sector', 'get_stock_list_in_sector', 'get_divid_factors',
                'get_weight_in_index', 'get_his_index_data', 'get_financial_data',
                'get_raw_financial_data'),
    'global': ('get_sector_list', 'get_stock_list_in_sector', 'get_history_index_weight'),
}


def debug_value(value, depth=0):
    if depth > 16:
        raise ValueError('debug value nesting exceeds limit')
    convert = lambda item: debug_value(item, depth + 1)
    if type(value).__module__.startswith('numpy') and getattr(value, 'ndim', None) == 0:
        value = value.item()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError('debug infinity is not serializable')
        return value
    if isinstance(value, (debug_datetime.datetime, debug_datetime.date)):
        return {'type': type(value).__name__, 'value': value.isoformat()}
    if type(value).__module__.startswith('pandas'):
        if type(value).__name__ == 'DataFrame':
            return dict(type='DataFrame', columns=convert(list(value.columns)),
                        index=convert(list(value.index)), data=convert(list(value.itertuples(index=False, name=None))))
        if type(value).__name__ == 'Series':
            return dict(type='Series', name=convert(value.name), index=convert(list(value.index)), data=convert(list(value)))
    if isinstance(value, (list, tuple, dict)):
        if len(value) > 100000:
            raise ValueError('debug collection exceeds limit')
        if isinstance(value, dict):
            if all(type(key) is str for key in value):
                return {key: convert(item) for key, item in value.items()}
            return dict(type='mapping', items=[[convert(key), convert(item)] for key, item in value.items()])
        return [convert(item) for item in value]
    raise ValueError('unsupported native debug result type')


class StrategyDebug:
    def __init__(self, context, global_api):
        self.context, self.global_api = context, global_api

    def _member(self, target):
        if type(target) is not str or len(target.split('.')) != 2:
            raise ValueError('target must be context.NAME or global.NAME')
        owner, name = target.split('.')
        if owner not in DEBUG_READS or not name.isidentifier() or name.startswith('_'):
            raise ValueError('only direct public native members are supported')
        member = getattr(self.context, name, None) if owner == 'context' else self.global_api.get(name)
        if not callable(member) or inspect.isclass(member):
            raise ValueError('target is not a native callable')
        return owner, name, member

    def _describe(self, target):
        owner, name, member = self._member(target)
        try:
            signature = str(inspect.signature(member))
        except (TypeError, ValueError):
            signature = None
        doc = inspect.getdoc(member) or ''
        return dict(target=target, callable=True, signature=signature, doc=doc[:16000],
                    doc_truncated=len(doc) > 16000, call_allowed=name in DEBUG_READS[owner])

    def execute(self, request):
        phase = 'pre_execution'
        try:
            if type(request) is not dict or set(request) - {'action', 'target', 'args', 'kwargs'}:
                raise ValueError('invalid debug request')
            action, target = request.get('action'), request.get('target')
            args, kwargs = request.get('args', []), request.get('kwargs', {})
            if action not in ('describe', 'call') or type(args) is not list or type(kwargs) is not dict:
                raise ValueError('invalid debug action or arguments')
            if action == 'describe':
                if args or kwargs:
                    raise ValueError('describe does not accept arguments')
                if target is None:
                    targets = []
                    for owner, names in (('context', dir(self.context)), ('global', self.global_api)):
                        for name in sorted(names):
                            if name.startswith('_'):
                                continue
                            if owner == 'global' and not (name.startswith(('get_', 'download_', 'down_')) or name in ('passorder', 'cancel', 'can_cancel_order')):
                                continue
                            member = getattr(self.context, name, None) if owner == 'context' else self.global_api[name]
                            if callable(member) and not inspect.isclass(member):
                                targets.append(owner + '.' + name)
                    value = dict(adapter='bigqmt', read_only=True, targets=targets[:512],
                                 truncated=len(targets) > 512, allowed_calls=DEBUG_READS)
                else:
                    value = self._describe(target)
            else:
                owner, name, member = self._member(target)
                if name not in DEBUG_READS[owner]:
                    raise ValueError('native call is outside the read-only debug allowlist')
                phase = 'sdk_execution'
                value = member(*args, **kwargs)
            phase = 'serialization'
            return dict(status='ok', data=debug_value(value))
        except Exception as exc:
            logging.getLogger(__name__).exception('BigQMT debug failed during %s', phase)
            return dict(status='error', error=dict(type=type(exc).__name__, message=str(exc), phase=phase,
                        outcome='not_executed' if phase == 'pre_execution' else 'unknown'))
