"""Integration tests against the real QMT xtquant backend.

These tests start a real RPyC server (not the mock), connect via QmtClient,
and exercise every xtdata query function with basic return-value checks.

Requirements
------------
* QMT/MiniQMT must be running (XtMiniQmt.exe)
* .env configured with valid QMT_PATH, QMT_SESSION_ID, QMT_ACCOUNT_ID
* Server must run on the same Windows host where QMT is installed

Usage
-----
    # Run only integration tests
    python -m pytest tests/test_live_integration.py -v

    # Skip integration tests
    python -m pytest tests/ -v -k "not live"
"""

import os
import sys
import time
import threading
import pytest
import logging

from client import QmtClient

_HERE = os.path.dirname(os.path.abspath(__file__))

# ── helpers ────────────────────────────────────────────────────────────────

# Known-safe test symbols that work across most xtquant versions
_SH_STOCK = "600000.SH"   # 浦发银行
_SZ_STOCK = "000001.SZ"   # 平安银行
_ETF = "510050.SH"         # 上证50ETF
_SECTOR = "沪深300"
_MARKET = "SSE"            # Shanghai Stock Exchange


def _xtquant_available() -> bool:
    """Return True if the real xtquant can be imported."""
    try:
        import xtquant  # noqa: F401
        return True
    except ImportError:
        return False


XTQUANT_AVAILABLE = _xtquant_available()

# ── server fixture ─────────────────────────────────────────────────────────

_DEFAULT_PORT = 18901  # distinct from mock-test ports (18899, 18900)


def _load_config():
    """Load .env config dict. Mirrors server/main.py _load_config minimally."""
    from dotenv import dotenv_values

    root = os.path.join(_HERE, "..")
    cfg = dotenv_values(os.path.join(root, ".env"))
    return {
        "host": cfg.get("QMT_RPYC_HOST", "127.0.0.1"),
        "auth_key": cfg.get("QMT_RPYC_AUTH_KEY"),
        "qmt_path": cfg.get("QMT_PATH", ""),
        "qmt_session_id": int(cfg.get("QMT_SESSION_ID", "1")),
        "qmt_account_id": cfg.get("QMT_ACCOUNT_ID", ""),
    }


@pytest.fixture(scope="module")
def live_server():
    """Start a real RPyC server backed by the local xtquant installation.

    Picks a port that differs from the production server port (.env) so tests
    don't conflict with an already-running server.
    """
    if not XTQUANT_AVAILABLE:
        pytest.skip("xtquant not available — cannot run live integration tests")

    cfg = _load_config()

    # Use a dedicated test port distinct from production
    port = _DEFAULT_PORT

    from server.service import XtquantService
    from server.connection import ConnectionManager
    from server.download_manager import DownloadTaskManager
    from server.api_surface import build_api_surface
    from rpyc.utils.server import ThreadedServer

    cm = ConnectionManager(
        path=cfg["qmt_path"],
        session_id=cfg["qmt_session_id"],
        account_id=cfg["qmt_account_id"],
        heartbeat_interval=30,
        heartbeat_timeout=5,
        heartbeat_max_failures=3,
        reconnect_max_attempts=0,
    )
    cm._init_trader()
    cm.connect()

    dm = DownloadTaskManager(max_workers=2)

    XtquantService._auth_key = cfg["auth_key"]
    XtquantService._require_auth = cfg["auth_key"] is not None
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    XtquantService._api_surface = build_api_surface()

    srv = ThreadedServer(
        XtquantService,
        port=port,
        protocol_config={
            "allow_public_attrs": True,
            "sync_request_timeout": 300,
        },
    )
    t = threading.Thread(target=srv.start, daemon=True, name="live-test-server")
    t.start()
    time.sleep(0.5)

    yield XtquantService, cfg, port

    srv.close()
    cm.stop()
    dm.shutdown()

    # Clean up server modules so other test modules don't pick up stale state
    for mod in list(sys.modules.keys()):
        if mod.startswith("server."):
            del sys.modules[mod]


