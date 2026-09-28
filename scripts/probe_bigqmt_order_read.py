# coding: utf-8
"""Read existing simulated ORDER records once. Never submit or cancel.

Configure only SIM_ACCOUNT_ID locally. The window refers to the prior probe.
Candidates are diagnostic hints, not proof of submission identity.
Python 3.6, standard library only; no private values in output.
"""
import json

SIM_ACCOUNT_ID = ""
TARGET_DATE = "20260928"
TARGET_START_TIME = "204800"
TARGET_END_TIME = "204900"
_RAN = False


def _emit(event, data):
    print("QMT_RPYC_ORDER_READ " + json.dumps(
        {"event": event, "data": data}, ensure_ascii=True, sort_keys=True, allow_nan=False))


def _field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _window_match(row):
    day, clock = _field(row, "m_strInsertDate"), _field(row, "m_strInsertTime")
    if not isinstance(day, str) or not isinstance(clock, str):
        return None
    # Both formats are exposed explicitly; no inference from missing dates.
    clock = clock.replace(":", "")
    if len(day) != 8 or len(clock) != 6 or not (day + clock).isdigit():
        return None
    return day == TARGET_DATE and TARGET_START_TIME <= clock <= TARGET_END_TIME


def _summary(row):
    result = {"window_match": _window_match(row)}
    for name in ("m_nOrderStatus", "m_nOrderSubmitStatus", "m_nOrderPriceType",
                 "m_nDirection", "m_nOffsetFlag", "m_nOpType", "m_nErrorID"):
        value = _field(row, name)
        if type(value) is int:
            result[name] = value
    strings = {}
    for name in ("m_strOrderRef", "m_strOrderSysID", "m_strRemark", "m_strSource",
                 "m_strStrategyName", "m_strOrderStrategyType", "m_strErrorMsg", "m_strCancelInfo"):
        value = _field(row, name)
        strings[name] = {"type": type(value).__name__, "present": value is not None}
        if isinstance(value, str):
            strings[name].update(length=len(value), nonempty=bool(value))
            if name in ("m_strRemark", "m_strSource", "m_strStrategyName", "m_strOrderStrategyType"):
                strings[name]["starts_with_probe_prefix"] = value.startswith("qp")
                strings[name]["matches_probe_strategy"] = value == "qmt_rpyc_probe"
    result["fields"] = strings
    return result


def init(ContextInfo):
    global _RAN
    if _RAN:
        return
    _RAN = True
    if not isinstance(SIM_ACCOUNT_ID, str) or not SIM_ACCOUNT_ID or SIM_ACCOUNT_ID.strip() != SIM_ACCOUNT_ID:
        _emit("configuration_required", {"field": "SIM_ACCOUNT_ID"})
        return
    try:
        rows = get_trade_detail_data(SIM_ACCOUNT_ID, "STOCK", "ORDER")
        if not isinstance(rows, (list, tuple)):
            raise ValueError("unexpected ORDER result type")
        if any(_field(row, "m_strAccountID") != SIM_ACCOUNT_ID for row in rows):
            raise ValueError("account mismatch")
        candidates = [row for row in rows if _field(row, "m_strInstrumentID") == "000001"
                      and _field(row, "m_strExchangeID") == "SZ"
                      and _field(row, "m_nVolumeTotalOriginal") == 100
                      and _window_match(row) is not False]
        _emit("order_read_result", {"order_count": len(rows), "candidate_count": len(candidates),
            "candidates": [_summary(row) for row in candidates[:10]],
            "truncated": len(candidates) > 10, "identity_confirmed": False})
    except Exception as exc:
        _emit("order_read_error", {"error_type": type(exc).__name__})
    _emit("order_read_complete", {"submit_calls": 0, "cancel_calls": 0, "action": "stop_this_probe_strategy"})


def handlebar(ContextInfo):
    pass
