# Dynamic Trader Account Adaptation Implementation Plan

**Goal:** Automatically adapt Trader methods whose runtime signature contains an
`account` parameter, while preserving every existing client API name, RPC
endpoint, argument convention, and response envelope.

**Architecture:** Keep the existing API discovery and dynamic client proxies
unchanged. At server startup, inspect the actual broker-customized
`XtQuantTrader` class and build a server-local map describing which method
parameters require `StockAccount` conversion. Merge the discovered map over the
existing nine-method compatibility fallback. Dispatch continues through
`call_trader(name, args, kwargs)`; the server converts account ID strings in
either positional or keyword arguments immediately before invoking xtquant.

**Tech Stack:** Python 3.10/3.11 on the Windows server, RPyC 6.x, xtquant,
pytest, `inspect.signature`

## Verified Baseline

- `server/api_surface.py` already discovers public xtdata callables and public
  `XtQuantTrader` methods at server startup.
- `client/proxy.py` already creates remote methods dynamically from the returned
  API surface and retains a non-private `__getattr__` fallback.
- No new RPC endpoint or client release is required to expose a newly discovered
  method.
- The deployed surface contains 51 Trader methods. All 51 have readable
  signatures.
- Thirty-five Trader methods have `account` as their first business parameter.
  The hard-coded `_ACCOUNT_METHODS` set currently covers nine, leaving 26
  without automatic `StockAccount` conversion.
- Eleven of those 26 methods also accept a callback. Account conversion and
  callback transport are separate concerns.

## Compatibility Invariants

- Do not rename, remove, or relocate `client.xtdata`, `client.trader`, or
  `client.xtconstant`.
- Do not change `call_xtdata`, `call_trader`, `get_api_surface`, event polling,
  download handles, or their response envelopes.
- Do not change the existing API surface shape or the existing `signature` and
  `doc` metadata.
- Preserve all nine entries in `_ACCOUNT_METHODS` as a fallback when runtime
  signature inspection fails.
- Preserve the existing behavior for non-string account values, including an
  already-created `StockAccount`.
- Continue allowing any authenticated client to use any account ID; this
  deployment intentionally trusts holders of `QMT_RPYC_AUTH_KEY`.
- Do not add account matching against `QMT_ACCOUNT_ID`.
- Do not change xtdata batching or introduce process-wide xtdata serialization.
- Do not block, rename, or otherwise alter already exposed Trader lifecycle
  methods in this change.
- Do not move `StockAccount` construction to the cross-platform client.

## Explicit Non-Goals

- Redesigning authentication, TLS, authorization, account isolation, or event
  ownership.
- Changing the generic dynamic proxy mechanism.
- Adding a strict RPC allowlist.
- Solving reverse RPyC transport for asynchronous callback arguments.
- Claiming that every method present in the API surface is end-to-end usable.
- Testing order placement or cancellation against a live securities account.
- Removing currently exposed lifecycle/internal methods such as `start`, `stop`,
  `connect`, `run_forever`, or `register_callback`.

---

### Task 1: Add failing regression coverage for runtime account discovery

**Files:**

- Modify: `tests/_xtquant_mock.py`
- Modify: `tests/test_connection.py`

**Purpose:** Prove the bug independently of the existing nine-method manual
list, including positional and keyword invocation.

- [ ] **Step 1: Extend the portable Trader mock with a method absent from the
  legacy fallback**

Add a read-only mock method using the name and parameter convention present in
the deployed SDK:

```python
def query_new_purchase_limit(self, account):
    return {
        "account_id": account.account_id,
        "limit": 10000,
    }
```

Also add a control method whose parameter is named `account_id`, not `account`,
so the discovery rule can be tested against false positives:

```python
def echo_account_id(self, account_id):
    return account_id
```

- [ ] **Step 2: Add a discovery unit test**

Add a test for the new private discovery helper:

