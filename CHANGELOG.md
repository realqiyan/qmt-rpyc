# Changelog

## [0.3.0rc1] - 2026-07-29

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
