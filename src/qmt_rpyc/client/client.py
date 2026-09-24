"""Public client composed from typed capability groups and a transport."""
import logging

from qmt_rpyc.contracts.errors import (
    NotConnectedError,
    ProtocolError,
)
from qmt_rpyc.contracts.operations import CONTRACT_VERSION
from qmt_rpyc.contracts.system import Capabilities, Health
from qmt_rpyc.transport.messages import invoke, negotiate
from qmt_rpyc.transport.rpyc import RpycTransport

from .downloads import DownloadsAPI
from .financials import FinancialsAPI
from .instruments import InstrumentsAPI
from .market import MarketAPI
from .options import OptionsAPI
from .reference import ReferenceAPI
from .system import SystemAPI
from .trading import TradingAPI

logger = logging.getLogger(__name__)


class QmtClient:
    def __init__(self):
        self._conn = None
        self._closed = False
        self.options = OptionsAPI(self)
        self.instruments = InstrumentsAPI(self)
        self.market = MarketAPI(self)
        self.reference = ReferenceAPI(self)
        self.financials = FinancialsAPI(self)
        self.downloads = DownloadsAPI(self)
        self.trading = TradingAPI(self)
        self.system = SystemAPI(self)
        self._capabilities = None

    @property
    def contract_version(self):
        return CONTRACT_VERSION

    def _negotiate(self):
        self._capabilities = negotiate(self._conn)

    def capabilities(self) -> Capabilities:
        """Startup compatibility snapshot; not live connectivity."""
        if self._capabilities is None:
            raise ProtocolError("has not negotiated a contract")
        return self._capabilities

    def health(self) -> Health:
        return self.system.get_health()

    def _invoke(self, operation, request):
        return invoke(self._ensure_connected(), operation, request)

    @classmethod
    def connect(cls, host, port=18812, auth_key=None, timeout=30, tls_config=None, **protocol_config):
        client = cls()
        client._conn = RpycTransport.connect(host, port, auth_key, timeout, tls_config, **protocol_config)
        try:
            client._negotiate()
        except Exception:
            client.close()
            raise
        return client

    @classmethod
    def connect_profile(cls, name="default", **overrides):
        from qmt_rpyc.config import resolve_profile

        profile = resolve_profile(name, overrides)
        tls_config = {
            key: profile.get(key)
            for key in ("ca_certs", "certfile", "keyfile")
            if profile.get(key)
        }
        return cls.connect(
            profile["host"],
            port=profile["port"],
            auth_key=profile.get("auth_key"),
            timeout=profile["timeout"],
            tls_config=tls_config or None,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        conn = self._conn
        self._conn = None
        if conn:
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close RPyC connection", exc_info=True)

    def _ensure_connected(self):
        if self._closed or self._conn is None:
            raise NotConnectedError("client is closed")
        return self._conn

    def self_test(self, test_symbols=None, timeout=30):
        from .diagnostics import run_self_test
        return run_self_test(self, test_symbols, timeout)
