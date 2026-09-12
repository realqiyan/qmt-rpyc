import inspect
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from qmt_rpyc.protocol import STATUS_OK, STATUS_ERROR
from qmt_rpyc.server.serializer import serialize
from qmt_rpyc.server.event_bus import event_bus

logger = logging.getLogger(__name__)

# ── defaults (overridable via constructor / env vars) ──────────────
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 5
HEARTBEAT_MAX_FAILURES = 3
RECONNECT_BACKOFF_SECONDS = [10, 30, 60, 600]
RECONNECT_MAX_ATTEMPTS = 0  # 0 = unlimited
_STOP_JOIN_TIMEOUT = 2

_ACCOUNT_METHODS = {
    "order_stock", "cancel_order_stock", "cancel_order_stock_sysid",
    "query_stock_asset", "query_stock_order", "query_stock_orders",
    "query_stock_trades", "query_stock_position", "query_stock_positions",
}


@dataclass(frozen=True)
class _AccountParameter:
    name: str
    position: Optional[int]


def _discover_account_parameters(trader_cls):
    """Return public Trader methods whose exact parameter name is account."""
    discovered = {}
    for name in dir(trader_cls):
        if name.startswith("_"):
            continue
        method = getattr(trader_cls, name, None)
        if not callable(method):
            continue
        try:
            parameters = inspect.signature(method).parameters.values()
        except (TypeError, ValueError) as e:
            logger.debug("Cannot inspect Trader method %s: %s", name, e)
            continue

        position = 0
        for parameter in parameters:
            if parameter.name in ("self", "cls"):
                continue
            if parameter.name == "account":
                if parameter.kind == inspect.Parameter.KEYWORD_ONLY:
                    discovered[name] = _AccountParameter("account", None)
                elif parameter.kind in (
                        inspect.Parameter.POSITIONAL_ONLY,
                        inspect.Parameter.POSITIONAL_OR_KEYWORD):
                    discovered[name] = _AccountParameter("account", position)
                break
            if parameter.kind in (
                    inspect.Parameter.POSITIONAL_ONLY,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD):
                position += 1
    return discovered


class _Callback:
    """Bridge xtquant trader callbacks → event_bus.

    All callbacks are invoked from within xtquant's C++ layer.  Any exception
    that escapes into C++ is undefined behaviour — we defensively guard every
    serialize() call and event publish, falling back to str(obj) so the event
    still reaches subscribers even when serialization fails.
    """

    def __init__(self, manager, trader=None):
        self._manager = manager
        self._trader = trader

    @staticmethod
    def _safe_serialize(obj):
        """Serialize *obj*, falling back to str(obj) on any failure.

        This MUST NOT raise — it is called from xtquant C++ callbacks.
        """
        try:
            return serialize(obj)
        except Exception:
            logger.warning("serialize failed for %s, falling back to str()",
                           type(obj).__name__)
            try:
                return str(obj)
            except Exception:
                return "<serialization failed>"

    @staticmethod
    def _safe_publish(event):
        """Publish *event* to the event bus, catching all exceptions.

        This MUST NOT raise — same reason as _safe_serialize.
        """
        try:
            event_bus.publish(event)
        except Exception:
            logger.warning("event_bus.publish failed for event type=%s",
                           event.get("type", "?"))

    def on_stock_order(self, order):
        self._safe_publish({
            "type": "order",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(order, "account_id", None),
            "data": self._safe_serialize(order),
        })

    def on_stock_trade(self, trade):
        self._safe_publish({
            "type": "trade",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(trade, "account_id", None),
            "data": self._safe_serialize(trade),
        })

    def on_disconnected(self):
        try:
            self._manager.mark_disconnected(self._trader, publish_event=True)
        except Exception:
            logger.warning("mark_disconnected failed", exc_info=True)

    def on_order_error(self, order_error):
        self._safe_publish({
            "type": "order_error",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(order_error, "account_id", None),
            "data": self._safe_serialize(order_error),
        })

    def on_cancel_error(self, cancel_error):
        self._safe_publish({
            "type": "cancel_error",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(cancel_error, "account_id", None),
            "data": self._safe_serialize(cancel_error),
        })

    def on_order_stock_async_response(self, response):
        self._safe_publish({
            "type": "async_response",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(response, "account_id", None),
            "data": self._safe_serialize(response),
        })

    def on_account_status(self, status):
        self._safe_publish({
            "type": "account_status",
            "timestamp": datetime.now().isoformat(),
            "account_id": getattr(status, "account_id", None),
            "data": self._safe_serialize(status),
        })


