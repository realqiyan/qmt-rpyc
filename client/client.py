import os
import secrets
import time
import threading
import logging

import rpyc

from common.protocol import make_auth_token
from client.proxy import _RemoteModule, _RemoteTrader, DownloadTaskHandle
from client.exceptions import QmtAuthError, _map_error

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
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)

    def _loop(self):
        while not self._stop.wait(self._interval):
            try:
                events, dropped = self._conn.root.poll_events(self._sub_id, 100)
                if dropped > 0:
                    logger.warning("Event subscription %s dropped %d events",
                                   self._sub_id, dropped)
                if self._on_event:
                    for event in events:
                        self._on_event(event)
            except Exception as e:
                logger.error("Event poll failed: %s", e)


class QmtClient:
    def __init__(self):
        self._conn = None
        self._surface = None
        self._xtdata = None
        self._trader = None
        self._xtconstant = None
        self._pollers = {}

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
        client = cls()
        client._conn = conn
        client._authenticate(auth_key)
        client._init_surface()
        return client

    def _authenticate(self, auth_key):
        if auth_key is None:
            return
        nonce = secrets.token_hex(8)
        timestamp = int(time.time())
        token = make_auth_token(auth_key, nonce, timestamp)
        ok = self._conn.root.authenticate(nonce, timestamp, token)
        if not ok:
            self._conn.close()
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
        if surface == "xtdata":
            resp = self._conn.root.call_xtdata(name, list(args), dict(kwargs))
        elif surface == "trader":
            resp = self._conn.root.call_trader(name, list(args), dict(kwargs))
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
        resp = self._conn.root.batch_call_xtdata(name, calls_json)
        resp = rpyc.classic.obtain(resp)
        if resp.get("status") != "ok":
            raise _map_error(resp)
        return resp["results"]

    def health(self):
        return rpyc.classic.obtain(self._conn.root.health())

    def subscribe(self, event_types, account_id=None, on_event=None, poll_interval=1.0):
        sub_id = self._conn.root.subscribe_event(list(event_types), account_id)
        poller = _EventPoller(self._conn, sub_id, on_event, poll_interval)
        if on_event is not None:
            poller.start()
        self._pollers[sub_id] = poller
        return sub_id

    def unsubscribe(self, sub_id):
        poller = self._pollers.pop(sub_id, None)
        if poller:
            poller.stop()
        self._conn.root.unsubscribe_event(sub_id)

    def drain_events(self, sub_id, max_count=100):
        return rpyc.classic.obtain(self._conn.root.poll_events(sub_id, max_count))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        for poller in self._pollers.values():
            poller.stop()
        self._pollers.clear()
        if self._conn:
            self._conn.close()

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