```python
def test_discovers_runtime_account_parameter(self, mock_xtquant):
    from server.connection import _discover_account_parameters
    from xtquant.xttrader import XtQuantTrader

    discovered = _discover_account_parameters(XtQuantTrader)

    assert discovered["query_new_purchase_limit"].name == "account"
    assert discovered["query_new_purchase_limit"].position == 0
    assert "echo_account_id" not in discovered
```

The concrete internal representation may be a frozen dataclass or an immutable
tuple, but the test must verify both parameter name and client-visible
positional index.

- [ ] **Step 3: Add positional invocation coverage**

Call the dynamically discovered method with:

```python
result = cm.call_trader_method(
    "query_new_purchase_limit",
    ["ACC1"],
    {},
)
```

Assert that the mock receives a `StockAccount` and the RPC result is successful.
The method must not be added to `_ACCOUNT_METHODS` for this test.

- [ ] **Step 4: Add keyword invocation coverage**

Call the same method with:

```python
result = cm.call_trader_method(
    "query_new_purchase_limit",
    [],
    {"account": "ACC1"},
)
```

Assert success. This is a regression test for the current implementation, which
only examines `args[0]`.

- [ ] **Step 5: Cover values that must remain unchanged**

Add focused tests proving:

- An existing `StockAccount` instance is passed through unchanged.
- `echo_account_id(account_id="ACC1")` receives a string.
- A non-string `account` value is not reconstructed.
- Existing `order_stock` and `query_stock_asset` calls still work.

- [ ] **Step 6: Run the focused tests and confirm the new dynamic tests fail**

Run:

```bash
.venv/bin/python -m pytest tests/test_connection.py -v
```

Expected before implementation: existing tests pass; the new dynamically
discovered and keyword-account tests fail.

---

### Task 2: Implement signature-based account parameter discovery

**Files:**

- Modify: `server/connection.py`

**Purpose:** Replace the hard-coded list as the primary source of truth without
removing its compatibility value.

- [ ] **Step 1: Add a small immutable account-parameter descriptor**

Define a private descriptor containing:

- `name`: currently `"account"`
- `position`: zero-based position in the bound method's client-visible
  positional arguments, or `None` for a keyword-only parameter

Do not place SDK objects or callables in this descriptor.

- [ ] **Step 2: Add `_discover_account_parameters(trader_cls)`**

For each public callable on the actual Trader class:

1. Obtain `inspect.signature`.
2. Skip `self` or `cls`.
3. Track the position of positional-only and positional-or-keyword parameters.
4. Select only a parameter whose exact name is `account`.
5. Record keyword-only `account` with `position=None`.
6. Ignore methods whose signatures cannot be inspected.

Do not infer from documentation text, method-name prefixes, annotations, or
substrings such as `account_id`.

Signature failures should be logged at debug level with the method name. They
must not prevent Trader initialization.

- [ ] **Step 3: Seed the runtime map from `_ACCOUNT_METHODS`**

In `ConnectionManager.__init__`, initialize:

```python
self._account_parameters = {
    name: _AccountParameter(name="account", position=0)
    for name in _ACCOUNT_METHODS
}
```

This preserves the behavior of all nine existing methods even if a future
pybind11 build stops exposing signatures.

- [ ] **Step 4: Merge runtime discovery after constructing Trader**

Inside `_init_trader`, immediately after creating `XtQuantTrader`, discover
parameters from `type(self._trader)` and update the instance map.

Perform this on every fresh Trader construction so reconnect/reset uses the
actual current class. Discovery must happen before the Trader becomes callable
through the service.

Log one information-level summary:

```text
Discovered N Trader methods requiring StockAccount adaptation
```

Do not log account IDs or method arguments.

- [ ] **Step 5: Generalize `_wrap_account_if_needed`**

Change it to accept and return both `args` and `kwargs`:

```python
args, kwargs = self._wrap_account_if_needed(name, args, kwargs)
```

Required behavior:

- If no descriptor exists, return both inputs unchanged.
- If the account value is supplied positionally and is a string, replace only
  that element in a copied argument sequence.
- Otherwise, if `kwargs["account"]` is a string, replace only that value in a
  copied dictionary.
