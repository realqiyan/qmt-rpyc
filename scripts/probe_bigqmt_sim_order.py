# coding: utf-8
"""One simulated stock LIMIT buy, correlate by remark, cancel only that order.

Standalone Python 3.6 QMT strategy. Set the three local configuration fields.
No automatic resubmission, no latest-order lookup, no private values in output.
Restarting this strategy is a NEW experiment and can create another order.
"""
import json
import math
import sys
import time
import uuid

SIM_ACCOUNT_ID = ""
SIMULATION_CONFIRMED = False
LIMIT_PRICE = None
INSTRUMENT = "000001.SZ"
QUANTITY = 100
_STATE = {}
_ENUMS = ("m_nBrokerType", "m_nDirection", "m_nOffsetFlag", "m_nOpType",
          "m_nOrderPriceType", "m_nOrderStatus")


def _emit(event, data):
    print("QMT_RPYC_SIM_ORDER " + json.dumps(
        {"event": event, "data": data}, ensure_ascii=True, sort_keys=True, allow_nan=False))


def _field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _finish(reason):
    _STATE["done"] = True
    _emit("sim_order_complete", {"reason": reason, "submit_calls": _STATE.get("submit_calls", 0),
        "cancel_calls": _STATE.get("cancel_calls", 0),
        "order_reads": _STATE.get("order_reads", 0),
        "last_order_count": _STATE.get("last_order_count"),
        "last_marker_matches": _STATE.get("last_marker_matches"),
        "action": "stop_strategy_and_check_simulated_order_locally"})


def _rows(kind):
    rows = get_trade_detail_data(SIM_ACCOUNT_ID, "STOCK", kind)
    if not isinstance(rows, (tuple, list)):
        raise ValueError("unexpected query shape")
    if any(_field(row, "m_strAccountID") != SIM_ACCOUNT_ID for row in rows):
        raise ValueError("account mismatch")
    return rows


def _ids(row):
    return (_field(row, "m_strOrderRef"), _field(row, "m_strOrderSysID"))


def _same_order(left, right):
    return (right is not None and _ids(left) == _ids(right)
            and all(_field(left, key) == _field(right, key) for key in
                    ("m_strAccountID", "m_strInstrumentID", "m_strExchangeID", "m_strRemark")))


def _observe(row):
    data = {name: _field(row, name) for name in _ENUMS if type(_field(row, name)) is int}
    ref, sysid = _ids(row)
    data["identities"] = {name: {"type": type(value).__name__,
        "nonempty": isinstance(value, str) and bool(value),
        "digits_only": isinstance(value, str) and value.isdigit()}
        for name, value in (("order_ref", ref), ("order_sysid", sysid))}
    data["identities_equal"] = ref == sysid
    returned = _STATE.get("submit_result")
    data["submit_return_matches_ref"] = type(returned) is int and str(returned) == ref
    data["submit_return_matches_sysid"] = type(returned) is int and str(returned) == sysid
    data["remark_matches"] = _field(row, "m_strRemark") == _STATE["marker"]
    data["source_matches_strategy"] = _field(row, "m_strSource") == "qmt_rpyc_probe"
    data["strategy_name_matches"] = _field(row, "m_strStrategyName") == "qmt_rpyc_probe"
    names = row.keys() if isinstance(row, dict) else dir(row)
    names = sorted(name for name in names if isinstance(name, str) and name.startswith("m_str"))[:120]
    data["strategy_matching_fields"] = [name for name in names if _field(row, name) == "qmt_rpyc_probe"]
    data["remark_matching_fields"] = [name for name in names if _field(row, name) == _STATE["marker"]]
    data["quantity_matches"] = _field(row, "m_nVolumeTotalOriginal") == QUANTITY
    data["filled_any"] = (_field(row, "m_nVolumeTraded") or 0) > 0
    _emit("matched_order", data)


def _poll(context):
    if time.monotonic() >= _STATE["deadline"]:
        _finish("observation_timeout_no_write_retry")
        return
    rows = _rows("ORDER")
    matches = [row for row in rows if _field(row, "m_strRemark") == _STATE["marker"]]
    _STATE["order_reads"] = _STATE.get("order_reads", 0) + 1
    _STATE["last_order_count"] = len(rows)
    _STATE["last_marker_matches"] = len(matches)
    counts = (len(rows), len(matches))
    if counts != _STATE.get("last_progress_counts"):
        _STATE["last_progress_counts"] = counts
        _emit("query_progress", {"order_count": len(rows), "marker_matches": len(matches)})
    if len(matches) > 1:
        _finish("ambiguous_correlation_no_cancel")
        return
    if not matches:
        return
    row = matches[0]
    code = str(_field(row, "m_strInstrumentID")) + "." + str(_field(row, "m_strExchangeID"))
    if code != INSTRUMENT or _field(row, "m_nVolumeTotalOriginal") != QUANTITY:
        _finish("correlation_payload_mismatch_no_cancel")
        return
    ref, sysid = _ids(row)
    if ((ref and ref in _STATE["before_refs"]) or (sysid and sysid in _STATE["before_sysids"])):
        _finish("preexisting_identity_no_cancel")
        return
    _observe(row)
    if not isinstance(sysid, str) or not sysid:
        return
    if _STATE.get("matched_ids") and _STATE["matched_ids"] != (ref, sysid):
        _finish("correlated_identity_changed_no_cancel_retry")
        return
    if not _STATE.get("lookup_done"):
        _STATE["lookup_done"] = True
        # Reference docs name sysid as the lookup/cancel identity; verify it.
        found = get_value_by_order_id(sysid, SIM_ACCOUNT_ID, "STOCK", "ORDER")
        _STATE["lookup_matches"] = _same_order(row, found)
        _emit("lookup_result", {"identity": "m_strOrderSysID", "matches": _STATE["lookup_matches"]})
        if not _STATE["lookup_matches"]:
            _finish("lookup_mismatch_no_cancel")
            return
        _STATE["matched_ids"] = (ref, sysid)
    if _STATE.get("cancel_calls"):
        _STATE["after_cancel_reads"] += 1
        if _STATE["after_cancel_reads"] >= 3:
            _finish("post_cancel_samples_collected_not_a_success_assertion")
        return
    permitted = can_cancel_order(sysid, SIM_ACCOUNT_ID, "STOCK")
    _emit("cancelability", {"type": type(permitted).__name__,
        "value": permitted if type(permitted) is bool else None})
    if permitted is not True:
        return
    # Mark before crossing the native boundary, including native exceptions.
    _STATE["cancel_calls"] = 1
    _STATE["after_cancel_reads"] = 0
    _emit("cancel_start", {"identity": "m_strOrderSysID", "only_correlated_probe_order": True})
    try:
        result = cancel(sysid, SIM_ACCOUNT_ID, "STOCK", context)
        _emit("cancel_return", {"type": type(result).__name__,
            "value": result if type(result) is bool else None})
    except Exception as exc:
        _emit("cancel_unknown", {"error_type": type(exc).__name__, "retry": False})


