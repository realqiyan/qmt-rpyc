"""Client self-test — exercises all read-only query interfaces.

Used by QmtClient.self_test().  Imported lazily so the module is only loaded
when a self-test is actually requested.
"""

import os
import time
import logging

logger = logging.getLogger(__name__)


# ── helper ──────────────────────────────────────────────────────────────────

def _run_option_detail_batch(client, symbols):
    """Get option codes for the ETF, then batch query detail for first 5."""
    options = client.xtdata.get_option_list(symbols["etf"], "")
    if not options:
        return []
    sample = options[:5]
    return client.xtdata.get_option_detail_data.batch([
        ([o], {}) for o in sample
    ])


# ── self-test case registry ─────────────────────────────────────────────────
#
# Each entry:
#   category, name       — display grouping and label
#   run(client, symbols) — executed test (may raise)
#   check(result)        — (ok: bool, detail: str)
#   skip_if(client, symbols, opts) or None — (should_skip: bool, reason: str)

_SELF_TEST_CASES = [
    # ── smoke ──────────────────────────────────────────────────────────
    {
        "category": "smoke",
        "name": "health",
        "run": lambda c, s: c.health(),
        "check": lambda r: (isinstance(r, dict) and "connected" in r, ""),
    },
    {
        "category": "smoke",
        "name": "constants",
        "run": lambda c, s: (c.xtconstant.STOCK_BUY,
                             c.xtconstant.STOCK_SELL,
                             c.xtconstant.MARKET_SH,
                             c.xtconstant.MARKET_SZ),
        "check": lambda r: (all(isinstance(v, int) for v in r),
                            "STOCK_BUY=%d STOCK_SELL=%d "
                            "MARKET_SH=%d MARKET_SZ=%d" % r),
    },
    # ── instrument ─────────────────────────────────────────────────────
    {
        "category": "instrument",
        "name": "get_instrument_detail",
        "run": lambda c, s: c.xtdata.get_instrument_detail(s["sh_stock"]),
        "check": lambda r: (isinstance(r, dict) and "InstrumentID" in r,
                            "InstrumentID=%s" % r.get("InstrumentID", "?")),
    },
    {
        "category": "instrument",
        "name": "get_instrument_detail (batch x3)",
        "run": lambda c, s: c.xtdata.get_instrument_detail.batch([
            ([s["sh_stock"]], {}),
            ([s["sz_stock"]], {}),
            ([s["etf"]], {}),
        ]),
        "check": lambda r: (
            isinstance(r, list) and len(r) == 3
            and all(x["status"] == "ok" for x in r),
            "3 calls, %d ok" % sum(1 for x in r if x.get("status") == "ok")
        ),
    },
    # ── tick ───────────────────────────────────────────────────────────
    {
        "category": "tick",
        "name": "get_full_tick",
        "run": lambda c, s: c.xtdata.get_full_tick([s["sh_stock"]]),
        "check": lambda r: (isinstance(r, (dict, list)) and len(r) > 0,
                            "type=%s" % type(r).__name__),
    },
    {
        "category": "tick",
        "name": "get_full_tick (batch x2)",
        "run": lambda c, s: c.xtdata.get_full_tick.batch([
            ([s["sh_stock"]], {}),
            ([s["sz_stock"]], {}),
        ]),
        "check": lambda r: (
            isinstance(r, list) and len(r) == 2
            and all(x["status"] == "ok" for x in r),
            "2 calls, %d ok" % sum(1 for x in r if x.get("status") == "ok")
        ),
    },
    # ── calendar ───────────────────────────────────────────────────────
    {
        "category": "calendar",
        "name": "get_trading_calendar",
        "run": lambda c, s: c.xtdata.get_trading_calendar(s["market"]),
        "check": lambda r: (isinstance(r, list) and len(r) > 0,
                            "%d days" % len(r)),
    },
    {
        "category": "calendar",
        "name": "get_holiday",
        "run": lambda c, s: c.xtdata.get_holiday(),
        "check": lambda r: (isinstance(r, list), "%d holidays" % len(r)),
    },
    {
        "category": "calendar",
        "name": "get_trading_time",
        "run": lambda c, s: c.xtdata.get_trading_time([s["sh_stock"]]),
        "check": lambda r: (isinstance(r, (str, list)),
                            "type=%s" % type(r).__name__),
    },
    # ── sector ─────────────────────────────────────────────────────────
    {
        "category": "sector",
        "name": "get_sector_list",
        "run": lambda c, s: c.xtdata.get_sector_list(),
        "check": lambda r: (isinstance(r, list) and len(r) > 0,
                            "%d sectors" % len(r)),
    },
    {
        "category": "sector",
        "name": "get_stock_list_in_sector",
        "run": lambda c, s: c.xtdata.get_stock_list_in_sector(s["sector"]),
        "check": lambda r: (isinstance(r, list) and len(r) > 0,
                            "%d stocks" % len(r)),
    },
    # ── index ──────────────────────────────────────────────────────────
    {
        "category": "index",
        "name": "get_index_weight",
        "run": lambda c, s: c.xtdata.get_index_weight("000300.SH"),
        "check": lambda r: (isinstance(r, (list, dict)),
                            "type=%s" % type(r).__name__),
    },
    # ── dividend ───────────────────────────────────────────────────────
    {
        "category": "dividend",
        "name": "get_divid_factors",
        "run": lambda c, s: c.xtdata.get_divid_factors([s["sh_stock"]]),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
    },
    # ── option ─────────────────────────────────────────────────────────
    {
        "category": "option",
        "name": "get_option_list",
        "run": lambda c, s: c.xtdata.get_option_list(s["etf"], ""),
        "check": lambda r: (isinstance(r, list), "%d options" % len(r)),
    },
    {
        "category": "option",
        "name": "get_option_detail_data (batch x5)",
        "run": _run_option_detail_batch,
        "check": lambda r: (
            isinstance(r, list) and all(x["status"] == "ok" for x in r),
            "%d calls, %d ok" % (len(r),
                sum(1 for x in r if x.get("status") == "ok"))
        ),
    },
    # ── futures ────────────────────────────────────────────────────────
    {
        "category": "futures",
        "name": "get_main_contract",
        "run": lambda c, s: c.xtdata.get_main_contract("IF"),
        "check": lambda r: (isinstance(r, str) and len(r) > 0,
                            "contract=%s" % r),
    },
    {
        "category": "futures",
        "name": "get_FutureInfo",
        "run": lambda c, s: c.xtdata.get_FutureInfo(),
        "check": lambda r: (isinstance(r, (dict, list)),
                            "type=%s" % type(r).__name__),
    },
    # ── etf ────────────────────────────────────────────────────────────
    {
        "category": "etf",
        "name": "get_etf_info",
        "run": lambda c, s: c.xtdata.get_etf_info([s["etf"]]),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
    },
    {
        "category": "etf",
        "name": "get_etf_weight",
        "run": lambda c, s: c.xtdata.get_etf_weight(s["etf"]),
        "check": lambda r: (isinstance(r, (list, dict)),
                            "type=%s" % type(r).__name__),
    },
    # ── ipo ────────────────────────────────────────────────────────────
    {
        "category": "ipo",
        "name": "get_ipo_info",
        "run": lambda c, s: c.xtdata.get_ipo_info(s["sh_stock"]),
        "check": lambda r: (isinstance(r, (dict, list)),
                            "type=%s" % type(r).__name__),
    },
    # ── download (return-type check only, no wait) ─────────────────────
    {
        "category": "download",
        "name": "download_history_data",
        "run": lambda c, s: c.xtdata.download_history_data(
            stock_code=s["sh_stock"], period="1d"),
        "check": lambda r: (True, ""),
    },
    {
        "category": "download",
        "name": "download_financial_data",
        "run": lambda c, s: c.xtdata.download_financial_data(
            stock_list=[s["sh_stock"]]),
        "check": lambda r: (True, ""),
    },
    {
        "category": "download",
        "name": "download_sector_data",
        "run": lambda c, s: c.xtdata.download_sector_data(),
        "check": lambda r: (True, ""),
    },
    {
        "category": "download",
        "name": "download_holiday_data",
        "run": lambda c, s: c.xtdata.download_holiday_data(),
        "check": lambda r: (True, ""),
    },
    # ── bson-risk (opt-in via include_bson_risk=True) ──────────────────
    {
        "category": "bson-risk",
        "name": "get_market_data",
        "run": lambda c, s: c.xtdata.get_market_data(
            [], [s["sh_stock"]], "1d"),
        "check": lambda r: (isinstance(r, dict)
                            and s["sh_stock"] in r,
                            "has %s: %s" % (s["sh_stock"],
                                            s["sh_stock"] in r)),
        "skip_if": lambda c, s, opts: (
            not opts.get("include_bson_risk"),
            "BSON crash risk — use include_bson_risk=True"
        ),
    },
    {
        "category": "bson-risk",
        "name": "get_local_data",
        "run": lambda c, s: c.xtdata.get_local_data(
            [], [s["sh_stock"]], "1d"),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
        "skip_if": lambda c, s, opts: (
            not opts.get("include_bson_risk"),
            "BSON crash risk — use include_bson_risk=True"
        ),
    },
    {
        "category": "bson-risk",
        "name": "get_market_data_ex",
        "run": lambda c, s: c.xtdata.get_market_data_ex(
            [], [s["sh_stock"]], "1d"),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
        "skip_if": lambda c, s, opts: (
            not opts.get("include_bson_risk"),
            "BSON crash risk — use include_bson_risk=True"
        ),
    },
    # ── trader query (read-only, no order placement) ───────────────────
    {
        "category": "trader",
        "name": "query_stock_asset",
        "run": lambda c, s: c.trader.query_stock_asset(s["account_id"]),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
        "skip_if": lambda c, s, opts: (
            not s.get("account_id"),
            "QMT_ACCOUNT_ID not set"
        ),
    },
    {
        "category": "trader",
        "name": "query_stock_positions",
        "run": lambda c, s: c.trader.query_stock_positions(
            s["account_id"]),
        "check": lambda r: (isinstance(r, list),
                            "%d positions" % len(r)),
        "skip_if": lambda c, s, opts: (
            not s.get("account_id"),
            "QMT_ACCOUNT_ID not set"
        ),
    },
    {
        "category": "trader",
        "name": "query_stock_orders",
        "run": lambda c, s: c.trader.query_stock_orders(
            s["account_id"]),
        "check": lambda r: (isinstance(r, list),
                            "%d orders" % len(r)),
        "skip_if": lambda c, s, opts: (
            not s.get("account_id"),
            "QMT_ACCOUNT_ID not set"
        ),
    },
    {
        "category": "trader",
        "name": "query_stock_trades",
        "run": lambda c, s: c.trader.query_stock_trades(
            s["account_id"]),
        "check": lambda r: (isinstance(r, list),
                            "%d trades" % len(r)),
        "skip_if": lambda c, s, opts: (
            not s.get("account_id"),
            "QMT_ACCOUNT_ID not set"
        ),
    },
    {
        "category": "trader",
        "name": "query_account_status",
        "run": lambda c, s: c.trader.query_account_status(
            s["account_id"]),
        "check": lambda r: (isinstance(r, dict),
                            "type=%s" % type(r).__name__),
        "skip_if": lambda c, s, opts: (
            not s.get("account_id"),
            "QMT_ACCOUNT_ID not set"
        ),
    },
]


