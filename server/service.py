import logging
import os
import time
import threading

import rpyc

from common.protocol import verify_auth_token, STATUS_OK, STATUS_ERROR
from server.auth_limiter import rate_limiter
from server.event_bus import event_bus
from server.download_manager import is_download_function
from server.serializer import serialize
from server.batch_worker import execute_one

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

# RPyC passes container types as netref proxies.  pybind11 (xtquant's C++
# layer) only accepts plain builtins.list / builtins.dict — netref proxies
# trip its strict type checking.  Materialize recursively before calling
# into xtquant.
_MATERIALIZE_MAX_DEPTH = 64


def _materialize(obj, _depth=0):
    if _depth > _MATERIALIZE_MAX_DEPTH:
        return obj
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
_BATCH_MAX_WORKERS = int(os.environ.get("QMT_BATCH_MAX_WORKERS", "8"))


try:
    from xtquant import xtdata
except ImportError:
    xtdata = None

# Auto-detect whether xtdata is the real pybind11 module or a test mock.
# Real modules have __file__; mock instances (e.g. _XtData()) do not.
# ProcessPoolExecutor only makes sense for real pybind11 (GIL-bound) code.
_USE_PROCESS_POOL = (
    xtdata is not None
    and hasattr(xtdata, "__file__")
    and os.environ.get("QMT_BATCH_EXECUTOR", "process") == "process"
)


class QmtAuthError(Exception):
    pass


