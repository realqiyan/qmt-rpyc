"""Safety boundaries of the standalone QMT investigation strategy."""
import ctypes
from ctypes import wintypes
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).resolve().parents[1] / "scripts" / "probe_bigqmt_strategy.py"
    spec = importlib.util.spec_from_file_location("bigqmt_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROBE_PHASE = 1
    return module


def records(output):
    return [json.loads(line.removeprefix("QMT_RPYC_PROBE "))
            for line in output.splitlines()]


def test_phase_seven_reads_two_raw_tables_without_flattening_holder_lists(probe, capsys):
    calls = []
    class Context:
        def get_raw_financial_data(self, fields, codes, start, end, basis):
            calls.append((fields, codes, start, end, basis))
            return {codes[0]: {field: {1782748800000: [1., 2., 3., 4.]}
                               for field in fields}}
    probe.PROBE_PHASE = 7
    probe.init(Context())
    probe.handlebar(Context())
    assert len(calls) == 2
    assert [call[0][0] for call in calls] == ['TOP10HOLDER.declareDate', 'TOP10FLOWHOLDER.declareDate']
    assert all(call[1:] == (['000001.SZ'], '20260629', '20260703', 'report_time') for call in calls)
    output = records(capsys.readouterr().out)
    assert output[-1]['event'] == 'top10_raw_complete'
    sample = next(row['data']['sample'] for row in output if row['event'] == 'top10_raw_sample')
    leaf = sample['items'][0][1]['items'][0][1]['items'][0][1]
    assert leaf == {'type': 'list', 'count': 4, 'items': [1., 2., 3.], 'truncated': True}


def test_phase_eight_preserves_duplicate_indices_columns_and_all_ten_rows(probe, capsys):
    pd = pytest.importorskip('pandas')
    calls = []
    class Context:
        def get_raw_financial_data(self, *args):
            calls.append(args)
            return {'000001.SZ': pd.DataFrame(
                [[rank, rank * 100] for rank in range(1, 11)],
                index=[1782748800000] * 10, columns=['same', 'same'])}
    probe.PROBE_PHASE = 8
    probe.init(Context())
    probe.handlebar(Context())
    output = records(capsys.readouterr().out)
    assert output[-1]['event'] == 'top10_frame_complete'
    assert len(calls) == 2
    assert all(args[1:] == (['000001.SZ'], '20260629', '20260703', 'report_time', 'frame') for args in calls)
    sample = next(row['data']['sample'] for row in output if row['event'] == 'top10_frame_sample')
    frame = sample['items'][0][1]
    assert frame['row_values'] == [[rank, rank * 100] for rank in range(1, 11)]
    assert frame['index'] == [1782748800000] * 10
    assert frame['columns'] == ['same', 'same']
    assert not frame['index_is_unique'] and not frame['columns_is_unique'] and not frame['truncated']


def test_phase_eight_reports_rejected_candidate_without_format_retries(probe, capsys):
    calls = []
    class Context:
        def get_raw_financial_data(self, *args):
            calls.append(args)
            raise ValueError('private-native-error')
    probe.PROBE_PHASE = 8
    probe.init(Context())
    output = capsys.readouterr().out
    assert len(calls) == 2
    assert 'private-native-error' not in output
    assert sum(row['event'] == 'capability_read_result' and row['data']['status'] == 'error'
               for row in records(output)) == 2


def test_top10_frame_sample_is_bounded_and_retains_full_row_count(probe):
    pd = pytest.importorskip('pandas')
    frame = pd.DataFrame({'rank': range(1, 101)})
    sample = probe._top10_frame_sample(frame)
    assert sample['shape'] == [100, 1] and sample['truncated']
    assert len(sample['row_values']) == 12


def test_probe_only_invokes_public_reads_and_timer(probe, monkeypatch, capsys):
    calls = []

    class Context:
        def get_full_tick(self, codes):
            calls.append(("ticks", codes))
            return {codes[0]: {"lastPrice": 0, "time": None}}

        def get_instrumentdetail(self, code):
            calls.append(("details", code))
            return {"InstrumentName": "UNREDACTED_VALUE"}

        def run_time(self, name, interval, start):
            calls.append(("timer", name, interval))

        def __getattr__(self, name):
            if name.startswith(("get_", "download", "down_")) or name in ("passorder", "cancel"):
                def forbidden(*args, **kwargs):
                    pytest.fail("Probe invoked an unapproved method: " + name)
                return forbidden
            raise AttributeError(name)

    monkeypatch.setattr(probe, "_probe_pipe", lambda: {"status": "test_stub"})
    probe.init(Context())
    output = capsys.readouterr().out
    assert calls == [("ticks", ["000001.SZ"]), ("details", "000001.SZ"),
                     ("timer", "probe_timer", "1000nMilliSecond")]
    assert "UNREDACTED_VALUE" not in output
    rows = records(output)
    tick = next(row["data"] for row in rows if row["event"] == "read_result")
    fields = tick["shape"]["fields"]["000001.SZ"]["fields"]
    assert fields["lastPrice"]["present"] is True
    assert fields["time"]["present"] is False
    assert any(row["event"] == "init_complete" for row in rows)


def test_query_error_is_visible_without_raw_details_and_next_query_runs(probe, capsys):
    class Context:
        def get_full_tick(self, codes):
            raise RuntimeError("private account or filesystem path")

        def get_instrumentdetail(self, code):
            return {}

    probe._public_reads(Context())
    output = capsys.readouterr().out
    assert "private account" not in output
    results = [row["data"] for row in records(output) if row["event"] == "read_result"]
    assert results[0]["error_type"] == "RuntimeError"
    assert results[1]["status"] == "returned"
    assert results[1]["shape"]["count"] == 0


def test_timer_registration_does_not_claim_callback_execution(probe, monkeypatch, capsys):
    context = SimpleNamespace(run_time=lambda *args: None)
    monkeypatch.setattr(probe, "_probe_pipe", lambda: {"status": "test_stub"})
    probe.init(context)
    initial = records(capsys.readouterr().out)
    registered = next(row["data"] for row in initial if row["event"] == "timer_registration")
    assert registered["callback_observed"] is False
    assert not any(row["event"] == "callback" for row in initial)
    for _ in range(100):
        probe.probe_timer(context)
        probe.handlebar(context)
    callbacks = records(capsys.readouterr().out)
    assert len([row for row in callbacks if row["event"] == "callback"]) == 7
    assert len([row for row in callbacks if row["event"] == "callback_sample_complete"]) == 1


def test_capability_phase_uses_returned_members_without_guessing_native_signatures(probe, monkeypatch, capsys):
    calls = []

    def metadata_only(*args):
        pytest.fail("Unknown native signature must not be called")

    metadata_only.__doc__ = "get_history_index_weight(index, date) -> dict"
    monkeypatch.setattr(probe, "get_history_index_weight", metadata_only, raising=False)
    monkeypatch.setattr(probe, "get_sector_list", lambda node: [["PRIVATE_SECTOR"], []], raising=False)

    class Context:
        def get_sector(self, code):
            return ["600000.SH", "600000.SH", "000001.SZ", "600036.SH", "601398.SH", None]

        def get_weight_in_index(self, index, code):
            calls.append((index, code))
            return 0.75

        def get_instrument_detail(self, code):
            # A second iscomplete argument would fail this deployed signature.
            return {"IsTrading": None, "SettlementPrice": None}

        def download_financial_data(self, *args):
            pytest.fail("Read-only probe must not download")

        def run_time(self, *args):
            pytest.fail("Capability phase does not need a timer")

    probe.PROBE_PHASE = 2
    probe.init(Context())
    output = capsys.readouterr().out
    assert "PRIVATE_SECTOR" not in output
    assert calls == [("000300.SH", "600000.SH"), ("000300.SH", "000001.SZ"),
                     ("000300.SH", "600036.SH")]
    assert "get_history_index_weight(index, date)" in output
    assert records(output)[-1]["event"] == "capability_probe_complete"
    assert not probe._STATE


def test_empty_index_members_do_not_trigger_weight_queries(probe, capsys):
    def forbidden(*args):
        pytest.fail("Cannot infer constituents from an empty result")

    context = SimpleNamespace(get_sector=lambda code: [], get_weight_in_index=forbidden)
    probe._capability_probe(context)
    rows = records(capsys.readouterr().out)
    assert not any(row["event"] == "index_weight_sample" for row in rows)
    assert rows[-1]["event"] == "capability_probe_complete"


def test_mapping_phase_reads_public_samples_without_trading_or_downloads(probe, monkeypatch, capsys):
    calls = []

    def forbidden(*args, **kwargs):
        pytest.fail("Mapping probe must not invoke trading or downloads")

    monkeypatch.setattr(probe, "get_history_index_weight", lambda index: {"20260928": ["000001.SZ"]}, raising=False)
    monkeypatch.setattr(probe, "passorder", forbidden, raising=False)
    monkeypatch.setattr(probe, "get_trade_detail_data", forbidden, raising=False)
    monkeypatch.setattr(probe, "download_history_data", forbidden, raising=False)

    class Context:
        def get_option_undl_data(self, underlying):
            return ["10000001.SH", "10000001.SH", "10000002.SH", "10000003.SH"]

        def get_option_detail_data(self, code):
            calls.append(("option", code))
            return {"OptUnit": 10000, "optType": "CALL"}

        def get_instrumentdetail(self, code):
            return {"IsTrading": None, "SettlementPrice": None}

        def get_market_data_ex(self, **kwargs):
            assert kwargs["subscribe"] is False
            assert kwargs["count"] == 2
            calls.append(("bars", kwargs["period"]))
            return {"000001.SZ": []}

        def get_financial_data(self, *args):
            assert args[1] == ["000001.SZ"]
            calls.append(("financials", args[-1]))
            return {"tot_assets": float("nan")}

    probe.PROBE_PHASE = 3
    probe.init(Context())
    rows = records(capsys.readouterr().out)
    assert calls == [("option", "10000001.SH"), ("option", "10000002.SH"),
                     ("bars", "1d"), ("financials", "report_time")]
    assert rows[-1]["event"] == "mapping_probe_complete"
    sample = next(row["data"]["sample"] for row in rows if row["event"] == "financial_mapping_sample")
    assert sample["items"][0][1] == {"nonfinite": True}


def test_public_dataframe_sample_keeps_all_bounded_columns_and_only_two_rows(probe):
    pd = pytest.importorskip("pandas")
    frame = pd.DataFrame({"field_" + str(i): [i, i + 1, i + 2] for i in range(10)})
    sample = probe._public_sample({"000001.SZ": frame})["items"][0][1]
    assert sample["shape"] == [3, 10]
    assert len(sample["rows"]) == 2
    assert sample["rows"][0]["field_9"] == 9
    assert sample["truncated"] is True


def test_financial_phase_uses_same_small_window_and_default_raw_format(probe, capsys):
    calls = []

    class Context:
        def get_financial_data(self, fields, codes, start, end, report_type):
            calls.append(("filled", fields, codes, start, end, report_type))
            return {"tot_assets": float("nan")}

        def get_raw_financial_data(self, fields, codes, start, end, report_type, data_type="default"):
            assert data_type == "default"
            calls.append(("raw", fields, codes, start, end, report_type))
            return {"000001.SZ": {"ASHAREBALANCESHEET.tot_assets": {1782748800000: 123.5}}}

        def get_option_undl_data(self, underlying):
            return ["10000001.SHO", "10000002.SHO"]

        def get_option_detail_data(self, code):
            calls.append(("option", code))
            return {"optType": "CALL"}

        def get_market_data_ex(self, *args, **kwargs):
            pytest.fail("No bar query is needed in phase 4")

        def passorder(self, *args):
            pytest.fail("Financial probe must not trade")

        def download_financial_data(self, *args):
            pytest.fail("Financial probe must not explicitly download")

    probe.PROBE_PHASE = 4
    probe.init(Context())
    rows = records(capsys.readouterr().out)
    expected = (["ASHAREBALANCESHEET.tot_assets"], ["000001.SZ"], "20260629", "20260703", "report_time")
    assert calls == [("filled",) + expected, ("raw",) + expected, ("option", "10000001.SHO")]
    comparisons = [r["data"] for r in rows if r["event"] == "financial_comparison_sample"]
    assert len(comparisons) == 2
    assert comparisons[0]["sample"]["items"][0][1] == {"nonfinite": True}
    assert comparisons[1]["sample"]["items"][0][1]["items"][0][1]["items"] == [[1782748800000, 123.5]]
    assert next(r["data"] for r in rows if r["event"] == "option_type_sample")["fields"] == {"optType": "CALL"}
    assert rows[-1]["event"] == "financial_probe_complete"
    assert not probe._STATE


@pytest.mark.parametrize("end", ["20260628", "20260707"])
def test_financial_phase_rejects_reversed_or_large_window(probe, monkeypatch, capsys, end):
    def forbidden(*args):
        pytest.fail("Invalid financial window must not reach QMT")
    monkeypatch.setattr(probe, "FINANCIAL_END", end)
    probe._financial_probe(SimpleNamespace(get_financial_data=forbidden, get_raw_financial_data=forbidden))
    assert records(capsys.readouterr().out)[-1]["event"] == "invalid_financial_window"


def test_public_series_sample_preserves_date_index_and_nan_marker(probe):
    pd = pytest.importorskip("pandas")
    series = pd.Series([float("nan"), 123.5, 124., 125.], index=["20260629", "20260630", "20260701", "20260702"], name="tot_assets")
    sample = probe._public_sample(series)
    assert sample["index"] == ["20260629", "20260630", "20260701", "20260702"]
    assert sample["values"] == [{"nonfinite": True}, 123.5, 124., 125.]
    assert sample["name"] == "tot_assets"
    assert sample["truncated"] is False


def test_shareholder_series_shows_all_field_names_but_bounds_each_list(probe):
    pd = pytest.importorskip('pandas')
    value = pd.Series({name: list(range(11)) for name in
        ['holdName', 'holderType', 'holdNum', 'field4', 'field5', 'field6', 'field7', 'field8', 'field9', 'field10']},
        name='2026-06-30 00:00:00')
    sample = probe._public_sample(value)
    assert len(sample['index']) == 10
    assert sample['index'][-1] == 'field10'
    assert sample['values'][-1] == {'type': 'list', 'count': 11, 'items': [0, 1, 2], 'truncated': True}
    assert sample['name'] == '2026-06-30 00:00:00'
    assert sample['truncated'] is False


def test_gap_phase_uses_known_small_windows_and_never_retries_failed_candidates(probe, capsys):
    calls = []
    class Context:
        def get_raw_financial_data(self, fields, codes, start, end, report_type):
            calls.append((fields, codes, start, end, report_type))
            if any('s_fa_eps' in field for field in fields):
                raise ValueError('synthetic unknown candidate field')
            return {'000001.SZ': {field: {} for field in fields}}

        def get_top10_share_holder(self, codes, kind, start, end, report_type):
            calls.append((kind, codes, start, end, report_type))
            return {}

    probe.PROBE_PHASE = 6
    probe.init(Context())
    events = records(capsys.readouterr().out)
    assert len(calls) == 6
    assert calls[0][2:] == ('20260814', '20260816', 'announce_time')
    assert all(call[1] == ['000001.SZ'] for call in calls)
    assert all(call[2:] == ('20260629', '20260703', 'report_time') for call in calls[1:])
    results = [r['data'] for r in events if r['event'] == 'capability_read_result']
    assert [r['status'] for r in results] == ['returned', 'error', 'returned', 'returned', 'returned', 'returned']
    assert events[-1]['event'] == 'financial_gaps_complete'
    assert not probe._STATE


def test_financial_table_phase_is_bounded_and_preserves_native_record_shapes(probe, capsys):
    pd = pytest.importorskip('pandas')
    calls = []

    class Context:
        def get_raw_financial_data(self, fields, codes, start, end, report_type, data_type='default'):
            assert data_type == 'default'
            assert codes == ['000001.SZ']
            assert (start, end, report_type) == ('20260629', '20260703', 'report_time')
            assert 1 <= len(fields) <= 8
            calls.append(('raw', fields[0].split('.')[0]))
            # Synthetic values only; both dates must remain distinct evidence.
            return {'000001.SZ': {field: {1782748800000: (
                1782748800000 if field.endswith('.m_timetag') else
                1787500800000 if field.endswith('.m_anntime') else 123.5)} for field in fields}}

        def get_holder_num(self, codes, start, end, report_type):
            calls.append(('holder_num', (codes, start, end, report_type)))
            return pd.DataFrame({'endDate': [20260630], 'declareDate': [20260824], 'shareholder': [120.]})

        def get_top10_share_holder(self, codes, kind, start, end, report_type):
            calls.append(('top10', (codes, kind, start, end, report_type)))
            return pd.DataFrame({'endDate': [20260630] * 11, 'quantity': list(range(11)),
                                 'declareDate': [20260824] * 11, 'rank': list(range(1, 12))},
                                index=[20260630] * 11)

        def get_financial_data(self, *args):
            pytest.fail('phase 5 must not repeat the filled query')

        def download_financial_data(self, *args):
            pytest.fail('phase 5 must not explicitly download')

    probe.PROBE_PHASE = 5
    probe.init(Context())
    events = records(capsys.readouterr().out)
    assert len(calls) == 8
    samples = {r['data']['table']: r['data']['sample'] for r in events if r['event'] == 'financial_table_sample'}
    assert set(samples) == {'Balance', 'Income', 'CashFlow', 'Capital', 'PershareIndex',
                            'HolderNum', 'Top10Holder', 'Top10FlowHolder'}
    fields = dict(samples['Balance']['items'][0][1]['items'])
    assert fields['ASHAREBALANCESHEET.m_timetag']['items'] == [[1782748800000, 1782748800000]]
    assert fields['ASHAREBALANCESHEET.m_anntime']['items'] == [[1782748800000, 1787500800000]]
    assert samples['Top10Holder']['shape'] == [11, 4]
    assert samples['Top10Holder']['index'] == [20260630, 20260630]
    assert [row['rank'] for row in samples['Top10Holder']['rows']] == [1, 2]
    assert samples['Top10Holder']['truncated'] is True
    assert events[-1]['event'] == 'financial_tables_complete'
    assert not probe._STATE


def test_financial_table_phase_skips_incompatible_or_missing_entry_points(probe, capsys):
    def incompatible(required, second, third, fourth, fifth, sixth):
        pytest.fail('signature mismatch must be caught before native execution')
    probe._financial_tables_probe(SimpleNamespace(get_raw_financial_data=incompatible))
    events = records(capsys.readouterr().out)
    skipped = [r['data'] for r in events if r['event'] == 'financial_table_skipped']
    assert len(skipped) == 8
    assert [r['reason'] for r in skipped].count('signature_unverified') == 5
    assert not any(r['event'] == 'capability_read_start' for r in events)


def test_financial_table_phase_rejects_large_windows_without_native_reads(probe, monkeypatch, capsys):
    monkeypatch.setattr(probe, 'FINANCIAL_END', '20260928')
    probe._financial_tables_probe(SimpleNamespace())
    assert [r['event'] for r in records(capsys.readouterr().out)] == ['invalid_financial_window']


@pytest.mark.parametrize("valid_handle", [True, False])
def test_pipe_probe_closes_valid_64_bit_handle_and_reports_failure(probe, monkeypatch, valid_handle):
    class NativeCall:
        def __init__(self, result):
            self.result = result
            self.calls = []

        def __call__(self, *args):
            self.calls.append(args)
            return self.result

    handle = 2**40 + 7 if valid_handle else ctypes.c_void_p(-1).value
    dll = SimpleNamespace(CreateNamedPipeW=NativeCall(handle), CloseHandle=NativeCall(True))
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: dll, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(probe.sys, "platform", "win32")
    result = probe._probe_pipe()
    assert dll.CreateNamedPipeW.restype is wintypes.HANDLE
    assert dll.CloseHandle.argtypes == [wintypes.HANDLE]
    if valid_handle:
        assert dll.CloseHandle.calls == [(handle,)]
        assert result == {"status": "created_and_closed", "roundtrip_tested": False}
    else:
        assert dll.CloseHandle.calls == []
        assert result == {"status": "failed", "stage": "create_pipe", "winerror": 5}
