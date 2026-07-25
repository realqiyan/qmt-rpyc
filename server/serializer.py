import base64
import math
from datetime import date, datetime, time
from decimal import Decimal

import numpy as np
import pandas as pd

_MAX_DEPTH = 64


def serialize(obj, _depth=0, _seen=None):
    if _depth > _MAX_DEPTH:
        return "<serialization depth exceeded>"
    if obj is None:
        return None
    if obj is pd.NA or obj is pd.NaT:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, str):
        return obj
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        value = float(obj)
        return value if math.isfinite(value) else None
    if isinstance(obj, np.datetime64):
        return None if np.isnat(obj) else pd.Timestamp(obj).isoformat()
    if isinstance(obj, np.timedelta64):
        return str(obj)
    if isinstance(obj, (datetime, date, time, pd.Timestamp)):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, bytes):
        return {
            "__type__": "bytes",
            "base64": base64.b64encode(obj).decode("ascii"),
        }

    if _seen is None:
        _seen = set()
    obj_id = id(obj)
    if obj_id in _seen:
        return "<serialization cycle detected>"
    _seen.add(obj_id)

    try:
        return _serialize_composite(obj, _depth, _seen)
    finally:
        _seen.discard(obj_id)


def _serialize_composite(obj, depth, seen):
    if isinstance(obj, np.ndarray):
        return serialize(obj.tolist(), depth + 1, seen)
    if isinstance(obj, pd.DataFrame):
        return serialize(obj.to_dict(orient="split"), depth + 1, seen)
    if isinstance(obj, pd.Series):
        return serialize({
            "name": obj.name,
            "index": obj.index.tolist(),
            "data": obj.tolist(),
        }, depth + 1, seen)
    if isinstance(obj, pd.Index):
        return serialize(obj.tolist(), depth + 1, seen)
    if isinstance(obj, dict):
        return {
            k if isinstance(k, str) else str(k):
                serialize(v, depth + 1, seen)
            for k, v in obj.items()
        }
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [serialize(i, depth + 1, seen) for i in obj]
    if hasattr(obj, "__dict__"):
        return {k: serialize(v, depth + 1, seen)
                for k, v in vars(obj).items() if not k.startswith("_")}
    attrs = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            val = getattr(obj, name)
            if callable(val):
                continue
            attrs[name] = serialize(val, depth + 1, seen)
        except Exception:
            continue
    return attrs if attrs else str(obj)
