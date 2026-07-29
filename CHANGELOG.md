# Changelog

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