- If both positional and keyword values are supplied, adapt the positional value
  and let Python/xtquant report the duplicate argument normally.
- If the selected value is not a string, pass it through unchanged.
- Construct `StockAccount` only on the Windows server.

- [ ] **Step 6: Keep adaptation inside the standard error envelope**

Move account adaptation inside the existing `try` block in
`call_trader_method`. An import or constructor failure must return:

```python
{
    "status": STATUS_ERROR,
    "error_type": type(e).__name__,
    "error_message": str(e),
}
```

It must not escape `exposed_call_trader` as an unstructured RPyC exception.

- [ ] **Step 7: Run the focused tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_connection.py -v
```

Expected: all connection tests pass.

---

### Task 3: Lock down wire and client compatibility with regression tests

**Files:**

- Modify: `tests/test_api_surface.py`
- Modify: `tests/test_client.py` or `tests/test_integration.py`
- Do not modify: `client/client.py`
- Do not modify: `client/proxy.py`

**Purpose:** Demonstrate that the server-side fix is additive and requires no
consumer migration.

- [ ] **Step 1: Verify dynamic API discovery still exposes the mock method**

Assert that `query_new_purchase_limit` appears in:

```python
surface["XtQuantTrader"]["methods"]
```

Also assert that its existing metadata keys remain `signature` and `doc`. Do not
add required metadata fields that would force client upgrades.

- [ ] **Step 2: Verify the existing client dynamically creates the method**

Through the existing integration fixture:

```python
assert callable(client.trader.query_new_purchase_limit)
```

Invoke it positionally and verify the successful result. This deliberately uses
a synchronous, read-only API so callback transport is not coupled to account
adaptation.

- [ ] **Step 3: Preserve fallback behavior**

Retain or add tests showing:

- An API listed in the surface becomes an explicit `_RemoteCallable`.
- An unknown non-private method still uses `_RemoteTrader.__getattr__`.
- Private names still raise `AttributeError`.
- Existing method names and call routing remain unchanged.

- [ ] **Step 4: Run compatibility tests**

Run:

```bash
.venv/bin/python -m pytest \
  tests/test_api_surface.py \
  tests/test_proxy.py \
  tests/test_client.py \
  tests/test_integration.py -v
