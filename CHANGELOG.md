# Changelog

## [0.5.0] - 2026-09-24

- Replace dynamic SDK proxies with 28 typed business operations, fixed request/result models and contract negotiation. Existing clients must upgrade.
- Add readable API/field documentation, authenticated opt-in SDK diagnostics and the versioned xtquant_2.0.6.1 adapter.
- Support LIMIT and LATEST_PRICE orders, preserving existing reference-price arguments and uncertain-submission protection.
- Validate Windows dev4 read-only queries and retain identical runtime code and contract in this stable release.

## [0.5.0.dev4] - Unreleased

- Preserve caller-supplied LATEST_PRICE reference prices through to the SDK, matching existing a-trader submissions.
- Restrict order pricing to LIMIT and LATEST_PRICE; defer the two unvalidated best-price modes. Clients and servers must upgrade together.

## [0.5.0.dev3] - Unreleased

- Require explicit order pricing and support LIMIT and LATEST_PRICE. Limit orders require a positive finite price; latest-price orders preserve an optional finite non-negative reference price supplied by existing callers (zero when omitted).
- Normalize both modes in order queries and probe their SDK constants; unknown pricing never falls back. Contract fingerprint changes require coordinated client/server upgrades.

## [0.5.0.dev2] - Unreleased

- Remove the unsupported global datetime replacement and its diagnostic-script import; retain standard datetime identities for the strict codec and test timestamp boundaries and import order.

- Make API list/describe readable by default, with complete field documentation and explicit JSON output. Documentation does not alter the contract hash.
- Version the broker adapter as xtquant_2.0.6.1 (Python package xtquant_2_0_6_1); add explicit server-side selection for startup, checks, discovery and debugging.
- Preserve adapter/debug settings when the Windows initialization wizard rewrites configuration.

## [0.5.0.dev1] - Unreleased

- Add an opt-in authenticated raw SDK debug client/CLI for deployed signatures, constants and direct calls, independent of business contract negotiation.

- Provide 28 fixed operations with typed requests, immutable results, strict JSON encoding and contract negotiation.
- Separate domain contracts, client, transport, server services and typed SDK providers. Public imports are independent of protocol version numbers.
- Cover current option discovery, instrument/reference data, ticks/bars, financials, downloads and synchronous trading, preserving consumer field dependencies.
- Keep bounded batch isolation and execution uncertainty; never replay a mutation with an unknown outcome.
- Align CLI, documentation, tests and both consuming applications with the current API.

## [0.4.0.dev2] - Unreleased

- Print the server package version in the startup log and console summary.
- Normalize invalid tick display timestamps from the validated Unix-millisecond `time` field in the baseline server adapter, retaining UTC+8, prices and valid existing display values. Invalid source timestamps still fail validation; the V1 snapshot and hash are unchanged.
- Fix a-options to filter actual option expiry dates before fetching quotes. Add HTTP regressions for homepage and position profit/loss using recorded V1 tick shapes.
- Reject overflowing numeric values with structured contract errors instead of leaking a Python exception. Keep live SDK downloads behind an explicit test opt-in.

## [0.4.0.dev1] - Unreleased

- Introduce an immutable V1 business contract (22 APIs, 16 constants), fixed input/output models and contract negotiation independent of package/SDK versions.
- Add per-endpoint adapter strategies and registry selection so different MiniQMT SDK builds can implement the same V1 contract. Signature drift warns without blocking startup and rejects only affected calls.
- Route calls, batches and downloads through validation; preserve time formats, project known fields, and distinguish pre-execution rejection from unknown trading outcomes.
- Include both synchronous cancellation APIs. Remove uncontracted SDK/legacy event exports; self-test performs read-only queries.
- Coordinate a-trader/a-options migration. Initial Windows read-only validation passed, but subsequent full-chain usage exposed a tick display-time compatibility gap addressed in dev2. This development version has not been published.

## [0.3.1] - 2026-09-12

