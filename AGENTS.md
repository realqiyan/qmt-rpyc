# Repository Guidelines

## Project Overview

qmt-rpyc is an RPyC bridge that exposes stable typed business operations backed by xtquant for QMT/MiniQMT to remote clients. The server runs on Windows beside QMT; the client package works on Linux, macOS, and Windows.

## Project Structure

- `src/qmt_rpyc/contracts/`: pure Python requests and results grouped by business capability; operation registry and schema generation.
- `src/qmt_rpyc/client/`: explicit `QmtClient` and typed capability groups, download handles and read-only diagnostics.
- `src/qmt_rpyc/transport/`: socket authentication, RPyC connection, JSON codec and correlated messages.
- `src/qmt_rpyc/server/`: RPC entry, operation dispatch, downloads, health and process lifecycle.
- `src/qmt_rpyc/adapters/`: typed provider interfaces; `xtquant_2_0_6_1/` owns native SDK translation, probing, discovery and connection lifecycle.
- `tests/`: portable tests with a synthetic SDK; opt-in deployment tests.
- `scripts/`: setup, verification, schema export and read-only benchmarking.

## Setup, Run, and Test Commands

Client setup and packaging:

```bash
scripts/setup.sh
source .venv/bin/activate
pip install -e ".[dev]"
```

Windows server setup and startup:

```bat
scripts\setup.bat
start-rpyc.bat
REM Equivalent source entry point:
.venv\Scripts\qmt-rpyc-server.exe --config .env start
```

Common test commands:

```bash
python -m pytest tests/ -v
python -m pytest tests/ -v -k "not live"
python -m pytest tests/test_service.py -v
python -m pytest tests/test_service.py::test_all_read_capabilities_have_typed_results -v
python -m pytest tests/test_live_integration.py -v
bash scripts/test.sh
```

`scripts/test.sh` runs the portable client-oriented subset. On Windows, `scripts\test_server.bat` runs the full server suite. Live tests are opt-in via `QMT_RPYC_LIVE=1` and use the client profile named by `QMT_RPYC_PROFILE` (default: `default`). They can run from Linux/macOS without a local SDK; connection failures fail once enabled.

Important helpers:

| Path | Purpose |
|---|---|
| `qmt-rpyc-server init / check / xtquant repair` | Initializes configuration, checks the local SDK/QMT environment, and repairs the SDK link without requiring RPC. |
| `scripts/dump_api_surface.py` | Dumps the actual broker-customized xtquant API surface from the Windows server environment as JSON. |
| `scripts/setup.bat` | Creates the server environment, installs dependencies, and runs the environment check. |
| `scripts/setup.sh` | Creates a cross-platform client virtual environment. |
| `scripts/remote_bench.py` | Measures remote-call and batch-call performance. |

## Architecture and Data Flow

`server/main.py` composes typed SDK providers, task manager, dispatcher and authenticated RPyC service. RPC starts independently of background QMT connection attempts. `service.py` exposes contract negotiation and JSON operation calls, plus a separately gated SDK diagnostic RPC.

`contracts/operations.py` fixes the 28 public operations. Public requests/results are frozen dataclasses grouped by domain; protocol versions belong to metadata, not import paths. Contracts never depend on transport, client, server or SDK packages. The client never imports server or adapters. `__init__.py` contains exports only.

Adapters return public models directly; SDK names and data transformations remain private to `adapters/xtquant_2_0_6_1`. Probes inspect actual deployed signatures/constants without SDK calls. Discovery is diagnostics only and cannot add public operations. The explicitly enabled `QMT_RPYC_DEBUG=1` diagnostic entry may invoke direct public SDK methods outside the operation registry; it is authenticated, off by default, and not a stable application contract. `adapters/interfaces.py` is the replacement seam, not generic string invocation.