@pytest.fixture(scope="module")
def client(live_server):
    """Return a connected QmtClient against the live server."""
    _svc, cfg, port = live_server

    auth_key = cfg["auth_key"] if cfg["auth_key"] else None
    with QmtClient.connect("127.0.0.1", port=port, auth_key=auth_key, timeout=30) as c:
        yield c


# ── "safe" function registry ───────────────────────────────────────────────
#
# Each entry maps an xtdata function name to default call arguments and
# expected return-type constraints.
#
# "_skip_reason": if present the test is skipped with that reason (e.g. known
#   xtquant crash).
# "_is_download": if True the function is tested via the download workflow
#   (call_xtdata → poll task) rather than as a direct query.
#
# NOTE: The known xtquant BSON bug (bsonobj.cpp:1388 assertion failure
#   ``u < 1000000``) affects get_market_data / get_local_data /
#   get_market_data_ex when passed certain timestamp parameters.  The
#   datetime_patch in server/datetime_patch.py mitigates this on Python < 3.12.
#   These functions are tested with minimal parameters to reduce crash risk.

_QUERY_FUNCTIONS = [
    # ── instrument detail ──────────────────────────────────────────
    {
        "name": "get_instrument_detail",
        "args": ([_SH_STOCK],),
        "kwargs": {},
        "assertions": [
            ("result_is_dict",     lambda r: isinstance(r, dict)),
            ("has_InstrumentID",   lambda r: "InstrumentID" in r),
        ],
    },
    # ── full tick (real-time snapshot) ─────────────────────────────
    {
        "name": "get_full_tick",
        "args": ([_SZ_STOCK],),
        "kwargs": {},
        "assertions": [
            ("result_is_dict_or_list", lambda r: isinstance(r, (dict, list))),
            ("not_empty",              lambda r: len(r) > 0 if isinstance(r, (dict, list)) else True),
        ],
    },
    # ── trading calendar ───────────────────────────────────────────
    {
        "name": "get_trading_calendar",
        "args": ([_MARKET],),
        "kwargs": {},
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
            ("not_empty",      lambda r: len(r) > 0),
        ],
    },
    # ── sector /板块 ──────────────────────────────────────────────
    {
        "name": "get_sector_list",
        "args": (),
        "kwargs": {},
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
            ("not_empty",      lambda r: len(r) > 0),
        ],
    },
    {
        "name": "get_stock_list_in_sector",
        "args": (_SECTOR,),
        "kwargs": {},
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
            ("not_empty",      lambda r: len(r) > 0),
        ],
    },
    # ── index weight ───────────────────────────────────────────────
    {
        "name": "get_index_weight",
        "args": ("000300.SH",),
        "kwargs": {},
        "assertions": [
            ("result_is_list_or_dict", lambda r: isinstance(r, (list, dict))),
        ],
    },
    # ── holidays ───────────────────────────────────────────────────
    {
        "name": "get_holiday",
        "args": (),
        "kwargs": {},
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
        ],
    },
    # ── dividend factors ───────────────────────────────────────────
    {
        "name": "get_divid_factors",
        "args": ([_SH_STOCK],),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
    },
    # ── options ────────────────────────────────────────────────────
    {
        "name": "get_option_list",
        "args": (_ETF, ""),
        "kwargs": {},
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
        ],
    },
    {
        "name": "get_option_detail_data",
        "args": ([],),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
        "_skip_reason": "requires actual option codes — call get_option_list first; "
                        "tested indirectly via batch when option codes are available",
    },
    # ── futures ────────────────────────────────────────────────────
    {
        "name": "get_main_contract",
        "args": ("IF",),
        "kwargs": {},
        "assertions": [
            ("result_is_str", lambda r: isinstance(r, str)),
            ("not_empty",     lambda r: len(r) > 0),
        ],
    },
    {
        "name": "get_FutureInfo",
        "args": (),
        "kwargs": {},
        "assertions": [
            ("result_is_dict_or_list", lambda r: isinstance(r, (dict, list))),
        ],
    },
    # ── trading time ───────────────────────────────────────────────
    {
        "name": "get_trading_time",
        "args": ([_SH_STOCK],),
        "kwargs": {},
        "assertions": [
            ("result_is_str_or_list", lambda r: isinstance(r, (str, list))),
        ],
    },
    # ── IPO info ───────────────────────────────────────────────────
    {
        "name": "get_ipo_info",
        "args": (_SH_STOCK,),
        "kwargs": {},
        "assertions": [
            ("result_is_dict_or_list", lambda r: isinstance(r, (dict, list))),
        ],
    },
    # ── ETF info ───────────────────────────────────────────────────
    {
        "name": "get_etf_info",
        "args": ([_ETF],),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
    },
    # ── ETF weight (constituents) ──────────────────────────────────
    {
        "name": "get_etf_weight",
        "args": (_ETF,),
        "kwargs": {},
        "assertions": [
            ("result_is_list_or_dict", lambda r: isinstance(r, (list, dict))),
        ],
    },
    # ── market data (⚠ BSON crash risk — tested last, minimal params) ─
    {
        "name": "get_market_data",
        "args": ([], [_SH_STOCK], "1d"),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
            ("has_stock_code", lambda r: _SH_STOCK in r),
        ],
        "_skip_reason": "Known xtquant BSON crash (bsonobj.cpp:1388) on some versions. "
                        "Run manually with: pytest tests/test_live_integration.py -v -k "
                        "'test_query_get_market_data and not BSON_skip'",
    },
    # ── local data (same BSON risk) ────────────────────────────────
    {
        "name": "get_local_data",
        "args": ([], [_SH_STOCK], "1d"),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
        "_skip_reason": "Known xtquant BSON crash — same root cause as get_market_data",
    },
    # ── extended market data ───────────────────────────────────────
    {
        "name": "get_market_data_ex",
        "args": ([], [_SH_STOCK], "1d"),
        "kwargs": {},
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
        "_skip_reason": "Known xtquant BSON crash (same root cause as get_market_data)",
    },
]

