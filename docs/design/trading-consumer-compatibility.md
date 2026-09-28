# Trading simplification: consumer compatibility audit

Reviewed 2026-09-28 against local a-trader and a-options sources and qmt-rpyc
0.6.0. The resulting simplification is implemented in 0.7.0 (contract v7).

## Actual consumers

- a-trader/core/execution/qmt.py reads Asset.account/cash/frozen_cash/market_value/
  total_asset; Position.instrument/quantity/available_quantity; and
  Order.order_id/submitted_at/requested_quantity/filled_quantity/
  average_fill_price/instrument/correlation_ref/source_status/status.
- a-trader submits LATEST_PRICE orders with price, strategy_name and
  correlation_ref. It branches on Submitted/Rejected and converts order IDs to
  canonical positive integers bounded by SQLite signed 64-bit storage.
- a-trader/core/execution/reconciliation.py retains raw remote_status in audit
  evidence and checks it against normalized terminal status. Removing
  source_status requires an application evidence migration, not just removing
  an unused display field. Never manufacture raw codes from normalized status.
- a-options/market/qmt_gateway.py uses market, instrument, option, dividend and
  daily-history download operations; it does not call the trading capability.

## Revised proposal

Keep source_status and correlation_ref. strategy_name was passed to native SDKs but never used for application reconciliation, order matching or cancellation. Remove it publicly; adapters pass an empty string at the native positional slot. Keep both public order
identity and optional exchange identity, account validation, available quantity,
fill quantities/prices, normalized status and explicit unknown outcomes.

Candidates unused by these consumers: source_account_type; Position.frozen_volume,
on_road_volume and yesterday_volume; Order.source_order_type and source_price_type;
submission/cancellation source_code. Their removal still breaks the published
wire schema and must be coordinated, not shipped as an incidental debug fix.
Application-local strategy identities and durable correlation remarks preserve traceability.
Avoid cosmetic field/status renames during this reduction.

## Existing version blockers

Both applications pin qmt-rpyc==0.5.1 in requirements.txt. a-trader also targets
0.5.1 in core/runtime/qmt_upgrade.py and explicitly requires contract_version==2
in core/runtime/qmt.py. qmt-rpyc 0.6.0 uses contract 4 and strict schema
negotiation. Updating requirements alone is insufficient; the runtime gate and
startup installation target must be migrated together with client/server.

## xtquant compatibility boundary

The existing xtquant_2_0_6_1 adapter remains authoritative for this deployed SDK,
not generic public xtquant releases. Preserve its native mappings:

- order_stock side BUY/SELL -> 23/24, LIMIT/LATEST_PRICE -> 11/5; forward price,
  correlation_ref unchanged, with an empty native strategy_name argument.
- A positive native integer submission ID becomes an opaque public string;
  -1 is rejected; malformed post-submission results have unknown outcomes.
- cancel_order_stock accepts the validated integer order ID;
  cancel_order_stock_sysid uses SH/SZ -> 0/1 and the exchange ID.
- Native cancellation integer 0 means request success; this differs from the
  BigQMT boolean result and must remain adapter-specific. Neither proves final
  cancellation.
- Preserve raw status and its normalized mapping, and never retry uncertain
  mutations. Public IDs remain opaque even though this consumer needs integers.

No native SDK upgrade or live trading call was performed. Consumer dependencies and startup targets now select 0.7.0; a-trader checks contract v7 and rejects intraday requests before RPC. Full deployed Windows compatibility cannot be
inferred for every xtquant version from synthetic tests.

## Validation

- qmt-rpyc: `.venv/bin/python -m pytest tests/ -q`: 534 passed, 5 skipped.
- a-trader with PYTHONPATH pointing to qmt-rpyc/src: test_qmt.py,
  test_qmt_safety.py, test_qmt_rpyc_upgrade.py: 64 passed.
- a-options with the same source override: test_qmt_contract.py and
  test_qmt_query_errors.py: 30 passed.

Consumer tests use synthetic connections; passing does not resolve the real
contract-2 gate or prove connectivity to a contract-4 server. Before adopting a
new contract, migrate those gates/pins, test actual negotiation locally, rerun
both adapters and consumer regressions, and validate the deployed Windows SDK.

## a-options underlying discovery

The gateway invokes instruments.list_option_underlyings(), filters SH/SZ codes,
and batches get_details() for names. It has no static list or local discovery
cache. Portfolio queries merge saved portfolio underlyings and ledger holdings
with discovery; without a gateway, or after discovery failure, local identities
can still be returned with market_available=false. Portfolio configuration lists
read local storage, not the market-wide QMT directory.

The xtquant adapter queries get_option_undl_data and validates candidate expiry
with concurrent native detail reads. BigQMT must execute native calls on the
strategy thread and uses a persisted last-successful directory plus background
refresh. UI responsiveness alone does not measure a cold BigQMT discovery. This
inspection did not measure the currently running a-options process or change its
cache policy.

Post-migration: a-trader full suite 1339 passed; two additional real DTO/installed-contract tests passed. a-options final full suite: 426 passed, 23 skipped (including upgrade and QMT regression coverage). A read-only timing attempt with its installed 0.5.1 client failed at protocol negotiation, before querying the directory; no valid live latency was measured.

