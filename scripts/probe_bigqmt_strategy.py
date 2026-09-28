# coding: utf-8
"""Run as a standalone QMT strategy, not with the external Python interpreter.

Investigation only: no xtquant, sockets, accounts, orders, or downloads.
Uses Python 3.6-compatible syntax and standard-library modules only.
Prints bounded JSON records prefixed with QMT_RPYC_PROBE.
Phase 1: environment and callbacks; stop after 15 seconds.
Phase 2: targeted capability reads; stop at capability_probe_complete.
Phase 3: public model mapping samples; stop at mapping_probe_complete.
Phase 4: narrow financial comparison; stop at financial_probe_complete.
Phase 5: financial dates and table fields; stop at financial_tables_complete.
Phase 6: shareholder fields and remaining financial cases; stop at financial_gaps_complete.
Phase 7: raw top-ten shareholder records; stop at top10_raw_complete.
Phase 8: raw top-ten frame layout; stop at top10_frame_complete.
"""

import json
import sys
import time


PROBE_PHASE = 8

# Five days around a recent report-period boundary. Keep both probes identical.
FINANCIAL_START = "20260629"
FINANCIAL_END = "20260703"

# Only fields supported by the reference's local field list are queried here.
# This list is investigation input, not a claim of deployed compatibility.
_FINANCIAL_FIELDS = (
    ("Balance", "ASHAREBALANCESHEET", ("m_timetag", "m_anntime", "tot_assets",
        "tot_liab", "tot_shrhldr_eqy_excl_min_int", "total_equity", "cap_stk")),
    ("Income", "ASHAREINCOME", ("m_timetag", "m_anntime", "revenue",
        "oper_profit", "net_profit_excl_min_int_inc")),
    ("CashFlow", "ASHARECASHFLOW", ("m_timetag", "m_anntime",
        "net_cash_flows_oper_act", "net_cash_flows_inv_act", "net_cash_flows_fnc_act")),
    ("Capital", "CAPITALSTRUCTURE", ("m_timetag", "m_anntime", "total_capital",
        "circulating_capital", "restrict_circulating_capital", "free_float_capital")),
    ("PershareIndex", "PERSHAREINDEX", ("m_timetag", "m_anntime", "s_fa_bps",
        "s_fa_ocfps", "s_fa_eps_basic", "s_fa_eps_diluted", "du_return_on_equity", "gear_ratio")),
)


_API_NAMES = (
    "get_full_tick", "get_market_data", "get_market_data_ex", "get_local_data",
    "get_trading_dates", "get_trading_calendar", "get_instrumentdetail",
    "get_instrument_detail", "get_option_detail_data", "get_option_list",
    "get_option_undl_data", "get_option_underlying", "get_sector_list",
    "get_stock_list_in_sector", "get_divid_factors", "get_divid_factors_ex",
    "get_weight_in_index", "get_index_weight", "get_financial_data",
    "get_raw_financial_data", "download_history_data", "down_history_data",
    "download_financial_data", "download_sector_data", "download_index_weight",
    "get_trade_detail_data", "get_value_by_order_id", "get_last_order_id",
    "passorder", "cancel", "can_cancel_order", "run_time", "stop_run_time",
)
_STATE = {}


def _api_docs(context, names=None):
    # Read deployed metadata for unresolved interfaces, never invoke them.
    names = names if names is not None else ("get_sector_list", "get_sector", "get_history_index_weight",
             "get_his_index_data", "get_weight_in_index", "get_instrument_detail",
             "download_financial_data", "download_financial_data2",
             "download_sector_data", "download_index_weight", "passorder",
             "cancel", "can_cancel_order", "get_trade_detail_data",
             "get_value_by_order_id", "get_last_order_id")
    for name in names:
        for label, owner in (("context", context), ("global", globals())):
            result = {"name": name, "owner": label}
            result.update(_describe_api(owner, name))
            if result.get("callable"):
                try:
                    value = owner.get(name) if isinstance(owner, dict) else getattr(owner, name)
                    for attr in ("__doc__", "__text_signature__"):
                        doc = getattr(value, attr, None)
                        if isinstance(doc, str):
                            result[attr] = doc[:4000]
                            result[attr + "_truncated"] = len(doc) > 4000
                except Exception as exc:
                    result["metadata_error"] = type(exc).__name__
            _emit("api_metadata", result)


