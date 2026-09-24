"""codec and behavioral boundary regression tests, independent of QMT."""
from qmt_rpyc.contracts.common import BatchResult, CodesRequest, Failure, ItemError, Success
from qmt_rpyc.contracts.options import OptionChain, OptionContract
from qmt_rpyc.contracts.instruments import DatePlaceholder, KnownDate, SourceDate
from qmt_rpyc.contracts.market import DailyBarsQuery
from qmt_rpyc.contracts.reference import IndexWeights
from qmt_rpyc.contracts.financials import FINANCIAL_TABLES, FinancialReports
from qmt_rpyc.contracts.downloads import DownloadStatus, HistoryDownloadRequest
from qmt_rpyc.contracts.trading import Submitted
from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from typing import Tuple

import pytest

from qmt_rpyc.contracts.errors import BatchIncompleteError
from qmt_rpyc.transport.codec import decode, dumps, encode, loads
from qmt_rpyc.contracts.errors import ProtocolError
from qmt_rpyc.contracts.operations import CONTRACT_HASH, OPERATIONS, manifest


def option():
    return OptionContract("510050.SH", "original A", "CALL", date(2026, 10, 28), 2.5, 10000)


def test_generic_batch_roundtrip_preserves_values_and_partial_failure():
    value = BatchResult((Success("one.SHO", option()),
                           Failure("two.SHO", ItemError("MISSING_RESULT", "omitted"))))
    result = decode(BatchResult[OptionContract], loads(dumps(value)))
    assert result == value
    assert type(result.items[0].value.strike_price) is float
    with pytest.raises(BatchIncompleteError) as caught:
        result.require_all()
    assert caught.value.result is result
    assert BatchResult((value.items[0],)).require_all() == {"one.SHO": option()}
    with pytest.raises(FrozenInstanceError):
        result.items = ()


@pytest.mark.parametrize("bad", [True, "2.5", float("nan"), float("inf"), None])
def test_numeric_fields_reject_coercion_and_nonfinite(bad):
    raw = encode(option())
    raw["strike_price"] = bad
    with pytest.raises(ProtocolError):
        decode(OptionContract, raw)


@pytest.mark.parametrize("bad", [True, 10000.0, 10000.5, "10000", 0, -1])
def test_unit_is_strict_positive_integer(bad):
    raw = encode(option())
    raw["contract_unit"] = bad
    with pytest.raises(ProtocolError):
        decode(OptionContract, raw)


def test_dates_and_instants_have_separate_canonical_encodings():
    instant = datetime(2026, 9, 24, 0, 0, 0, 123000, tzinfo=timezone(timedelta(hours=8)))
    assert encode(instant) == "2026-09-23T16:00:00.123000Z"
    assert decode(datetime, encode(instant)) == instant
    assert decode(date, "2026-09-24") == date(2026, 9, 24)
    for bad in ("20260924", "2026-09-24T00:00:00Z", "2026-02-30"):
        with pytest.raises(ProtocolError):
            decode(date, bad)
    for bad in ("2026-09-24T00:00:00Z", "2026-09-24T00:00:00.000000+08:00"):
        with pytest.raises(ProtocolError):
            decode(datetime, bad)
    with pytest.raises(ProtocolError):
        encode(datetime(2026, 9, 24))


def test_missing_unknown_and_discriminator_fields_are_rejected():
    raw = encode(option())
    del raw["name"]
    with pytest.raises(ProtocolError):
        decode(OptionContract, raw)
    raw = encode(option())
    raw["extra"] = 1
    with pytest.raises(ProtocolError):
        decode(OptionContract, raw)
    with pytest.raises(ProtocolError):
        decode(BatchResult[OptionContract], {"items": [{"code": "one", "value": encode(option())}]})


@pytest.mark.parametrize("payload", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{"a":1e309}', '[1,]'])
def test_json_parser_rejects_duplicate_keys_nonfinite_and_invalid_json(payload):
    with pytest.raises(ProtocolError):
        loads(payload)


def test_source_date_distinguishes_placeholder_from_missing_or_known():
    assert decode(SourceDate, {"kind": "placeholder", "raw": "99999999"}) == DatePlaceholder("99999999")
    assert decode(SourceDate, {"kind": "known", "value": "1999-11-10"}) == KnownDate(date(1999, 11, 10))
    with pytest.raises(ProtocolError):
        decode(SourceDate, {"kind": "known", "value": "99999999"})


def test_public_order_id_is_opaque_and_not_a_baseline_integer():
    assert decode(Submitted, {"status": "submitted", "order_id": "venue:0001"}).order_id == "venue:0001"


@pytest.mark.parametrize("codes", [[""], [" "], [" padded"], ["padded "], ["B", "A"], ["A", "A"]])
def test_option_chain_rejects_invalid_identity_collection(codes):
    with pytest.raises(ProtocolError):
        decode(OptionChain, {"market_date": "2026-09-24", "contract_codes": codes})


def test_option_chain_accepts_opaque_non_numeric_identity():
    result = decode(OptionChain, {"market_date": "2026-09-24", "contract_codes": ["IO2103-P-3100.IF"]})
    assert result.contract_codes == ("IO2103-P-3100.IF",)


def test_mapping_values_are_frozen_after_decoding():
    result = decode(IndexWeights, {"weights": {"600000.SH": 0.5}})
    with pytest.raises(TypeError):
        result.weights["600000.SH"] = 1


def test_financial_all_tables_have_selected_fields_and_unrequested_none():
    values = {name: None for name in FINANCIAL_TABLES}
    values["Balance"] = []
    result = decode(FinancialReports, values)
    assert result.Balance == () and result.Income is None
    assert len(manifest()["operations"]["financials.get_reports"]["response"]["fields"]) > 0


@pytest.mark.parametrize("kwargs", [dict(count=0), dict(count=-1), dict(count=True),
                                   dict(start=date(2026, 9, 24), count=3),
                                   dict(start=date(2026, 9, 24), end=date(2026, 9, 23))])
def test_queries_reject_ambiguous_or_unbounded_count_semantics(kwargs):
    with pytest.raises(ValueError):
        DailyBarsQuery(("510050.SH",), **kwargs)


def test_duplicate_and_excess_codes_are_rejected_but_empty_is_valid():
    assert CodesRequest(()).codes == ()
    for codes in (("one", "one"), tuple(str(i) for i in range(501)), (" padded",)):
        with pytest.raises(ValueError):
            CodesRequest(codes)


def test_download_period_enforces_date_or_instant_boundaries():
    with pytest.raises(ValueError):
        HistoryDownloadRequest("510050.SH", "1m", date(2026, 9, 24))
    with pytest.raises(ValueError):
        HistoryDownloadRequest("510050.SH", "1d", datetime.now(timezone.utc))


def test_task_failure_is_not_inferred_from_null_result():
    at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    value = DownloadStatus("task", "HISTORY", "completed", at, at, None, None)
    assert decode(DownloadStatus, encode(value)).result is None
    with pytest.raises(ValueError):
        replace(value, status="failed")


def test_registry_covers_all_groups_and_all_mutations_include_downloads():
    assert len(OPERATIONS) == 28
    assert len(CONTRACT_HASH) == 64
    assert all(operation.mutation for name, operation in OPERATIONS.items() if name.startswith("downloads.start_"))
    assert not OPERATIONS["downloads.get_task"].mutation
    assert manifest() == manifest()
