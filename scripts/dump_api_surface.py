#!/usr/bin/env python3
"""Dump the API surface from the xtquant build installed on the server.

Run this script with the Windows server virtual environment so it imports the
same broker-customized xtquant build used by qmt-rpyc.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_ROOT = _PROJECT_ROOT / "src"
if str(_SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SOURCE_ROOT))

# This must be imported before xtquant on Python versions that need the patch.
import qmt_rpyc.server.datetime_patch  # noqa: E402, F401


def _get_xtquant_version():
    import xtquant

    for name in ("__version__", "version", "VERSION"):
        value = getattr(xtquant, name, None)
        if isinstance(value, (str, int, float)):
            return str(value)
    return None


def _remove_docs(surface):
    for desc in surface.get("xtdata", {}).get("functions", {}).values():
        desc.pop("doc", None)
    for desc in surface.get("XtQuantTrader", {}).get("methods", {}).values():
        desc.pop("doc", None)


def build_dump(include_docs=True):
    from qmt_rpyc.server.api_surface import build_api_surface

    surface = build_api_surface()
    if not include_docs:
        _remove_docs(surface)

    return {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "runtime": {
            "python_version": platform.python_version(),
            "platform": platform.system(),
            "machine": platform.machine(),
            "xtquant_version": _get_xtquant_version(),
        },
        "api_surface": surface,
    }


def _write_dump(payload, output, force=False):
    text = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"

    if output == "-":
        sys.stdout.write(text)
        return None

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if force else "x"
    with output_path.open(mode, encoding="utf-8", newline="\n") as file:
        file.write(text)
    return output_path.resolve()


def _build_parser():
    parser = argparse.ArgumentParser(
        description=(
            "Dump the API surface from the xtquant build installed in the "
            "current Windows server environment."
        )
    )
    parser.add_argument(
        "-o",
        "--output",
        default=str(_PROJECT_ROOT / "api_surface.json"),
        help=(
            "output JSON path, or '-' for stdout "
            "(default: api_surface.json in the project root)"
        ),
    )
    parser.add_argument(
        "--without-docs",
        action="store_true",
        help="omit function and method docstrings to reduce output size",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output file",
    )
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        payload = build_dump(include_docs=not args.without_docs)
        output_path = _write_dump(payload, args.output, force=args.force)
    except ImportError as error:
        print(
            "ERROR: xtquant could not be imported from this Python environment: "
            f"{error}",
            file=sys.stderr,
        )
        return 1
    except FileExistsError:
        print(
            f"ERROR: output file already exists: {args.output!r}; "
            "use --force to overwrite it",
            file=sys.stderr,
        )
        return 2

    surface = payload["api_surface"]
    xtdata_count = len(surface.get("xtdata", {}).get("functions", {}))
    trader_count = len(
        surface.get("XtQuantTrader", {}).get("methods", {})
    )
    constant_count = len(
        surface.get("xtconstant", {}).get("constants", {})
    )
    destination = "stdout" if output_path is None else str(output_path)
    print(
        "API surface written to {}: {} xtdata functions, {} trader methods, "
        "{} constants".format(
            destination,
            xtdata_count,
            trader_count,
            constant_count,
        ),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