def _capability_read(owner, label, name, args, kwargs=None):
    _emit("capability_read_start", {"owner": label, "method": name})
    started = time.monotonic()
    try:
        method = owner.get(name) if isinstance(owner, dict) else getattr(owner, name, None)
        if not callable(method):
            _emit("capability_read_result", {"owner": label, "method": name, "status": "missing"})
            return None
        value = method(*args, **(kwargs or {}))
        _emit("capability_read_result", {
            "owner": label, "method": name, "status": "returned",
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "shape": _shape(value)})
        return value
    except Exception as exc:
        _emit("capability_read_result", dict(_error(exc), owner=label, method=name, status="error"))
        return None


def _capability_probe(context):
    _api_docs(context)
    # The reference project's bundled manual supplies these read-only call
    # shapes. Deployed behavior remains unverified until this probe returns.
    tree = _capability_read(globals(), "global", "get_sector_list", ("",))
    if isinstance(tree, (list, tuple)) and len(tree) == 2:
        sectors, folders = tree
        if isinstance(sectors, (list, tuple)) and isinstance(folders, (list, tuple)):
            _emit("sector_tree", {"sector_count": len(sectors), "folder_count": len(folders),
                                  "all_names_are_strings": all(
                                      isinstance(name, str) for name in list(sectors) + list(folders)),
                                  "full_tree_enumerated": False})
    members = _capability_read(context, "context", "get_sector", ("000300.SH",))
    if isinstance(members, (list, tuple)):
        codes = [code for code in members if isinstance(code, str) and code.endswith((".SH", ".SZ"))
                 and len(code.split(".")[0]) == 6 and code.split(".")[0].isdigit()]
        # At most three actual returned members; no full-market probing.
        for code in list(dict.fromkeys(codes))[:3]:
            weight = _capability_read(context, "context", "get_weight_in_index", ("000300.SH", code))
            import math
            if type(weight) in (int, float) and math.isfinite(weight):
                _emit("index_weight_sample", {"index": "000300.SH", "code": code,
                                              "raw_weight": weight, "unit_verified": False})
    # The deployed signature has ONE argument, despite a different signature
    # in the reference documentation. Do not guess a second boolean argument.
    _capability_read(context, "context", "get_instrument_detail", ("000001.SZ",))
    _capability_read(context, "context", "get_instrumentdetail", ("510050.SH",))
    _capability_read(context, "context", "get_option_undl_data", ("510050.SH",))
    _emit("capability_probe_complete", {"action": "stop_this_probe_strategy"})


def _public_sample(value, depth=0):
    """Bounded values ONLY for explicitly selected public market-data calls."""
    import math
    from datetime import date, datetime
    if value is None or type(value) is bool:
        return value
    if type(value) in (int, float):
        return value if math.isfinite(value) else {"nonfinite": True}
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    result = {"type": type(value).__name__}
    if depth >= 4:
        return result
    if isinstance(value, dict):
        result["count"] = len(value)
        pairs = list(value.items())[:8]
        result["items"] = [[_public_sample(key, depth + 1), _public_sample(item, depth + 1)]
                           for key, item in pairs]
        result["truncated"] = len(value) > 8
    elif isinstance(value, (list, tuple)):
        result["count"] = len(value)
        result["items"] = [_public_sample(item, depth + 1) for item in value[:3]]
        result["truncated"] = len(value) > 3
    elif type(value).__module__.startswith("pandas") and type(value).__name__ == "DataFrame":
        result["shape"] = list(value.shape)
        result["columns"] = [str(column) for column in value.columns[:40]]
        frame = value.iloc[:2, :40]
        result["index"] = [_public_sample(index) for index in frame.index]
        result["rows"] = [{str(key): _public_sample(item) for key, item in row.items()}
                          for row in frame.to_dict(orient="records")]
        result["truncated"] = value.shape[0] > 2 or value.shape[1] > 40
    elif type(value).__module__.startswith("pandas") and type(value).__name__ == "Series":
        result["shape"] = list(value.shape)
        result["name"] = _public_sample(value.name)
        # Series may represent columns of parallel shareholder lists, not rows.
        # Keep all bounded column names; nested lists still show at most 3 items.
        result["index"] = [_public_sample(index) for index in value.index[:40]]
        result["values"] = [_public_sample(item) for item in value.iloc[:40]]
        result["truncated"] = len(value) > 40
    elif type(value).__module__.startswith("numpy") and getattr(value, "ndim", None) == 0:
        return _public_sample(value.item(), depth + 1)
    return result


