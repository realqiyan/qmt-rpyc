#!/usr/bin/env python3
"""Collect local SDK definitions for the proposed V1 contract.

Run with the same Windows Python environment as the qmt-rpyc server. This
imports and inspects SDK modules, without constructing a Trader or calling
query, download, connection, subscription, or trading methods. SDK imports
may have their own side effects. No configuration or account data is read.
"""

import argparse
import hashlib
import importlib
import inspect
import json
from pathlib import Path
import platform
import sys


_SOURCE_ROOT = Path(__file__).resolve().parent.parent / "src"
if _SOURCE_ROOT.is_dir():
    sys.path.insert(0, str(_SOURCE_ROOT))

_XTDATA_NAMES = (
    "get_full_tick", "get_trading_dates", "download_history_data",
    "get_market_data_ex", "get_divid_factors", "get_stock_list_in_sector",
    "get_instrument_detail", "get_option_undl_data", "get_option_list",
    "get_option_detail_data", "get_sector_list", "download_sector_data",
    "get_financial_data", "download_financial_data", "get_index_weight",
    "download_index_weight",
)
_SUPPLEMENT_TRADER_NAMES = ("cancel_order_stock", "cancel_order_stock_sysid")
_TRADER_NAMES = (
    "query_stock_asset", "query_stock_orders", "query_stock_positions",
    "order_stock",
) + _SUPPLEMENT_TRADER_NAMES
_SUPPLEMENT_TYPE_NAMES = ("XtCancelOrderResponse", "XtCancelError", "XtOrderResponse")
_TYPE_NAMES = ("StockAccount", "XtAsset", "XtOrder", "XtPosition") + _SUPPLEMENT_TYPE_NAMES
_INTERNAL_XTDATA_NAMES = (
    "_get_market_data_ex_ori_221207", "get_market_data_ex_ori",
    "timetag_to_datetime",
)
_INTERNAL_TRADER_NAMES = ("common_op_sync_with_seq",)
_SUPPLEMENT_CONSTANT_NAMES = (
    "SH_MARKET", "SZ_MARKET", "ORDER_UNREPORTED", "ORDER_WAIT_REPORTING",
    "ORDER_REPORTED", "ORDER_UNKNOWN",
)
_CONSTANT_NAMES = (
    "STOCK_BUY", "STOCK_SELL", "LATEST_PRICE", "ORDER_SUCCEEDED",
    "ORDER_PART_CANCEL", "ORDER_CANCELED", "ORDER_JUNK", "ORDER_PART_SUCC",
    "ORDER_PARTSUCC_CANCEL", "ORDER_REPORTED_CANCEL",
) + _SUPPLEMENT_CONSTANT_NAMES


def _describe(owner, name):
    obj = getattr(owner, name, None)
    if obj is None:
        return {"available": False}
    result = {"available": True, "inspection_errors": {}}
    operations = {
        "signature": lambda: str(inspect.signature(obj)),
        "doc": lambda: inspect.getdoc(obj) or "",
        "source": lambda: inspect.getsource(obj),
    }
    for field, operation in operations.items():
        try:
            result[field] = operation()
        except Exception as error:
            # Record unavailable evidence explicitly; avoid local paths in errors.
            result["inspection_errors"][field] = type(error).__name__
    return result


def _module_fingerprint(module):
    try:
        source_file = getattr(module, "__file__", None)
        if not source_file:
            return {"available": False, "reason": "no_module_file"}
        return {"sha256": hashlib.sha256(Path(source_file).read_bytes()).hexdigest()}
    except OSError as error:
        return {"available": False, "reason": type(error).__name__}


def build_report(supplement=False):
    import qmt_rpyc.server.datetime_patch  # noqa: F401
    from qmt_rpyc.version import __version__

    sdk = importlib.import_module("xtquant")
    modules = {
        name: importlib.import_module("xtquant." + name)
        for name in ("xtdata", "xttrader", "xttype", "xtconstant")
    }
    trader = getattr(modules["xttrader"], "XtQuantTrader", None)
    sdk_version = getattr(sdk, "__version__", None)
    if not isinstance(sdk_version, (str, int, float)):
        sdk_version = None
    xtdata_names = () if supplement else _XTDATA_NAMES
    trader_names = _SUPPLEMENT_TRADER_NAMES if supplement else _TRADER_NAMES
    type_names = _SUPPLEMENT_TYPE_NAMES if supplement else _TYPE_NAMES
    constant_names = _SUPPLEMENT_CONSTANT_NAMES if supplement else _CONSTANT_NAMES
    constants = {}
    for name in constant_names:
        value = getattr(modules["xtconstant"], name, None)
        try:
            constants[name] = {"value": int(value)}
        except (TypeError, ValueError, OverflowError) as error:
            constants[name] = {"available": False, "reason": type(error).__name__}
    return {
        "evidence_schema_version": 1,
        "collection_scope": "supplement" if supplement else "full",
        "note": "Definition evidence only; not a verified input/output contract.",
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "qmt_rpyc_version": __version__,
            "xtquant_version": sdk_version,
        },
        "module_fingerprints": {
            name: _module_fingerprint(module) for name, module in modules.items()
        },
        "xtdata": {
            name: _describe(modules["xtdata"], name) for name in xtdata_names
        },
        "trader": {name: _describe(trader, name) for name in trader_names},
        "xttype": {
            name: _describe(modules["xttype"], name) for name in type_names
        },
        "internal_xtdata": {
            name: _describe(modules["xtdata"], name)
            for name in _INTERNAL_XTDATA_NAMES
        },
        "internal_trader": {
            name: _describe(trader, name) for name in _INTERNAL_TRADER_NAMES
        },
        "constants": constants,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", help="output JSON path; existing files are preserved")
    parser.add_argument(
        "--supplement", action="store_true",
        help="collect new cancellation definitions and unresolved dependencies only",
    )
    args = parser.parse_args(argv)
    default_output = (
        "contract-v1-supplement.json" if args.supplement else "contract-v1-sources.json"
    )
    output = Path(args.output or default_output)
    if output.exists():
        parser.error("output already exists; choose a new filename")
    try:
        report = build_report(supplement=args.supplement)
        with output.open("x", encoding="utf-8") as file:
            json.dump(report, file, ensure_ascii=False, indent=2, allow_nan=False)
            file.write("\n")
    except (ImportError, OSError) as error:
        print("Collection failed: {}: {}".format(type(error).__name__, error),
              file=sys.stderr)
        return 1
    entries = [entry for section in (
        "xtdata", "trader", "xttype", "internal_xtdata", "internal_trader")
               for entry in report[section].values()]
    incomplete = sum(not entry["available"] or bool(entry["inspection_errors"])
                     for entry in entries)
    print("Wrote {} ({} definitions with missing inspection evidence)".format(
        output, incomplete))
    return 0


if __name__ == "__main__":
    sys.exit(main())
