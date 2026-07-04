import logging
import threading
import time
from datetime import datetime
from typing import Optional

from common.protocol import STATUS_OK, STATUS_ERROR
from server.serializer import serialize
from server.event_bus import event_bus

logger = logging.getLogger(__name__)

# ── defaults (overridable via constructor / env vars) ──────────────
HEARTBEAT_INTERVAL_SECONDS = 30
HEARTBEAT_TIMEOUT_SECONDS = 5
HEARTBEAT_MAX_FAILURES = 3
RECONNECT_BACKOFF_SECONDS = [1, 2, 4, 8, 16, 30]
RECONNECT_MAX_ATTEMPTS = 0  # 0 = unlimited
_STOP_JOIN_TIMEOUT = 2

_ACCOUNT_METHODS = {
    "order_stock", "cancel_order_stock", "cancel_order_stock_sysid",
    "query_stock_asset", "query_stock_order", "query_stock_orders",
    "query_stock_trades", "query_stock_position", "query_stock_positions",
    "query_account_status",
}


class _Callback:
    """Bridge xtquant trader callbacks → event_bus.

    All callbacks are invoked from within xtquant's C++ layer.  Any exception
    that escapes into C++ is undefined behaviour — we defensively guard every
    serialize() call and event publish, falling back to str(obj) so the event
    still reaches subscribers even when serialization fails.
    """

    def __init__(self, manager):
        self._manager = manager

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
        self._safe_publish({
            "type": "disconnect",
            "timestamp": datetime.now().isoformat(),
            "account_id": None,
            "data": None,
        })
        try:
            self._manager.mark_disconnected()
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
    start()              – one-shot init → connect → heartbeat (preferred)
    stop()               – graceful shutdown
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

    # ── public entry points ────────────────────────────────────────

    def start(self):
        """Initialize trader, connect, and begin heartbeat.  Safe to call once."""
        self._init_trader()
        if self._trader is not None:
            if self.connect():
                self.start_heartbeat()
                logger.info("QMT connected, heartbeat started (interval=%ds, "
                            "timeout=%ds, max_failures=%d)",
                            self._heartbeat_interval, self._heartbeat_timeout,
                            self._heartbeat_max_failures)
            else:
                logger.warning("QMT initial connection failed — scheduling reconnect")
                self.schedule_reconnect()
        else:
            logger.warning("xtquant not available — trader is None")

    def stop(self):
        """Graceful shutdown: stop heartbeat, cancel reconnect, stop trader."""
        self._stop_event.set()
        for t in (self._heartbeat_thread, self._reconnect_thread):
            if t is not None and t.is_alive():
                t.join(timeout=_STOP_JOIN_TIMEOUT)
        with self._trader_lock:
            if self._trader is not None:
                try:
                    self._trader.stop()
                except Exception:
                    pass
                self._trader = None
            self._connected = False
        logger.info("ConnectionManager stopped")

    # ── trader init / connect ──────────────────────────────────────

    def _init_trader(self):
        with self._trader_lock:
            try:
                from xtquant.xttrader import XtQuantTrader
                self._trader = XtQuantTrader(self._path, self._session_id)
                self._callback = _Callback(self)
                self._trader.register_callback(self._callback)
                self._trader.start()
                self._daemonize_threads()
                logger.info("XtQuantTrader initialized (path=%s, session=%d)",
                            self._path or "(empty)", self._session_id)
            except ImportError:
                logger.error("xtquant not available")
            except Exception as e:
                logger.error("trader init failed: %s", e)

    def connect(self) -> bool:
        """Attempt a single connection.  Returns True on success."""
        with self._trader_lock:
            if self._trader is None:
                return False
            try:
                result = self._trader.connect()
                if result != 0:
                    self._connected = False
                    return False
            except Exception as e:
                logger.error("connect failed: %s", e)
                self._connected = False
                return False

            self._connected = True
            self._last_heartbeat = datetime.now()
            self._heartbeat_failures = 0
            self._reconnect_attempts = 0

        # Subscribe after successful connect
        if self._account_id:
            self.subscribe(self._account_id)
        return True

    def subscribe(self, account_id) -> bool:
        with self._trader_lock:
            if self._trader is None:
                return False
            try:
                from xtquant.xttype import StockAccount
                acc = StockAccount(account_id)
                result = self._trader.subscribe(acc)
                if result == 0:
                    logger.info("Subscribed to account %s", account_id)
                    return True
                else:
                    logger.warning("subscribe returned %s for account %s",
                                   result, account_id)
                    return False
            except Exception as e:
                logger.error("subscribe failed: %s", e)
                return False

    # ── properties ─────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
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
        if not self._stop_event.is_set():
            self._stop_event.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, name="qmt-heartbeat", daemon=True)
        self._heartbeat_thread.start()
        logger.info("Heartbeat thread started")

    def _heartbeat_loop(self):
        while not self._stop_event.wait(self._heartbeat_interval):
            if not self._connected:
                continue

            ok = self._do_heartbeat()
            if ok:
                self._last_heartbeat = datetime.now()
                self._heartbeat_failures = 0
            else:
                self._heartbeat_failures += 1
                logger.warning("Heartbeat failure %d/%d",
                               self._heartbeat_failures,
                               self._heartbeat_max_failures)
                if self._heartbeat_failures >= self._heartbeat_max_failures:
                    logger.error("Heartbeat lost — %d consecutive failures",
                                 self._heartbeat_failures)
                    self.mark_disconnected()

    def _do_heartbeat(self) -> bool:
        """Execute one heartbeat check with timeout.  Returns True if alive.

        Strategy:
        - If account_id is set → query_stock_asset (tests full trading path).
        - Otherwise → try xtdata.get_trading_calendar (lightweight data-path check).
        - Both are wrapped in a daemon thread with join-timeout to guard
          against xtquant hangs (e.g. QMT process frozen but not dead).
        """
        result_container = [False]

        def _check():
            try:
                if self._account_id:
                    with self._trader_lock:
                        if self._trader is None:
                            return
                        from xtquant.xttype import StockAccount
                        acc = StockAccount(self._account_id)
                        asset = self._trader.query_stock_asset(acc)
                    if asset is not None:
                        result_container[0] = True
                else:
                    # No account ― test data path as lightweight liveness probe
                    try:
                        from xtquant import xtdata
                        cal = xtdata.get_trading_calendar("SSE")
                        if cal is not None:
                            result_container[0] = True
                    except ImportError:
                        # xtdata not available either; mark alive if trader exists
                        with self._trader_lock:
                            result_container[0] = self._trader is not None
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

    def mark_disconnected(self):
        """Mark connection as lost and trigger reconnection.

        Called from _Callback.on_disconnected (xtquant callback) or after
        consecutive heartbeat failures.
        """
        was_connected = self._connected
        self._connected = False
        self._last_heartbeat = None
        if was_connected:
            logger.warning("QMT connection lost")
        self.schedule_reconnect()

    def schedule_reconnect(self):
        """Start the reconnect loop if not already running."""
        with self._trader_lock:
            if self._trader is None:
                return
        self._connected = False
        with self._state_lock:
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_loop, name="qmt-reconnect", daemon=True)
            self._reconnect_thread.start()

    def _reconnect_loop(self):
        """Reconnect with exponential backoff.

        On success: re-subscribes account, publishes 'reconnect' event,
        and resets failure counters so the heartbeat resumes cleanly.
        """
        while not self._stop_event.is_set():
            if self._reconnect_max_attempts > 0 and \
                    self._reconnect_attempts >= self._reconnect_max_attempts:
                logger.error("Max reconnect attempts (%d) reached — giving up",
                             self._reconnect_max_attempts)
                return

            delay = self.RECONNECT_BACKOFF[
                min(self._reconnect_attempts, len(self.RECONNECT_BACKOFF) - 1)]
            if self._stop_event.wait(delay):
                return

            self._reconnect_attempts += 1
            logger.info("Reconnect attempt %d (delay=%ds) ...",
                        self._reconnect_attempts, delay)
            self._reset_trader()

            if self.connect():
                # Re-subscribe account after successful reconnect
                if self._account_id:
                    self.subscribe(self._account_id)
                # Notify subscribers
                event_bus.publish({
                    "type": "reconnect",
                    "timestamp": datetime.now().isoformat(),
                    "account_id": self._account_id,
                    "data": {"attempts": self._reconnect_attempts},
                })
                self._daemonize_threads()
                self._heartbeat_failures = 0
                logger.info("Reconnect successful after %d attempt(s)",
                            self._reconnect_attempts)
                return

    def _reset_trader(self):
        """Stop old trader and create a fresh one."""
        with self._trader_lock:
            if self._trader is not None:
                try:
                    self._trader.stop()
                except Exception:
                    pass
                self._trader = None
            self._init_trader()

    # ── trader method dispatch ─────────────────────────────────────

    def _wrap_account_if_needed(self, name, args):
        if name in _ACCOUNT_METHODS and args and isinstance(args[0], str):
            try:
                from xtquant.xttype import StockAccount
                return (StockAccount(args[0]),) + tuple(args[1:])
            except ImportError:
                pass
        return args

    def call_trader_method(self, name, args, kwargs):
        with self._trader_lock:
            if self._trader is None:
                return {"status": STATUS_ERROR, "error_type": "NotConnected",
                        "error_message": "trader not connected"}
            method = getattr(self._trader, name, None)
            if method is None:
                return {"status": STATUS_ERROR, "error_type": "AttributeError",
                        "error_message": f"trader has no method {name!r}"}
            args = self._wrap_account_if_needed(name, args)
            try:
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
        }

    # ── internal helpers ───────────────────────────────────────────

    def _daemonize_threads(self):
        """Mark all non-daemon threads as daemon so they don't block exit."""
        for t in threading.enumerate():
            if t is not threading.current_thread() and not t.daemon:
                try:
                    t.daemon = True
                except RuntimeError:
                    pass