Stable release of the changes described under 0.3.1rc1 and 0.3.1rc2.

Validated on Windows against the deployed broker-customized SDK: with MiniQMT
stopped, the RPC server started, served authentication, API discovery, and
health while the connection was down, then connected on its fourth background
attempt after waiting 10s, 30s, and 60s, without restarting the service.
Maintenance failures were ordinary `Trader.connect` failures returning after
about three seconds, not blocked native calls.

### Changed

- Server logs now mask account ids the same way as the startup summary, so
  `Subscribed to account ...` no longer writes the full account id.

### Not yet implemented

- Local-query completeness checks and degraded fallback (Q3/Q7/Q10): xtdata
  forwarding keeps its previous behavior, and a missing or incomplete range
  is not reported as a distinguishable error.
- The disconnect rule for downloads (Q8):
  `DownloadTaskManager.fail_pending()` exists but is not wired to
  connection-state detection.
- The 10-minute retry tier and recovery from a disconnect during a running
  session have not been observed on real QMT.

## [0.3.1rc2] - 2026-09-12

### Added

- Added `DownloadTaskManager.fail_pending(reason)`, which terminates queued
  download tasks and records the reason while leaving calls already inside the
  SDK to report their real result. It is not yet wired to connection-state
  detection, so the disconnect rule for downloads is not end-to-end active.
- Added `ConnectionManager.probe()`, a single blocking initialize-and-connect
  attempt for diagnostics that must report the current connection outcome.

### Changed

- The RPC server now starts independently of the trading connection. The first
  background connection attempt runs immediately; failures wait 10 seconds,
  30 seconds, 1 minute, then 10 minutes between subsequent attempts, unlimited
  by default. Invalid local configuration, SDK import failures, and occupied
  ports still fail startup.
- `health()` keeps every existing field and adds `connection_state`,
  `consecutive_failures`, `last_connection_error`, and `next_retry_at`. Startup
  output reports the same state. An available RPC connection no longer implies
  trading readiness, and disconnected trading requests fail without queuing or
  replay.
- `qmt-rpyc-server check` now reports the outcome of one blocking connection
  probe instead of scheduling a background attempt, which previously made it
  report success whenever QMT was unreachable.

### Fixed

- Fixed disconnect events being dropped when a stale callback arrived after
  reconnection, and fixed heartbeat results being applied to a replaced
  trader. Account subscription failures after a successful `connect()` are now
  surfaced through `last_connection_error`.

## [0.3.1rc1] - 2026-07-29

### Added

- Added descriptions, argument guidance, examples, and getting-started flows
  to every client and server CLI command level.
- Added `--version` support to both installed commands.
- Added actionable guidance when server-only dependencies are missing.
- Added initialization results that point to the appropriate check and start
  commands, including custom server configuration paths.

### Changed

- Ctrl-C now exits client commands cleanly with status 130 and no traceback;
  foreground server shutdown also preserves status 130 after resource cleanup.
- Custom authentication keys are now accepted at any non-empty length. The
  generated strong key remains the recommended default.
- CLI usage errors now include the relevant command help and examples.

## [0.3.0rc3] - 2026-07-29

- Replaced an account-shaped README example with an explicit placeholder
  before publishing to a Python package index.

- Raised the client minimum to Python 3.9 so modern SPDX package metadata can
  be used consistently. The RC1 tag did not produce release artifacts.

### Added

- Unified `qmt-rpyc` distribution and `qmt_rpyc` package namespace.
- Client and server CLIs with guided configuration.
- Client profiles with system keyring support.
- Pre-RPyC HMAC socket authentication and protocol version checks.
- Dynamic API inspection and guarded JSON CLI calls.
- Managed Windows installation, update, status, and uninstall commands.

### Changed

- Initial QMT connection failure now prevents the server from listening.
- Server dispatch is restricted to the discovered API allowlist.
- Real deployment API dumps are local artifacts rather than repository files.

### Removed

- The legacy `client`, `server`, and `common` import namespaces.
