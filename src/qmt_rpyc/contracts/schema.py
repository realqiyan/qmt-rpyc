"""Recursive schema description, independent of any transport."""
from dataclasses import MISSING, fields, is_dataclass
from typing import TypeVar, get_args, get_origin, get_type_hints


def encode_default(value):
    if isinstance(value, tuple):
        return [encode_default(item) for item in value]
    return value

def schema(value_type, bindings=None):
    bindings = bindings or {}
    if isinstance(value_type, TypeVar):
        return schema(bindings[value_type], bindings) if value_type in bindings else {"parameter": value_type.__name__}
    origin = get_origin(value_type)
    args = get_args(value_type)
    concrete = origin or value_type
    if is_dataclass(concrete):
        nested = dict(bindings)
        for param, arg in zip(getattr(concrete, "__parameters__", ()), args):
            nested[param] = bindings.get(arg, arg) if isinstance(arg, TypeVar) else arg
        hints = get_type_hints(concrete)
        members = {}
        for descriptor in fields(concrete):
            item = {"type": schema(hints[descriptor.name], nested)}
            if descriptor.default is not MISSING:
                item["default"] = encode_default(descriptor.default)
            members[descriptor.name] = item
        return {"model": concrete.__name__, "fields": members, "extra": "forbid"}
    if origin is not None:
        return {"type": getattr(origin, "__name__", str(origin)), "args": [
            schema(arg, bindings) if isinstance(arg, (type, TypeVar)) or get_origin(arg) else
            ("..." if arg is Ellipsis else arg) for arg in args]}
    return {"type": getattr(value_type, "__name__", str(value_type))}
