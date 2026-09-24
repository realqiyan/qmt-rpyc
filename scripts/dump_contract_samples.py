#!/usr/bin/env python3
"""Collect bounded, read-only return samples from the local Windows xtquant SDK.

Copy this file to the server and run it with the server's Python environment.
It uses the installed server package's timestamp fix, but no RPC service,
configuration or account. Reads
may connect to MiniQMT and use its existing local data; no download, subscription
or Trader operation is requested. SDK imports may have their own side effects.
Samples are evidence, not a complete schema or a proof of semantic compatibility.
"""

import argparse
from contextlib import contextmanager, nullcontext, redirect_stderr, redirect_stdout
import datetime as dt
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
import platform
import re
import sys
import time


_MAX_ITEMS = 3
_MAX_FIELDS = 128
_MAX_DEPTH = 8
_MAX_STRING = 256
_COLLECTOR_REVISION = 2
_DEFAULT_CODES = ("600000.SH", "000001.SZ", "510050.SH")
_FINANCIAL_TABLES = ("Balance", "Income", "CashFlow", "Capital", "HolderNum",
                     "Top10Holder", "Top10FlowHolder", "PershareIndex")
_CODE = re.compile(r"[0-9]{6,8}\.[A-Z]{2,5}\Z")
_PRIVATE_PATH = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|(?:^|\s)/(?:[^\s/]+/)+)")


def _type_name(value):
    kind = type(value)
    return kind.__module__ + "." + kind.__qualname__


def _safe_text(value):
    if _PRIVATE_PATH.search(value):
        return {"redacted": "path_like_string", "length": len(value)}
    if len(value) > _MAX_STRING:
        return {"prefix": value[:_MAX_STRING], "length": len(value), "truncated": True}
    return value


def _sample(value, pandas, numpy, depth=0, seen=None):
    """Preserve observed types; never stringify arbitrary SDK objects."""
    result = {"type": _type_name(value)}
    if value is None or isinstance(value, (bool, int)):
        return {**result, "value": value}
    if isinstance(value, float):
        return {**result, "value": value if math.isfinite(value) else str(value)}
    if isinstance(value, str):
        return {**result, "value": _safe_text(value)}
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return {**result, "value": value.isoformat()}
    if isinstance(value, numpy.generic):
        return {**result, "dtype": str(value.dtype),
                "item": _sample(value.item(), pandas, numpy, depth + 1, seen)}
    if depth >= _MAX_DEPTH:
        return {**result, "truncated": True, "reason": "depth_limit"}
    seen = set() if seen is None else seen
    if id(value) in seen:
        return {**result, "truncated": True, "reason": "cycle"}
    seen.add(id(value))
    try:
        return _sample_container(value, pandas, numpy, depth, seen, result)
    finally:
        seen.remove(id(value))


def _sample_container(value, pandas, numpy, depth, seen, result):
    def sample(item):
        return _sample(item, pandas, numpy, depth + 1, seen)

    if isinstance(value, pandas.DataFrame):
        row_count, column_count = value.shape
        positions = list(range(min(row_count, _MAX_ITEMS)))
        return {
            **result, "shape": [row_count, column_count], "empty": value.empty,
            "columns": [
                {"name": sample(name), "dtype": str(value.dtypes.iloc[index])}
                for index, name in enumerate(value.columns)
            ],
            "columns_truncated": False,
            "index_type": _type_name(value.index),
            "index_dtype": str(value.index.dtype),
            "index_names": [sample(name) for name in value.index.names],
            "index_sample": [sample(value.index[index]) for index in positions],
            "row_sample": [
                [sample(value.iloc[row, column]) for column in range(column_count)]
                for row in positions
            ],
            "rows_truncated": row_count > len(positions),
        }
    if isinstance(value, (pandas.Series, pandas.Index)):
        count = len(value)
        positions = range(min(count, _MAX_ITEMS))
        values = value.iloc if isinstance(value, pandas.Series) else value
        report = {**result, "count": count, "empty": count == 0,
                  "dtype": str(value.dtype), "name": sample(value.name),
                  "item_sample": [sample(values[index]) for index in positions],
                  "truncated": count > _MAX_ITEMS}
        if isinstance(value, pandas.Series):
            report["index_sample"] = [sample(value.index[index]) for index in positions]
        return report
    if isinstance(value, numpy.ndarray):
        return {**result, "shape": list(value.shape), "dtype": str(value.dtype),
                "count": int(value.size), "empty": value.size == 0,
                "flat_item_sample": [sample(item) for item in value.flat[:_MAX_ITEMS]],
                "truncated": value.size > _MAX_ITEMS}
    if isinstance(value, dict):
        fields = []
        for index, (key, item) in enumerate(value.items()):
            if index >= _MAX_FIELDS:
                break
            fields.append({"key": sample(key), "value": sample(item)})
        return {**result, "count": len(value), "empty": not value,
                "fields": fields, "truncated": len(value) > len(fields)}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = []
        for index, item in enumerate(value):
            if index >= _MAX_ITEMS:
                break
            items.append(sample(item))
        return {**result, "count": len(value), "empty": not value,
                "item_sample": items, "truncated": len(value) > len(items)}
    if isinstance(value, bytes):
        return {**result, "count": len(value), "sample_omitted": "binary_value"}
    return {**result, "sample_omitted": "unsupported_type_no_repr_or_attribute_access"}


