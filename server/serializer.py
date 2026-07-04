import numpy as np
import pandas as pd

_MAX_DEPTH = 64


def serialize(obj, _depth=0):
    if _depth > _MAX_DEPTH:
        return "<serialization depth exceeded>"
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float, str)):
        return obj
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="split")
    if isinstance(obj, dict):
        return {k: serialize(v, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [serialize(i, _depth + 1) for i in obj]
    if hasattr(obj, "__dict__"):
        return {k: serialize(v, _depth + 1)
                for k, v in vars(obj).items() if not k.startswith("_")}
    attrs = {}
    for name in dir(obj):
        if name.startswith("_"):
            continue
        try:
            val = getattr(obj, name)
            if callable(val):
                continue
            attrs[name] = serialize(val, _depth + 1)
        except Exception:
            continue
    return attrs if attrs else str(obj)