Keep bounded concurrent batch execution (500 distinct codes maximum, per-item errors, input order); do not introduce process-wide xtdata serialization. Non-idempotent requests never retry after an unknown outcome. Public callback/event delivery is outside the contract. See `docs/design/architecture.md` and `docs/api/contract.md`.

## Runtime Constraints and Configuration

- The deployed broker SDK requires 64-bit Python 3.10 or 3.11 on Windows.
- The client supports Python 3.9+.
- Server compatibility pins include `numpy>=1.24,<2` and `pandas>=2,<3`.
- The deployed xtquant SDK is a broker-customized offline build and may differ from public xtquant releases and documentation. Treat the API surface discovered from the actual Windows deployment as the source of truth for supported functions and signatures.
- Runtime API exposure is fixed by `src/qmt_rpyc/contracts/operations.py`; shipped contracts are immutable. SDK compatibility is reported per operation.
- Diagnostic discovery does not guarantee JSON transport compatibility. Native callbacks cannot be passed through the debug JSON interface.
- Production deployment is confined to a trusted internal LAN. It currently authenticates clients with the shared `QMT_RPYC_AUTH_KEY`; TLS and mTLS certificates are not deployed.
- Each server instance is dedicated to one person's QMT deployment and securities account environment, with one Trader shared by all connected clients.
- Possession of `QMT_RPYC_AUTH_KEY` grants full trust within that server instance: clients may call every contracted API and access account data available through it. Per-client authorization and multi-tenant isolation are out of scope.
- Source setup/start scripts explicitly use the repository `.env`; the managed Windows installation defaults to `%LOCALAPPDATA%\qmt-rpyc\config.env`. Use `qmt-rpyc-server --config PATH init` for other locations. Server environment variables override the selected config. Never commit authentication keys, account IDs, certificates, local QMT paths, or logs.
- Client and server must use the same `QMT_RPYC_AUTH_KEY`. TLS and mTLS are configured through the `QMT_RPYC_TLS_*` variables.
- Authentication uses an HMAC-SHA256 server challenge before RPyC startup, with per-IP failure throttling.

## Coding Style and Naming

Use four-space indentation. Follow existing Python naming: `snake_case` for functions and variables, `PascalCase` for classes, and `UPPER_SNAKE_CASE` for constants. Prefer focused modules, small functions, early returns, immutable updates, explicit boundary validation, named constants, and detailed server-side error logging. Do not silently swallow errors.

No formatter or linter is configured, so match neighboring code and keep imports orderly. Preserve CRLF endings in `.bat` and `.cmd` files as required by `.gitattributes`.

## Testing Guidelines

Name files `test_<area>.py` and tests `test_<behavior>`. Add focused regression coverage for bug fixes and use `_xtquant_mock.py` unless real QMT behavior is specifically under test. `tests/conftest.py` adds the repository root and `src` to `sys.path`. Full tests require `.[server,dev]` on Python 3.10/3.11; use `scripts/test.sh` with `.[dev]` for client-only environments. No coverage threshold is currently enforced.

`QmtClient.self_test()` probes read-only typed capabilities; unavailable capabilities or missing account inputs skip. Live tests require an explicit `QMT_RPYC_LIVE=1` and a configured client profile.

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
3. Update `src/qmt_rpyc/version.py`, `CHANGELOG.md`, the online installer
   release pin `QMT_RPYC_VERSION` in `install-server.bat`, and version-specific
   examples in `README.md` and `README.en.md`.
4. Run `python -m pytest tests/ -v`. Live tests skip unless explicitly enabled,
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
Run the `Release` workflow manually with the existing tag as its `tag` input
and `repository=pypi` (the default). To publish the same files to TestPyPI,
select `repository=testpypi`. The `publish-existing-release` job downloads
exactly the wheel and sdist attached to the
existing GitHub Release and publishes them through the selected trusted-publisher
environment; do not rebuild between Windows validation and promotion. Stable
tags publish directly to production PyPI after CI and GitHub Release creation.
