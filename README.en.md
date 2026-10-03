# qmt-rpyc

A typed Python bridge to a broker-customized QMT/MiniQMT deployment. The server runs on Windows with Python 3.10/3.11; clients support Python 3.9+ on Linux, macOS and Windows.

Current source version: **0.9.2**. Matching builds are recommended; connection compatibility is checked by contract version and hash. [中文](README.md) · [Architecture](docs/design/architecture.md) · [Operations and fields](docs/api/contract.md)

## Installation and startup

```bash
scripts/setup.sh
source .venv/bin/activate
qmt-rpyc-client init --profile office
qmt-rpyc-client check --profile office
```

Install the client with `python -m pip install "qmt-rpyc==0.9.2"`, then configure it with `qmt-rpyc-client init --profile office`.

The Windows server requires 64-bit Python 3.10/3.11. The default `xtquant_2.0.6.1` adapter requires the deployed broker SDK and MiniQMT. The `bigqmt` adapter requires full QMT running the matching bridge strategy, without external xtquant; the generated strategy supports QMT's embedded Python 3.6.

For a source server installation:

```bat
scripts\setup.bat
.venv\Scripts\qmt-rpyc-server.exe --config .env start
```

Source scripts create an editable `.venv` using the checkout `.env`. `start` now runs in the background under the current user; use `start --foreground` for debugging. Each Python environment manages one service instance.

For first installation, run `install-server.bat` (BigQMT: `install-server.bat --adapter bigqmt`). It prefers a single adjacent wheel, otherwise the pinned release (currently 0.9.2; release candidates come from TestPyPI, stable releases from PyPI), installs into `%LOCALAPPDATA%\qmt-rpyc\venv`, preserves configuration, and prints next commands. It refuses to overwrite an existing installation. Use the installed `%LOCALAPPDATA%\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe` with `start`, `stop`, `restart`, `status`, or `update` thereafter. `restart` preserves the original configuration and environment overrides. `status` distinguishes process state, RPC readiness and QMT connectivity; local process state remains available without RPC. Background log paths are included in command output. Windows services, boot startup and crash supervision are not included.

### Local updates

See [local update design](docs/design/software-update.md) for the mechanism and limits.

```bash
qmt-rpyc-client update
qmt-rpyc-server update
qmt-rpyc-server update --pre
qmt-rpyc-server update --version 0.9.2
```

Updates affect the current local Python environment only. Stable releases come from official PyPI; `--pre` or an explicit prerelease version selects official TestPyPI for qmt-rpyc, while third-party dependencies come from official PyPI. Explicit versions may downgrade, including to versions without process management. Default updates never downgrade. Unchanged versions leave the service untouched. Editable installations must be updated through Git instead.

After downloading the package and dependencies, updates drain the local service's requests and downloads for up to 60 seconds. Timeout cancels the update and restores admission without force-killing. Client updates also coordinate a service in the same environment. Configuration is retained and the server configuration is backed up. New processes verify the installed version and CLI loading. **Updates never start the service**; they print suggested next commands. Installation/verification failures report their stage, with no automatic rollback. QMT connectivity is not an installation success criterion.

On Windows the command returns `scheduled` and a log path. A helper waits for the old CLI executable to exit before installing; `scheduled` does not mean success. Follow the printed PowerShell `Get-Content -LiteralPath "log path" -Wait` command to read the final result and recommendations (Ctrl-C after the final JSON), or use `type "log path"` in CMD. Coordinate client/server/BigQMT strategy versions when crossing MINOR versions; updating the Python package does not replace the strategy inside QMT.

### Windows update checks

For first installation, extract the complete Windows Release ZIP with the installer, `verify-install.py`, wheel and strategy. Subsequent updates use the installed executable:

```powershell
& "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" update
# Read the update log and check its final result.
# After the update succeeds, generate from the new installation.
& "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" qmt generate --output .\bigqmt_strategy.py --force
# Stop the old QMT bridge, copy/run the new GBK strategy, then start the server.
& "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe" start
```