```

Expected: all tests pass without modifying client production code.

---

### Task 4: Document the runtime behavior and its boundary

**Files:**

- Modify: `AGENTS.md`
- Optionally modify: `README.md` if it currently describes the manual
  `_ACCOUNT_METHODS` behavior

**Purpose:** Prevent future work from reintroducing a manual completeness bug or
overstating callback support.

- [ ] **Step 1: Update the architecture description**

Document that:

- Public API names are discovered from the installed server-side xtquant build.
- Trader methods with an exact `account` parameter are adapted automatically.
- The legacy method set remains a signature-inspection fallback.
- Both positional and keyword account IDs are supported.
- The client remains xtquant-free and never constructs `StockAccount`.

- [ ] **Step 2: Document the callback limitation**

State that API discovery is not a guarantee of transport compatibility.
Methods accepting callbacks require separate live validation because the
current client does not run an explicit RPyC background-serving thread.

- [ ] **Step 3: Retain the accepted xtdata decision**

Keep the existing statement that process-wide xtdata serialization is not part
of this bug-fix scope.

---

### Task 5: Run portable regression and compatibility validation

**Files:** No production changes.

- [ ] **Step 1: Compile changed Python modules**

Run:

```bash
.venv/bin/python -m py_compile server/connection.py tests/_xtquant_mock.py
```

- [ ] **Step 2: Run all portable tests**

Run:

```bash
.venv/bin/python -m pytest tests/ -v -k "not live"
```

If the repository's portable wrapper is required, also run:

```bash
bash scripts/test.sh
```

- [ ] **Step 3: Check the worktree**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Do not alter or include unrelated existing changes such as `CLAUDE.md` or
`.improve-toolkit/`.

---

### Task 6: Validate against the broker-customized Windows SDK

**Files:** No committed changes expected from validation.

**Safety:** Use read-only queries only. Do not invoke order, cancel, `stop`,
`start`, `connect`, `register_callback`, or `run_forever`.

- [ ] **Step 1: Run the Windows server test suite**

Run:

```bat
scripts\test_server.bat
```

- [ ] **Step 2: Verify runtime discovery counts**

At server startup, confirm the log reports at least the 35 currently known
account-adapted Trader methods. If the count changes, compare it with a newly
generated surface rather than hard-coding 35 as a permanent invariant.

- [ ] **Step 3: Regenerate the surface for comparison**

Run:

```bat
python scripts\dump_api_surface.py
```

Compare API name sets with the tracked snapshot. Timestamp/runtime changes are
expected; no previously present function, method, constant, or class may
disappear because of this fix.

- [ ] **Step 4: Run a read-only positional smoke test**

Call:

```python
client.trader.query_new_purchase_limit(account_id)
```

Success criteria:

- The server does not report a pybind11 type mismatch for `account`.
- A normal broker result is returned, including an empty result if that is valid
  for the account.

- [ ] **Step 5: Run a read-only keyword smoke test**

Call:

```python
client.trader.query_new_purchase_limit(account=account_id)
```

Apply the same success criteria. If the customized wrapper itself rejects
keyword arguments despite exposing that signature, record that SDK behavior;
the existing positional API remains unaffected.

- [ ] **Step 6: Record unsupported business capabilities separately**

If credit or appointment queries return a broker-level unsupported result,
do not treat that as account-adaptation failure. Confirm the error is no longer
a `StockAccount`/argument-type error.

- [ ] **Step 7: Do not certify callback methods in this task**

The following account-taking async query family also needs callback transport:

- `query_appointment_info_async`
- `query_credit_assure_async`
- `query_credit_detail_async`
- `query_credit_slo_code_async`
- `query_credit_subjects_async`
- `query_new_purchase_limit_async`
- `query_stk_compacts_async`
- `query_stock_asset_async`
- `query_stock_orders_async`
- `query_stock_positions_async`
- `query_stock_trades_async`

Do not claim these are end-to-end supported until a separate design verifies
callback lifetime, re-entrant RPyC servicing, disconnect cleanup, and native
thread safety.

---

### Task 7: Commit and deploy in compatibility-preserving units

- [ ] **Step 1: Commit implementation and regression tests together**

```bash
git add server/connection.py tests/_xtquant_mock.py \
  tests/test_connection.py tests/test_api_surface.py \
  tests/test_client.py tests/test_integration.py
git commit -m "fix: auto-adapt trader account arguments"
```

Only add test files that were actually modified.

- [ ] **Step 2: Commit documentation separately**

```bash
git add AGENTS.md README.md \
  docs/superpowers/plans/2026-07-25-dynamic-trader-account-adaptation.md
git commit -m "docs: describe dynamic trader API adaptation"
```

Only add `README.md` if it was actually changed.

- [ ] **Step 3: Push only after portable tests pass**

```bash
git push origin master
```

- [ ] **Step 4: Perform Windows/QMT validation after pull**

If Windows validation finds a customized-signature exception, add a narrowly
scoped server-side override and a regression test. Do not change the client
call shape or require dependent systems to migrate.

## Acceptance Criteria

- Every inspectable public Trader method whose exact parameter name is
  `account` is registered for server-side `StockAccount` conversion.
- The existing nine-method fallback remains intact.
- Positional and keyword account ID strings are both converted.
- Existing `StockAccount` and non-string values are passed through.
- Account-construction failures use the normal structured RPC error response.
- No production client file changes.
- No RPC endpoint, API name, argument order, or result envelope changes.
- No xtdata execution-model changes.
- Portable tests pass.
- Windows read-only smoke tests show no account type mismatch.
- Async callback methods remain explicitly unverified rather than being
  incorrectly advertised as fully supported.

## Rollback

Because the wire protocol and client are unchanged, rollback is server-only:
revert the implementation commit and restart the Windows service. Existing
clients require no rollback or configuration change.