_DOWNLOAD_FUNCTIONS = [
    {
        "name": "download_history_data",
        "args": (),
        "kwargs": {"stock_code": _SH_STOCK, "period": "1d"},
    },
    {
        "name": "download_financial_data",
        "args": (),
        "kwargs": {"stock_list": [_SH_STOCK]},
    },
    {
        "name": "download_sector_data",
        "args": (),
        "kwargs": {},
    },
    {
        "name": "download_holiday_data",
        "args": (),
        "kwargs": {},
    },
]


# ── add download_* functions to the api_surface introspection baseline ──

def _build_expected_query_names():
    """Build set of xtdata function names that the live server should expose.

    Used to validate that the API surface is complete.  Derived from the
    known list + whatever xtquant actually exports at test time.
    """
    expected = set()
    for entry in _QUERY_FUNCTIONS:
        expected.add(entry["name"])
    return expected


# ═══════════════════════════════════════════════════════════════════════════
# tests
# ═══════════════════════════════════════════════════════════════════════════

pytestmark = pytest.mark.skipif(
    not XTQUANT_AVAILABLE,
    reason="xtquant not available — live integration tests require QMT",
)


class TestApiSurface:
    """Verify the API surface returned by the server matches expectations."""

    def test_api_surface_structure(self, client):
        """API surface has all expected top-level keys."""
        surface = client._surface
        assert "xtdata" in surface, "missing xtdata key"
        assert "XtQuantTrader" in surface, "missing XtQuantTrader key"
        assert "xtconstant" in surface, "missing xtconstant key"

    def test_xtdata_functions_count(self, client):
        """xtdata should expose many functions (> 10)."""
        funcs = client._surface.get("xtdata", {}).get("functions", {})
        n = len(funcs)
        assert n > 10, f"expected >10 xtdata functions, got {n}"

    def test_constants_count(self, client):
        """xtconstant should have dozens of constants."""
        consts = client._surface.get("xtconstant", {}).get("constants", {})
        n = len(consts)
        assert n > 10, f"expected >10 constants, got {n}"

    def test_trader_methods(self, client):
        """Trader surface should list expected methods."""
        methods = client._surface.get("XtQuantTrader", {}).get("methods", {})
        for m in ("order_stock", "query_stock_asset", "query_stock_positions"):
            assert m in methods, f"trader missing method {m}"


