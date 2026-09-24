import os
import socket
import logging

import rpyc
from rpyc.core.stream import SocketStream

from qmt_rpyc.protocol import (
    API_SURFACE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
    SocketAuthError,
    authenticate_client_socket,
)
from qmt_rpyc.proxy import _RemoteModule, _RemoteTrader, DownloadTaskHandle
from qmt_rpyc.exceptions import NotConnectedError, QmtAuthError, _map_error
from qmt_rpyc.contract import (
    CONTRACT_VERSION, CONTRACT_HASH, SUPPORTED_CONTRACTS, ContractFailure,
    bind, manifest, method_spec,
)

logger = logging.getLogger(__name__)


class QmtClient:
    def __init__(self):
        self._conn = None
        self._surface = None
        self._xtdata = None
        self._trader = None
        self._xtconstant = None
        self._closed = False

    @classmethod
    def connect(cls, host, port=18812, auth_key=None,
                timeout=30, tls_config=None, **protocol_config):
        config = {
            "allow_public_attrs": True,
            "allow_pickle": True,
            "sync_request_timeout": timeout,
            **protocol_config,
        }
        sock = None
        conn = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            if tls_config:
                import ssl
                context = ssl.create_default_context(
                    cafile=tls_config.get("ca_certs"))
                if tls_config.get("certfile"):
                    context.load_cert_chain(
                        certfile=tls_config["certfile"],
                        keyfile=tls_config.get("keyfile"))
                sock = context.wrap_socket(sock, server_hostname=host)
            if auth_key is not None:
                authenticate_client_socket(sock, auth_key)
            conn = rpyc.connect_stream(SocketStream(sock), config=config)
            sock = None
            client = cls()
            client._conn = conn
            client._init_surface()
            return client
        except SocketAuthError as e:
            if conn is not None:
                conn.close()
            elif sock is not None:
                sock.close()
            raise QmtAuthError("Auth", str(e)) from e
        except Exception:
            try:
                if conn is not None:
                    conn.close()
                elif sock is not None:
                    sock.close()
            except Exception:
                logger.warning(
                    "Failed to close RPyC connection after connect error",
                    exc_info=True,
                )
            raise

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

    def _init_surface(self):
        try:
            self._surface = rpyc.classic.obtain(
                self._conn.root.get_api_surface(list(SUPPORTED_CONTRACTS)))
        except (AttributeError, TypeError) as e:
            raise _map_error(ContractFailure(
                "ContractVersion", "", "server lacks contract negotiation; upgrade the server").response()) from e
        if self._surface.get("status") == "error":
            raise _map_error(self._surface)
        protocol_version = self._surface.get("protocol_version")
        if protocol_version != PROTOCOL_VERSION:
            raise QmtAuthError(
                "ProtocolVersion",
                "protocol mismatch: client={}, server={}".format(
                    PROTOCOL_VERSION, protocol_version
                ),
            )
        schema_version = self._surface.get("schema_version")
        if schema_version != API_SURFACE_SCHEMA_VERSION:
            raise QmtAuthError(
                "SchemaVersion",
                "API surface schema mismatch: client={}, server={}".format(
                    API_SURFACE_SCHEMA_VERSION, schema_version
                ),
            )
        if (self._surface.get("contract_version") != CONTRACT_VERSION
                or self._surface.get("contract_hash") != CONTRACT_HASH):
            raise _map_error(ContractFailure(
                "ContractVersion", "", "server does not implement this client's V1 contract").response())
        fixed = manifest()
        self._xtdata = _RemoteModule(self, "xtdata", fixed["xtdata"])
        self._trader = _RemoteTrader(self, fixed["XtQuantTrader"])
        self._xtconstant = _RemoteModule(self, "xtconstant", fixed["xtconstant"])

    @property
    def contract_version(self):
        return CONTRACT_VERSION

    def capabilities(self):
        """A local snapshot of startup compatibility; health() includes live status."""
        import copy
        return copy.deepcopy((self._surface or {}).get("capabilities", {}))

    @property
    def xtdata(self):
        return self._xtdata

    @property
    def trader(self):
        return self._trader

    @property
    def xtconstant(self):
        return self._xtconstant

    def _call(self, surface, name, args, kwargs):
        conn = self._ensure_connected()
        api = surface + "." + name
        try:
            bind(api, args, kwargs)
        except ContractFailure as e:
            raise _map_error(e.response()) from e
        try:
            if surface == "xtdata":
                resp = conn.root.call_xtdata(name, list(args), dict(kwargs))
            elif surface == "trader":
                resp = conn.root.call_trader(name, list(args), dict(kwargs))
            else:
                raise ValueError(f"unknown surface: {surface}")
            resp = rpyc.classic.obtain(resp)
        except Exception as e:
            if method_spec(api)["mutation"]:
                raise _map_error(ContractFailure(
                    "TransportError", api, "request outcome unknown; reconcile before retrying",
                    "transport", "unknown").response()) from e
            raise

        # Materialize netref proxy → local Python objects.
        # Without this, every dict/list access in user code triggers a hidden
        # RPC back to the server, making remote use unusably slow and breaking
        # json.dumps / pickle / isinstance checks.
        status = resp.get("status")
        if status == "ok":
            data = resp["data"]
            if isinstance(data, dict) and data.get("_is_download_task"):
                return DownloadTaskHandle(self, data["task_id"])
            return data
        raise _map_error(resp)

    def _batch_call(self, surface, name, calls):
        """Dispatch a batch call to the server.

        Args:
            surface: must be "xtdata" (trader batch not supported)
            name: xtdata function name
            calls: list of (args, kwargs) tuples

        Returns:
            list of result dicts, same order as calls

        Raises:
            ValueError: if surface is not "xtdata"
            QmtError: if the overall batch dispatch fails
        """
        if surface != "xtdata":
            raise ValueError(
                f"batch_call only supports xtdata, got {surface}")
        # Serialize calls as JSON to avoid RPyC netref round-trips
        # during server-side materialization.  A nested list of
        # (args, kwargs) tuples triggers ~4 reverse RPCs per call
        # element; for 134 calls that adds 29s on a LAN connection.
        import json
        calls_json = json.dumps(calls, ensure_ascii=False)
        conn = self._ensure_connected()
        resp = conn.root.batch_call_xtdata(name, calls_json)
        resp = rpyc.classic.obtain(resp)
        if resp.get("status") != "ok":
            raise _map_error(resp)
        return resp["results"]

    def health(self):
        conn = self._ensure_connected()
        return rpyc.classic.obtain(conn.root.health())

    def query_download(self, task_id):
        conn = self._ensure_connected()
        result = rpyc.classic.obtain(conn.root.query_download(task_id))
        if result.get("status") != "ok":
            raise _map_error(result)
        return result["data"]

    def download_handle(self, task_id):
        return DownloadTaskHandle(self, task_id)

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
            raise NotConnectedError("NotConnected", "client is closed")
        return self._conn

    def self_test(self, test_symbols=None, timeout=30.0):
        """Run a self-test against all read-only query interfaces.

        Prints real-time ✓/✗/○ results to stdout, then returns a
        structured report dict.

        Args:
            test_symbols: Optional dict overriding default test symbols.
                Keys: sh_stock, sz_stock, etf, sector, market, account_id.
            timeout: Reserved for future use.

        Returns:
            dict with keys: total, passed, failed, skipped,
            duration_seconds, results (list of per-test dicts).
        """
        from qmt_rpyc.self_test import run_self_test
        return run_self_test(self, test_symbols=test_symbols,
                             timeout=timeout)
