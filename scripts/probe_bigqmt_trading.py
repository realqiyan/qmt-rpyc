# coding: utf-8
"""Standalone QMT strategy: read simulated account record shapes only.

Set SIM_ACCOUNT_ID locally. No orders, cancellations, subscriptions or SDK
imports. Output excludes account IDs, instruments, order IDs and amounts.
Compatible with the deployed Python 3.6 runtime.
"""
import inspect
import json
import math
import sys
import time


SIM_ACCOUNT_ID = ""
SIM_ACCOUNT_TYPE = "STOCK"

_ENUM_FIELDS = frozenset(("m_nAccountType", "m_nBrokerType", "m_nOrderStatus", "m_nOrderPriceType",
                         "m_nDirection", "m_nOffsetFlag", "m_nOpType"))
_ID_FIELDS = ("m_nOrderID", "m_strOrderID", "m_strOrderSysID", "m_strOrderRef",
              "m_strUserOrderID", "m_strUserOrderId", "m_strRemark")


def _emit(event, data):
    print("QMT_RPYC_TRADE_PROBE " + json.dumps(
        {"event": event, "data": data}, ensure_ascii=True, sort_keys=True, allow_nan=False))


def _summary(value):
    result = {"type": type(value).__name__, "present": value is not None}
    if isinstance(value, str):
        result.update(nonempty=bool(value), length=len(value), digits_only=value.isdigit())
    elif type(value) in (int, float):
        result["finite"] = math.isfinite(value)
    return result


def _field(row, name):
    return row.get(name) if isinstance(row, dict) else getattr(row, name, None)


def _record(row):
    # Native record properties are scalar metadata. Never print repr/str(row).
    names = row.keys() if isinstance(row, dict) else dir(row)
    names = sorted(name for name in names if isinstance(name, str) and name.startswith("m_"))
    fields = {}
    identities = {}
    for name in names[:120]:
        try:
            value = _field(row, name)
            if callable(value):
                continue
            field = _summary(value)
            if name in _ENUM_FIELDS and type(value) is int:
                field["enum_value"] = value
            if name in _ID_FIELDS and type(value) in (str, int) and value not in ("", 0):
                identities[name] = value
            if name == "m_strAccountID" and isinstance(value, str):
                field["matches_requested_account"] = value == SIM_ACCOUNT_ID
            fields[name] = field
        except Exception as exc:
            fields[name] = {"error_type": type(exc).__name__}
    comparisons = []
    for i, left in enumerate(_ID_FIELDS):
        for right in _ID_FIELDS[i + 1:]:
            if left in identities and right in identities:
                comparisons.append({"left": left, "right": right,
                                    "same_text": str(identities[left]) == str(identities[right])})
    return {"fields": fields, "truncated": len(names) > 120, "identity_comparisons": comparisons}


def _metadata():
    for name in ("get_trade_detail_data", "passorder", "cancel", "can_cancel_order",
                 "get_value_by_order_id", "get_last_order_id"):
        method = globals().get(name)
        data = {"name": name, "callable": callable(method)}
        if callable(method):
            try:
                parameters = inspect.signature(method).parameters.values()
                data["parameters"] = [{"name": p.name, "kind": str(p.kind)} for p in parameters]
            except (ValueError, TypeError) as exc:
                data["signature_error"] = type(exc).__name__
        _emit("api", data)


def _read(kind):
    _emit("read_start", {"kind": kind})
    started = time.monotonic()
    try:
        method = globals().get("get_trade_detail_data")
        if not callable(method):
            _emit("read_result", {"kind": kind, "status": "missing"})
            return
        value = method(SIM_ACCOUNT_ID, SIM_ACCOUNT_TYPE, kind)
        if not isinstance(value, (list, tuple)):
            _emit("read_result", {"kind": kind, "status": "unexpected_type", "type": type(value).__name__})
            return
        _emit("read_result", {"kind": kind, "status": "returned", "count": len(value),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "records": [_record(row) for row in value[:2]], "truncated": len(value) > 2})
    except Exception as exc:
        _emit("read_result", {"kind": kind, "status": "error", "error_type": type(exc).__name__})


def init(ContextInfo):
    _emit("runtime", {"schema_version": 1, "python": list(sys.version_info[:3]),
        "platform": sys.platform, "bits": 64 if sys.maxsize > 2**32 else 32})
    if not isinstance(SIM_ACCOUNT_ID, str) or not SIM_ACCOUNT_ID or SIM_ACCOUNT_ID.strip() != SIM_ACCOUNT_ID:
        _emit("configuration_required", {"field": "SIM_ACCOUNT_ID", "action": "set_simulated_account_locally"})
        return
    if not isinstance(SIM_ACCOUNT_TYPE, str) or not SIM_ACCOUNT_TYPE or SIM_ACCOUNT_TYPE.strip() != SIM_ACCOUNT_TYPE:
        _emit("configuration_required", {"field": "SIM_ACCOUNT_TYPE"})
        return
    _metadata()
    for kind in ("ACCOUNT", "POSITION", "ORDER"):
        _read(kind)
    _emit("trading_read_probe_complete", {"action": "stop_this_probe_strategy", "mutation_calls": 0})


def handlebar(ContextInfo):
    pass