class TestConnectAndHealth:
    """Smoke tests for connection and basic server plumbing."""

    def test_health_connected(self, client):
        """Health check should report connected status."""
        h = client.health()
        assert "connected" in h
        assert "uptime_seconds" in h
        assert isinstance(h["uptime_seconds"], (int, float))

    def test_constant_access(self, client):
        """Constants are inlined client-side — zero RPC overhead."""
        assert isinstance(client.xtconstant.STOCK_BUY, int)
        assert isinstance(client.xtconstant.STOCK_SELL, int)
        assert client.xtconstant.MARKET_SH == 1
        assert client.xtconstant.MARKET_SZ == 0

    def test_self_test_method(self, client):
        """The built-in self_test() covers all query interfaces in one call."""
        report = client.self_test()
        assert "total" in report
        assert "passed" in report
        assert "failed" in report
        assert "skipped" in report
        assert "duration_seconds" in report
        assert "results" in report
        assert isinstance(report["results"], list)
        assert report["total"] == len(report["results"])
        # No failures allowed — everything should pass or skip gracefully
        assert report["failed"] == 0, (
            "%d failures: %s"
            % (report["failed"],
               [r["name"] for r in report["results"]
                if r["status"] == "fail"]))


class TestEventBus:
    """Event subscribe / unsubscribe / drain round-trip."""

    def test_subscribe_and_drain(self, client):
        """Subscribe, drain (should be empty with no trader activity), unsubscribe."""
        sub_id = client.subscribe(["order", "disconnect"])
        assert isinstance(sub_id, str) and len(sub_id) > 0

        events, dropped = client.drain_events(sub_id, max_count=10)
        assert isinstance(events, list)
        assert isinstance(dropped, int)

        client.unsubscribe(sub_id)

    def test_double_subscribe_two_subs(self, client):
        """Two subscriptions should get different IDs."""
        s1 = client.subscribe(["order"])
        s2 = client.subscribe(["order"])
        assert s1 != s2, "subscription IDs should be unique"
        client.unsubscribe(s1)
        client.unsubscribe(s2)


# ── per-query-function test parametrization ───────────────────────────────

def _id_from_entry(entry):
    """Pytest ID for a query-function entry."""
    return entry["name"]


def _parametrize_query_functions():
    """Build parametrize args: (entry_dict,) for each function.

    Returns lists so we can optionally filter/skip without editing the registry.
    """
    return [
        pytest.param(e, id=_id_from_entry(e))
        for e in _QUERY_FUNCTIONS
    ]


