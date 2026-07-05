# QmtClient.self_test() — Client Self-Test Method

**Date:** 2026-07-05
**Status:** approved

## Motivation

Currently, the only way to verify a qmt-rpyc client setup end-to-end is to run
`scripts/test_client.py` as a standalone script, or the pytest-based
`tests/test_live_integration.py`.  Neither is available in the installed client
package (`pip install -e .` only installs `client/` and `common/`).

Add a `self_test()` method directly on `QmtClient` so users can verify their
connection and all query interfaces with a single call after connecting.

`scripts/test_client.py` is removed — its functionality is subsumed by the new
method (for runtime use) and `tests/test_live_integration.py` (for automated
testing).

## Design

### API

```python
class QmtClient:
    def self_test(
        self,
        test_symbols: dict | None = None,
        include_bson_risk: bool = False,
        timeout: float = 30.0,
    ) -> dict:
        """Run a self-test against all read-only query interfaces.

        Prints real-time ✓/✗/○ results to stdout, then returns a structured
        report dict.

        Args:
            test_symbols: Optional override for test symbols.  Keys:
                sh_stock (default "600000.SH"),
                sz_stock (default "000001.SZ"),
                etf     (default "510050.SH"),
                sector  (default "沪深300"),
                market  (default "SSE").
            include_bson_risk: If True, also test get_market_data,
                get_local_data, get_market_data_ex (known xtquant BSON crash).
            timeout: Per-call timeout in seconds (passed through to rpyc).

        Returns:
            dict with keys: total, passed, failed, skipped, duration_seconds,
            results (list of per-test result dicts).
        """
```

### Return value

```python
{
    "total": 25,
    "passed": 18,
    "failed": 0,
    "skipped": 7,
    "duration_seconds": 3.2,
    "results": [
        {
            "name": "get_instrument_detail",
            "category": "instrument",
            "status": "pass",       # "pass" | "fail" | "skip"
            "duration_ms": 12,
            "detail": "",
        },
        {
            "name": "get_market_data",
            "category": "market",
            "status": "skip",
            "duration_ms": 0,
            "detail": "BSON crash risk — use include_bson_risk=True",
        },
        {
            "name": "query_stock_asset",
            "category": "trader",
            "status": "skip",
            "duration_ms": 0,
            "detail": "QMT_ACCOUNT_ID not set",
        },
        ...
    ],
}
```

### Test coverage

All functions are **read-only**.  No order placement, cancellation, or
state-mutating calls.

| Category | Functions | Notes |
|---|---|---|
| smoke | `health`, constants (`STOCK_BUY`, `MARKET_SH`) | Basic connectivity |
| instrument | `get_instrument_detail` | Also tested via batch |
| tick | `get_full_tick` | |
| calendar | `get_trading_calendar`, `get_holiday`, `get_trading_time` | |
| sector | `get_sector_list`, `get_stock_list_in_sector` | |
| index | `get_index_weight` | |
| dividend | `get_divid_factors` | |
| option | `get_option_list`, `get_option_detail_data` | `get_option_detail_data` tested via batch if options are available |
| futures | `get_main_contract`, `get_FutureInfo` | |
| etf | `get_etf_info`, `get_etf_weight` | |
| ipo | `get_ipo_info` | |
| download | `download_history_data`, `download_financial_data`, `download_sector_data`, `download_holiday_data` | Only verify they return `DownloadTaskHandle`; do NOT wait for completion |
| trader | `query_stock_asset`, `query_stock_positions`, `query_stock_orders`, `query_stock_trades`, `query_account_status` | Skipped if `QMT_ACCOUNT_ID` not set or trader not connected |
| batch | `get_instrument_detail.batch(...)`, `get_full_tick.batch(...)` | Cross-market batch |
| bson-risk | `get_market_data`, `get_local_data`, `get_market_data_ex` | **Skipped by default**; opt-in via `include_bson_risk=True`. Known xtquant BSON assertion crash on some versions. |

### Output format (stdout)

```
=== qmt-rpyc client self-test ===
  [smoke]
  ✓ health                        (2ms)
  ✓ constants                     (0ms)
  [instrument]
  ✓ get_instrument_detail         (12ms)
  ✓ get_instrument_detail (batch) (15ms)
  [tick]
  ✓ get_full_tick                 (45ms)
  [calendar]
  ✓ get_trading_calendar          (8ms)
  ✓ get_holiday                   (5ms)
  ...
  [bson-risk]
  ○ get_market_data               — BSON crash risk, skipped
  [trader]
  ○ query_stock_asset             — no account configured
  ...
--- 18 passed, 0 failed, 7 skipped in 3.2s ---
```

### Implementation location

`client/client.py` — new method `self_test()` on `QmtClient`, plus a
module-level constant `_SELF_TEST_CASES: list[dict]` defining the test
registry.

Test discovery uses `getattr(client.xtdata, name, None)` and
`getattr(client.trader, name, None)` dynamically, so it gracefully handles
missing functions (version differences in xtquant).

### Files changed

| File | Change |
|---|---|
| `client/client.py` | Add `self_test()` method + `_SELF_TEST_CASES` constant (~150 lines) |
| `scripts/test_client.py` | **Deleted** |
| `tests/test_live_integration.py` | Unchanged — serves different purpose (full server lifecycle, pytest-based) |

### Non-goals

- Does NOT replace `tests/test_live_integration.py` — that file tests the full
  server lifecycle (start → connect → query → stop) and is designed for
  automated CI. `self_test()` is for runtime verification of an already-
  connected client.
- Does NOT test order placement or any mutating trader methods.
- Does NOT wait for download completion (too slow for a quick self-test).

## Error handling

- Per-call exceptions are caught, logged at DEBUG, and recorded as `"fail"`
  with the error message in `detail`.
- A single failing call does NOT abort the remaining tests.
- If the client is not connected, `self_test()` raises `QmtError` immediately
  (pre-condition check).
- Functions not found in the API surface are recorded as `"skip"` (version
  differences), not `"fail"`.