@contextmanager
def _quiet_sdk():
    """Discard Python and native stdout/stderr; keep logs out of evidence files."""
    saved = []
    with open(os.devnull, "w", encoding="utf-8") as sink:
        try:
            for stream in (sys.stdout, sys.stderr):
                stream.flush()
            for descriptor in (1, 2):
                copy = os.dup(descriptor)
                saved.append((descriptor, copy))
                os.dup2(sink.fileno(), descriptor)
            with redirect_stdout(sink), redirect_stderr(sink):
                yield
        finally:
            for descriptor, copy in reversed(saved):
                os.dup2(copy, descriptor)
                os.close(copy)


def _fingerprint(module):
    try:
        source = getattr(module, "__file__", None)
        if not source:
            return {"available": False, "reason": "no_module_file"}
        return {"sha256": hashlib.sha256(Path(source).read_bytes()).hexdigest()}
    except OSError as error:
        return {"available": False, "reason": _type_name(error)}


def build_report(args, checkpoint=lambda report: None):
    local_now = dt.datetime.now().astimezone()
    report = {
        "evidence_schema_version": 1,
        "collector_revision": _COLLECTOR_REVISION,
        "collection_scope": "read_only_return_samples",
        "note": "Observed samples only; not complete schemas or semantic validation.",
        "generated_at_utc": local_now.astimezone(dt.timezone.utc).isoformat(),
        "runtime": {
            "python_version": platform.python_version(), "platform": platform.system(),
            "local_timezone_name": local_now.tzname(),
            "local_utc_offset_seconds": int(local_now.utcoffset().total_seconds()),
            "local_time": local_now.isoformat(),
        },
        "limits": {"sequence_items": _MAX_ITEMS, "mapping_fields": _MAX_FIELDS,
                   "depth": _MAX_DEPTH, "string_chars": _MAX_STRING,
                   "dataframe_rows": _MAX_ITEMS, "dataframe_columns": "all"},
        "sdk_output": ("shown in terminal only; no SDK logs included"
                       if args.show_sdk_output else
                       "stdout/stderr discarded; no SDK logs included"),
        "collection_status": "importing",
        "imports": [],
        "calls": [],
    }
    checkpoint(report)
    sdk_output = nullcontext if args.show_sdk_output else _quiet_sdk

    def import_module(name):
        entry = {"module": name, "status": "running"}
        report["imports"].append(entry)
        checkpoint(report)
        print("Importing {}...".format(name), flush=True)
        with sdk_output():
            module = importlib.import_module(name)
        entry["status"] = "ok"
        checkpoint(report)
        return module

    try:
        # Match server/main.py: initialize pandas/numpy before replacing the
        # datetime class, while still loading the patch before any SDK import.
        pandas = import_module("pandas")
        numpy = import_module("numpy")
        patch = import_module("qmt_rpyc.server.datetime_patch")
        version = import_module("qmt_rpyc.version")
        report["runtime"]["qmt_rpyc_version"] = version.__version__
        report["runtime"]["datetime_patch_applied"] = getattr(patch, "_PATCHED", None)
        sdk = import_module("xtquant")
        xtdata = import_module("xtquant.xtdata")
        report["module_fingerprints"] = {
            "xtquant": _fingerprint(sdk), "xtdata": _fingerprint(xtdata),
            "datetime_patch": _fingerprint(patch),
        }
    except (Exception, SystemExit, KeyboardInterrupt) as error:
        interrupted = isinstance(error, KeyboardInterrupt)
        report["collection_status"] = "interrupted" if interrupted else "failed"
        current_import = report["imports"][-1]
        if current_import["status"] == "running":
            current_import["status"] = "interrupted" if interrupted else "error"
        report["collection_error"] = {"stage": "import", "type": _type_name(error),
                                      "module": current_import["module"],
                                      "message_omitted": "may_contain_private_paths",
                                      "guidance": "Run in the existing Windows server Python environment with qmt-rpyc and xtquant installed."}
        if isinstance(error, SystemExit) and isinstance(error.code, int):
            report["collection_error"]["exit_code"] = error.code
        checkpoint(report)
        return report
    report["collection_status"] = "collecting"

    def call(name, positional, keyword, operation):
        entry = {"api": name, "args": positional, "kwargs": keyword, "status": "running",
                 "started_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        report["calls"].append(entry)
        checkpoint(report)
        print("Reading {} (call {})...".format(name, len(report["calls"])), flush=True)
        started = time.monotonic()
        raw = None
        try:
            with sdk_output():
                raw = operation()
            entry["status"] = "ok"
            try:
                entry["result"] = _sample(raw, pandas, numpy)
            except Exception as error:
                entry["sampling_error"] = {"type": _type_name(error)}
                entry["result"] = {"type": _type_name(raw)}
        except Exception as error:
            entry["status"] = "error"
            entry["error"] = {"type": _type_name(error),
                              "message_omitted": "may_contain_private_paths"}
        entry["duration_seconds"] = round(time.monotonic() - started, 6)
        checkpoint(report)
        return raw

    codes = list(args.codes)
    start, end = args.start, args.end
    call("get_full_tick", [codes], {}, lambda: xtdata.get_full_tick(codes))
    for market in sorted({code.rsplit(".", 1)[1] for code in codes} & {"SH", "SZ"}):
        call("get_trading_dates", [market, start, end], {"count": 3},
             lambda: xtdata.get_trading_dates(market, start, end, count=3))
    for period in ("1d", "1m"):
        kwargs = {"period": period, "start_time": start, "end_time": end,
                  "count": 3, "dividend_type": "none", "fill_data": True}
        call("get_market_data_ex", [[], codes], kwargs,
             lambda: xtdata.get_market_data_ex([], codes, **kwargs))
    for code in codes:
        call("get_divid_factors", [code, start, end], {},
             lambda: xtdata.get_divid_factors(code, start, end))
        call("get_instrument_detail", [code], {"iscomplete": True},
             lambda: xtdata.get_instrument_detail(code, iscomplete=True))
    call("get_sector_list", [], {}, lambda: xtdata.get_sector_list())
    tables = list(_FINANCIAL_TABLES)
    call("get_financial_data", [codes, tables, start, end], {"report_type": "report_time"},
         lambda: xtdata.get_financial_data(codes, tables, start, end, report_type="report_time"))
    call("get_index_weight", [args.index_code], {},
         lambda: xtdata.get_index_weight(args.index_code))
    month = local_now.strftime("%Y%m")
    options = call("get_option_list", ["510050.SH", month, "", False], {},
                   lambda: xtdata.get_option_list("510050.SH", month, "", False))
    option = None
    if isinstance(options, (list, tuple, dict)):
        option = next((code for code in options if isinstance(code, str) and _CODE.fullmatch(code)), None)
    report["option_selection"] = {"selected": option, "max_selected": 1,
                                  "reason": "first_valid_code" if option else "no_valid_code_returned"}
    if option is not None:
        call("get_option_detail_data", [option], {}, lambda: xtdata.get_option_detail_data(option))
        call("get_instrument_detail", [option], {"iscomplete": True},
             lambda: xtdata.get_instrument_detail(option, iscomplete=True))
        call("get_full_tick", [[option]], {}, lambda: xtdata.get_full_tick([option]))
    report["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    report["collection_status"] = "completed"
    checkpoint(report)
    return report


def _date(value):
    try:
        if len(value) != 8 or not value.isdigit():
            raise ValueError
        dt.date(int(value[:4]), int(value[4:6]), int(value[6:]))
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be a valid YYYYMMDD") from error
    return value


def _code(value):
    if not _CODE.fullmatch(value):
        raise argparse.ArgumentTypeError("code must look like 600000.SH or 10000001.SHO")
    return value


def main(argv=None):
    today = dt.date.today()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="contract-v1-samples.json",
                        help="new JSON file; existing files are never overwritten")
    parser.add_argument("--codes", nargs="+", type=_code, default=list(_DEFAULT_CODES))
    parser.add_argument("--start", type=_date,
                        default=(today - dt.timedelta(days=730)).strftime("%Y%m%d"))
    parser.add_argument("--end", type=_date, default=today.strftime("%Y%m%d"))
    parser.add_argument("--index-code", type=_code, default="000300.SH")
    parser.add_argument("--show-sdk-output", action="store_true",
                        help="show SDK diagnostics in the terminal only, never in JSON")
    args = parser.parse_args(argv)
    if args.start > args.end:
        parser.error("--start must be on or before --end")
    if len(args.codes) > 10:
        parser.error("at most 10 codes may be sampled per run")
    try:
        # Reserve the output before importing the SDK or making any calls.
        with Path(args.output).open("x", encoding="utf-8", newline="\n") as output:
            def checkpoint(report):
                text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
                output.seek(0)
                output.write(text + "\n")
                output.truncate()
                output.flush()

            report = build_report(args, checkpoint=checkpoint)
    except FileExistsError:
        parser.error("output already exists; choose a new filename")
    except OSError as error:
        print("Collection/output failed: " + _type_name(error), file=sys.stderr)
        return 1
    failed = sum(call["status"] != "ok" or "sampling_error" in call for call in report["calls"])
    print("Wrote evidence: {} calls, {} incomplete. Samples are not complete schemas.".format(
        len(report["calls"]), failed))
    if "collection_error" in report:
        print("Import failed. Run in the existing Windows server Python environment "
              "with qmt-rpyc and xtquant installed; see collection_error.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
