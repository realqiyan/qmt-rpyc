

def _sequence(values):
    if isinstance(values, (str, bytes)):
        raise ValueError("expected a sequence of identities, not a string")
    return tuple(values)


class _API:
    def __init__(self, client):
        self._client = client

    def _call(self, operation, request):
        return self._client._invoke(operation, request)