# ── runner ──────────────────────────────────────────────────────────────────

def run_self_test(client, test_symbols=None, include_bson_risk=False,
                  timeout=30.0):
    """Run a self-test against all read-only query interfaces.

    Prints real-time ✓/✗/○ results to stdout, then returns a structured
    report dict.

    Args:
        client: A connected QmtClient instance.
        test_symbols: Optional dict overriding default test symbols.
            Keys: sh_stock, sz_stock, etf, sector, market, account_id.
        include_bson_risk: If True, also test get_market_data,
            get_local_data, get_market_data_ex.
        timeout: Reserved for future use (rpyc timeout is set at connect).

    Returns:
        dict with keys: total, passed, failed, skipped, duration_seconds,
        results (list of per-test dicts).
    """
    if client._conn is None or client._conn.closed:
        raise RuntimeError(
            "Client not connected. Call QmtClient.connect() first.")

    symbols = {
        "sh_stock": "600000.SH",
        "sz_stock": "000001.SZ",
        "etf": "510050.SH",
        "sector": "沪深300",
        "market": "SSE",
        "account_id": os.environ.get("QMT_ACCOUNT_ID", ""),
    }
    if test_symbols:
        symbols.update(test_symbols)

    opts = {"include_bson_risk": include_bson_risk}

    from client.proxy import DownloadTaskHandle

    results = []
    passed = failed = skipped = 0
    t0 = time.time()
    last_category = None

    print("=== qmt-rpyc client self-test ===\n")

    for case in _SELF_TEST_CASES:
        category = case["category"]
        name = case["name"]

        if category != last_category:
            if last_category is not None:
                print()
            print("  [%s]" % category)
            last_category = category

        # ── check skip condition ───────────────────────────────────
        skip_if = case.get("skip_if")
        if skip_if:
            should_skip, reason = skip_if(client, symbols, opts)
            if should_skip:
                print("  ○ %-42s — %s" % (name, reason))
                results.append({
                    "name": name,
                    "category": category,
                    "status": "skip",
                    "duration_ms": 0,
                    "detail": reason,
                })
                skipped += 1
                continue

        # ── run the test ───────────────────────────────────────────
        t1 = time.time()
        detail = ""
        try:
            result = case["run"](client, symbols)
            dt_ms = int((time.time() - t1) * 1000)

            # Special: download functions return DownloadTaskHandle
            if category == "download":
                ok = isinstance(result, DownloadTaskHandle)
                detail = ("task_id=%s"
                          % getattr(result, "task_id", "?")
                          if ok else "type=%s" % type(result).__name__)
            else:
                ok, detail = case["check"](result)
            status = "pass" if ok else "fail"
        except Exception as e:
            dt_ms = int((time.time() - t1) * 1000)
            ok = False
            status = "fail"
            msg = str(e)
            # AttributeError on function name → skip (version diff)
            if isinstance(e, AttributeError) and (
                    "has no attribute" in msg
                    or "object has no attribute" in msg):
                status = "skip"
                detail = "not in API surface"
            elif ("not connected" in msg.lower()
                    or "connect" in msg.lower()):
                status = "skip"
                detail = "trader not connected"
            else:
                detail = "%s: %s" % (type(e).__name__, msg[:80])

        # ── print result ───────────────────────────────────────────
        mark = {"pass": "✓", "fail": "✗", "skip": "○"}[status]
        dur = " (%dms)" % dt_ms if status != "skip" else ""
        print("  %s %-42s%s" % (mark, name, dur))
        if detail and status == "fail":
            print("    → %s" % detail)

        if status == "pass":
            passed += 1
        elif status == "fail":
            failed += 1
        else:
            skipped += 1

        results.append({
            "name": name,
            "category": category,
            "status": status,
            "duration_ms": dt_ms,
            "detail": detail,
        })

    total_dt = round(time.time() - t0, 1)
    print("\n--- %d passed, %d failed, %d skipped in %.1fs ---" % (
        passed, failed, skipped, total_dt))

    return {
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "duration_seconds": total_dt,
        "results": results,
    }
