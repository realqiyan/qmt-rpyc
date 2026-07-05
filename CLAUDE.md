# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RPyC-based bridge that exposes the xtquant SDK (QMT/MiniQMT quantitative trading platform) to any RPyC client. Server runs on Windows (where QMT is installed), clients can be on Linux/macOS/Windows.

## Commands

### Windows (server / dev)

```bat
REM One-click server setup (venv + deps + xtquant wiring + .env)
scripts\setup.bat

REM Dev setup (adds pytest + client in editable mode)
scripts\setup_dev.bat

REM Start the server
start-rpyc.bat
REM or: python -m server.main
```

### Tests

```bash
# Run all tests (pure Python mock, no xtquant/.pyd needed)
python -m pytest tests/ -v

# Run a single test file
python -m pytest tests/test_service.py -v

# Run a single test function
python -m pytest tests/test_service.py::test_health -v
```

### Client packaging

```bash
# Client package install (cross-platform, only needs rpyc)
pip install -e .
```

### Helper scripts

| Script | Purpose |
|---|---|
| `scripts/setup.bat` | Full server setup (venv + deps + xtquant wiring + .env) |
| `scripts/setup_dev.bat` | Dev setup (setup.bat + pytest + client editable install) |
| `scripts/setup.sh` | Client setup (Linux/macOS/WSL) |
| `scripts/install_xtquant.py` | Wires xtquant from QMT install into venv via `.pth` file |
| `env_check.py` | Self-check: Python version, deps, MiniQMT process detection, xtquant import, .env validation. Auto-detects running MiniQMT, wires xtquant, and patches .env with detected values. |
| `init_env.bat` | Standalone venv init (create venv → install deps → run env_check). Lighter than setup.bat; skips xtquant wiring and .env creation. |
| `start-rpyc.bat` | Start server (validates .venv + .env, creates logs dir, runs `python -m server.main`) |

## Architecture

### Three-layer design

```
client/          Cross-platform RPyc client — auto-discovers API surface on connect
common/          Shared protocol constants (HMAC auth, status codes, event types)
server/          Windows-only — hosts xtquant, ConnectionManager, EventBus, download tasks
```

### Server component lifecycle

