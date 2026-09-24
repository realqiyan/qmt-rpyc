"""Client protocol/context/uncertainty tests using a local fake transport."""
from qmt_rpyc.contracts.common import BatchResult, Failure, ItemError, OperationError, Success
from qmt_rpyc.contracts.market import DailyBarSeries
from qmt_rpyc.contracts.downloads import DownloadStatus, TaskRef
from qmt_rpyc.contracts.system import Capabilities, Capability
from qmt_rpyc.contracts.trading import RequestSucceeded
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from qmt_rpyc import QmtClient
from qmt_rpyc.transport.codec import dumps, encode, loads
from qmt_rpyc.contracts.errors import OutcomeUnknownError, ProtocolError, QmtError
from qmt_rpyc.contracts.operations import CONTRACT_HASH, OPERATIONS


class Root:
    def __init__(self, callback):
        self.callback = callback
        self.calls = []

    def negotiate(self, digest):
        assert digest == CONTRACT_HASH
        return dumps({"contract_version": 2, "contract_hash": CONTRACT_HASH,
                      "capabilities": Capabilities({op: Capability(True, "fake", None) for op in OPERATIONS})})

    def call(self, payload):
        request = loads(payload)
        self.calls.append(request)
        result = self.callback(request)
        if isinstance(result, str):
            return result
        return dumps(dict(contract_version=2, request_id=request["request_id"], operation=request["operation"], status="ok", data=result))


def client(callback):
    value = QmtClient()
    value._conn = SimpleNamespace(root=Root(callback))
    value._negotiate()
    return value


def test_negotiation_is_explicit_and_does_not_fall_back():
    value = QmtClient()
    value._conn = SimpleNamespace(root=SimpleNamespace())
    with pytest.raises(ProtocolError, match="contract negotiation failed"):
        value._negotiate()
    connected = client(lambda request: [])
    assert connected.contract_version == 2
    assert set(connected.capabilities().operations) == set(OPERATIONS)


def test_requests_use_dates_and_explicit_defaults_without_remote_objects():
    value = client(lambda request: BatchResult((Success("510050.SH", DailyBarSeries((), "none")),)))
    result = value.market.get_daily_bars(["510050.SH"], end=date(2026, 9, 24), count=20)
    assert result.require_all()["510050.SH"].rows == ()
    payload = value._conn.root.calls[0]["payload"]
    assert payload == {"codes": ["510050.SH"], "start": None, "end": "2026-09-24", "count": 20,
                       "adjustment": "none", "fill_data": True}


def test_empty_codes_do_not_dispatch_or_masquerade_as_market_query():
    value = client(lambda request: pytest.fail("should not dispatch"))
    assert value.market.get_ticks([]).items == ()
    assert value._conn.root.calls == []


def test_single_string_is_not_split_into_security_codes():
    value = client(lambda request: pytest.fail("should not dispatch"))
    with pytest.raises(ValueError, match="sequence"):
        value.market.get_ticks("ABC")
    assert value._conn.root.calls == []


@pytest.mark.parametrize("operation", ["reference.list_sectors", "reference.get_sector_members", "instruments.list_option_underlyings"])
@pytest.mark.parametrize("identities", [[""], [" "], [" padded"], ["padded "], ["B", "A"], ["A", "A"]])
def test_discovery_identity_collections_are_validated(operation, identities):
    value = client(lambda request: identities)
    group, method = operation.split(".")
    call = getattr(getattr(value, group), method)
    with pytest.raises(ProtocolError):
        call("sector") if method == "get_sector_members" else call()


@pytest.mark.parametrize("change", ["extra", "missing"])
def test_runtime_capabilities_require_exact_operation_set(change):
    operations = {name: Capability(True, "fake", None) for name in OPERATIONS}
    if change == "extra":
        operations["undeclared.operation"] = Capability(True, "fake", None)
    else:
        del operations["market.get_ticks"]
    value = client(lambda request: Capabilities(operations))
    with pytest.raises(ProtocolError, match="operation set"):
        value.system.get_capabilities()


