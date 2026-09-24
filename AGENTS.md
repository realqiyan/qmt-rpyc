# Repository Guidelines

## Project Overview

qmt-rpyc is an RPyC bridge that exposes the xtquant SDK for QMT/MiniQMT to remote clients. The server runs on Windows beside QMT; the client package works on Linux, macOS, and Windows.

## Project Structure

- `client/`: cross-platform `QmtClient`, dynamic API proxies, exceptions, download handles, and the read-only self-test registry.
- `server/`: Windows service, API discovery, QMT connection lifecycle, serialization, authentication, event bus, logging, and asynchronous downloads.
- `common/`: protocol constants and HMAC helpers shared by client and server.
- `tests/`: pytest unit and integration tests. `_xtquant_mock.py` supplies a portable in-memory xtquant replacement.
- `scripts/`: environment setup, validation, testing, and benchmarking tools.

## Setup, Run, and Test Commands

Client setup and packaging:

```bash
scripts/setup.sh
source .venv/bin/activate
pip install -e .
```

Windows server setup and startup:

```bat
scripts\setup.bat
start-rpyc.bat
REM Equivalent entry point: qmt-rpyc-server start
```

Common test commands:

```bash
python -m pytest tests/ -v
python -m pytest tests/ -v -k "not live"
python -m pytest tests/test_service.py -v
python -m pytest tests/test_service.py::test_health -v
python -m pytest tests/test_live_integration.py -v
bash scripts/test.sh
```

`scripts/test.sh` runs the portable client-oriented subset. On Windows, `scripts\test_server.bat` runs the full server suite. Live tests require MiniQMT, xtquant, and a valid `.env`; otherwise they skip automatically.

Important helpers:

| Path | Purpose |
|---|---|
| `scripts/env_check.py` | Validates Python, dependencies, QMT availability, xtquant imports, and `.env`; it can detect MiniQMT and wire xtquant automatically. |
| `scripts/dump_api_surface.py` | Dumps the actual broker-customized xtquant API surface from the Windows server environment as JSON. |
| `scripts/setup.bat` | Creates the server environment, installs dependencies, and runs the environment check. |
| `scripts/setup.sh` | Creates a cross-platform client virtual environment. |
| `scripts/remote_bench.py` | Measures remote-call and batch-call performance. |

## Architecture and Data Flow

`server/main.py` is the server entry point. It loads `.env`, enables native crash diagnostics, applies the Python timestamp compatibility patch, configures rotating logs, initializes shared connection and download managers, probes contract adapters, and starts an RPyC `ThreadedServer` with optional TLS.

`server/service.py` exposes authentication, contract negotiation, xtdata/trader dispatch, batch calls, download status, and health checks. Before invoking pybind11-backed xtquant functions, remote RPyC netrefs must be materialized into local Python objects. Responses are converted by `server/serializer.py`; numpy arrays, pandas DataFrames, and xtquant objects become JSON-safe structures, with recursion limited to 64 levels.

`server/connection.py` owns trader initialization, heartbeat checks, callback forwarding, and exponential-backoff reconnection. At runtime it discovers Trader methods with an exact `account` parameter and converts positional or keyword account ID strings into `StockAccount`; `_ACCOUNT_METHODS` remains a compatibility fallback when SDK signatures are unavailable. The shared `EventBus` provides bounded per-subscription queues, while `DownloadTaskManager` runs `download_*` work asynchronously.

The client negotiates a fixed bridge contract and its hash at connection time. Proxies and constants come from the bundled snapshot; capabilities report SDK compatibility separately. Unknown proxy attributes are rejected. Downloads use `DownloadTaskHandle`; non-idempotent requests never retry after an unknown outcome. Public event delivery is outside V1.

Batching is supported only for xtdata calls to the same function. The project intentionally retains the existing bounded concurrent batch implementation; do not introduce process-wide xtdata serialization as part of bug fixes unless this decision is explicitly revisited. The server rejects `download_*` batch calls, caps batches at 500 entries, and keeps per-call failures isolated in the result list.

## Runtime Constraints and Configuration