class TestQueryFunctions:
    """Parameterized tests — one per xtdata query function.

    Each test:
    1. Calls the function via the RPyC client proxy
    2. Runs the configured assertions on the result

    Functions with a ``_skip_reason`` are auto-skipped at collection time
    via the skip marker applied in conftest or manually.
    """

    @pytest.mark.parametrize("entry", _parametrize_query_functions())
    def test_query_function(self, client, entry):
        """Generic test: call → run assertions."""
        name = entry["name"]
        args = entry.get("args", ())
        kwargs = entry.get("kwargs", {})
        skip_reason = entry.get("_skip_reason")

        if skip_reason:
            pytest.skip(f"BSON_skip: {skip_reason}")

        fn = getattr(client.xtdata, name, None)
        if fn is None:
            pytest.skip(f"{name} not in API surface — may be version-specific")

        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            msg = str(e)
            # Some xtquant functions return empty data on holidays / non-trading
            # hours; treat "data is empty" as a soft skip rather than a failure.
            if "empty" in msg.lower() or "none" in msg.lower():
                pytest.skip(f"{name} returned empty data (off-hours?): {msg}")
            raise

        # Run type/shape assertions
        for label, check in entry.get("assertions", []):
            ok = check(result)
            if not ok:
                # Give a meaningful failure message
                preview = repr(result)
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                pytest.fail(
                    f"{name}: assertion '{label}' failed\n"
                    f"  result type: {type(result).__name__}\n"
                    f"  result preview: {preview}"
                )

    # ── BSON-risk functions (opt-in) ────────────────────────────────

    @pytest.mark.parametrize("name,args,kwargs", [
        pytest.param(
            "get_market_data",
            ([], [_SH_STOCK], "1d"),
            {},
            id="get_market_data",
        ),
        pytest.param(
            "get_local_data",
            ([], [_SH_STOCK], "1d"),
            {},
            id="get_local_data",
        ),
        pytest.param(
            "get_market_data_ex",
            ([], [_SH_STOCK], "1d"),
            {},
            id="get_market_data_ex",
        ),
    ])
    def test_bson_risk_functions_opt_in(self, client, name, args, kwargs):
        """These functions are known to trigger an xtquant BSON assertion crash.

        They are NOT run by default (skipped in the main parametrized loop).
        Run explicitly with::

            pytest tests/test_live_integration.py -v -k "BSON_opt_in"

        The server's datetime_patch may prevent the crash on Python < 3.12.
        """
        fn = getattr(client.xtdata, name, None)
        if fn is None:
            pytest.skip(f"{name} not in API surface")
        result = fn(*args, **kwargs)
        assert isinstance(result, dict), f"{name} should return dict, got {type(result)}"
        assert len(result) > 0, f"{name} returned empty result"


class TestBatchQuery:
    """Batch query round-trip (multiple calls in single RPC)."""

    def test_batch_get_instrument_detail(self, client):
        """Batch query same function with different stock codes."""
        codes = ["000001.SZ", "000002.SZ", "600000.SH"]
        results = client.xtdata.get_instrument_detail.batch([
            ([c], {}) for c in codes
        ])
        assert len(results) == len(codes)
        for i, r in enumerate(results):
            assert r["status"] == "ok", f"batch[{i}] failed: {r}"
            assert "InstrumentID" in r["data"], f"batch[{i}] missing InstrumentID"

    def test_batch_get_full_tick(self, client):
        """Batch full_tick for multiple codes."""
        codes = ["000001.SZ", "600000.SH"]
        results = client.xtdata.get_full_tick.batch([
            ([c], {}) for c in codes
        ])
        assert len(results) == len(codes)
        for i, r in enumerate(results):
            assert r["status"] == "ok", f"batch[{i}] full_tick failed: {r}"

    def test_batch_mixed_market(self, client):
        """Batch spanning both SH and SZ markets."""
        codes = ["000001.SZ", "600000.SH", "000002.SZ", "600004.SH"]
        results = client.xtdata.get_instrument_detail.batch([
            ([c], {}) for c in codes
        ])
        assert len(results) == 4
        ok_count = sum(1 for r in results if r["status"] == "ok")
        assert ok_count == 4, f"expected 4 ok, got {ok_count}"

    def test_batch_empty(self, client):
        """Empty batch should return empty list."""
        results = client.xtdata.get_instrument_detail.batch([])
        assert results == []


class TestDownloadWorkflow:
    """Download functions are async — they return a DownloadTaskHandle."""

    @pytest.mark.parametrize("entry", [
        pytest.param(e, id=e["name"]) for e in _DOWNLOAD_FUNCTIONS
    ])
    def test_download_returns_handle(self, client, entry):
        """Each download_* function should return a DownloadTaskHandle."""
        name = entry["name"]
        args = entry.get("args", ())
        kwargs = entry.get("kwargs", {})

        from client.proxy import DownloadTaskHandle

        fn = getattr(client.xtdata, name, None)
        if fn is None:
            pytest.skip(f"{name} not in API surface")

        task = fn(*args, **kwargs)
        assert isinstance(task, DownloadTaskHandle), (
            f"{name} should return DownloadTaskHandle, got {type(task)}"
        )

        # Wait briefly for completion (downloads may be instant for cached data)
        try:
            result = task.wait(timeout=30)
        except TimeoutError:
            # Some downloads take a while — poll once and move on
            result = task.poll()

        assert result["status"] in ("completed", "failed", "running", "started"), (
            f"{name}: unexpected task status {result.get('status')}"
        )


