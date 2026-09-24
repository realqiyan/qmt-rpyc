class QmtError(Exception):
    def __init__(self, error_type, message, *, api=None, contract_version=None,
                 phase=None, outcome=None):
        self.error_type = error_type
        self.api = api
        self.contract_version = contract_version
        self.phase = phase
        self.outcome = outcome
        super().__init__(f"[{error_type}] {message}")

    @classmethod
    def from_response(cls, response):
        """Materialize a per-item batch error using the same mapping as calls."""
        return _map_error(response)


class NotConnectedError(QmtError):
    pass


class RemoteCallError(QmtError):
    pass


class QmtAuthError(QmtError):
    pass


class ContractError(RemoteCallError):
    """Unavailable API, invalid input/output, or incompatible wire contract."""


class OutcomeUnknownError(RemoteCallError):
    """An order/cancellation may have reached the SDK. Never retry blindly."""


def _map_error(resp):
    etype = resp.get("error_type", "Unknown")
    msg = resp.get("error_message", "")
    cls = RemoteCallError
    if resp.get("outcome") == "unknown":
        cls = OutcomeUnknownError
    elif etype == "NotConnected":
        cls = NotConnectedError
    elif etype in ("UnknownAPI", "InvalidArguments", "InvalidResult", "APIUnavailable", "ContractVersion"):
        cls = ContractError
    return cls(etype, msg, **{k: resp.get(k) for k in
               ("api", "contract_version", "phase", "outcome")})
