"""Strict primitive JSON codec shared by client and server."""
import collections.abc
import json
import math
import re
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Literal, TypeVar, Union, get_args, get_origin, get_type_hints

from qmt_rpyc.contracts.errors import ProtocolError

_INSTANT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")


def encode(value):
    """Materialize public values into JSON primitives, without pickle."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ProtocolError("non-finite number")
        return value
    if type(value) is datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ProtocolError("naive datetime is forbidden")
        return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    if type(value) is date:
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    if isinstance(value, collections.abc.Mapping):
        if any(type(key) is not str for key in value):
            raise ProtocolError("object keys must be strings")
        return {key: encode(item) for key, item in value.items()}
    raise ProtocolError("unsupported public value: " + type(value).__name__)


def decode(model_type, value, _bindings=None):
    """Decode a declared type; reject extra fields, bool numbers and coercions."""
    bindings = _bindings or {}
    if isinstance(model_type, TypeVar):
        if model_type not in bindings:
            raise ProtocolError("unbound generic parameter")
        return decode(bindings[model_type], value, bindings)
    origin = get_origin(model_type)
    args = get_args(model_type)
    if origin is Union:
        failures = []
        for candidate in args:
            try:
                return decode(candidate, value, bindings)
            except (ProtocolError, ValueError, TypeError) as exc:
                failures.append(str(exc))
        raise ProtocolError("no declared union variant matches: " + "; ".join(failures))
    if origin is Literal:
        if not any(type(value) is type(item) and value == item for item in args):
            raise ProtocolError("invalid literal: " + repr(value))
        return value
    if model_type is type(None) or model_type is None:
        if value is not None:
            raise ProtocolError("expected null")
        return None
    if model_type in (str, bool, int):
        if type(value) is not model_type:
            raise ProtocolError("expected " + model_type.__name__)
        return value
    if model_type is float:
        if type(value) not in (int, float):
            raise ProtocolError("expected finite number")
        try:
            number = float(value)
        except (OverflowError, ValueError) as exc:
            raise ProtocolError("number outside float range") from exc
        if not math.isfinite(number):
            raise ProtocolError("expected finite number")
        return number
    if model_type is date:
        if type(value) is not str or not _DATE.fullmatch(value):
            raise ProtocolError("expected YYYY-MM-DD date")
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ProtocolError("invalid calendar date") from exc
    if model_type is datetime:
        if type(value) is not str or not _INSTANT.fullmatch(value):
            raise ProtocolError("expected UTC instant with six fractional digits")
        try:
            return datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise ProtocolError("invalid UTC instant") from exc
    if origin in (tuple, list):
        if type(value) is not list:
            raise ProtocolError("expected JSON array")
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(decode(args[0], item, bindings) for item in value)
        if len(value) != len(args):
            raise ProtocolError("fixed tuple length mismatch")
        return tuple(decode(t, item, bindings) for t, item in zip(args, value))
    if origin in (dict, collections.abc.Mapping):
        if type(value) is not dict:
            raise ProtocolError("expected JSON object")
        return MappingProxyType({decode(args[0], k, bindings): decode(args[1], v, bindings)
                                 for k, v in value.items()})
    concrete = origin or model_type
    if is_dataclass(concrete):
        if type(value) is not dict:
            raise ProtocolError("expected " + concrete.__name__ + " object")
        parameters = getattr(concrete, "__parameters__", ())
        nested = dict(bindings)
        for parameter, argument in zip(parameters, args):
            nested[parameter] = bindings.get(argument, argument) if isinstance(argument, TypeVar) else argument
        declared = {f.name: f for f in fields(concrete)}
        if set(value) - set(declared):
            raise ProtocolError("unknown fields for " + concrete.__name__)
        hints = get_type_hints(concrete)
        kwargs = {}
        for name, descriptor in declared.items():
            if name not in value:
                if name not in ("status", "kind") and descriptor.default is not MISSING:
                    kwargs[name] = descriptor.default
                    continue
                raise ProtocolError("missing " + concrete.__name__ + "." + name)
            kwargs[name] = decode(hints[name], value[name], nested)
        try:
            result = concrete(**kwargs)
            return result
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ProtocolError(concrete.__name__ + ": " + str(exc)) from exc
    raise ProtocolError("unsupported declared type: " + str(model_type))


def dumps(value):
    return json.dumps(encode(value), ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON key: " + key)
        result[key] = value
    return result


def _constant(value):
    raise ProtocolError("non-finite JSON value: " + value)


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ProtocolError("JSON number exceeds finite range")
    return result


def loads(payload):
    if type(payload) is not str:
        raise ProtocolError("transport payload must be a JSON string")
    try:
        return json.loads(payload, object_pairs_hook=_object, parse_constant=_constant, parse_float=_float)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProtocolError("invalid JSON: " + str(exc)) from exc