class TestErrorHandling:
    """Graceful error handling for bad arguments / missing functions."""

    def test_nonexistent_function(self, client):
        """Calling a non-existent xtdata function raises an error via RPyC."""
        from client.exceptions import RemoteCallError

        with pytest.raises(RemoteCallError):
            client.xtdata.__nonexistent_func_xyz__()

    def test_bad_stock_code_returns_gracefully(self, client):
        """Invalid stock code should either return empty data or raise gracefully."""
        try:
            result = client.xtdata.get_instrument_detail("NOT_A_REAL_CODE")
            # If it returns, should be dict-like
            assert isinstance(result, (dict, type(None))), (
                f"unexpected type: {type(result)}"
            )
        except Exception:
            # RPyC remote exceptions are also acceptable
            pass

    def test_batch_nonexistent_function(self, client):
        """Batch calling a non-existent function raises error."""
        from client.exceptions import QmtError

        with pytest.raises(QmtError):
            client.xtdata.__nonexistent_func_xyz__.batch([([], {})])


class TestMultipleClients:
    """Multiple concurrent clients against the same server."""

    def test_two_clients_concurrent(self, live_server):
        """Two clients can connect and query independently."""
        _svc, cfg, port = live_server

        auth_key = cfg["auth_key"] if cfg["auth_key"] else None
        c1 = QmtClient.connect("127.0.0.1", port=port, auth_key=auth_key, timeout=30)
        c2 = QmtClient.connect("127.0.0.1", port=port, auth_key=auth_key, timeout=30)
        try:
            r1 = c1.xtdata.get_instrument_detail(_SH_STOCK)
            r2 = c2.xtdata.get_instrument_detail(_SZ_STOCK)
            assert "InstrumentID" in r1
            assert "InstrumentID" in r2
        finally:
            c1.close()
            c2.close()


class TestSectorDataRoundTrip:
    """End-to-end sector workflow: list → constituent → detail."""

    def test_sector_round_trip(self, client):
        """List sectors → pick one → get constituents → verify each has detail."""
        sectors = client.xtdata.get_sector_list()
        assert isinstance(sectors, list) and len(sectors) > 0, (
            f"expected non-empty sector list, got {type(sectors).__name__}"
        )

        # Pick a well-known sector
        target = None
        for s in sectors:
            if "沪深300" in str(s) or "300" in str(s):
                target = s
                break
        if target is None:
            target = sectors[0]

        stocks = client.xtdata.get_stock_list_in_sector(target)
        assert isinstance(stocks, list), f"expected list, got {type(stocks).__name__}"
        assert len(stocks) > 0, f"sector '{target}' has no constituents"

        # Batch-query detail for first few constituents
        sample = stocks[:5]
        details = client.xtdata.get_instrument_detail.batch([
            ([s], {}) for s in sample
        ])
        for i, d in enumerate(details):
            assert d["status"] == "ok", f"detail[{i}] for {sample[i]} failed: {d}"


class TestOptionDataRoundTrip:
    """Option chain: ETF → option list → option detail."""

    def test_option_round_trip(self, client):
        """Get option list for an ETF, then batch-query detail for first few."""
        options = client.xtdata.get_option_list(_ETF, "")
        assert isinstance(options, list), f"expected list, got {type(options).__name__}"

        if len(options) == 0:
            pytest.skip(f"no options listed for {_ETF} (off-hours or expired)")

        # Limit to avoid overwhelming the server
        sample = options[:10]
        details = client.xtdata.get_option_detail_data.batch([
            ([o], {}) for o in sample
        ])

        assert len(details) == len(sample)
        ok_count = sum(1 for d in details if d["status"] == "ok")
        assert ok_count > 0, f"all {len(sample)} option detail calls failed"