`server/main.py` is the entry point. On startup it:
1. Enables `faulthandler` for native crash diagnostics (dumps to `logs/crash.log`)
2. Loads config from `.env` via `python-dotenv`
3. Applies `server/datetime_patch.py` (monkey-patches `datetime.fromtimestamp` on Python < 3.12 to prevent a CPython C assertion crash `u < 1000000` triggered by xtquant's float-precision timestamps; on Python ≥ 3.12 the bug is fixed upstream so it no-ops)
4. Sets up logging via `server/logging_config.py` (daily rotation, 7-day retention, console at WARNING+)
5. Creates `ConnectionManager` and `DownloadTaskManager` singletons (the third singleton, `EventBus`, is a module-level instance in `server/event_bus.py` imported by both service and connection modules)
6. Builds the API surface (`server/api_surface.py`) — introspects xtdata functions, XtQuantTrader methods, xtconstant constants, and xttype classes
7. Sets the manager instances plus auth config and API surface as class-level attributes on `XtquantService` (so each per-connection service instance shares the same singletons)
8. Daemonizes leftover non-daemon threads from xtquant to prevent them from blocking server exit
9. Starts an RPyC `ThreadedServer` (optionally TLS-wrapped via `ssl.wrap_socket`)

### RPyC service (`server/service.py`)

`XtquantService` is the RPyC service class. Each client connection gets its own service instance. Exposed methods:

- `authenticate` — HMAC challenge-response (see `common/protocol.py`); rate-limited per IP (5 failures/60s → 300s lockout)
- `get_api_surface` — returns the introspected xtquant API for client-side proxy building
- `call_xtdata(name, args, kwargs)` — dispatch to `xtdata.*` functions; routes `download_*` to `DownloadTaskManager`; materializes RPyC netref proxies to plain Python objects before calling xtquant (pybind11 rejects netref types)
- `call_trader(name, args, kwargs)` — dispatch to `XtQuantTrader` methods via `ConnectionManager`; auto-wraps string account_id → `StockAccount`
- `batch_call_xtdata(name, calls)` — batch execute N calls to the SAME xtdata function in one RPC. `calls` is a list of `(args, kwargs)` tuples. Returns `{status, results: [...]}`. Server executes them concurrently via ThreadPoolExecutor (max_workers = min(N, 50)). Rejects `download_*` functions. Max 500 calls per batch.
- `subscribe_event` / `unsubscribe_event` / `poll_events` — EventBus pub/sub
- `query_download` — poll async download task status
- `health` — connection health check

Every RPC method logs the request (peer, method, summarized args) and the result (status + summarized data) via the `_summarize_*` helpers. Heartbeat polls (`health`) are logged at DEBUG; everything else at INFO.

### ConnectionManager (`server/connection.py`)

Manages the xtquant trader lifecycle:
- `start()` → init trader → connect → begin heartbeat loop
- Heartbeat pings via `query_stock_asset` (with account) or `get_trading_calendar` (without account), with timeout guard via daemon thread + `join(timeout)`
- `_Callback` bridge: xtquant trader callbacks → EventBus events
- Auto-reconnect with exponential backoff (1s→30s), re-subscribes account on success

### Client proxy (`client/proxy.py`)

`_RemoteModule` and `_RemoteTrader` build proxy objects from the API surface descriptor returned by `get_api_surface`. `_RemoteCallable.__call__` delegates to `QmtClient._call`, which routes to the correct RPyC endpoint and maps error responses to Python exceptions. Constants (e.g., `xtconstant.STOCK_BUY`) are inlined at connect time — zero RPC overhead for subsequent access.

### Serialization (`server/serializer.py`)

Recursively converts numpy arrays, pandas DataFrames, and arbitrary xtquant objects to JSON-safe dicts. DataFrame uses `orient="split"`. Depth-limited to 64 to prevent infinite recursion.

### Authentication (`common/protocol.py` + `server/auth_limiter.py`)

HMAC-SHA256 with nonce + timestamp (60s window). Rate limiter: 5 failures within 60s → 300s lockout per source IP.

### Event types

Defined in `common/protocol.py` `EVENT_TYPES`: `order`, `trade`, `disconnect`, `order_error`, `cancel_error`, `account_status`, `async_response`, plus `reconnect` (emitted by ConnectionManager).

## Key constraints

- **Python 3.10 or 3.11** on the server — xtquant ships `.pyd` extensions for cp36–cp311 only (no cp312+)
- Client has no such restriction — only needs `rpyc>=6.0.0`
- Server requires `numpy>=1.24,<2`, `pandas>=2.0,<3`, and `psutil>=5.0.0` (for MiniQMT process detection in `env_check.py`) pinned for xtquant compatibility
- Tests use `tests/_xtquant_mock.py` — a pure-Python in-memory mock, no Windows or `.pyd` needed
- `conftest.py` adds project root to `sys.path` for direct imports

## Trader method account auto-wrapping

When a client calls a trader method that requires a `StockAccount` object, the server automatically converts the first argument (if it's a string) to a `StockAccount`. The client just passes `account_id` as a string. The list of methods requiring this wrapping is `_ACCOUNT_METHODS` in `connection.py`.

## Batch xtdata calls

The server provides `batch_call_xtdata(name, calls)` for executing multiple calls
to the same xtdata function in a single RPC round-trip.  This is the primary way
to reduce latency for option-chain or multi-instrument queries.

**Server side:**
- `max_workers = min(len(calls), QMT_BATCH_MAX_WORKERS)` (env var, default 50)
- Max 500 calls per batch (hard cap `_BATCH_MAX_CALLS`)
- `download_*` functions are rejected (use `call_xtdata` for async downloads)
- Each call independently materializes args, calls xtdata, and serializes the result

**Client side:**
```python
# Every _RemoteCallable has a .batch() method
results = client.xtdata.get_option_detail_data.batch([
    ([code], {}) for code in codes
])
# results[i] = {"status": "ok", "data": ...} or {"status": "error", ...}
```
