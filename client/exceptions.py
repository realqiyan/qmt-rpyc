class QmtError(Exception):
    def __init__(self, error_type, message):
        self.error_type = error_type
        super().__init__(f"[{error_type}] {message}")


class NotConnectedError(QmtError):
    pass


class RemoteCallError(QmtError):
    pass


class QmtAuthError(QmtError):
    pass


def _map_error(resp):
    etype = resp.get("error_type", "Unknown")
    msg = resp.get("error_message", "")
    if etype == "NotConnected":
        return NotConnectedError(etype, msg)
    return RemoteCallError(etype, msg)