The bundle verifier remains a first-install/release acceptance tool; routine starts and updates do not depend on stale strategy files beside an old ZIP. Environment overrides still apply. Stop legacy foreground servers manually before first migrating to managed processes; local management does not take over or kill unregistered processes. PowerShell uses `&` for quoted executable paths and `$env:LOCALAPPDATA` for environment expansion, unlike CMD's `%LOCALAPPDATA%`.


The managed server configuration lives in `%LOCALAPPDATA%\qmt-rpyc\config.env`. Client profiles use the platform configuration directory; credentials use the system keyring or `QMT_RPYC_AUTH_KEY`.

Server environment variables override the selected configuration file (`--config PATH` or the default). Importing an existing `.env` without prompts requires `init --non-interactive --yes`.

With `.[dev]` installed, `python -m build` creates a wheel/sdist; the release workflow separately assembles the Windows ZIP. Stable releases are published to PyPI. For a first server installation:

```bat
py -3.11 -m pip install --index-url https://pypi.org/simple "qmt-rpyc[server]==0.9.2"
```

For the client, omit `[server]`. Existing installations can use `qmt-rpyc-client update` or `qmt-rpyc-server update`. Prerelease testing still uses `update --pre` to fetch the target package from TestPyPI.


Startup `SDK module` log entries show the imported xtquant modules and loaded native extension paths; `resolved` follows filesystem junctions. Use these paths to verify SDK upgrades: the adapter name does not identify the loaded SDK version.

Set `QMT_XTQUANT_PATH` in the server config or environment to the absolute **xtquant package directory** containing `__init__.py`, independently of `QMT_PATH`. Environment values override the config. Empty retains normal imports; invalid explicit paths fail without fallback. Restart to change SDKs. `init --xtquant-path PATH` saves it and preserves it on subsequent initialization without changing the old junction. `start`, `check`, `xtquant check`, and `api dump` share this selection; the standalone dump script reads the process environment. When configured, edit this setting instead of using `xtquant repair`.

The default server adapter is `QMT_RPYC_ADAPTER=xtquant_2.0.6.1`, implemented in the valid Python package `adapters/xtquant_2_0_6_1`. Selection is explicit and requires a restart; it does not install or switch the broker SDK.

Full QMT can use `QMT_RPYC_ADAPTER=bigqmt`, backed by this project's independent strategy bridge without xtquant. Stock-account trading is implemented with explicit submission reconciliation and no automatic write retries; the trading path has been validated on Windows. Bridge variables and defaults are listed in `.env.example`; result semantics are in the [public API](docs/api/contract.md).

## Full QMT strategy deployment

Extract the complete Windows Release ZIP and run `install-server.bat --adapter bigqmt`. The ZIP includes a GBK strategy using the default pipe name. For a custom pipe or a fresh strategy, generate it from the installed package without a source checkout, running QMT or RPC:

```powershell
$serverExe = "$env:LOCALAPPDATA\qmt-rpyc\venv\Scripts\qmt-rpyc-server.exe"
& $serverExe qmt generate --output .\bigqmt_strategy.py
# Put global options before qmt:
# & $serverExe --config C:\qmt\config.env qmt generate --output .\bigqmt_strategy.py --force
```

Parent directories are created automatically; overwriting requires `--force`. The pipe name comes from environment variable `QMT_RPYC_BIGQMT_PIPE`, then the selected config file, then the built-in default, matching server precedence. The file contains the installed package version and source fingerprint, without credentials or account identifiers.

Copy the file into full QMT, keep GBK encoding, and run it as a separate strategy. Stop the previous bridge before replacing it to avoid a pipe conflict. Then run server `check` and `start`, and client `check` or `self-test`. See [version rules](docs/versioning.md) for compatibility.

The release ZIP provides `python check_bigqmt_bridge.py --extended` for local read-only checks; from source use `python scripts/check_bigqmt_bridge.py --extended`. BigQMT trading uses the STOCK account supplied in each request. Installation, generation and startup never submit trades; BigQMT debug calls are restricted to a read-only allowlist.

## Persistent server data

