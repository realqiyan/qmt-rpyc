import inspect

from qmt_rpyc.protocol import (
    API_SURFACE_SCHEMA_VERSION,
    PROTOCOL_VERSION,
)
from qmt_rpyc.version import __version__


def _owner_modules(obj):
    names = set()
    module_name = getattr(obj, "__name__", None)
    if isinstance(module_name, str):
        names.add(module_name)
    class_module = getattr(type(obj), "__module__", None)
    if isinstance(class_module, str) and class_module != "builtins":
        names.add(class_module)
    object_module = getattr(obj, "__module__", None)
    if isinstance(object_module, str):
        names.add(object_module)
    return names


def _is_public_api_callable(obj, name):
    if name.startswith("_"):
        return False
    value = getattr(obj, name, None)
    if not callable(value) or inspect.isclass(value):
        return False
    origin = getattr(value, "__module__", None)
    if not origin:
        return True
    return any(
        origin == owner or origin.startswith(owner + ".")
        for owner in _owner_modules(obj)
    )


def _public_callables(obj):
    return [name for name in dir(obj)
            if _is_public_api_callable(obj, name)]


def _func_meta(fn):
    try:
        sig = str(inspect.signature(fn))
    except (ValueError, TypeError):
        sig = ""
    return {"signature": sig, "doc": inspect.getdoc(fn) or ""}


def build_api_surface():
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader
    from xtquant import xtconstant

    xtdata_funcs = {}
    for name in _public_callables(xtdata):
        fn = getattr(xtdata, name)
        xtdata_funcs[name] = _func_meta(fn)

    trader_methods = {}
    for name in _public_callables(XtQuantTrader):
        fn = getattr(XtQuantTrader, name)
        trader_methods[name] = _func_meta(fn)

    constants = {}
    for name in dir(xtconstant):
        if name.startswith("_"):
            continue
        val = getattr(xtconstant, name)
        if callable(val):
            continue
        if isinstance(val, (int, float, str, bool)):
            constants[name] = val
        elif hasattr(val, "__int__") and not callable(val):
            # pybind11 / numpy integer types that quack like int
            # but fail the strict isinstance check above
            try:
                constants[name] = int(val)
            except Exception:
                pass

    try:
        from xtquant import xttype
        classes = [n for n in dir(xttype)
                   if not n.startswith("_") and inspect.isclass(getattr(xttype, n))]
    except ImportError:
        classes = []

    return {
        "package_version": __version__,
        "protocol_version": PROTOCOL_VERSION,
        "schema_version": API_SURFACE_SCHEMA_VERSION,
        "xtdata": {
            "module_name": "xtquant.xtdata",
            "functions": xtdata_funcs,
        },
        "XtQuantTrader": {
            "class_name": "xtquant.xttrader.XtQuantTrader",
            "methods": trader_methods,
        },
        "xtconstant": {
            "constants": constants,
        },
        "xttype": {
            "classes": classes,
            "note": "StockAccount etc. constructed server-side; "
                    "client passes account_id string, server auto-wraps",
        },
    }