- The server must use Python 3.10 or 3.11 because xtquant native extensions do not support CPython 3.12+.
- The client supports Python 3.9+.
- Server compatibility pins include `numpy>=1.24,<2` and `pandas>=2,<3`.
- The deployed xtquant SDK is a broker-customized offline build and may differ from public xtquant releases and documentation. Treat the API surface discovered from the actual Windows deployment as the source of truth for supported functions and signatures.
- Runtime API exposure is fixed by `src/qmt_rpyc/contracts/v1.json` (22 APIs, 16 constants). `server/adapters` selects server-only SDK strategies per endpoint; multiple SDK strategies may implement the same contract version. Keep full SDK discovery for diagnostics only. Never mutate a published contract snapshot or expose unselected SDK methods. See `docs/design/contract-v1-implementation.md`.
- API discovery does not by itself guarantee transport compatibility. Trader methods that accept callbacks require separate live validation because the client does not run an explicit RPyC background-serving thread.
- Production deployment is confined to a trusted internal LAN. It currently authenticates clients with the shared `QMT_RPYC_AUTH_KEY`; TLS and mTLS certificates are not deployed.
- Each server instance is dedicated to one person's QMT deployment and securities account environment, with one Trader shared by all connected clients.
- Possession of `QMT_RPYC_AUTH_KEY` grants full trust within that server instance: clients may call every contracted API and access account data available through it. Per-client authorization and multi-tenant isolation are out of scope.
- Copy `.env.example` to `.env`. Never commit authentication keys, account IDs, certificates, local QMT paths, or logs.
- Client and server must use the same `QMT_RPYC_AUTH_KEY`. TLS and mTLS are configured through the `QMT_RPYC_TLS_*` variables.
- Authentication uses HMAC-SHA256 with timestamp/nonce validation and per-IP failure throttling.

## Coding Style and Naming

Use four-space indentation. Follow existing Python naming: `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer focused modules, small functions, early returns, immutable updates, explicit boundary validation, named constants, and detailed server-side error logging. Do not silently swallow errors.

No formatter or linter is configured, so match neighboring code and keep imports orderly. Preserve CRLF endings in `.bat` and `.cmd` files as required by `.gitattributes`.

## Testing Guidelines

Name files `test_<area>.py` and tests `test_<behavior>`. Add focused regression coverage for bug fixes and use `_xtquant_mock.py` unless real QMT behavior is specifically under test. `tests/conftest.py` adds the repository root to `sys.path`. The live suite exercises the real API surface and needs QMT running with `.env` configured. No coverage threshold is currently enforced.

`QmtClient.self_test()` is separate from pytest: it probes 30+ read-only interfaces on a connected server and returns passed, failed, skipped, duration, and detailed results. Missing API functions, unavailable accounts, and unsupported queries should skip rather than fail when appropriate.

## Commit and Pull Request Guidelines

Use concise Conventional Commit subjects matching recent history: `feat:`, `fix:`, `perf:`, `refactor:`, `docs:`, or `chore:`. Keep each commit scoped to one logical change. Pull requests should explain motivation and behavior changes, list commands run, link relevant issues or design documents, and identify Windows/QMT-specific validation. Include logs or screenshots only when they clarify runtime behavior.

## Release Process

Versions are defined in `src/qmt_rpyc/version.py` and tags use the matching
`v<version>` form, for example `0.3.1rc1` and `v0.3.1rc1`. Python package
versions and release tags are immutable: never reuse a published version or
move an existing release tag.

Before creating a release:

1. Start from an up-to-date `master` branch with no unrelated worktree
   changes.
2. Review the complete diff and scan tracked files and the built wheel/sdist
   for authentication keys, account IDs, certificates, local QMT paths, logs,
   and other local environment data.
3. Update `src/qmt_rpyc/version.py`, `CHANGELOG.md`, and version-specific
   examples in `README.md` and `README.en.md`.
4. Run `python -m pytest tests/ -v`. Live tests may skip outside Windows/QMT,
   but the portable and mocked suites must pass.
5. Build into a clean temporary directory with `python -m build --outdir
   <temp-dir>`, run `python -m twine check <temp-dir>/*`, install the wheel in
   a fresh virtual environment, and smoke-test both `qmt-rpyc-client` and
   `qmt-rpyc-server` help/version commands.
6. Commit the release preparation with a scoped Conventional Commit, create
   the matching annotated tag, then push the commit and tag.

Pushing an RC tag runs `.github/workflows/release.yml`. The workflow reruns CI,
builds distributions once, creates a prerelease on GitHub with the Windows
bootstrap bundle and checksums, and publishes the Python distributions to
TestPyPI through the `testpypi` trusted-publisher environment. Verify the
workflow, GitHub Release assets, and TestPyPI metadata before asking for
Windows validation.

Install an RC for Windows validation while resolving third-party dependencies
from production PyPI:

```bat
py -3.11 -m pip install --index-url https://pypi.org/simple ^
  --extra-index-url https://test.pypi.org/simple --pre ^
  "qmt-rpyc[server]==<version>"
```

Production promotion of an RC is a separate, explicitly authorized action.
Run the `Release` workflow manually with the existing tag as its `tag` input.
The `promote-to-pypi` job downloads exactly the wheel and sdist attached to the
existing GitHub Release and publishes them through the `pypi` trusted-publisher
environment; do not rebuild between Windows validation and promotion. Stable
tags publish directly to production PyPI after CI and GitHub Release creation.