def _mapping_probe(context):
    _api_docs(context, ("get_history_index_weight", "passorder", "cancel", "can_cancel_order",
                        "get_trade_detail_data", "get_value_by_order_id", "get_last_order_id"))
    history = _capability_read(globals(), "global", "get_history_index_weight", ("000300.SH",))
    _emit("public_mapping_sample", {"method": "get_history_index_weight", "sample": _public_sample(history)})
    codes = _capability_read(context, "context", "get_option_undl_data", ("510050.SH",))
    if isinstance(codes, (list, tuple)):
        selected = []
        for code in codes:
            if isinstance(code, str) and code and code.strip() == code and code not in selected:
                selected.append(code)
                if len(selected) == 2:
                    break
        for code in selected:
            for name in ("get_option_detail_data", "get_instrumentdetail"):
                row = _capability_read(context, "context", name, (code,))
                if isinstance(row, dict):
                    fields = ("InstrumentID", "ExchangeID", "InstrumentName", "OptUndlCode",
                              "OptUndlMarket", "OptUndlCodeFull", "optType", "OptType", "OptExercisePrice",
                              "ExpireDate", "EndDelivDate", "OptUnit", "VolumeMultiple",
                              "IsTrading", "SettlementPrice", "ExtendInfo")
                    _emit("option_mapping_sample", {"code": code, "method": name,
                          "fields": {key: _public_sample(row[key]) for key in fields if key in row}})
    for period in ("1d",):
        bars = _capability_read(context, "context", "get_market_data_ex", (), {
            "fields": [], "stock_code": ["000001.SZ"], "period": period,
            "count": 2, "dividend_type": "none", "fill_data": True, "subscribe": False})
        _emit("bar_mapping_sample", {"period": period, "sample": _public_sample(bars)})
    _emit("financial_read_notice", {"note": "First native financial query may take minutes; do not retry while pending"})
    financials = _capability_read(context, "context", "get_financial_data", (
        ["ASHAREBALANCESHEET.m_timetag", "ASHAREBALANCESHEET.m_anntime", "ASHAREBALANCESHEET.tot_assets"],
        ["000001.SZ"], FINANCIAL_START, FINANCIAL_END, "report_time"))
    _emit("financial_mapping_sample", {"sample": _public_sample(financials)})
    _emit("mapping_probe_complete", {"action": "stop_this_probe_strategy"})


