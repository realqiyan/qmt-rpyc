# Changelog

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
