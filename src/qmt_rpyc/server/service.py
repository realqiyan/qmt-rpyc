import json
import logging
import os
import time
import threading

import rpyc

from qmt_rpyc.protocol import (
    EVENT_TYPES,
    STATUS_OK,
    STATUS_ERROR,
)
from qmt_rpyc.version import __version__
from qmt_rpyc.protocol import PROTOCOL_VERSION
from qmt_rpyc.server.event_bus import event_bus
from qmt_rpyc.server.download_manager import is_download_function
from qmt_rpyc.contract import CONTRACT_VERSION, ContractFailure, manifest
from qmt_rpyc.server.adapters import create_dispatcher

logger = logging.getLogger(__name__)

# ── argument / return-value summarizers for logging ──────────────────

def _summarize_value(v, max_str_len=80, max_items=3):
    """Compact one-line summary of a value, safe for log output."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        s = repr(v)
        if len(s) > max_str_len:
            s = s[:max_str_len] + "...'"
        return s
    if isinstance(v, (list, tuple)):
        n = len(v)
        if n == 0:
            return "[]" if isinstance(v, list) else "()"
        items = [_summarize_value(x, 30, 1) for x in v[:max_items]]
        suffix = f", +{n - max_items}" if n > max_items else ""
        inner = ", ".join(items) + suffix
        lo, hi = ("[", "]") if isinstance(v, list) else ("(", ")")
        return f"{lo}{inner}{hi}"
    if isinstance(v, dict):
        n = len(v)
        if n == 0:
            return "{}"
        keys = list(v.keys())[:max_items]
        inner = ", ".join(
            f"{_summarize_value(k, 20, 1)}: {_summarize_value(v[k], 30, 1)}"
            for k in keys)
        suffix = f", +{n - max_items}" if n > max_items else ""
        return f"{{{inner}{suffix}}}"
    if hasattr(v, "__dict__"):
        return f"<{type(v).__name__}>"
    return f"<{type(v).__name__}>"


def _summarize_args(args, kwargs, max_str_len=80, max_items=3):
    """Compact one-line summary of call arguments for logging."""
    parts = []
    for a in args:
        parts.append(_summarize_value(a, max_str_len, max_items))
    for k in sorted(kwargs.keys()):
        parts.append(f"{k}={_summarize_value(kwargs[k], max_str_len, max_items)}")
    return ", ".join(parts) if parts else "(none)"


def _summarize_rpc_result(result, max_str_len=120):
    """Compact one-line summary of an RPC result dict for logging."""
    if not isinstance(result, dict):
        return _summarize_value(result)
    status = result.get("status")
    if status == STATUS_ERROR:
        err_type = result.get("error_type", "?")
        err_msg = result.get("error_message", "")
        if len(err_msg) > max_str_len:
            err_msg = err_msg[:max_str_len] + "..."
        return f"ERR {err_type}: {err_msg}"
    data = result.get("data")
    return f"OK {_summarize_value(data)}"

# RPyC passes container types as netref proxies.  xtdata functions expect
# plain builtins.list / builtins.dict — netref proxies trip the type
# checking.  Materialize recursively before calling into xtquant.
_MATERIALIZE_MAX_DEPTH = 64


def _materialize(obj, _depth=0):
    if _depth > _MATERIALIZE_MAX_DEPTH:
        raise ValueError("argument nesting exceeds materialization depth limit")
    module = getattr(type(obj), "__module__", "")
    if module == "rpyc.core.netref":
        if isinstance(obj, list):
            return [_materialize(x, _depth + 1) for x in obj]
        if isinstance(obj, dict):
            return {k: _materialize(v, _depth + 1) for k, v in obj.items()}
        if isinstance(obj, tuple):
            return tuple(_materialize(x, _depth + 1) for x in obj)
    return obj

# ── batch call helpers ───────────────────────────────────────────────

_BATCH_MAX_CALLS = 500
_EVENT_POLL_MAX_COUNT = 1000


def _positive_env_int(name, default):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as e:
        raise ValueError(f"{name} must be an integer") from e
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    return value


_BATCH_MAX_WORKERS = _positive_env_int("QMT_BATCH_MAX_WORKERS", 8)


def _error(error_type, message):
    return {
        "status": STATUS_ERROR,
        "error_type": error_type,
        "error_message": message,
    }


def _execute_one(name, args, kwargs, dispatcher):
    return dispatcher.call('xtdata.' + name, args, kwargs)


try:
    from xtquant import xtdata
except ImportError:
    xtdata = None


class QmtAuthError(Exception):
    pass


class XtquantService(rpyc.Service):
    _require_auth = True
    _connection_mgr = None
    _download_mgr = None
    _api_surface = None
    _dispatcher = None
    _active_clients = 0
    _active_clients_lock = threading.Lock()

    @classmethod
    def get_service_name(cls):
        return "xtquant"

    def __init__(self):
        self._authenticated = False
        self._peer = None
        self._subscription_ids = set()

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
        for sub_id in list(self._subscription_ids):
            event_bus.unsubscribe(sub_id)
        self._subscription_ids.clear()

    def _require_authed(self):
        if self.__class__._require_auth and not self._authenticated:
            raise QmtAuthError("not authenticated")

    def _log_request(self, method, detail="", level=logging.INFO):
        """Log an incoming RPC request with peer info."""
        peer = "%s:%s" % self._peer if self._peer else "unknown"
        if detail:
            logger.log(level, "[%s] %s -- %s", peer, method, detail)
        else:
            logger.log(level, "[%s] %s", peer, method)

    @classmethod
    def _allowed_names(cls, surface, key):
        return manifest().get(surface, {}).get(key, {})

    def _contract(self):
        if self.__class__._dispatcher is None:
            self.__class__._dispatcher = create_dispatcher(self.__class__._connection_mgr)
        return self.__class__._dispatcher

    def exposed_get_api_surface(self, supported_contracts=None):
        self._require_authed()
        if supported_contracts is not None and CONTRACT_VERSION not in _materialize(supported_contracts):
            return ContractFailure('ContractVersion', '', 'no common bridge contract').response()
        return self._contract().surface()

    def _dispatch(self, surface, name, args, kwargs):
        api = surface + '.' + str(name)
        try:
            args = _materialize(args)
            kwargs = _materialize(kwargs)
            dispatcher = self._contract()
            parameters = dispatcher.prepare(api, args, kwargs)
            if surface == 'xtdata' and is_download_function(name):
                task_id = self.__class__._download_mgr.submit(
                    dispatcher.execute, function_name=name, _args=(api, parameters))
                return {'status': STATUS_OK, 'data': {
                    'task_id': task_id, '_is_download_task': True}}
            return {'status': STATUS_OK, 'data': dispatcher.execute(api, parameters)}
        except ContractFailure as e:
            return e.response()
        except Exception:
            logger.exception('Bridge dispatch failed for %s', api)
            return ContractFailure('BridgeError', api, 'bridge dispatch failed',
                                   'dispatch', 'unknown' if surface == 'trader' else 'not_applicable').response()

    def exposed_call_xtdata(self, name, args, kwargs):
        self._require_authed()
        result = self._dispatch('xtdata', name, args, kwargs)
        self._log_request('call_xtdata', '{} => {}'.format(name, _summarize_rpc_result(result)))
        return result

    def exposed_call_trader(self, name, args, kwargs):
        self._require_authed()
        result = self._dispatch('trader', name, args, kwargs)
        self._log_request('call_trader', '{} => {}'.format(name, _summarize_rpc_result(result)))
        return result

    def exposed_health(self):
        self._require_authed()
        self._log_request("health", level=logging.DEBUG)
        cm = self.__class__._connection_mgr
        if cm is None:
            result = {"connected": False, "trader_available": False}
        else:
            result = cm.get_health_status()
        with self.__class__._active_clients_lock:
            result["active_clients"] = self.__class__._active_clients
        dm = self.__class__._download_mgr
        result["download_tasks"] = dm.get_stats() if dm is not None else {}
        result["package_version"] = __version__
        result["protocol_version"] = PROTOCOL_VERSION
        result["contract_version"] = CONTRACT_VERSION
        result["capabilities"] = self._contract().surface()["capabilities"]
        return result

    def exposed_query_download(self, task_id):
        self._require_authed()
        self._log_request("query_download", "task_id=%s" % task_id)
        task = self.__class__._download_mgr.get_task(task_id)
        if task is None:
            return {"status": STATUS_ERROR, "error_type": "KeyError",
                    "error_message": f"task {task_id!r} not found"}
        return {"status": STATUS_OK, "data": task}

    def exposed_batch_call_xtdata(self, name, calls):
        result = self._batch_call_xtdata(name, calls)
        if result.get('status') == STATUS_ERROR:
            return {**ContractFailure(result['error_type'], 'xtdata.' + str(name),
                                      result['error_message']).response(), **result}
        return result

    def _batch_call_xtdata(self, name, calls):
        self._require_authed()

        # ── deserialise calls (JSON string from new clients,
        #     netref list from old clients) ───────────────────────
        if isinstance(calls, str):
            try:
                calls = json.loads(calls)
            except (TypeError, ValueError) as e:
                return _error("InvalidBatch", f"invalid calls JSON: {e}")

        if not isinstance(calls, (list, tuple)):
            return _error(
                "TypeError",
                f"calls must be a list or tuple, got {type(calls).__name__}",
            )

        allowed = self._allowed_names("xtdata", "functions")
        if name not in allowed:
            return _error(
                "UnknownAPI",
                f"xtdata function {name!r} is not in the API allowlist",
            )

        try:
            arg_str = f"fn={name}, calls={len(calls)}"
        except Exception:
            arg_str = "<summarize failed>"
        self._log_request("batch_call_xtdata", arg_str)

        # ── validation ──────────────────────────────────────────
        if len(calls) > _BATCH_MAX_CALLS:
            return {
                "status": STATUS_ERROR,
                "error_type": "BatchTooLarge",
                "error_message": (
                    f"max {_BATCH_MAX_CALLS} calls per batch, got {len(calls)}"
                ),
            }
        if is_download_function(name):
            return {
                "status": STATUS_ERROR,
                "error_type": "BatchRejected",
                "error_message": (
                    f"'{name}' is a download function; use call_xtdata"
                ),
            }
        # Reject downloads even when the batch is empty. All other contracted
        # xtdata methods accept an empty batch as a no-op.
        if not calls:
            self._log_request("batch_call_xtdata", f"fn={name}, calls=0")
            return {"status": STATUS_OK, "results": []}
        # ── structural validation ──────────────────────────────────
        for i, call in enumerate(calls):
            if not (isinstance(call, (list, tuple)) and len(call) == 2):
                return {
                    "status": STATUS_ERROR,
                    "error_type": "TypeError",
                    "error_message": (
                        f"calls[{i}] must be (args, kwargs), "
                        f"got {type(call).__name__}"
                    ),
                }
            args, kwargs = call
            if not isinstance(args, (list, tuple)):
                return _error(
                    "TypeError",
                    f"calls[{i}].args must be a list or tuple, "
                    f"got {type(args).__name__}",
                )
            if not isinstance(kwargs, dict):
                return _error(
                    "TypeError",
                    f"calls[{i}].kwargs must be a dict, "
                    f"got {type(kwargs).__name__}",
                )

        # ── concurrent execution ─────────────────────────────────
        max_workers = min(len(calls), _BATCH_MAX_WORKERS)
        results = [None] * len(calls)
        _t_total = time.time()

        # Materialize args before submitting to executor.
        try:
            materialized_calls = [
                ([_materialize(a) for a in args],
                 {k: _materialize(v) for k, v in kwargs.items()})
                for args, kwargs in calls
            ]
        except Exception as e:
            return _error(type(e).__name__, str(e))
        _t_mat = time.time()

        from concurrent.futures import ThreadPoolExecutor, as_completed

        _t_setup = time.time()
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(_execute_one, name, args, kwargs, self._contract()): i
                for i, (args, kwargs) in enumerate(materialized_calls)
            }
            _t_submit = time.time()
            first_result = True
            for f in as_completed(futures):
                if first_result:
                    _t_first = time.time()
                    first_result = False
                i = futures[f]
                try:
                    results[i] = f.result()
                except Exception as e:
                    results[i] = {
                        "status": STATUS_ERROR,
                        "error_type": type(e).__name__,
                        "error_message": str(e),
                    }
            _t_last = time.time()

        # ── timing log ───────────────────────────────────────────
        _n = len(calls)
        logger.info(
            "batch timing: ThreadPool workers=%d calls=%d | "
            "materialize=%.0fms setup=%.0fms submit=%.0fms "
            "first_result=%.0fms total=%.0fms",
            max_workers, _n,
            (_t_mat - _t_total) * 1000,
            (_t_setup - _t_mat) * 1000,
            (_t_submit - _t_setup) * 1000,
            (_t_first - _t_total) * 1000,
            (_t_last - _t_total) * 1000,
        )

        # ── summary log ──────────────────────────────────────────
        ok_count = sum(1 for r in results if r.get("status") == STATUS_OK)
        err_count = len(results) - ok_count
        self._log_request(
            "batch_call_xtdata",
            f"fn={name}, calls={len(calls)}, ok={ok_count}, err={err_count}",
        )

        return {"status": STATUS_OK, "results": results}
