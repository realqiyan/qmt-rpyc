# Changelog

## [0.9.0rc1] - 2026-09-30

- Read daily bars as the source's own unadjusted series and derive everything else locally: every adjustment mode (none, front, back, front_ratio, back_ratio) and suspension filling are reproduced by the bridge, so a request the bridge can reproduce asks the source for unadjusted bars only, whatever adjustment or filling it was asked for. The stored bars are therefore always real observations, and the filled series a caller receives is rebuilt at derivation time.
- Fill suspended sessions the way the source does: the fabricated row repeats the previous close as its whole range, with no volume, turnover, previous close or settlement price, the previous open interest, the suspension flag set and the session's Shanghai midnight as its source time. Adjustment runs first, so a halt that spans an ex-dividend date stays continuous in the adjusted series. A window whose first session has no bar keeps the source path.
- Count the rows a filled counted request returns the way the source does: filling runs first and the trim to `count` follows it.
- Reuse a closed bar window when every reported bar falls on a source session; a session without a bar is a day the source holds no trading data for, which filling reproduces. Which of those days are suspensions cannot be attested, so a bar the source has merely not downloaded yet reads the same until an explicit refresh.
- Resolve a daily-bar beginning on the server: an unset `start` becomes one year before the request end, and a beginning earlier than the listing date the source reports starts at the listing, where the source has no fabricated rows. The resolved window is what is read, what is evidenced and what is stored. Direct RPC callers no longer get an unbounded daily-bar read.
- Serve one adjustment policy for both adapters, because they read the same QMT data: the five modes derive locally, gugai events keep the source path. The BigQMT evidence rule for dividend events is unchanged.
- Contract and bridge protocol identifiers become 9; use the matching client, server and regenerated strategy builds. The bridge protocol no longer carries the sector read operations, and the public contract's sector request model is gone.
- Remove the one-off probe scripts and their design records, and the dead sector surface they left behind: adapter methods, reader calls, the download kind in the CLI and the deployment signature entries. Verification now runs through the client debug entry instead of repository scripts.
- Windows validation of this release candidate is pending; run the data-storage and BigQMT acceptance steps before promoting it.

## [0.8.2] - 2026-09-30

- Reuse verified history when a daily-bar or trading-calendar request runs through today: split the request internally at the last day the persistent cache proves, serve that verified part from the cache and read the unproven remainder from the source as the still-open tail. The tail's closed part is persisted once its own evidence lands, so the cache boundary keeps advancing across days; today is never persisted or marked reusable. A failed tail fails the whole item instead of returning history alone, and refresh, unbounded, future-ended, or adjusted/filled requests keep the exact original source call. Evidence now reuses the stored calendar through the bridge instead of re-reading it per security, so a batch resolves the calendar once. Callers receive the same single result; no request parameters or compatibility identifiers change.

## [0.8.1] - 2026-09-29

- Release the validated 0.8.1rc2 runtime as stable on PyPI, with no runtime changes beyond the version and CLI version example.
- Includes local client/server updates and background server management, with Windows venv startup timeout and blank-console fixes.
- Windows deployment validation confirmed rc1-to-rc2 upgrade via `update --pre`, successful background startup and QMT connection, no blank console, and continued service after closing the terminal.
- Public contract and BigQMT bridge compatibility identifiers remain 8.

## [0.8.1rc2] - 2026-09-29

- Fix background startup falsely timing out under Windows virtual environments, where the Python launcher and server interpreter have different PIDs. Match readiness to a unique launch identifier instead.
- Start Windows background servers without a visible console, including when the virtual-environment launcher creates a separate interpreter process.

## [0.8.1rc1] - 2026-09-29

- Publish a release candidate to TestPyPI for Windows validation; preserve public contract and BigQMT compatibility identifiers at 8.
- Clarify QMT connection waiting, recovery and retry exhaustion logs without masking unexpected SDK failures.

- Add shared local client/server updates with official PyPI stable releases, TestPyPI prereleases, exact-version downgrades, editable-install rejection, configuration backups and fresh-process verification. Updates leave services stopped and print next commands.
- Manage one user-level background server per Python environment with start/stop/restart/status and foreground debugging. Local management works independently of business RPC; stopping waits for active requests, replies and downloads, restoring admission on timeout.
- Hand Windows updates to a helper that waits for the old executable to exit and records the final result in a log. Do not automatically roll back or replay trading requests.
- Consolidate Windows first installation into install-server.bat, persist adapter selection through init --adapter, and remove redundant install/start wrappers. Keep editable development setup scripts.

## [0.8.0] - 2026-09-29