def _financial_probe(context):
    # One field isolates business-field support from date metadata support.
    # Do not force data_type='dict': that option is not verified on this build.
    _api_docs(context, ("get_financial_data", "get_raw_financial_data"))
    from datetime import datetime
    start = datetime.strptime(FINANCIAL_START, "%Y%m%d")
    end = datetime.strptime(FINANCIAL_END, "%Y%m%d")
    if not 0 <= (end - start).days <= 7:
        _emit("invalid_financial_window", {"max_days": 7})
        return
    _emit("financial_read_notice", {
        "start": FINANCIAL_START, "end": FINANCIAL_END,
        "fields": ["ASHAREBALANCESHEET.tot_assets"],
        "note": "Two reads, no retries; record the method printed before any popup"})
    for name in ("get_financial_data", "get_raw_financial_data"):
        value = _capability_read(context, "context", name, (
            ["ASHAREBALANCESHEET.tot_assets"], ["000001.SZ"],
            FINANCIAL_START, FINANCIAL_END, "report_time"))
        _emit("financial_comparison_sample", {"method": name, "sample": _public_sample(value)})
    # Phase 3 discovered the lower-case key but did not print its value.
    codes = _capability_read(context, "context", "get_option_undl_data", ("510050.SH",))
    if isinstance(codes, (list, tuple)):
        code = next((code for code in codes if isinstance(code, str) and code and code.strip() == code), None)
        if code is not None:
            row = _capability_read(context, "context", "get_option_detail_data", (code,))
            if isinstance(row, dict):
                _emit("option_type_sample", {"code": code, "fields": {
                    key: _public_sample(row[key]) for key in ("optType", "OptType") if key in row}})
    _emit("financial_probe_complete", {"action": "stop_this_probe_strategy"})


def _financial_tables_probe(context):
    from datetime import datetime
    import inspect
    start = datetime.strptime(FINANCIAL_START, "%Y%m%d")
    end = datetime.strptime(FINANCIAL_END, "%Y%m%d")
    if not 0 <= (end - start).days <= 7:
        _emit("invalid_financial_window", {"max_days": 7})
        return
    _api_docs(context, ("get_raw_financial_data", "get_holder_num", "get_top10_share_holder"))
    _emit("financial_tables_notice", {
        "start": FINANCIAL_START, "end": FINANCIAL_END, "code": "000001.SZ",
        "max_calls": 8, "report_type": "report_time",
        "note": "One read per table; no retries or explicit downloads",
        "unverified_public_fields": {
            "Income": ["s_fa_eps_basic", "s_fa_eps_diluted"],
            "CashFlow": ["cash_cash_equ_end_period"],
            "Capital": ["freeFloatCapital vs free_float_capital"]}})
    calls = []
    for public_table, native_table, fields in _FINANCIAL_FIELDS:
        calls.append((public_table, "get_raw_financial_data", (
            [native_table + "." + field for field in fields], ["000001.SZ"],
            FINANCIAL_START, FINANCIAL_END, "report_time")))
    calls.extend((
        ("HolderNum", "get_holder_num", (["000001.SZ"], FINANCIAL_START, FINANCIAL_END, "report_time")),
        ("Top10Holder", "get_top10_share_holder", (["000001.SZ"], "holder", FINANCIAL_START, FINANCIAL_END, "report_time")),
        ("Top10FlowHolder", "get_top10_share_holder", (["000001.SZ"], "flow_holder", FINANCIAL_START, FINANCIAL_END, "report_time")),
    ))
    for table, name, args in calls:
        method = getattr(context, name, None)
        if not callable(method):
            _emit("financial_table_skipped", {"table": table, "method": name, "reason": "missing"})
            continue
        # Shareholder entry points have not been called on this deployment.
        # Require the actual Python signature to accept the documented shape.
        try:
            inspect.signature(method).bind(*args)
        except (ValueError, TypeError):
            _emit("financial_table_skipped", {"table": table, "method": name, "reason": "signature_unverified"})
            continue
        _emit("financial_table_start", {"table": table, "method": name})
        value = _capability_read(context, "context", name, args)
        # Never pivot shareholder rows by date: multiple holders share a date.
        _emit("financial_table_sample", {"table": table, "method": name,
            "sample": _public_sample(value)})
    _emit("financial_tables_complete", {"action": "stop_this_probe_strategy"})


