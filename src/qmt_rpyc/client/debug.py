"""Raw SDK diagnostic client, independent of business contract negotiation."""
from qmt_rpyc.transport.codec import dumps, loads
from qmt_rpyc.transport.rpyc import RpycTransport


class DebugError(RuntimeError):
    def __init__(self, error):
        self.error = error
        self.phase = error['phase']
        self.outcome = error['outcome']
        super().__init__(error['message'])


class DebugClient:
    """Experimental API: SDK arguments and results have no stability guarantee."""
    def __init__(self, transport):
        self._transport = transport

    @classmethod
    def connect(cls, host, port=18812, auth_key=None, timeout=30, tls_config=None):
        return cls(RpycTransport.connect(host, port, auth_key, timeout, tls_config))

    @classmethod
    def connect_profile(cls, name='default', **overrides):
        from qmt_rpyc.config import resolve_profile
        profile = resolve_profile(name, overrides)
        tls = {key: profile[key] for key in ('ca_certs', 'certfile', 'keyfile') if profile.get(key)}
        return cls.connect(profile['host'], profile['port'], profile.get('auth_key'),
                           profile['timeout'], tls or None)

    def describe(self, target=None):
        """Inspect the complete deployment, a method signature or a constant."""
        return self._request({'action': 'describe', 'target': target})

    def call(self, target, args=(), kwargs=None):
        """Forward JSON-compatible arguments once, without binding SDK defaults."""
        if not isinstance(args, (list, tuple)) or (kwargs is not None and type(kwargs) is not dict):
            raise ValueError('args must be a list/tuple; kwargs must be a dict')
        return self._request({'action': 'call', 'target': target,
                              'args': list(args), 'kwargs': {} if kwargs is None else kwargs})

    def _request(self, request):
        payload = dumps(request)  # Encoding failures occur before dispatch.
        root = self._transport.root
        try:
            response = loads(root.debug(payload))
            if type(response) is not dict:
                raise ValueError('invalid debug response')
            if response.get('status') == 'ok' and set(response) == {'status', 'data'}:
                return response['data']
            if response.get('status') == 'error' and set(response) == {'status', 'error'}:
                error = response['error']
                if (type(error) is dict and set(error) == {'type', 'message', 'phase', 'outcome'}
                        and all(type(value) is str for value in error.values())
                        and error['outcome'] in ('not_executed', 'unknown')):
                    raise DebugError(error)
            raise ValueError('invalid debug response envelope')
        except DebugError:
            raise
        except Exception as exc:
            # Any raw function may have side effects. No name-based retry guessing.
            raise DebugError({'type': 'TRANSPORT_ERROR', 'message': 'debug outcome unknown; do not automatically retry',
                              'phase': 'transport', 'outcome': 'unknown'}) from exc

    def close(self):
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