## 0.7.0 Windows acceptance (2026-09-28)

The user started the Windows bundle. Live negotiation reported package 0.7.0,
contract 5, BigQMT connected, zero heartbeat failures and all 27 capabilities
available. Read-only tick, daily bar, instrument, nullable trading reference,
calendar, option expiry/chain/detail, asset, position and order calls passed.
No order submission or cancellation was performed.

The initial two underlying calls failed while cold discovery was pending.
Background discovery subsequently populated nine entries with no cache error.
Two subsequent list calls each took about 0.001 seconds. Fetching the nine
instrument details separately took 9.808 and 11.792 seconds. The actual a-options
gateway's two full list-plus-name calls took 11.717 and 11.205 seconds. Thus
successful list caching does not eliminate the separate detail-query latency.

Both consumer virtual environments were upgraded from the bundled 0.7.0 wheel
without changing credentials or other dependencies. pip check passed in both.
Installed-package regressions: a-trader 66 passed; a-options 35 passed. The actual
a-trader provider connected and validated its account/calendar, then read
positions and orders successfully. Existing long-running application processes
must restart to load the new package. This validates BigQMT on this deployment;
it is not a new real-SDK xtquant trading test or a PyPI publication.

## Option-chain incident: omitted option ticks

Both authenticated a-options option-chain HTTP requests for 510300.SH and
159915.SZ (expiry 20261028) returned 503. Each chain contained 22 options, all
omitted by native get_full_tick; ETF ticks were present. Prior acceptance checked
option metadata but not quotes for all discovered contracts. The missing-result
unit test exercised rejection, but did not establish end-to-end chain usability.

Native debug confirmed get_market_data_ex(period='tick', count=1, fill_data=False,
subscribe=True) returns the option timestamp, previous settlement and full
five-level book. subscribe=False returned empty frames for cold contracts on
this deployment. The strategy now recovers only missing explicit SHO/SZO codes,
in groups of at most 16, preserving existing ticks and explicit failures for
unrecoverable contracts. No invented prices, renamed exchanges, dropped batch
items or mutation retries are used. Public contract and private protocol remain
unchanged; replace the embedded strategy to activate the fix.

Regression coverage replays public-market DataFrames captured from this
installation through StrategyRuntime, JSON and MarketAdapter, preserving original
book and timestamp values. It also covers empty/error responses, existing native
quotes, group bounds and stock isolation. An opt-in live test now requires full
quotes and metadata for the nearest future Shanghai and Shenzhen option chains.
Authenticated HTTP acceptance must also pass after deployment.

After the user's 01:20:38 QMT strategy restart, the bridge instance changed.
Authenticated a-options Web API checks were rerun serially: Shanghai 510300.SH
and Shenzhen 159915.SZ, expiry 20261028, both returned HTTP 200 and all 22
contracts, in 32.66 and 32.98 seconds. Initial overlapping acceptance requests
had timed out around the restart; those failures are not counted as passes.
The page failure is resolved on this deployment, but full-chain detail latency
remains material. Both PM2 services are online and a-trader's QMT health is
connected. Portable regression: 540 passed, 7 skipped. The Windows ZIP now
includes the same corrected GBK strategy sent for deployment.

Further live regression after both HTTP successes still failed during contract
metadata reads and briefly invalidated bridge connectivity. Therefore two HTTP
200 responses do not constitute stable acceptance. Full option records now use
the existing bounded option_details operation with private supplemental instrument
identity/name data. Old strategy responses remain readable through the prior
name lookup; new deployments need both the updated strategy and service for the
two-exchange path. A 22-contract replay checks two pipe calls instead of 44;
source failures remain per-item. No public or private protocol version changes.

After the combined option-chain-fix-2 deployment, read-only acceptance succeeded:

- Authenticated a-options expiry endpoints returned four dates for each market
  (first observed reads 9.61s SH / 1.41s SZ; subsequent cached SH read <0.01s).
- Authenticated chains for 510300.SH and 159915.SZ at 20261028 each returned all
  22 contracts: first 4.06s / 1.74s, repeat 1.79s / 1.88s, all HTTP 200.
- Opt-in tests/test_live_option_chain_quotes.py: 2 passed in 3.73s, requiring
  every contract's metadata and quote, with no partial-result suppression.
- Concurrent expiry, volatility and 252-day-bar HTTP queries all returned 200
  (0.00s, 0.64s, 1.98s); the root page responded in 0.005s during these reads.
- Final bridge health: connected, zero heartbeat failures, no active business
  requests. These checks performed no trading mutations.

The a-options consumer now uses the dedicated expiry operation instead of
reloading every contract. Market reads run outside its HTTP event loop while
SQLite work remains on the owning thread; shared client access/reconnection is
locked. Successful underlying names are cached for one hour. Portable suites:
qmt-rpyc 543 passed / 7 skipped; a-options 429 passed / 23 skipped. This is a
bounded live acceptance run, not a long-running stability or browser UI test.

Release alignment: 0.7.0 uses public contract 7 and private bridge protocol 7.
Earlier acceptance above used the same business implementation before identifier
alignment. Final identifier changes require coordinated deployment; no trading
semantics or native API calls changed.