def _top10_frame_sample(value, depth=0):
    # Preserve duplicate date indices AND duplicate column labels positionally.
    # Public fields only; bounded output is separate from row count metadata.
    if depth >= 4:
        return {"type": type(value).__name__, "truncated": True}
    if type(value).__module__.startswith("pandas") and type(value).__name__ == "DataFrame":
        frame = value.iloc[:12, :10]
        return {"type": "DataFrame", "shape": list(value.shape),
            "index_is_unique": bool(value.index.is_unique),
            "columns_is_unique": bool(value.columns.is_unique),
            "columns": [_public_sample(column) for column in frame.columns],
            "index": [_public_sample(index) for index in frame.index],
            "row_values": [[_public_sample(item) for item in row]
                           for row in frame.itertuples(index=False, name=None)],
            "truncated": value.shape[0] > 12 or value.shape[1] > 10}
    if isinstance(value, dict):
        return {"type": "dict", "count": len(value), "truncated": len(value) > 8,
            "items": [[_public_sample(key), _top10_frame_sample(item, depth + 1)]
                      for key, item in list(value.items())[:8]]}
    if isinstance(value, (list, tuple)):
        return {"type": type(value).__name__, "count": len(value), "truncated": len(value) > 12,
            "items": [_top10_frame_sample(item, depth + 1) for item in value[:12]]}
    return _public_sample(value)


def _top10_frame_probe(context):
    _emit("top10_frame_notice", {"max_calls": 2, "start": "20260629", "end": "20260703",
        "note": "Candidate data_type=frame; preserve duplicate rows; no retries or downloads"})
    for table in ("TOP10HOLDER", "TOP10FLOWHOLDER"):
        args = ([table + "." + field for field in
                 ("declareDate", "endDate", "quantity", "ratio", "rank")],
                ["000001.SZ"], "20260629", "20260703", "report_time", "frame")
        _emit("top10_frame_start", {"table": table})
        value = _capability_read(context, "context", "get_raw_financial_data", args)
        _emit("top10_frame_sample", {"table": table, "sample": _top10_frame_sample(value)})
    _emit("top10_frame_complete", {"action": "stop_this_probe_strategy"})


def _top10_raw_probe(context):
    # Candidate source fields; do not flatten holders sharing one timestamp.
    _emit("top10_raw_notice", {"max_calls": 2, "start": "20260629", "end": "20260703",
        "note": "Unverified raw table fields; no retries, downloads or date substitution"})
    for table in ("TOP10HOLDER", "TOP10FLOWHOLDER"):
        args = ([table + "." + field for field in
                 ("declareDate", "endDate", "quantity", "ratio", "rank")],
                ["000001.SZ"], "20260629", "20260703", "report_time")
        _emit("top10_raw_start", {"table": table})
        value = _capability_read(context, "context", "get_raw_financial_data", args)
        _emit("top10_raw_sample", {"table": table, "sample": _public_sample(value)})
    _emit("top10_raw_complete", {"action": "stop_this_probe_strategy"})


def _financial_gaps_probe(context):
    # Fixed windows from the observed report/announcement dates. No wide scan.
    raw = "get_raw_financial_data"
    reports = ("20260629", "20260703", "report_time")
    announcements = ("20260814", "20260816", "announce_time")
    calls = (
        ("Balance_announcement", raw, (["ASHAREBALANCESHEET." + field for field in
            ("m_timetag", "m_anntime", "tot_assets")], ["000001.SZ"]) + announcements),
        ("Income_optional_candidates", raw, (["ASHAREINCOME." + field for field in
            ("m_timetag", "m_anntime", "revenue", "s_fa_eps_basic", "s_fa_eps_diluted")], ["000001.SZ"]) + reports),
        ("CashFlow_optional_candidate", raw, (["ASHARECASHFLOW." + field for field in
            ("m_timetag", "m_anntime", "net_cash_flows_oper_act", "cash_cash_equ_end_period")], ["000001.SZ"]) + reports),
        ("HolderNum_raw_candidate", raw, (["SHAREHOLDER." + field for field in
            ("declareDate", "endDate", "shareholder", "shareholderA", "shareholderB", "shareholderH",
             "shareholderFloat", "shareholderOther")], ["000001.SZ"]) + reports),
        ("Top10Holder_all_fields", "get_top10_share_holder", (["000001.SZ"], "holder") + reports),
        ("Top10FlowHolder_all_fields", "get_top10_share_holder", (["000001.SZ"], "flow_holder") + reports),
    )
    _emit("financial_gaps_notice", {"max_calls": len(calls), "code": "000001.SZ",
        "report_window": list(reports), "announcement_window": list(announcements),
        "note": "Optional field names are unverified candidates; no retries or cross-table substitution"})
    for case, name, args in calls:
        _emit("financial_gap_start", {"case": case, "method": name})
        value = _capability_read(context, "context", name, args)
        _emit("financial_gap_sample", {"case": case, "method": name, "sample": _public_sample(value)})
    _emit("financial_gaps_complete", {"action": "stop_this_probe_strategy"})


