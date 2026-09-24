"""Authenticated JSON RPC entry point."""
import logging
import threading

import rpyc

from qmt_rpyc.contracts.errors import QmtAuthError
from qmt_rpyc.contracts.operations import CONTRACT_HASH

logger = logging.getLogger(__name__)

class XtquantService(rpyc.Service):
    _require_auth = True
    _connection_mgr = None
    _download_mgr = None
    _dispatcher = None
    _debug_handler = None
    _active_clients = 0
    _active_clients_lock = threading.Lock()

    @classmethod
    def get_service_name(cls):
        return 'qmt'

    def __init__(self):
        self._authenticated = False
        self._negotiated = False
        self._peer = None

    def on_connect(self, conn):
        credentials = conn._config.get("credentials") or {}
        self._authenticated = (
            not self.__class__._require_auth
            or credentials.get("authenticated") is True
        )
        try:
            self._peer = conn._channel.stream.sock.getpeername()
        except Exception:
            self._peer = ("unknown", 0)
        with self.__class__._active_clients_lock:
            self.__class__._active_clients += 1
        logger.info("Client connected from %s", self._peer)

    def on_disconnect(self, conn):
        logger.info("Client %s disconnected", self._peer)
        with self.__class__._active_clients_lock:
            self.__class__._active_clients = max(
                0, self.__class__._active_clients - 1
            )


    def _require_authed(self):
        if self.__class__._require_auth and not self._authenticated:
            raise QmtAuthError("not authenticated")

    def exposed_negotiate(self, contract_hash):
        self._require_authed()
        self._negotiated = type(contract_hash) is str and contract_hash == CONTRACT_HASH
        return self.__class__._dispatcher.negotiate(contract_hash)

    def exposed_call(self, payload):
        self._require_authed()
        dispatcher = self.__class__._dispatcher
        if not self._negotiated:
            return dispatcher.error('', '', 'CONTRACT_MISMATCH', 'negotiate before invoking operations',
                                    'pre_execution', 'not_executed')
        return dispatcher.call(payload)

    def exposed_debug(self, payload):
        self._require_authed()
        if self.__class__._debug_handler is None:
            from qmt_rpyc.transport.codec import dumps
            return dumps({'status': 'error', 'error': {
                'type': 'DEBUG_DISABLED', 'message': 'set QMT_RPYC_DEBUG=1 on the server and restart',
                'phase': 'pre_execution', 'outcome': 'not_executed'}})
        return self.__class__._debug_handler(payload)
