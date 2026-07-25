# qmt-rpyc

RPyC-based bridge exposing the full [xtquant](https://dict.thinktrader.net/) SDK (QMT/MiniQMT quantitative trading) to Linux/macOS clients.

## Quick Start

### Server (Windows)

**Prerequisites:**

- MiniQMT installed and running
- Python **3.10 or 3.11** — xtquant ships native `.pyd` extensions for cp36–cp311 only (no cp312+)

**One-click setup:**

```bat
scripts\setup.bat
```

This will: check Python version → create `.venv` → install dependencies → wire xtquant → create `.env`.

**Run the server:**

```bat
start-rpyc.bat
```

### Client (Linux/macOS/Windows)

```bash
# One-click client setup
scripts/setup.sh
```

Or install directly:

```bash
pip install -e .
```

```python
from client import QmtClient

client = QmtClient.connect("192.168.1.100", port=18812, auth_key="your-secret-key")

# Market data (returns JSON-safe dict, DataFrame split orient)
result = client.xtdata.get_market_data(
    stock_list=["600000.SH"], period="1d", start_time="20240101", count=20)

# Constants (inlined at connect time, zero RPC)
print(client.xtconstant.STOCK_BUY)  # → 23

# Trading (account_id passed as string, server auto-wraps StockAccount)
order_id = client.trader.order_stock("1000000365", "600000.SH",
                                      client.xtconstant.STOCK_BUY, 100,
                                      client.xtconstant.FIX_PRICE, 0.1)

# Batch calls — execute N calls to the same xtdata function in one RPC round-trip
results = client.xtdata.get_instrument_detail.batch([
    (["600000.SH"], {}),
    (["000001.SZ"], {}),
    (["510050.SH"], {}),
])
# results[i] = {"status": "ok", "data": {...}} or {"status": "error", ...}

# Health
print(client.health())

# Self-test — exercises all read-only query interfaces, prints ✓/✗/○ report
report = client.self_test()
print(f"{report['passed']} passed, {report['failed']} failed, {report['skipped']} skipped")

# Context manager (auto-closes on exit)
with QmtClient.connect("192.168.1.100", port=18812, auth_key="my-key") as client:
    print(client.health())

client.close()
```

### Dump the Deployed API Surface

Run the dump script with the Windows server virtual environment so it imports
the same broker-customized xtquant build as the server:

```bat
.venv\Scripts\python.exe scripts\dump_api_surface.py
```

The JSON file is written to `api_surface.json` in the project root by default.
It contains runtime version metadata, discovered xtdata functions, trader
methods, constants, and xttype classes, but no authentication keys, account
IDs, or local QMT paths. Use `--without-docs` for a smaller file or `--force`
to replace an existing dump.

### Client Package (cross-platform)

```bash
pip install -e .
```

The client package only needs `rpyc>=6.0.0` and works on any platform.

## Configuration (.env)

All server configuration lives in `.env` (copy from `.env.example`):

| Variable | Default | Description |
|---|---|---|
| `QMT_RPYC_HOST` | `0.0.0.0` | Listen address |
| `QMT_RPYC_PORT` | `18812` | Listen port |
| `QMT_RPYC_AUTH_KEY` | *(required)* | HMAC shared secret (client and server must match) |
| `QMT_PATH` | — | QMT/MiniQMT `userdata_mini` directory |
| `QMT_SESSION_ID` | `1` | QMT session ID |
| `QMT_ACCOUNT_ID` | — | Trading account ID (optional; enables heartbeat via `query_stock_asset`) |
| `QMT_RPYC_TLS_KEY` | — | TLS private key path (optional; set with `QMT_RPYC_TLS_CERT` to enable TLS) |
| `QMT_RPYC_TLS_CERT` | — | TLS certificate path |
| `QMT_RPYC_TLS_CA` | — | TLS CA cert path (optional; enables mTLS) |
| `QMT_HEARTBEAT_INTERVAL` | `30` | Seconds between heartbeat pings |
| `QMT_HEARTBEAT_TIMEOUT` | `5` | Seconds before heartbeat times out |
| `QMT_HEARTBEAT_MAX_FAILURES` | `3` | Consecutive failures before triggering reconnect |
| `QMT_RECONNECT_MAX_ATTEMPTS` | `0` | Max reconnect attempts (0 = unlimited) |
| `QMT_BATCH_MAX_WORKERS` | `50` | Max concurrent workers for `batch_call_xtdata` |
| `QMT_RPYC_LOG_DIR` | `logs` | Log directory (daily rotation, 7-day retention) |

## TLS / mTLS

Set `QMT_RPYC_TLS_KEY` + `QMT_RPYC_TLS_CERT` in `.env` to enable TLS on the server.
Set `QMT_RPYC_TLS_CA` to require client certificates (mTLS).

Client connects with:

```python
client = QmtClient.connect(
    host="192.168.1.100", port=18812, auth_key="my-key",
    tls_config={"ca_certs": "/path/to/ca.pem"},
)
```

For mTLS, also pass `certfile` and `keyfile` in `tls_config`.

## Batch API

Every `_RemoteCallable` on `client.xtdata` has a `.batch()` method for executing multiple calls to the same xtdata function in a single RPC round-trip. This is the primary way to reduce latency for option-chain or multi-instrument queries.

**Server side:**
- Calls are executed concurrently via `ThreadPoolExecutor` with `max_workers = min(len(calls), QMT_BATCH_MAX_WORKERS)` (env-configurable, default 50)
- Max 500 calls per batch (hard cap `_BATCH_MAX_CALLS`)
- `download_*` functions are rejected in batch mode (use `call_xtdata` for async downloads)
- Each call independently materializes args (RPyC netref → plain Python), calls xtdata, and serializes the result

**Client side:**

```python
# Get all option codes, then batch-query detail for each
codes = client.xtdata.get_option_list("510050.SH", "")
results = client.xtdata.get_option_detail_data.batch([
    ([code], {}) for code in codes
])

# Each result carries independent status
for i, r in enumerate(results):
    if r["status"] == "ok":
        print(f"{codes[i]}: {r['data']}")
    else:
        print(f"{codes[i]}: ERROR [{r['error_type']}] {r['error_message']}")

# Errors are per-call — partial success is normal
ok_count = sum(1 for r in results if r["status"] == "ok")
print(f"{ok_count}/{len(results)} succeeded")
```

Error handling:
- Per-call failures → individual result with `status: "error"`, `error_type`, `error_message`
- Batch-level rejections (download function, batch too large, nonexistent function) → raises `QmtError`

## Client Self-Test

`QmtClient.self_test()` exercises all read-only query interfaces against the live server and prints a real-time ✓/✗/○ report:

```python
client = QmtClient.connect("192.168.1.100", port=18812, auth_key="my-key")

# Run all tests with default symbols
report = client.self_test()

# Override symbols, skip trader tests by clearing account_id
report = client.self_test(test_symbols={
    "sh_stock": "601318.SH",
    "etf": "159919.SZ",
    "account_id": "",  # skip all trader tests
})

# Inspect individual results
for item in report["results"]:
    print(f"{item['status']:4s} {item['category']:12s} {item['name']}")
```

The self-test covers: smoke (health, constants), instrument detail, tick data, trading calendar, sectors, index weights, dividend factors, options, futures, ETF info, convertible bonds, financial data, industry data, market data, downloads (return-type check), and trader queries (asset, positions, orders, trades, account status — skipped if no account configured).

Functions missing from the API surface (version differences) and trader queries without an account are auto-skipped rather than failing.

## Events

Subscribe to trader callbacks (orders, trades, disconnects, errors, account status):

```python
# Callback-based (background poller thread)
def on_event(event):
    print(f"[{event['type']}] {event['data']}")

sub_id = client.subscribe(
    event_types=["order", "trade", "disconnect"],
    account_id="1000000365",        # optional filter
    on_event=on_event,              # auto-starts background poller
    poll_interval=1.0,              # polling interval (seconds)
)

# … trading activity …

client.unsubscribe(sub_id)  # stops the poller thread
```

Or poll manually (no background thread):

```python
sub_id = client.subscribe(["order", "trade"])
events, dropped = client.drain_events(sub_id, max_count=50)
```

Event types: `order`, `trade`, `disconnect`, `order_error`, `cancel_error`, `account_status`, `async_response`, `reconnect`.

## Async Downloads

`download_*` functions return a `DownloadTaskHandle` immediately — the download runs on a server-side thread pool:

```python
task = client.xtdata.download_history_data("000001.SH", "1d", "20240101", "20240601")
print(task)           # <DownloadTask 3f8a91b2>
print(task.is_done)   # False (polls server)

# Block until complete
result = task.wait(timeout=300)
print(result["status"])  # "completed" or "failed"
```

## Architecture

```
client/              Cross-platform RPyC client — auto-discovers API surface on connect
common/              Shared protocol constants (HMAC auth, status codes, event types)
server/              Windows-only — hosts xtquant, trader lifecycle, event bus, downloads
```

### Server Components

| Component | File | Role |
|---|---|---|
| **XtquantService** | `server/service.py` | RPyC service — one instance per client. Handles auth, API dispatch (`call_xtdata`, `call_trader`, `batch_call_xtdata`), event subscribe/poll, download queries. Materializes RPyC netref proxies to plain Python objects before calling xtquant (pybind11 rejects netref types). |
| **ConnectionManager** | `server/connection.py` | Trader lifecycle: init → connect → heartbeat → auto-reconnect. The `_Callback` inner class bridges xtquant C++ callbacks into the EventBus. Auto-wraps string `account_id` → `StockAccount` for trader methods. |
| **EventBus** | `server/event_bus.py` | Pub/sub with per-subscription event queues (bounded at 1000 events). Supports filtering by event type and account ID. |
| **DownloadTaskManager** | `server/download_manager.py` | Thread-pool executor for async `download_*` calls. Poll via `query_download(task_id)`. |
| **API Surface** | `server/api_surface.py` | Introspects xtdata functions, XtQuantTrader methods, xtconstant constants, and xttype classes at startup. Clients receive this descriptor to build proxy objects — no per-call introspection needed. |
| **Serializer** | `server/serializer.py` | Recursively converts numpy arrays, pandas DataFrames, and xtquant objects to JSON-safe dicts. Depth-limited to 64. |
| **Auth** | `server/auth_limiter.py` + `common/protocol.py` | HMAC-SHA256 challenge-response (nonce + timestamp, 60s window). Rate limiter: 5 failures / 60s → 300s lockout per IP. |
| **Logging** | `server/logging_config.py` | TimedRotatingFileHandler with daily rotation (7-day retention) and console output (WARNING+). |
| **Datetime Patch** | `server/datetime_patch.py` | Monkey-patches `datetime.fromtimestamp` on Python < 3.12 to prevent a CPython C assertion crash (`u < 1000000`) triggered by xtquant's float-precision timestamps. Auto-applies on import. |

### Client Components

| Component | File | Role |
|---|---|---|
| **QmtClient** | `client/client.py` | Main entry point. Handles connect, auth, surface init, RPC dispatch, event subscriptions, batch calls, and self-test. |
| **Proxy** | `client/proxy.py` | Builds `_RemoteCallable`, `_RemoteModule`, `_RemoteTrader` from the API surface descriptor. Every `_RemoteCallable` exposes a `.batch()` method for batched xtdata calls. |
| **DownloadTaskHandle** | `client/proxy.py` | Client-side handle for async download tasks — `poll()`, `is_done`, `wait(timeout)`. |
| **Self-Test** | `client/self_test.py` | Comprehensive read-only query test suite with a declarative case registry covering 30+ xtdata functions and trader queries. |
| **Exceptions** | `client/exceptions.py` | `QmtError`, `NotConnectedError`, `RemoteCallError`, `QmtAuthError`. |

### Heartbeat & Reconnect

- Heartbeat pings QMT periodically (`QMT_HEARTBEAT_INTERVAL`). Uses `query_stock_asset` (with account) or `get_trading_calendar` (without account), guarded by a daemon-thread + `join(timeout)` to detect hangs.
- After `QMT_HEARTBEAT_MAX_FAILURES` consecutive failures, the ConnectionManager triggers auto-reconnect with exponential backoff (1s → 2s → 4s → 8s → 16s → 30s).
- On successful reconnect: re-subscribes account, publishes a `reconnect` event, resets failure counters.

### Trader Method Account Auto-Wrapping

When a client calls a trader method whose runtime signature has an exact `account` parameter, the server automatically converts an account ID string to `StockAccount`. Positional and keyword forms are both supported:

```python
client.trader.query_stock_asset("1000000365")
client.trader.query_stock_asset(account="1000000365")
```

The server discovers these methods from the installed broker-customized xtquant build. The original nine-method `_ACCOUNT_METHODS` set remains a compatibility fallback when a future SDK build does not expose inspectable signatures. The client remains xtquant-free and never constructs `StockAccount`.

API discovery only means that a method is available for remote dispatch. Trader methods that accept callback arguments require separate live validation of callback lifetime and reverse RPyC transport.

## Testing

```bash
# All server-side tests with mock (cross-platform, pure Python)
python -m pytest tests/ -v

# Skip live integration tests (requires real QMT)
python -m pytest tests/ -v -k "not live"

# Windows: full suite including live integration tests
scripts\test_server.bat

# Client-only tests (cross-platform, no xtquant needed)
bash scripts/test.sh
```

Tests use `tests/_xtquant_mock.py` — a pure-Python in-memory mock. No Windows or `.pyd` files needed for unit/integration tests.

`tests/test_live_integration.py` exercises every xtdata query function against a real QMT backend — requires MiniQMT running and `.env` configured.

## Troubleshooting

Run the self-check tool to diagnose common issues:

```bat
.venv\Scripts\python.exe scripts\env_check.py
```

This verifies Python version, dependencies, MiniQMT running status, xtquant availability, and `.env` configuration. It auto-detects the running MiniQMT process and can wire xtquant and fill `.env` values automatically.

For client-side diagnostics, use the built-in self-test:

```python
client = QmtClient.connect("server-ip", port=18812, auth_key="my-key")
report = client.self_test()
# Inspect failures: [r for r in report["results"] if r["status"] == "fail"]
```