class ConnectionManager:
    """Manages the xtquant trader lifecycle: init, connect, heartbeat, reconnect.

    Public API
    ----------
    start()              – launch background initialization and connection
    stop()               – graceful shutdown
    probe()              – initialize and connect once, synchronously
    connect()            – attempt a single connection (returns bool)
    mark_disconnected()  – force-disconnect (e.g. from callback)
    start_heartbeat()    – launch the heartbeat thread

    Backward-compatible: _init_trader() / connect() / start_heartbeat()
    can still be called individually (used by tests).
    """

    HEARTBEAT_INTERVAL = HEARTBEAT_INTERVAL_SECONDS
    RECONNECT_BACKOFF = RECONNECT_BACKOFF_SECONDS

    def __init__(self, path, session_id, account_id,
                 heartbeat_interval=HEARTBEAT_INTERVAL_SECONDS,
                 heartbeat_timeout=HEARTBEAT_TIMEOUT_SECONDS,
                 heartbeat_max_failures=HEARTBEAT_MAX_FAILURES,
                 reconnect_max_attempts=RECONNECT_MAX_ATTEMPTS):
        self._path = path
        self._session_id = session_id
        self._account_id = account_id
        self._heartbeat_interval = heartbeat_interval
        self._heartbeat_timeout = heartbeat_timeout
        self._heartbeat_max_failures = heartbeat_max_failures
        self._reconnect_max_attempts = reconnect_max_attempts

        self._trader = None
        self._native_lock = None
        self._callback = None
        self._connected = False
        self._trader_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._heartbeat_thread = None
        self._reconnect_thread = None
        self._reconnect_attempts = 0
        self._heartbeat_failures = 0
        self._start_time = time.time()
        self._last_heartbeat = None
        self._connection_state = "disconnected"
        self._last_connection_error = ""
        self._consecutive_failures = 0
        self._next_retry_at = None
        self._disconnect_generation = 0
        self._account_parameters = {
            name: _AccountParameter("account", 0)
            for name in _ACCOUNT_METHODS
        }

    # ── public entry points ────────────────────────────────────────

    def start(self):
        """Schedule the first attempt immediately, without waiting for QMT.

        SDK imports must be validated by the server before calling this method.
        The return value indicates scheduling, not trading readiness.
        """
        return self.schedule_reconnect(immediate=True)

    def stop(self):
        """Graceful shutdown: stop heartbeat, cancel reconnect, stop trader."""
        self._stop_event.set()
        for t in (self._heartbeat_thread, self._reconnect_thread):
            if t is not None and t.is_alive():
                t.join(timeout=_STOP_JOIN_TIMEOUT)
        with self._trader_lock:
            trader = self._trader
            self._trader = None
            self._native_lock = None
            self._connected = False
            self._connection_state = "stopped"
            self._next_retry_at = None
        self._stop_trader(trader)
        logger.info("ConnectionManager stopped")

    def probe(self):
        """Initialize and connect once, blocking until the attempt finishes.

        Unlike start(), which returns as soon as the background attempt is
        scheduled, this reports the actual outcome of a single attempt and
        never schedules retries.  The caller owns the lifecycle and must call
        stop() afterwards.
        """
        self._init_trader()
        if self._trader is None:
            return False
        return self.connect()

    # ── trader init / connect ──────────────────────────────────────

    def _init_trader(self):
        trader = None
        try:
            from xtquant.xttrader import XtQuantTrader
            trader = XtQuantTrader(self._path, self._session_id)
            discovered = _discover_account_parameters(type(trader))
            callback = _Callback(self, trader)
            trader.register_callback(callback)
            trader.start()
        except ImportError:
            logger.error("xtquant not available")
            self._record_connection_error("xtquant import failed")
            self._stop_trader(trader)
            return
        except Exception as e:
            logger.exception("trader init failed")
            self._record_connection_error("trader initialization failed: " + type(e).__name__)
            self._stop_trader(trader)
            return

        with self._trader_lock:
            if self._stop_event.is_set():
                should_stop = True
            else:
                self._trader = trader
                self._native_lock = threading.Lock()
                self._callback = callback
                self._account_parameters = {
                    name: _AccountParameter("account", 0)
                    for name in _ACCOUNT_METHODS
                }
                self._account_parameters.update(discovered)
                should_stop = False

        if should_stop:
            self._stop_trader(trader)
            return

        logger.info(
            "Discovered %d Trader methods requiring StockAccount adaptation",
            len(discovered),
        )
        logger.info("XtQuantTrader initialized (path=%s, session=%d)",
                    self._path or "(empty)", self._session_id)

    def connect(self) -> bool:
        """Attempt a single connection.  Returns True on success."""
        with self._trader_lock:
            trader = self._trader
            native_lock = self._native_lock
            if (trader is None or native_lock is None
                    or self._stop_event.is_set()):
                return False
            generation = self._disconnect_generation

        try:
            with native_lock:
                result = trader.connect()
        except Exception as e:
            logger.error("connect failed: %s", e)
            self._record_connection_error("Trader.connect failed: " + type(e).__name__)
            with self._trader_lock:
                if self._trader is trader:
                    self._connected = False
            return False

        with self._trader_lock:
            if self._trader is not trader or self._stop_event.is_set():
                return False
            if result != 0:
                self._connected = False
                self._last_connection_error = "Trader.connect returned {}".format(result)
                return False

        # Subscribe after successful connect
        if self._account_id and not self._subscribe_trader(
                trader, native_lock, self._account_id):
            with self._trader_lock:
                if self._trader is trader:
                    self._connected = False
                    self._last_connection_error = "account subscription failed"
            return False

        with self._trader_lock:
            if (self._trader is not trader or self._stop_event.is_set()
                    or self._disconnect_generation != generation):
                return False
            self._connected = True
            self._connection_state = "connected"
            self._next_retry_at = None
            self._last_heartbeat = datetime.now()
            self._heartbeat_failures = 0
        return True

    def subscribe(self, account_id) -> bool:
        with self._trader_lock:
            trader = self._trader
            native_lock = self._native_lock
            if (trader is None or native_lock is None
                    or not self._connected):
                return False
        return self._subscribe_trader(trader, native_lock, account_id)

    @staticmethod
    def _subscribe_trader(trader, native_lock, account_id) -> bool:
        try:
            from xtquant.xttype import StockAccount
            acc = StockAccount(account_id)
            with native_lock:
                result = trader.subscribe(acc)
            if result == 0:
                logger.info("Subscribed to account %s", account_id)
                return True
            logger.warning("subscribe returned %s for account %s",
                           result, account_id)
            return False
        except Exception as e:
            logger.error("subscribe failed: %s", e)
            return False

    # ── properties ─────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        with self._trader_lock:
            return self._connected

    @property
    def trader(self):
        with self._trader_lock:
            return self._trader

    # ── heartbeat ──────────────────────────────────────────────────

    def start_heartbeat(self):
        """Launch the heartbeat background thread (idempotent)."""
        if self._trader is None:
            return
        if self._heartbeat_thread is not None and self._heartbeat_thread.is_alive():
            return
        if self._stop_event.is_set():
            return
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="qmt-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        logger.info("Heartbeat thread started")

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self._heartbeat_interval):
            with self._trader_lock:
                if not self._connected:
                    continue
                trader = self._trader
                generation = self._disconnect_generation

            ok = self._do_heartbeat()
            with self._trader_lock:
                if (self._trader is not trader or not self._connected
                        or self._disconnect_generation != generation):
                    continue
                if ok:
                    self._last_heartbeat = datetime.now()
                    self._heartbeat_failures = 0
                else:
                    self._heartbeat_failures += 1
                    failures = self._heartbeat_failures
                    logger.warning("Heartbeat failure %d/%d",
                                   failures, self._heartbeat_max_failures)
            if not ok and failures >= self._heartbeat_max_failures:
                logger.error("Heartbeat lost — %d consecutive failures", failures)
                self.mark_disconnected(trader)

    def _do_heartbeat(self) -> bool:
        """Execute one heartbeat check with timeout.  Returns True if alive.

        Strategy:
        - If account_id is set → query_stock_asset (tests full trading path).
        - Otherwise → try xtdata.get_trading_calendar (lightweight data-path check).
        - Both are wrapped in a daemon thread with join-timeout to guard
          against xtquant hangs (e.g. QMT process frozen but not dead).
        """
        result_container = [False]
        with self._trader_lock:
            trader = self._trader
            native_lock = self._native_lock
            connected = self._connected

        if trader is None or native_lock is None or not connected:
            return False

        def _check():
            try:
                with native_lock:
                    if self._account_id:
                        from xtquant.xttype import StockAccount
                        acc = StockAccount(self._account_id)
                        asset = trader.query_stock_asset(acc)
                        if asset is not None:
                            result_container[0] = True
                    else:
                        # No account ― test data path as lightweight liveness probe
                        try:
                            from xtquant import xtdata
                            cal = xtdata.get_trading_calendar("SH")
                            if cal is not None:
                                result_container[0] = True
                        except ImportError:
                            # xtdata unavailable; current trader still counts as alive
                            with self._trader_lock:
                                result_container[0] = (
                                    self._trader is trader
                                    and self._connected)
                        except Exception:
                            pass
            except Exception:
                pass

        t = threading.Thread(target=_check, daemon=True)
        t.start()
        t.join(timeout=self._heartbeat_timeout)

        if t.is_alive():
            logger.warning("Heartbeat timed out after %ds", self._heartbeat_timeout)
            return False
        return result_container[0]

    # ── disconnect / reconnect ─────────────────────────────────────

    def mark_disconnected(self, source_trader=None, publish_event=False):
        """Mark connection as lost and trigger reconnection.

        Called from _Callback.on_disconnected (xtquant callback) or after
        consecutive heartbeat failures.
        """
        with self._trader_lock:
            if self._stop_event.is_set():
                return
            if source_trader is not None and self._trader is not source_trader:
                return
            was_connected = self._connected
            self._connected = False
            self._last_heartbeat = None
            self._disconnect_generation += 1
            self._last_connection_error = "QMT connection lost"
            self._connection_state = "disconnected"
            if publish_event:
                _Callback._safe_publish({
                    "type": "disconnect",
                    "timestamp": datetime.now().isoformat(),
                    "account_id": None,
                    "data": None,
                })
        if was_connected:
            logger.warning("QMT connection lost")
        self.schedule_reconnect()

    def schedule_reconnect(self, immediate=False):
        """Start the reconnect loop if not already running."""
        with self._trader_lock:
            if self._stop_event.is_set():
                return False
        with self._state_lock:
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return True
            if self.is_connected:
                return True
            self._reconnect_thread = threading.Thread(
                target=self._connection_worker, args=(immediate,),
                name="qmt-reconnect", daemon=True)
            self._reconnect_thread.start()
        return True

    def _connection_worker(self, immediate):
        try:
            self._reconnect_loop(immediate=immediate)
        finally:
            # A disconnect may arrive after connect succeeds but before this
            # worker exits. Clearing ownership under the scheduling lock avoids
            # losing that callback's request for another worker.
            with self._state_lock:
                self._reconnect_thread = None
            with self._trader_lock:
                retry = (not self._connected and not self._stop_event.is_set()
                         and self._connection_state != "exhausted")
            if retry:
                self.schedule_reconnect()

    def _record_connection_error(self, message):
        with self._trader_lock:
            self._last_connection_error = message

    def _reconnect_loop(self, immediate=False):
        """Connect with bounded, interruptible retry delays.

        On success: re-subscribes account, publishes 'reconnect' event,
        and resets failure counters so the heartbeat resumes cleanly.
        """
        delay_index = 0
        while not self._stop_event.is_set():
            if self._reconnect_max_attempts > 0 and \
                    self._reconnect_attempts >= self._reconnect_max_attempts:
                logger.error("Max reconnect attempts (%d) reached — giving up",
                             self._reconnect_max_attempts)
                with self._trader_lock:
                    self._connection_state = "exhausted"
                    self._next_retry_at = None
                return

            delay = 0 if immediate else self.RECONNECT_BACKOFF[
                min(delay_index, len(self.RECONNECT_BACKOFF) - 1)]
            if not immediate:
                delay_index += 1
            immediate = False
            with self._trader_lock:
                self._connection_state = "waiting_retry" if delay else "connecting"
                self._next_retry_at = (
                    datetime.fromtimestamp(time.time() + delay).isoformat()
                    if delay else None)
            if delay:
                logger.info("QMT waiting to retry in %ss", delay)
            if self._stop_event.wait(delay):
                return

            with self._trader_lock:
                self._connection_state = "connecting"
                self._next_retry_at = None
                self._reconnect_attempts += 1
            logger.info("Reconnect attempt %d (delay=%ds) ...",
                        self._reconnect_attempts, delay)
            attempt_number = self._reconnect_attempts
            try:
                self._reset_trader()
                connected = self.connect()
            except Exception as e:
                logger.exception("QMT connection attempt failed")
                self._record_connection_error("connection attempt failed: " + type(e).__name__)
                connected = False
            if connected:
                with self._trader_lock:
                    if not self._connected or self._stop_event.is_set():
                        # The callback won the race after connect returned.
                        # Worker cleanup schedules the next connection episode.
                        return
                    self._heartbeat_failures = 0
                    self._reconnect_attempts = 0
                    self._consecutive_failures = 0
                    self._last_connection_error = ""
                    _Callback._safe_publish({
                        "type": "reconnect",
                        "timestamp": datetime.now().isoformat(),
                        "account_id": self._account_id,
                        "data": {"attempts": attempt_number},
                    })
                self.start_heartbeat()
                logger.info("Reconnect successful after %d attempt(s)",
                            attempt_number)
                return
            with self._trader_lock:
                self._consecutive_failures += 1
            logger.warning("QMT connection attempt failed: %s",
                           self._last_connection_error)

    def _reset_trader(self):
        """Stop old trader and create a fresh one."""
        with self._trader_lock:
            trader = self._trader
            self._trader = None
            self._native_lock = None
            self._connected = False
        self._stop_trader(trader)
        self._init_trader()

    # ── trader method dispatch ─────────────────────────────────────

    def _wrap_account_if_needed(self, name, args, kwargs):
        parameter = self._account_parameters.get(name)
        if parameter is None:
            return args, kwargs

        if (parameter.position is not None
                and len(args) > parameter.position
                and isinstance(args[parameter.position], str)):
            from xtquant.xttype import StockAccount
            adapted_args = list(args)
            adapted_args[parameter.position] = StockAccount(
                args[parameter.position])
            return adapted_args, kwargs

        if (parameter.name in kwargs
                and isinstance(kwargs[parameter.name], str)):
            from xtquant.xttype import StockAccount
            adapted_kwargs = dict(kwargs)
            adapted_kwargs[parameter.name] = StockAccount(
                kwargs[parameter.name])
            return args, adapted_kwargs

        return args, kwargs

    def call_trader_method(self, name, args, kwargs):
        with self._trader_lock:
            trader = self._trader
            native_lock = self._native_lock
            if (trader is None or native_lock is None
                    or not self._connected):
                return {"status": STATUS_ERROR, "error_type": "NotConnected",
                        "error_message": "trader not connected"}
        try:
            with native_lock:
                with self._trader_lock:
                    if (self._trader is not trader or not self._connected
                            or self._stop_event.is_set()):
                        return {"status": STATUS_ERROR,
                                "error_type": "NotConnected",
                                "error_message": "trader not connected"}
                method = getattr(trader, name, None)
                if method is None:
                    return {
                        "status": STATUS_ERROR,
                        "error_type": "AttributeError",
                        "error_message": f"trader has no method {name!r}",
                    }
                args, kwargs = self._wrap_account_if_needed(
                    name, args, kwargs)
                result = method(*args, **kwargs)
            return {"status": STATUS_OK, "data": serialize(result)}
        except Exception as e:
            logger.warning("call_trader(%s) raised %s: %s",
                           name, type(e).__name__, e)
            return {"status": STATUS_ERROR, "error_type": type(e).__name__,
                    "error_message": str(e)}

    # ── health ─────────────────────────────────────────────────────

    def get_health_status(self):
        with self._trader_lock:
            trader_available = self._trader is not None
            return {
                "connected": self._connected,
                "last_heartbeat": self._last_heartbeat.isoformat()
                                  if self._last_heartbeat else "",
                "heartbeat_failures": self._heartbeat_failures,
                "reconnect_attempts": self._reconnect_attempts,
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "trader_available": trader_available,
                "connection_state": self._connection_state,
                "consecutive_failures": self._consecutive_failures,
                "last_connection_error": self._last_connection_error,
                "next_retry_at": self._next_retry_at,
            }

    # ── internal helpers ───────────────────────────────────────────

    @staticmethod
    def _stop_trader(trader):
        if trader is None:
            return

        def _stop():
            try:
                trader.stop()
            except Exception:
                logger.warning("trader stop failed", exc_info=True)

        thread = threading.Thread(
            target=_stop, name="qmt-trader-stop", daemon=True)
        thread.start()
        thread.join(timeout=_STOP_JOIN_TIMEOUT)
        if thread.is_alive():
            logger.error(
                "trader stop timed out after %ds; detaching stale trader",
                _STOP_JOIN_TIMEOUT,
            )