class XtquantService(rpyc.Service):
    _auth_key = None
    _require_auth = True
    _connection_mgr = None
    _download_mgr = None
    _api_surface = None

    @classmethod
    def get_service_name(cls):
        return "xtquant"

    def __init__(self):
        self._authenticated = False
        self._peer = None
        self._subscription_ids = set()

    def on_connect(self, conn):
        try:
            self._peer = conn._channel.stream.sock.getpeername()
        except Exception:
            self._peer = ("unknown", 0)
        logger.info("Client connected from %s", self._peer)

    def on_disconnect(self, conn):
        logger.info("Client %s disconnected", self._peer)
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

    def exposed_authenticate(self, nonce, timestamp, token):
        ip = self._peer[0] if self._peer else "unknown"
        self._log_request("authenticate", "ip=%s" % ip)
        if rate_limiter.is_locked(ip):
            logger.warning("[%s] authenticate blocked by rate limiter", ip)
            return False
        ok = verify_auth_token(self.__class__._auth_key, nonce, timestamp, token)
        if ok:
            self._authenticated = True
            rate_limiter.record_success(ip)
            logger.info("[%s] authenticate OK", ip)
        else:
            rate_limiter.record_failure(ip)
            logger.warning("[%s] authenticate FAILED", ip)
        return ok

    def exposed_get_api_surface(self):
        self._require_authed()
        self._log_request("get_api_surface")
        return self.__class__._api_surface

    def exposed_call_xtdata(self, name, args, kwargs):
        self._require_authed()
        try:
            arg_str = _summarize_args(args, kwargs)
        except Exception:
            arg_str = "<summarize failed>"
        self._log_request("call_xtdata", "fn=%s(%s)" % (name, arg_str))

        if is_download_function(name):
            result = self._submit_download(name, args, kwargs)
        elif xtdata is None:
            result = {"status": STATUS_ERROR, "error_type": "ImportError",
                      "error_message": "xtquant not available"}
        else:
            fn = getattr(xtdata, name, None)
            if fn is None:
                result = {"status": STATUS_ERROR, "error_type": "AttributeError",
                          "error_message": f"xtdata has no attribute {name!r}"}
            else:
                try:
                    # Materialize RPyC netref proxies → plain Python objects.
                    # pybind11 (xtquant's C++ layer) rejects netref lists/dicts
                    # because its strict type checking only accepts builtins.
                    args = [_materialize(a) for a in args]
                    kwargs = {k: _materialize(v) for k, v in kwargs.items()}
                    raw = fn(*args, **kwargs)
                    result = {"status": STATUS_OK, "data": serialize(raw)}
                except Exception as e:
                    logger.warning("call_xtdata(%s) raised %s: %s",
                                   name, type(e).__name__, e)
                    result = {"status": STATUS_ERROR, "error_type": type(e).__name__,
                              "error_message": str(e)}

        try:
            result_summary = _summarize_rpc_result(result)
        except Exception:
            result_summary = "<summarize failed>"
        self._log_request("call_xtdata", "fn=%s => %s" % (name, result_summary))
        return result

    def _submit_download(self, name, args, kwargs):
        if xtdata is None:
            return {"status": STATUS_ERROR, "error_type": "ImportError",
                    "error_message": "xtquant not available"}
        fn = getattr(xtdata, name, None)
        if fn is None:
            return {"status": STATUS_ERROR, "error_type": "AttributeError",
                    "error_message": f"xtdata has no attribute {name!r}"}
        try:
            task_id = self.__class__._download_mgr.submit(
                fn, function_name=name, has_progress=False, _args=args, **kwargs)
            return {"status": STATUS_OK, "data": {"task_id": task_id, "_is_download_task": True}}
        except Exception as e:
            return {"status": STATUS_ERROR, "error_type": type(e).__name__,
                    "error_message": str(e)}

    def exposed_call_trader(self, name, args, kwargs):
        self._require_authed()
        try:
            arg_str = _summarize_args(args, kwargs)
        except Exception:
            arg_str = "<summarize failed>"
        self._log_request("call_trader", "method=%s(%s)" % (name, arg_str))
        cm = self.__class__._connection_mgr
        if cm is None:
            result = {"status": STATUS_ERROR, "error_type": "NotConnected",
                      "error_message": "connection manager not configured"}
        else:
            args = [_materialize(a) for a in args]
            kwargs = {k: _materialize(v) for k, v in kwargs.items()}
            result = cm.call_trader_method(name, args, kwargs)
        try:
            result_summary = _summarize_rpc_result(result)
        except Exception:
            result_summary = "<summarize failed>"
        self._log_request("call_trader", "method=%s => %s" % (name, result_summary))
        return result

    def exposed_health(self):
        self._require_authed()
        self._log_request("health", level=logging.DEBUG)
        cm = self.__class__._connection_mgr
        if cm is None:
            return {"connected": False, "trader_available": False}
        return cm.get_health_status()

    def exposed_subscribe_event(self, event_types, account_id=None):
        self._require_authed()
        self._log_request("subscribe_event", "types=%s account=%s" % (event_types, account_id))
        sub_id = event_bus.subscribe(event_types, account_id)
        self._subscription_ids.add(sub_id)
        return sub_id

    def exposed_unsubscribe_event(self, sub_id):
        self._require_authed()
        self._log_request("unsubscribe_event", "sub_id=%s" % sub_id)
        self._subscription_ids.discard(sub_id)
        return event_bus.unsubscribe(sub_id)

    def exposed_poll_events(self, sub_id, max_count=100):
        self._require_authed()
        self._log_request("poll_events", "sub_id=%s max=%s" % (sub_id, max_count), level=logging.DEBUG)
        sub = event_bus.get_subscription(sub_id)
        if sub is None:
            return [], 0
        return sub.drain(max_count)

    def exposed_query_download(self, task_id):
        self._require_authed()
        self._log_request("query_download", "task_id=%s" % task_id)
        task = self.__class__._download_mgr.get_task(task_id)
        if task is None:
            return {"status": STATUS_ERROR, "error_type": "KeyError",
                    "error_message": f"task {task_id!r} not found"}
        return {"status": STATUS_OK, "data": task}

    def exposed_batch_call_xtdata(self, name, calls):
        self._require_authed()

        # ── early exit for empty batch ────────────────────────────
        if not calls:
            self._log_request("batch_call_xtdata", f"fn={name}, calls=0")
            return {"status": STATUS_OK, "results": []}

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
        if xtdata is None:
            return {
                "status": STATUS_ERROR,
                "error_type": "ImportError",
                "error_message": "xtquant not available",
            }

        fn = getattr(xtdata, name, None)
        if fn is None:
            return {
                "status": STATUS_ERROR,
                "error_type": "AttributeError",
                "error_message": f"xtdata has no attribute {name!r}",
            }

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

        # ── concurrent execution ─────────────────────────────────
        max_workers = min(len(calls), _BATCH_MAX_WORKERS)
        results = [None] * len(calls)
        _t_total = time.time()

        # Convert RPyC netref proxies to plain Python objects.
        # rpyc.utils.classic.obtain does this in one efficient RPC —
        # iterating element-by-element with isinstance checks on netrefs
        # triggers an RPC per element and takes seconds on LAN.
        import rpyc.utils.classic
        materialized_calls = rpyc.utils.classic.obtain(calls)
        _t_mat = time.time()

        if _USE_PROCESS_POOL:
            from concurrent.futures import ProcessPoolExecutor as _PoolExecutor
        else:
            from concurrent.futures import ThreadPoolExecutor as _PoolExecutor
        from concurrent.futures import as_completed

        _t_setup = time.time()
        with _PoolExecutor(max_workers=max_workers) as ex:
            futures = {
                ex.submit(execute_one, name, args, kwargs): i
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
        _pool_type = "ProcessPool" if _USE_PROCESS_POOL else "ThreadPool"
        _n = len(calls)
        logger.info(
            "batch timing: %s workers=%d calls=%d | "
            "materialize=%.0fms setup=%.0fms submit=%.0fms "
            "first_result=%.0fms total=%.0fms",
            _pool_type, max_workers, _n,
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