The server uses local SQLite business tables. Eight reads accept `refresh=True` for synchronous source refresh. Only fresh, proven coverage is reusable offline; successful source calls do not automatically establish coverage. xtquant dividend events and financials from both adapters lack reliable completeness evidence and continue to read the source. Front adjustment can reuse raw bars for ordinary dividend/bonus events; relevant gugai events or incomplete windows require source reads, so offline availability depends on the query. See [configuration and implementation boundaries](docs/design/persistent-cache.md).

When upgrading cache rules from 0.9.0 or earlier, stop the server and clear the old cache before restarting. The default file is `%LOCALAPPDATA%\qmt-rpyc\data.sqlite3`; custom locations use `QMT_RPYC_CACHE_PATH`. Contract and BigQMT bridge identifiers remain 9; see [version rules](docs/versioning.md) for coordinated upgrades.

## Python and CLI

```python
from qmt_rpyc import QmtClient

with QmtClient.connect_profile("office") as client:
    ticks = client.market.get_ticks(["510050.SH"]).require_all()
    print(ticks["510050.SH"].last_price)
    print(client.options.get_expiry_dates("510050.SH"))
    print(client.system.get_health())
```

```bash
qmt-rpyc-client api list
qmt-rpyc-client api describe market.get_ticks
qmt-rpyc-client call --profile office market.get_ticks --payload '{"codes":["510050.SH"]}'
qmt-rpyc-client download --profile office start history --payload '{"code":"510050.SH","period":"1d"}'
qmt-rpyc-client download --profile office wait TASK_ID
qmt-rpyc-client self-test --profile office
```

`api list` prints names and purposes; `api describe` prints parameters, defaults, result models and field meanings. Use `--json` for machine-readable documentation.

Underlying lists reuse only fresh Shanghai-day cache; expired lists refresh synchronously, and failures do not return stale data. Missing trading reference fields are null. Submission does not assert a fill, and cancellation does not assert final cancellation.

Trading calls require `--confirm-trading`. Public models live in `qmt_rpyc.contracts.<domain>` and errors in `qmt_rpyc.contracts.errors`. Each batch accepts at most 500 distinct codes, preserving input order and per-item failures. Option discovery covers current, unexpired contracts only.

## Raw SDK diagnostics

Set `QMT_RPYC_DEBUG=1` on the server and restart to enable the optional diagnostic entry point:

```bash
qmt-rpyc-client debug --profile office describe xtdata.get_option_detail_data
qmt-rpyc-client debug --profile office call xtdata.get_full_tick --args '[["510050.SH"]]'
```

It forwards JSON arguments directly and returns JSON-safe SDK fields, independently of business contract negotiation.
Authentication still applies. xtquant debug calls can have real side effects; BigQMT calls use a read-only allowlist. Neither adapter retries automatically or accepts native callbacks through JSON.
Python callers use `qmt_rpyc.client.debug.DebugClient`. See [debug interface](docs/api/debug.md) for serialization limits.

## Reliability and security

Contract names, parameters, result models and semantics participate in negotiation. RPC starts independently of the background QMT connection. No order or download creation is retried after an unknown outcome. Task completion means normal SDK return, not complete or fresh data. BigQMT history, financial and index-weight downloads are compatibility no-ops: full QMT manages data acquisition, and completion does not assert a download or refresh. Dates are calendar dates; instants are timezone-aware and encoded in UTC.

HMAC authenticates the socket before RPyC starts. Business payloads are strict JSON; pickle is disabled. A shared-key holder has full trust within the instance. Use a trusted internal network; HMAC does not encrypt, so untrusted networks require TLS or VPN. See [SECURITY.md](SECURITY.md).

## Validation

```bash
python -m pip install -e ".[dev]"
bash scripts/test.sh
# Full synthetic SDK suite: Python 3.10/3.11
python -m pip install -e ".[server,dev]"
python -m pytest tests/ -v
python scripts/dump_contract.py
```

Synthetic SDK and local socket tests are portable. Read-only deployment tests require a configured profile and `QMT_RPYC_LIVE=1`. Repeat deployment acceptance when upgrading the SDK or its adapter.

This project does not distribute xtquant, QMT or MiniQMT. [MIT License](LICENSE).
