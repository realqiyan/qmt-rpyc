# qmt-rpyc

RPyC-based bridge exposing the full [xtquant](https://dict.thinktrader.net/) SDK (QMT/MiniQMT quantitative trading) to Linux/macOS clients. Independent of `qmt-bridge/` (the MCP bridge).

## Quick Start

### Server (Windows)

**Prerequisites:**

- MiniQMT installed and running
- Python **3.10 or 3.11** — xtquant ships native `.pyd` extensions for cp36–cp311 only (no cp312/cp313)

**One-click setup:**

```bat
scripts\setup.bat
```

This will: check Python version → create `.venv` → install dependencies → wire xtquant → create `.env`.

**Manual setup (if needed):**

```bat
cd qmt-rpyc

REM 1. Create venv with Python 3.10/3.11
py -3.11 -m venv .venv

REM 2. Install dependencies
.venv\Scripts\python.exe -m pip install -r requirements-server.txt

REM 3. Wire xtquant from the QMT install into the venv
.venv\Scripts\python.exe scripts\install_xtquant.py C:\path\to\MiniQMT\userdata_mini

REM 4. Configure
copy .env.example .env
notepad .env
```

**Run the server:**

```bat
start_server.bat
```

### Client (Linux/macOS/Windows)

```bash
# One-click
scripts/setup.sh
```

```python
from client import QmtClient

client = QmtClient.connect("192.168.1.100", port=18812, auth_key="your-secret-key")

# Market data (returns JSON-safe dict, DataFrame split orient)
result = client.xtdata.get_market_data(
    stock_list=["600000.SH"], period="1d", start_time="20240101", count=20)

# Constants (inlined, zero RPC)
print(client.xtconstant.STOCK_BUY)  # → 23

# Trading (account_id passed as string, server auto-wraps StockAccount)
order_id = client.trader.order_stock("1000000365", "600000.SH",
                                      client.xtconstant.STOCK_BUY, 100,
                                      client.xtconstant.FIX_PRICE, 10.5)

# Health
print(client.health())

client.close()
```

### Dev Environment (Windows)

```bat
scripts\setup_dev.bat
```

This installs pytest and the client package in editable mode.

## Architecture

- **Server** (`server/`): Windows-only, holds xtquant + ConnectionManager + DownloadTaskManager + EventBus
- **Client** (`client/`): Cross-platform, auto-discovers API surface on connect, builds natural proxy objects
- **Common** (`common/`): Shared protocol (HMAC auth, envelope constants, event types)

Serialization happens server-side; clients receive JSON-safe structures and need only `rpyc`.

## Testing

```bash
cd qmt-rpyc
pip install -r requirements-dev.txt
python -m pytest
```

Tests use an in-memory xtquant mock, no Windows/.pyd dependencies required.
