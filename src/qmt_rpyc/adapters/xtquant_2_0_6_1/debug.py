"""Opt-in SDK diagnostics, deliberately outside the business contract."""
import inspect
import logging

from qmt_rpyc.transport.codec import dumps, loads

from .discovery import build_api_surface
from .serializer import serialize

logger = logging.getLogger(__name__)
MAX_REQUEST_BYTES = 1024 * 1024


class DebugGateway:
    def __init__(self, connection):
        from xtquant import xtconstant, xtdata
        from xtquant.xttrader import XtQuantTrader
        self.owners = {'xtdata': xtdata, 'trader': XtQuantTrader, 'xtconstant': xtconstant}
        self.connection = connection

    def __call__(self, payload):
        phase = 'pre_execution'
        target = ''
        try:
            if type(payload) is not str or len(payload.encode('utf-8')) > MAX_REQUEST_BYTES:
                raise ValueError('debug request must be a JSON string of at most 1 MiB')
            request = loads(payload)
            if type(request) is not dict or set(request) - {'action', 'target', 'args', 'kwargs'}:
                raise ValueError('expected action, target, args and kwargs')
            action = request.get('action')
            if action not in ('describe', 'call'):
                raise ValueError('action must be describe or call')
            target = request.get('target')
            args, kwargs = request.get('args', []), request.get('kwargs', {})
            if type(args) is not list or type(kwargs) is not dict:
                raise ValueError('args must be an array; kwargs must be an object')
            if action == 'describe' and (args or kwargs):
                raise ValueError('describe does not accept arguments')
            if action == 'describe' and target is None:
                return dumps({'status': 'ok', 'data': build_api_surface()})
            if type(target) is not str or len(target.split('.')) != 2:
                raise ValueError('target must be xtdata.NAME, trader.NAME or xtconstant.NAME')
            group, name = target.split('.')
            if group not in self.owners or not name.isidentifier() or name.startswith('_'):
                raise ValueError('only direct public SDK members are supported')
            member = getattr(self.owners[group], name)
            if action == 'describe':
                try:
                    signature = str(inspect.signature(member)) if callable(member) else None
                except (TypeError, ValueError):
                    signature = None
                return dumps({'status': 'ok', 'data': {
                    'target': target, 'callable': callable(member), 'signature': signature,
                    'doc': inspect.getdoc(member) or '',
                    'value': serialize(member) if group == 'xtconstant' else None}})
            if group == 'xtconstant' or not callable(member) or inspect.isclass(member):
                raise ValueError('call requires an SDK function or Trader method; use describe for constants')
            logger.info('SDK debug call %s', target)  # Arguments may contain account data.
            phase = 'sdk_execution'
            if group == 'trader':
                # Retain native Trader lock, connection check and StockAccount conversion.
                result = self.connection.call_trader_method(name, args, kwargs)
                if result['status'] != 'ok':
                    return dumps({'status': 'error', 'error': {
                        'type': result['error_type'], 'message': result['error_message'],
                        'phase': result['phase'], 'outcome': result['outcome']}})
                value = result['data']
            else:
                value = member(*args, **kwargs)
            phase = 'serialization'
            return dumps({'status': 'ok', 'data': serialize(value)})
        except Exception as exc:
            logger.exception('SDK debug %s failed during %s', target, phase)
            return dumps({'status': 'error', 'error': {
                'type': type(exc).__name__, 'message': str(exc), 'phase': phase,
                'outcome': 'not_executed' if phase == 'pre_execution' else 'unknown'}})