- Add server-owned SQLite business storage with typed provider decorators, category freshness policies, source-scoped coverage, synchronous refresh, per-security concurrency and fault degradation.
- Add `refresh=False` to eight reads; adjusted refreshes also refresh event dependencies. Preserve financial date-basis isolation and conservative invalidation without inventing source completeness.
- Reuse verified historical bar ranges and locally derive verified BigQMT adjustment cases. Keep unverified financial coverage, suspension filling and adapter-specific adjustments on the source path; record observed rows without claiming reusable coverage.
- Replace BigQMT underlying stale/background reads with synchronous source reads behind the shared persistent layer; expose persistent-data health.
- Remove sector listing, sector membership and sector download operations. Contract and BigQMT compatibility identifiers become 8; install matching client, server and generated strategy builds.

- Force-replace the pinned online package, verify installed compatibility identifiers and bundled strategy fingerprints, and show the selected Python/configuration at startup.
- Report BigQMT protocol mismatches without executing requests; retain actionable bridge errors in health diagnostics.

- Validate BigQMT historical bars, five adjustment modes, suspension fallback, refresh/read consistency and persistence across service restart. Financial source checks cover Income in both date bases; xtquant deployment, other financial tables and long-duration stability are not claimed as validated.

## [0.7.0] - 2026-09-28

- Align public contract and private BigQMT bridge compatibility identifiers at 7. Upgrade clients, service and strategy together.
- Use the package release version for strategy startup identification, with a deterministic source fingerprint instead of ad hoc revision labels. Keep wire compatibility identifiers unchanged.

- Batch complete option records and native names in groups of 16, reducing a 22-contract chain from 44 pipe exchanges to two while preserving per-item failures.
- Recover explicit BigQMT option quotes omitted by get_full_tick through bounded latest-tick reads, preserving timestamps and five-level books. Add deployed-data replay and full-chain live acceptance tests.

- Simplify the trading contract (v7): remove native account/order/pricing codes, auxiliary position quantities, strategy names and submission/cancellation source codes. Retain raw order status, business status, correlation remarks and both cancellation identities.
- Keep native xtquant calling conventions, using an empty strategy-name argument while preserving correlation remarks and non-retryable unknown outcomes. The private BigQMT protocol is v7.
- Coordinate a-trader/a-options dependency and startup pins; migrate a-trader to daily-only capabilities and contract v7. Install matching client/server builds.

## [0.6.0] - 2026-09-28

- Add an independent full-QMT adapter, with a GBK Python 3.6 strategy bridge and bounded Windows named-pipe transport. Include the generated strategy and BigQMT installer/start wrappers in the Windows release bundle. The default MiniQMT adapter remains available.
- Support STOCK asset, position, order, submission and cancellation operations. Match a unique broker record by correlation remark and validated payload, tolerating the observed local signal row. Preserve source order statuses; cancellation success means a signal was sent, not final broker cancellation. Never replay uncertain writes.
- Persist the last successful option underlying list and return it during background refresh or refresh failure. Refresh daily, throttle failed refreshes, bound cold waits, and retain a separate five-minute contract-discovery cache. Cache diagnostics expose age and refresh errors.
- Allow missing trading-reference IsTrading and SettlementPrice values as null; retain false and zero without substitution. BigQMT SettlementPrice is the source previous-settlement field.
- Upgrade the public contract to v4 (27 operations): daily bars only; remove intraday operations and shareholder financial tables. Retain Balance, Income, CashFlow, Capital and PershareIndex. Clients and servers must upgrade together. Private BigQMT bridge protocol is 4.
- Add authenticated opt-in read-only native debugging. Group native reads, split whole-market snapshots by market, and reserve a heartbeat connection slot. Download APIs in BigQMT are explicitly compatibility no-ops.
- Deployment validation covered market/reference/options/core financial queries and simulated order/cancel signals. Source order snapshots remained stale after a broker terminal-state rejection; the release preserves that evidence rather than inventing final status. Long-duration recovery testing remains ongoing.

## [0.5.1] - 2026-09-25

- Add explicit QMT_XTQUANT_PATH selection and startup diagnostics for loaded SDK modules, resolved junctions and native extensions. Invalid configured SDK paths fail without falling back.
- Preserve SDK selection across initialization, checks and API exports; require a restart when changing SDKs.
- Support Windows installation from PyPI when no wheel is bundled and managed startup from checkouts without a local environment.
- Preserve the 0.5.0 business contract. Validate queries and bounded historical downloads against the deployed 2.1.9.1 SDK; order submission/cancellation were not exercised.

## [0.5.1.dev2] - Unreleased

- Add QMT_XTQUANT_PATH for explicit SDK selection independently of userdata_mini, shared by startup, checks, initialization and API export. Invalid or conflicting paths fail without falling back to the old SDK.
- Preserve explicit SDK configuration during initialization without replacing existing junctions. Restart is required to change SDKs.

## [0.5.1.dev1] - Unreleased

- Log actual loaded SDK module paths, resolved junction targets and loaded native extensions during server startup.
- Allow the Windows installer to use the pinned stable PyPI release when no wheel is bundled; preserve exact bundled-wheel installation for acceptance builds.
- Start the managed server when a source checkout has no initialized local environment.
- Keep the 0.5.0 business contract unchanged.

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
