import os
import secrets
import time
import threading
import logging

import rpyc

from common.protocol import make_auth_token
from client.proxy import _RemoteModule, _RemoteTrader, DownloadTaskHandle
from client.exceptions import NotConnectedError, QmtAuthError, _map_error

logger = logging.getLogger(__name__)


class _EventPoller:
    def __init__(self, conn, sub_id, on_event, interval):
        self._conn = conn
        self._sub_id = sub_id
        self._on_event = on_event
        self._interval = interval
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(
            target=self._loop, daemon=True,
            name=f"event-poll-{self._sub_id}")
        self._thread.start()

    def stop(self):
        self.request_stop()
        if self._thread:
            self._thread.join(timeout=2)

    def request_stop(self):
        self._stop.set()

    def _loop(self):
        delay = self._interval
        failures = 0
        while not self._stop.wait(delay):
            try:
                events, dropped = rpyc.classic.obtain(
                    self._conn.root.poll_events(self._sub_id, 100))
                failures = 0
                delay = self._interval
                if dropped > 0:
                    logger.warning("Event subscription %s dropped %d events",
                                   self._sub_id, dropped)
                if self._on_event:
                    for event in events:
                        try:
                            self._on_event(event)
                        except Exception:
                            logger.exception(
                                "Event callback failed for subscription %s",
                                self._sub_id,
                            )
            except Exception:
                if self._stop.is_set():
                    return
                failures += 1
                delay = min(
                    max(self._interval, 0.1) * (2 ** min(failures, 8)),
                    30.0,
                )
                logger.warning(
                    "Event poll failed for subscription %s; retrying in %.1fs",
                    self._sub_id,
                    delay,
                    exc_info=True,
                )


class QmtClient:
    def __init__(self):
        self._conn = None
        self._surface = None
        self._xtdata = None
        self._trader = None
        self._xtconstant = None
        self._pollers = {}
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
        if tls_config:
            import ssl
            config["credentials"] = ssl.create_default_context(
                cafile=tls_config.get("ca_certs"))
            if tls_config.get("certfile"):
                config["credentials"].load_cert_chain(
                    certfile=tls_config["certfile"],
                    keyfile=tls_config.get("keyfile"))

        conn = rpyc.connect(host, port, config=config)
        try:
            client = cls()
            client._conn = conn
            client._authenticate(auth_key)
            client._init_surface()
            return client
        except Exception:
            try:
                conn.close()
            except Exception:
                logger.warning(
                    "Failed to close RPyC connection after connect error",
                    exc_info=True,
                )
            raise

    def _authenticate(self, auth_key):
        if auth_key is None:
            return
        nonce = secrets.token_hex(8)
        timestamp = int(time.time())
        token = make_auth_token(auth_key, nonce, timestamp)
        ok = self._conn.root.authenticate(nonce, timestamp, token)
        if not ok:
            raise QmtAuthError("Auth", "authentication failed")

    def _init_surface(self):
        self._surface = rpyc.classic.obtain(self._conn.root.get_api_surface())
        self._xtdata = _RemoteModule(self, "xtdata", self._surface.get("xtdata", {}))
        self._trader = _RemoteTrader(self, self._surface.get("XtQuantTrader", {}))
        self._xtconstant = _RemoteModule(self, "xtconstant", self._surface.get("xtconstant", {}))

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
        if surface == "xtdata":
            resp = conn.root.call_xtdata(name, list(args), dict(kwargs))
        elif surface == "trader":
            resp = conn.root.call_trader(name, list(args), dict(kwargs))
        else:
            raise ValueError(f"unknown surface: {surface}")

        # Materialize netref proxy → local Python objects.
        # Without this, every dict/list access in user code triggers a hidden
        # RPC back to the server, making remote use unusably slow and breaking
        # json.dumps / pickle / isinstance checks.
        resp = rpyc.classic.obtain(resp)

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

    def subscribe(self, event_types, account_id=None, on_event=None, poll_interval=1.0):
        if poll_interval <= 0:
            raise ValueError("poll_interval must be greater than 0")
        conn = self._ensure_connected()
        sub_id = conn.root.subscribe_event(list(event_types), account_id)
        poller = _EventPoller(conn, sub_id, on_event, poll_interval)
        if on_event is not None:
            poller.start()
        self._pollers[sub_id] = poller
        return sub_id

    def unsubscribe(self, sub_id):
        poller = self._pollers.pop(sub_id, None)
        if poller:
            poller.stop()
        conn = self._ensure_connected()
        conn.root.unsubscribe_event(sub_id)

    def drain_events(self, sub_id, max_count=100):
        conn = self._ensure_connected()
        return rpyc.classic.obtain(conn.root.poll_events(sub_id, max_count))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        pollers = list(self._pollers.values())
        for poller in pollers:
            poller.request_stop()
        self._pollers.clear()
        conn = self._conn
        self._conn = None
        if conn:
            try:
                conn.close()
            except Exception:
                logger.warning("Failed to close RPyC connection", exc_info=True)
        for poller in pollers:
            poller.stop()

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
        from client.self_test import run_self_test
        return run_self_test(self, test_symbols=test_symbols,
                             timeout=timeout)
