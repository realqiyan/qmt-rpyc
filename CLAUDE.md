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
start_server.bat
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

## Architecture

### Three-layer design

```
client/          Cross-platform RPyc client — auto-discovers API surface on connect
common/          Shared protocol constants (HMAC auth, status codes, event types)
server/          Windows-only — hosts xtquant, ConnectionManager, EventBus, download tasks
```

### Server component lifecycle

`server/main.py` is the entry point. On startup it:
1. Loads config from `.env` via `python-dotenv`
2. Applies `server/datetime_patch.py` (fixes `datetime.fromtimestamp` float precision on Python < 3.12, needed for xtquant)
3. Creates three singletons: `ConnectionManager` (trader lifecycle), `DownloadTaskManager` (async downloads), `EventBus` (pub/sub for trader callbacks)
4. Builds the API surface (`server/api_surface.py`) — introspects xtquant modules to list available functions/signatures/constants
5. Sets all singletons as class-level attributes on `XtquantService`
6. Starts an RPyC `ThreadedServer`

### RPyC service (`server/service.py`)

`XtquantService` is the RPyC service class. Each client connection gets its own service instance. Exposed methods:

- `authenticate` — HMAC challenge-response (see `common/protocol.py`)
- `get_api_surface` — returns the introspected xtquant API for client-side proxy building
- `call_xtdata(name, args, kwargs)` — dispatch to `xtdata.*` functions; routes `download_*` to `DownloadTaskManager`
- `call_trader(name, args, kwargs)` — dispatch to `XtQuantTrader` methods via `ConnectionManager`
- `subscribe_event` / `unsubscribe_event` / `poll_events` — EventBus pub/sub
- `query_download` — poll async download task status
- `health` — connection health check

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
- Server requires `numpy>=1.24,<2` and `pandas>=2.0,<3` pinned for xtquant compatibility
- Tests use `tests/_xtquant_mock.py` — a pure-Python in-memory mock, no Windows or `.pyd` needed
- `conftest.py` adds project root to `sys.path` for direct imports

## Trader method account auto-wrapping

When a client calls a trader method that requires a `StockAccount` object, the server automatically converts the first argument (if it's a string) to a `StockAccount`. The client just passes `account_id` as a string. The list of methods requiring this wrapping is `_ACCOUNT_METHODS` in `connection.py`.
