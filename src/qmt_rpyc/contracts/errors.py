"""Typed operation failures; execution uncertainty is never inferred as rejection."""
class QmtError(Exception):
    def __init__(self, error):
        self.error = error
        self.error_type = error.error_type
        self.phase = error.phase
        self.outcome = error.outcome
        self.operation = error.operation
        super().__init__(error.message)


class OutcomeUnknownError(QmtError):
    pass


class ProtocolError(ValueError):
    pass


class BatchIncompleteError(Exception):
    def __init__(self, result):
        self.result = result
        super().__init__("one or more batch items failed")


class DownloadFailedError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(status.error.message)


class QmtAuthError(Exception):
    """Socket authentication failed before protocol negotiation."""


class NotConnectedError(ConnectionError):
    """The client has no open transport connection."""
