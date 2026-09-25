# qmt-rpyc

A typed Python bridge to a broker-customized QMT/MiniQMT deployment. The server runs on Windows with Python 3.10/3.11; clients support Python 3.9+ on Linux, macOS and Windows.

Current source version: **0.5.0**. Matching builds are recommended; connection compatibility is checked by contract version and hash. [中文](README.md) · [Architecture](docs/design/architecture.md) · [Operations and fields](docs/api/contract.md)

## Install from source

```bash
scripts/setup.sh
source .venv/bin/activate
qmt-rpyc-client init --profile office
qmt-rpyc-client check --profile office
```

On Windows, with 64-bit Python 3.10/3.11 and the broker SDK installed:

```bat
scripts\setup.bat
start-rpyc.bat
```

Source scripts create `.venv`, install server/development dependencies and explicitly use the checkout `.env`. In a source checkout, `start-rpyc.bat` prefers an initialized source environment; if absent, it uses the managed installation and its configuration.

For Windows installation or upgrades, stop the existing server, run `install-server.bat`, then `start-rpyc.bat`. The installer prefers a single wheel beside the script; without one, it installs or upgrades from PyPI to the pinned release (currently 0.5.0). Existing configuration is preserved. It downloads third-party dependencies and installs into `%LOCALAPPDATA%\qmt-rpyc\venv`. For maintenance, use `"%LOCALAPPDATA%\qmt-rpyc\qmt-rpyc-server.bat" check`.

The managed server configuration lives in `%LOCALAPPDATA%\qmt-rpyc\config.env`. Client profiles use the platform configuration directory; credentials use the system keyring or `QMT_RPYC_AUTH_KEY`.

Server environment variables override the selected configuration file (`--config PATH` or the default). Importing an existing `.env` without prompts requires `init --non-interactive --yes`.

With `.[dev]` installed, `python -m build` creates a wheel/sdist; the release workflow separately assembles the Windows ZIP. Install the stable client with `pip install qmt-rpyc==0.5.0`, or the Windows server with `pip install "qmt-rpyc[server]==0.5.0"`.

Startup `SDK module` log entries show the imported xtquant modules and loaded native extension paths; `resolved` follows filesystem junctions. Use these paths to verify SDK upgrades: the adapter name does not identify the loaded SDK version.

The default server adapter is `QMT_RPYC_ADAPTER=xtquant_2.0.6.1`, implemented in the valid Python package `adapters/xtquant_2_0_6_1`. Selection is explicit and requires a restart; it does not install or switch the broker SDK.

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

Trading calls require `--confirm-trading`. Public models live in `qmt_rpyc.contracts.<domain>` and errors in `qmt_rpyc.contracts.errors`. Each batch accepts at most 500 distinct codes, preserving input order and per-item failures. Option discovery covers current, unexpired contracts only.

## Raw SDK diagnostics

Set `QMT_RPYC_DEBUG=1` on the server and restart to enable the optional diagnostic entry point:

```bash
qmt-rpyc-client debug --profile office describe xtdata.get_option_detail_data
qmt-rpyc-client debug --profile office call xtdata.get_full_tick --args '[["510050.SH"]]'
```

It forwards JSON arguments directly and returns JSON-safe SDK fields, independently of business contract negotiation.
Authentication still applies. Calls can have real side effects and are never automatically retried.
Python callers use `qmt_rpyc.client.debug.DebugClient`. See [debug interface](docs/api/debug.md) for serialization limits.

## Reliability and security

Contract names, parameters, result models and semantics participate in negotiation. RPC starts independently of the background QMT connection. No order or download creation is retried after an unknown outcome. Task completion means normal SDK return, not complete or fresh data. Dates are calendar dates; instants are timezone-aware and encoded in UTC.

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