@pytest.mark.parametrize("codes", [("B", "A"), ("A",), ("A", "A"), ("A", "B", "C")])
def test_batch_rejects_reordering_omission_duplication_or_extra_identity(codes):
    value = client(lambda request: {"items": [{"status": "error", "code": code, "error": {"error_type": "MISSING_RESULT", "message": "missing"}} for code in codes]})
    with pytest.raises(ProtocolError):
        value.market.get_ticks(["A", "B"])


@pytest.mark.parametrize("field,bad", [("request_id", "wrong"), ("operation", "wrong"), ("contract_version", 1)])
def test_read_response_must_match_request_context(field, bad):
    def response(request):
        envelope = dict(contract_version=2, request_id=request["request_id"], operation=request["operation"], status="ok", data=[])
        envelope[field] = bad
        return dumps(envelope)
    with pytest.raises(ProtocolError):
        client(response).reference.list_sectors()


@pytest.mark.parametrize("call", [lambda c: c.trading.submit_order("account", "600000.SH", "BUY", 100, 5),
                                  lambda c: c.trading.cancel_order("account", order_id="123"),
                                  lambda c: c.downloads.start_sectors()])
def test_invalid_mutation_response_is_unknown_and_never_retried(call):
    value = client(lambda request: "bad JSON")
    with pytest.raises(OutcomeUnknownError) as caught:
        call(value)
    assert caught.value.outcome == "unknown"
    assert len(value._conn.root.calls) == 1


def test_transport_error_preserves_read_vs_mutation_outcome():
    def broken(request):
        raise EOFError("lost")
    value = client(broken)
    with pytest.raises(QmtError) as caught:
        value.reference.list_sectors()
    assert caught.value.phase == "transport" and caught.value.outcome == "not_applicable"
    with pytest.raises(OutcomeUnknownError):
        value.downloads.start_index_weights()
    assert len(value._conn.root.calls) == 2


def test_known_preexecution_error_is_not_unknown_submission():
    def response(request):
        error = OperationError("NOT_CONNECTED", "not ready", request["operation"], 2,
                                 "pre_execution", "not_executed", request["request_id"])
        return dumps(dict(contract_version=2, request_id=request["request_id"], operation=request["operation"], status="error", error=error))
    with pytest.raises(QmtError) as caught:
        client(response).trading.submit_order("a", "600000.SH", "BUY", 100, 1)
    assert not isinstance(caught.value, OutcomeUnknownError)
    assert caught.value.outcome == "not_executed"


def test_cancel_requires_exactly_one_identity_kind():
    value = client(lambda request: RequestSucceeded(0))
    for kwargs in ({}, {"order_id": "123", "market": "SH"}, {"market": "SH"}):
        with pytest.raises(ValueError):
            value.trading.cancel_order("account", **kwargs)
    value.trading.cancel_order("account", market="SH", exchange_order_id="000123")
    assert value._conn.root.calls[0]["payload"]["target"] == {"kind": "exchange_order_id", "market": "SH", "exchange_order_id": "000123"}


def test_download_failed_terminal_is_returned_without_resubmission():
    at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    error = OperationError("SOURCE_ERROR", "failed", "downloads.start_history", 2, "sdk_execution", "unknown", "original")
    status = DownloadStatus("task", "HISTORY", "failed", at, at, None, error)
    value = client(lambda request: status)
    result = value.downloads.handle(TaskRef("task", "HISTORY")).wait()
    assert result.status == "failed" and result.error.message == "failed"
    assert [call["operation"] for call in value._conn.root.calls] == ["downloads.get_task"]


def test_download_wait_timeout_does_not_cancel_or_restart(monkeypatch):
    at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    status = DownloadStatus("task", "HISTORY", "running", at, None, None, None)
    value = client(lambda request: status)
    clock = iter((0, 2))
    monkeypatch.setattr("qmt_rpyc.client.downloads.time.monotonic", lambda: next(clock))
    with pytest.raises(TimeoutError):
        value.downloads.handle(TaskRef("task", "HISTORY")).wait(timeout=1)
    assert [call["operation"] for call in value._conn.root.calls] == ["downloads.get_task"]