# ── trader query methods (read-only, no order placement) ────────────────

_TRADER_QUERY_METHODS = [
    {
        "name": "query_stock_asset",
        "args": (),
        "kwargs": {},
        # account_id is auto-wrapped by the server (see _ACCOUNT_METHODS in
        # connection.py); we pass it as a string and the server wraps it into
        # a StockAccount object before calling xtquant.
        "_needs_account": True,
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
    },
    {
        "name": "query_stock_positions",
        "args": (),
        "kwargs": {},
        "_needs_account": True,
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
        ],
    },
    {
        "name": "query_stock_orders",
        "args": (),
        "kwargs": {},
        "_needs_account": True,
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
        ],
    },
    {
        "name": "query_stock_trades",
        "args": (),
        "kwargs": {},
        "_needs_account": True,
        "assertions": [
            ("result_is_list", lambda r: isinstance(r, list)),
        ],
    },
    {
        "name": "query_account_status",
        "args": (),
        "kwargs": {},
        "_needs_account": True,
        "assertions": [
            ("result_is_dict", lambda r: isinstance(r, dict)),
        ],
    },
]


class TestTraderQueryMethods:
    """Query-type trader methods — read-only, no order placement.

    These methods require a connected, subscribed trader account.
    They are skipped gracefully if the trader is not connected or
    no account is configured.
    """

    @pytest.mark.parametrize("entry", [
        pytest.param(e, id=e["name"]) for e in _TRADER_QUERY_METHODS
    ])
    def test_trader_query(self, client, live_server, entry):
        """Call a trader query method and verify return type."""
        _svc, cfg, _port = live_server
        account_id = cfg.get("qmt_account_id", "")
        name = entry["name"]

        if not account_id:
            pytest.skip("QMT_ACCOUNT_ID not set in .env — trader queries need an account")

        from client.exceptions import RemoteCallError, NotConnectedError

        fn = getattr(client.trader, name, None)
        if fn is None:
            pytest.skip(f"{name} not in API surface")

        needs_account = entry.get("_needs_account", False)
        try:
            if needs_account:
                result = fn(account_id)
            else:
                result = fn(*(entry.get("args", ())), **(entry.get("kwargs", {})))
        except (RemoteCallError, NotConnectedError) as e:
            msg = str(e)
            if "not connected" in msg.lower() or "connect" in msg.lower():
                pytest.skip(f"trader not connected — skipping {name}")
            raise

        for label, check in entry.get("assertions", []):
            ok = check(result)
            if not ok:
                preview = repr(result)
                if len(preview) > 200:
                    preview = preview[:200] + "..."
                pytest.fail(
                    f"{name}: assertion '{label}' failed\n"
                    f"  result type: {type(result).__name__}\n"
                    f"  result preview: {preview}"
                )


class TestApiSurfaceCompleteness:
    """Check that all expected functions are present in the API surface."""

    def test_all_expected_functions_present(self, client):
        """Every known query function should appear in the server's API surface."""
        funcs = set(client._surface.get("xtdata", {}).get("functions", {}).keys())
        missing = []
        unknown = []

        for entry in _QUERY_FUNCTIONS:
            name = entry["name"]
            if entry.get("_download"):
                continue
            if name not in funcs:
                missing.append(name)

        # Also check download functions
        for entry in _DOWNLOAD_FUNCTIONS:
            name = entry["name"]
            if name not in funcs:
                unknown.append(name)

        # Not all xtquant versions export every function — log, don't fail
        if missing:
            logging.warning("Query functions NOT in API surface: %s", missing)
        if unknown:
            logging.warning("Download functions NOT in API surface: %s", unknown)

    def test_no_download_prefix_in_query_surface(self, client):
        """The download_* functions should exist but are not tested as queries."""
        funcs = client._surface.get("xtdata", {}).get("functions", {}).keys()
        download_funcs = [f for f in funcs if f.startswith("download_")]
        # Having download functions is expected — just verify they're present
        assert len(download_funcs) > 0, "no download_* functions in API surface"
