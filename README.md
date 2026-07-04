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
# One-click client venv
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
                                      client.xtconstant.FIX_PRICE, 10.5)

# Health
print(client.health())

# Context manager (auto-closes on exit)
with QmtClient.connect("192.168.1.100", port=18812, auth_key="my-key") as client:
    print(client.health())

client.close()
```

### Dev Environment (Windows)

```bat
scripts\setup_dev.bat
```

This installs pytest and the client package in editable mode on top of `scripts\setup.bat`.

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
| `QMT_RPYC_LOG_DIR` | `logs` | Log directory (daily rotation, 30-day retention) |

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
| **XtquantService** | `server/service.py` | RPyC service — one instance per client. Handles auth, API dispatch, event subscribe/poll, download queries. Materializes RPyC netref proxies to plain Python objects before calling xtquant (pybind11 rejects netref types). |
| **ConnectionManager** | `server/connection.py` | Trader lifecycle: init → connect → heartbeat → auto-reconnect. The `_Callback` inner class bridges xtquant C++ callbacks into the EventBus. Auto-wraps string `account_id` → `StockAccount` for trader methods. |
| **EventBus** | `server/event_bus.py` | Pub/sub with per-subscription event queues (bounded at 1000 events). Supports filtering by event type and account ID. |
| **DownloadTaskManager** | `server/download_manager.py` | Thread-pool executor for async `download_*` calls. Poll via `query_download(task_id)`. |
| **API Surface** | `server/api_surface.py` | Introspects xtdata, XtQuantTrader, xtconstant at startup. Clients receive this descriptor to build proxy objects — no per-call introspection needed. |
| **Serializer** | `server/serializer.py` | Recursively converts numpy arrays, pandas DataFrames, and xtquant objects to JSON-safe dicts. |
| **Auth** | `server/auth_limiter.py` + `common/protocol.py` | HMAC-SHA256 challenge-response (nonce + timestamp, 60s window). Rate limiter: 5 failures / 60s → 300s lockout per IP. |
| **Logging** | `server/logging_config.py` | TimedRotatingFileHandler with daily rotation and console output (WARNING+). |
| **Datetime Patch** | `server/datetime_patch.py` | Monkey-patches `datetime.fromtimestamp` on Python < 3.12 to prevent a CPython C assertion crash (`u < 1000000`) triggered by xtquant's float-precision timestamps. |

### Heartbeat & Reconnect

- Heartbeat pings QMT periodically (`QMT_HEARTBEAT_INTERVAL`). Uses `query_stock_asset` (with account) or `get_trading_calendar` (without account), guarded by a daemon-thread + `join(timeout)` to detect hangs.
- After `QMT_HEARTBEAT_MAX_FAILURES` consecutive failures, the ConnectionManager triggers auto-reconnect with exponential backoff (1s → 2s → 4s → 8s → 16s → 30s).
- On successful reconnect: re-subscribes account, publishes a `reconnect` event, resets failure counters.

## Testing

```bash
cd qmt-rpyc
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Tests use `tests/_xtquant_mock.py` — a pure-Python in-memory mock. No Windows or `.pyd` files needed.

## Troubleshooting

Run the self-check tool to diagnose common issues:

```bat
.venv\Scripts\python.exe env_check.py
```

This verifies Python version, dependencies, MiniQMT running status, xtquant availability, and `.env` configuration. It auto-detects the running MiniQMT process and can wire xtquant and fill `.env` values automatically.
