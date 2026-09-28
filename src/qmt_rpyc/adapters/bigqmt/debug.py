"""Authenticated server-side diagnostics; public business contract unchanged."""
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.transport.codec import dumps, loads


class DebugGateway:
    def __init__(self, connection):
        self.connection = connection

    def __call__(self, payload):
        phase = 'pre_execution'
        try:
            if type(payload) is not str or len(payload.encode('utf-8')) > 48 * 1024:
                raise ValueError('debug request must be a JSON string of at most 48 KiB')
            request = loads(payload)
            if type(request) is not dict or set(request) - {'action', 'target', 'args', 'kwargs'}:
                raise ValueError('invalid debug request')
            target, action = request.get('target'), request.get('action')
            if type(target) is str and target.startswith('bridge.'):
                names = ('bridge.health', 'bridge.transport', 'bridge.cache_info', 'bridge.clear_cache')
                if target not in names or action not in ('describe', 'call') or request.get('args') or request.get('kwargs'):
                    raise ValueError('bridge diagnostics take no arguments')
                if action == 'describe':
                    data = dict(target=target, callable=True, signature='()', call_allowed=True)
                elif target == 'bridge.health':
                    data = self.connection.get_health_status()
                elif target == 'bridge.transport':
                    data = self.connection.transport.diagnostics()
                else:
                    if target == 'bridge.clear_cache':
                        self.connection.discovery_cache.clear()
                    data = self.connection.discovery_cache.info()
                    if hasattr(self.connection, 'underlying_cache'):
                        data['underlyings'] = self.connection.underlying_cache.info()
                return dumps(dict(status='ok', data=data))
            phase = 'sdk_execution'
            result = self.connection.transport.request('debug', {'request': request})
            if action == 'describe' and target is None and result.get('status') == 'ok':
                result['data']['bridge_targets'] = ['bridge.health', 'bridge.transport', 'bridge.cache_info', 'bridge.clear_cache']
            return dumps(result)
        except Exception as exc:
            if isinstance(exc, ProviderError):
                phase = exc.phase
            return dumps(dict(status='error', error=dict(type=type(exc).__name__, message=str(exc),
                phase=phase, outcome='not_executed' if phase == 'pre_execution' else 'unknown')))