def sim_order_timer(ContextInfo):
    if not _STATE or _STATE.get("done") or _STATE.get("busy"):
        return
    _STATE["busy"] = True
    try:
        if not _STATE.get("submit_calls"):
            _STATE["submit_calls"] = 1
            _STATE["deadline"] = time.monotonic() + 30
            _emit("submit_start", {"side": "BUY", "quantity": QUANTITY, "pricing": "LIMIT"})
            try:
                result = passorder(23, 1101, SIM_ACCOUNT_ID, INSTRUMENT, 11, LIMIT_PRICE,
                                   QUANTITY, "qmt_rpyc_probe", 2, _STATE["marker"], ContextInfo)
                _STATE["submit_result"] = result
                _emit("submit_return", {"type": type(result).__name__,
                    "sign": (0 if result == 0 else 1 if result > 0 else -1) if type(result) is int else None,
                    "zero": type(result) is int and result == 0})
            except Exception as exc:
                _emit("submit_unknown", {"error_type": type(exc).__name__, "retry": False})
            return
        _poll(ContextInfo)
    except Exception as exc:
        _emit("probe_error", {"error_type": type(exc).__name__})
        _finish("read_or_mapping_error_no_write_retry")
    finally:
        _STATE["busy"] = False


def _configuration_issues():
    issues = {}
    if not isinstance(SIM_ACCOUNT_ID, str) or not SIM_ACCOUNT_ID:
        issues["SIM_ACCOUNT_ID"] = "must_be_nonempty_text"
    elif SIM_ACCOUNT_ID.strip() != SIM_ACCOUNT_ID:
        issues["SIM_ACCOUNT_ID"] = "remove_surrounding_whitespace"
    if SIMULATION_CONFIRMED is not True:
        issues["SIMULATION_CONFIRMED"] = "must_be_boolean_True_after_checking_simulated_account"
    if type(LIMIT_PRICE) not in (int, float):
        issues["LIMIT_PRICE"] = "must_be_number_without_quotes"
    else:
        try:
            valid_price = math.isfinite(LIMIT_PRICE) and LIMIT_PRICE > 0
        except OverflowError:
            valid_price = False
        if not valid_price:
            issues["LIMIT_PRICE"] = "must_be_finite_and_positive"
    return issues


def init(ContextInfo):
    if _STATE:
        _emit("duplicate_init_ignored", {})
        return
    _STATE["done"] = True
    _emit("runtime", {"python": list(sys.version_info[:3]), "schema_version": 1})
    issues = _configuration_issues()
    if issues:
        _emit("configuration_required", {"fields": sorted(issues), "reasons": issues,
            "submit_calls": 0, "cancel_calls": 0})
        return
    try:
        accounts = _rows("ACCOUNT")
        if len(accounts) != 1:
            _finish("expected_one_matching_account")
            return
        _emit("account_metadata", {name: _field(accounts[0], name) for name in _ENUMS
                                  if type(_field(accounts[0], name)) is int})
        if type(_field(accounts[0], "m_nBrokerType")) is not int or _field(accounts[0], "m_nBrokerType") != 2:
            _finish("stock_account_type_unconfirmed_no_submit")
            return
        before = _rows("ORDER")
        _STATE.update(marker="qp" + uuid.uuid4().hex[:20],
                      before_refs=set(_ids(row)[0] for row in before),
                      before_sysids=set(_ids(row)[1] for row in before))
        for name in ("passorder", "cancel", "can_cancel_order", "get_value_by_order_id"):
            if not callable(globals().get(name)):
                _finish("required_api_missing")
                return
        ContextInfo.run_time("sim_order_timer", "1000nMilliSecond", "2020-01-01 00:00:00")
        _STATE["done"] = False
        _emit("probe_ready", {"max_submit_calls": 1, "max_cancel_calls": 1, "observation_seconds": 30})
    except Exception as exc:
        _emit("setup_error", {"error_type": type(exc).__name__})
        _finish("setup_failed_no_submit")


def handlebar(ContextInfo):
    pass