def _emit(event, data):
    print("QMT_RPYC_PROBE " + json.dumps(
        {"event": event, "data": data}, ensure_ascii=True, sort_keys=True,
        allow_nan=False))


def _error(exc):
    # Error messages and reprs can contain local paths or account details.
    return {"error_type": type(exc).__name__}


def _describe_api(owner, name):
    try:
        value = owner.get(name) if isinstance(owner, dict) else getattr(owner, name, None)
        result = {"callable": callable(value)}
        if not result["callable"]:
            return result
        try:
            import inspect
            parameters = inspect.signature(value).parameters.values()
            result["parameters"] = [
                {"name": p.name, "kind": str(p.kind),
                 "required": p.default is inspect.Parameter.empty
                 and p.kind not in (inspect.Parameter.VAR_POSITIONAL,
                                    inspect.Parameter.VAR_KEYWORD)}
                for p in parameters]
        except Exception as exc:
            result["signature_error"] = type(exc).__name__
        return result
    except Exception as exc:
        return _error(exc)


def _surface(context):
    namespace = globals()
    for name in _API_NAMES:
        _emit("api", {"name": name,
                      "context": _describe_api(context, name),
                      "global": _describe_api(namespace, name)})
    # Discover candidate alternatives without invoking unknown functions.
    for label, owner in (("context", context), ("global", namespace)):
        try:
            names = owner.keys() if isinstance(owner, dict) else dir(owner)
            candidates = sorted(name for name in names
                                if not name.startswith("_") and any(
                                    word in name.lower() for word in
                                    ("download", "down_", "sector", "index", "weight", "financial")))
            _emit("candidate_names", {"owner": label, "names": candidates[:100],
                                      "truncated": len(candidates) > 100})
        except Exception as exc:
            _emit("candidate_names", dict(_error(exc), owner=label))


def _shape(value, depth=0):
    """Describe a bounded public-data sample without emitting raw values."""
    result = {"type": type(value).__name__, "present": value is not None}
    if isinstance(value, str):
        result["present"] = bool(value)
    elif isinstance(value, (dict, list, tuple)):
        result["count"] = len(value)
        if depth < 3:
            if isinstance(value, dict):
                keys = [key for key in value if isinstance(key, str)]
                result["fields"] = {key: _shape(value[key], depth + 1)
                                    for key in sorted(keys)[:60]}
                result["truncated"] = len(keys) > 60 or len(keys) != len(value)
            else:
                result["sample"] = [_shape(item, depth + 1) for item in value[:2]]
                result["truncated"] = len(value) > 2
    return result


def _public_reads(context):
    calls = (("get_full_tick", (["000001.SZ"],)),
             ("get_instrumentdetail", ("000001.SZ",)))
    for name, args in calls:
        _emit("read_start", {"method": name})
        started = time.monotonic()
        try:
            method = getattr(context, name, None)
            if not callable(method):
                _emit("read_result", {"method": name, "status": "missing"})
                continue
            value = method(*args)
            _emit("read_result", {"method": name, "status": "returned",
                                  "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
                                  "shape": _shape(value)})
        except Exception as exc:
            _emit("read_result", dict(_error(exc), method=name, status="error"))


