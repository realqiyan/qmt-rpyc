import inspect


def _public_callables(obj):
    return [n for n in dir(obj)
            if not n.startswith("_") and callable(getattr(obj, n, None))]


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
