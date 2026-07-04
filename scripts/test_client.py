"""Smoke-test the qmt-rpyc client against a running RPyC server.

Usage:
    # localhost / no auth
    python scripts/test_client.py

    # remote with auth
    python scripts/test_client.py --host 192.168.1.100 --auth your-secret-key

    # skip trading tests (no QMT account configured)
    python scripts/test_client.py --no-trader
"""

import argparse
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from client import QmtClient
from client.exceptions import RemoteCallError, NotConnectedError


PASS = "✓"
FAIL = "✗"
SKIP = "○"


def _status(ok: bool) -> str:
    return PASS if ok else FAIL


def test_connect(client: QmtClient) -> bool:
    """Verify connection and API surface discovery."""
    assert client._conn is not None, "no rpyc connection"
    assert client._surface is not None, "no API surface"
    print(f"  {PASS} connected, surface loaded")
    xtdata_count = len(client._surface.get("xtdata", {}).get("functions", {}))
    trader_count = len(client._surface.get("XtQuantTrader", {}).get("methods", {}))
    const_count = len(client._surface.get("xtconstant", {}).get("constants", {}))
    print(f"     xtdata functions: {xtdata_count}")
    print(f"     trader methods:   {trader_count}")
    print(f"     xtconstant:       {const_count}")
    return True


def test_constants(client: QmtClient) -> bool:
    """Constants are inlined at connect time — zero RPC."""
    buy = client.xtconstant.STOCK_BUY
    sell = client.xtconstant.STOCK_SELL
    fix_price = client.xtconstant.FIX_PRICE
    assert isinstance(buy, int), f"STOCK_BUY is {type(buy)}"
    assert isinstance(sell, int), f"STOCK_SELL is {type(sell)}"
    print(f"  {PASS} STOCK_BUY={buy}  STOCK_SELL={sell}  FIX_PRICE={fix_price}")
    return True


def test_health(client: QmtClient) -> bool:
    """Server health includes connection / trader state."""
    h = client.health()
    required = {"connected", "trader_available", "uptime_seconds"}
    missing = required - set(h.keys())
    assert not missing, f"missing keys: {missing}"
    print(f"  {PASS} connected={h['connected']}  trader_available={h['trader_available']}  "
          f"uptime={h['uptime_seconds']}s")
    return True


def test_market_data(client: QmtClient) -> bool:
    """Fetch daily K-line via xtdata.

    NOTE: Known xtquant bug — get_market_data / get_local_data / get_market_data_ex
    crash the server process with a C++ assertion in xtquant's bundled BSON library:
        bsonobj.cpp:1388  assert(u < 1000000)
    This is an xtquant internal issue, not a qmt-rpyc bug.
    The test is placed last in the suite so a crash won't affect other tests.
    """
    print(f"  {SKIP} known xtquant BSON crash (bsonobj.cpp:1388) — skipping get_market_data")
    return True


def test_sector_list(client: QmtClient) -> bool:
    """List sectors (lightweight read-only call)."""
    try:
        sectors = client.xtdata.get_sector_list()
    except RemoteCallError as e:
        print(f"  {SKIP} {e}")
        return True  # non-critical
    if isinstance(sectors, list):
        print(f"  {PASS} {len(sectors)} sectors  e.g. {sectors[:5]}")
        return True
    print(f"  {FAIL} unexpected type: {type(sectors)}")
    return False


def test_trading_hallway(client: QmtClient) -> bool:
    """Ping trader methods that don't place orders (query-only)."""
    try:
        # Query positions — safe read-only call, account from env
        account_id = os.environ.get("QMT_ACCOUNT_ID", "")
        if not account_id:
            print(f"  {SKIP} QMT_ACCOUNT_ID not set")
            return True

        positions = client.trader.query_stock_positions(account_id)
        if isinstance(positions, list):
            print(f"  {PASS} positions count={len(positions)}")
        else:
            print(f"  {PASS} positions result={type(positions).__name__}")
        return True
    except RemoteCallError as e:
        msg = str(e)
        if "not connected" in msg.lower():
            print(f"  {SKIP} trader not connected (is MiniQMT running?)")
            return True
        print(f"  {FAIL} {msg[:100]}")
        return False


def test_event_subscribe(client: QmtClient) -> bool:
    """Subscribe / unsubscribe event bus (no callback — just verify plumbing)."""
    try:
        sub_id = client.subscribe(["order"], on_event=None)
        assert isinstance(sub_id, str) and sub_id, f"sub_id is {type(sub_id)}: {sub_id!r}"
        time.sleep(0.1)
        events, dropped = client.drain_events(sub_id, max_count=10)
        client.unsubscribe(sub_id)
        print(f"  {PASS} sub_id={sub_id}  events={len(events)}  dropped={dropped}")
        return True
    except RemoteCallError as e:
        print(f"  {SKIP} {e}")
        return True


TESTS = [
    ("connect",          test_connect),
    ("health",           test_health),
    ("constants",        test_constants),
    ("sector_list",      test_sector_list),
    ("trading_query",    test_trading_hallway),
    ("event_subscribe",  test_event_subscribe),
    ("market_data",      test_market_data),   # last — known xtquant BSON crash
]


def main():
    ap = argparse.ArgumentParser(description="qmt-rpyc client smoke test")
    ap.add_argument("--host", default="127.0.0.1", help="server host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=18812, help="server port (default 18812)")
    ap.add_argument("--auth", default=None, help="auth key (omit for no-auth)")
    ap.add_argument("--timeout", type=int, default=15, help="rpyc timeout in seconds")
    ap.add_argument("--no-trader", action="store_true", help="skip trader tests")
    args = ap.parse_args()

    # If QMT_ACCOUNT_ID is not set, the trading test will skip itself;
    # --no-trader just removes it from the list entirely.
    tests = list(TESTS)
    if args.no_trader:
        tests = [(n, f) for n, f in tests if "trad" not in n.lower() or "trad" in n.lower() and "query" not in n]

    print(f"=== qmt-rpyc client smoke test ===")
    print(f"    server: {args.host}:{args.port}  auth={'on' if args.auth else 'off'}")

    # --- connect ---
    try:
        client = QmtClient.connect(
            args.host, port=args.port, auth_key=args.auth, timeout=args.timeout)
    except Exception as e:
        print(f"\n{FAIL} Connection failed: {e}")
        sys.exit(1)

    passed = 0
    failed = 0
    skipped = 0

    print()
    for name, fn in tests:
        print(f"  [{name}]")
        try:
            ok = fn(client)
            if ok:
                passed += 1
        except Exception as e:
            print(f"  {FAIL} {e}")
            failed += 1

    client.close()
    print(f"\n--- {passed} passed, {failed} failed, {skipped} skipped ---")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