def _probe_pipe():
    """Create and close a local pipe; this does not prove message exchange."""
    if sys.platform != "win32":
        return {"status": "not_windows"}
    stage = "import_ctypes"
    try:
        import ctypes
        from ctypes import wintypes
        stage = "load_kernel32"
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        dll.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
        dll.CreateNamedPipeW.restype = wintypes.HANDLE
        dll.CloseHandle.argtypes = [wintypes.HANDLE]
        dll.CloseHandle.restype = wintypes.BOOL
        stage = "create_pipe"
        path = "\\\\.\\pipe\\qmt_rpyc_probe_%s_%s" % (id(dll), int(time.time() * 1000000))
        # Duplex, message mode, reject remote clients. No client is connected.
        handle = dll.CreateNamedPipeW(path, 3 | 0x00080000, 4 | 2 | 8,
                                      1, 4096, 4096, 0, None)
        if handle == ctypes.c_void_p(-1).value:
            return {"status": "failed", "stage": stage,
                    "winerror": ctypes.get_last_error()}
        stage = "close_pipe"
        if not dll.CloseHandle(handle):
            return {"status": "failed", "stage": stage,
                    "winerror": ctypes.get_last_error()}
        return {"status": "created_and_closed", "roundtrip_tested": False}
    except Exception as exc:
        return dict(_error(exc), status="failed", stage=stage)


def _thread_id():
    try:
        import threading
        return threading.get_ident()
    except Exception as exc:
        _emit("thread_probe_error", _error(exc))
        return None


def _callback(source):
    if not _STATE:
        return
    count = _STATE.get(source, 0) + 1
    _STATE[source] = count
    limit = 5 if source == "timer" else 2
    if count > limit:
        return
    now = time.monotonic()
    previous = _STATE.get(source + "_at")
    _STATE[source + "_at"] = now
    thread_id = _thread_id()
    same_thread = (thread_id == _STATE["init_thread"]
                   if thread_id is not None and _STATE["init_thread"] is not None else None)
    _emit("callback", {"source": source, "count": count,
                       "since_init_ms": round((now - _STATE["started"]) * 1000, 3),
                       "interval_ms": None if previous is None else round((now - previous) * 1000, 3),
                       "same_thread_as_init": same_thread})
    if source == "timer" and count == limit:
        _emit("callback_sample_complete", {"action": "stop_this_probe_strategy"})


def probe_timer(ContextInfo):
    _callback("timer")


def handlebar(ContextInfo):
    _callback("handlebar")


def init(ContextInfo):
    _STATE.clear()
    _STATE.update(started=time.monotonic(), init_thread=_thread_id())
    _emit("runtime", {"schema_version": 1, "phase": PROBE_PHASE, "python": list(sys.version_info[:3]),
                      "platform": sys.platform, "bits": 64 if sys.maxsize > 2**32 else 32})
    if PROBE_PHASE == 8:
        _top10_frame_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 7:
        _top10_raw_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 6:
        _financial_gaps_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 5:
        _financial_tables_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 4:
        _financial_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 3:
        _mapping_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE == 2:
        _capability_probe(ContextInfo)
        _STATE.clear()
        return
    if PROBE_PHASE != 1:
        _emit("invalid_phase", {"expected": [1, 2, 3, 4, 5, 6, 7, 8]})
        _STATE.clear()
        return
    _surface(ContextInfo)
    _emit("named_pipe", _probe_pipe())
    _public_reads(ContextInfo)
    try:
        from datetime import datetime, timedelta
        start = (datetime.now() + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        method = getattr(ContextInfo, "run_time", None)
        if not callable(method):
            _emit("timer_registration", {"status": "missing"})
        else:
            method("probe_timer", "1000nMilliSecond", start)
            _emit("timer_registration", {"status": "returned", "callback_observed": False})
    except Exception as exc:
        _emit("timer_registration", dict(_error(exc), status="error"))
    _emit("init_complete", {"action": "observe_for_15_seconds_then_stop_strategy"})
