"""qmt-rpyc client command-line interface."""

import argparse
import contextlib
import getpass
import json
import os
import sys
import time

from qmt_rpyc import QmtClient
from qmt_rpyc.config import (
    DEFAULT_PROFILE,
    client_config_path,
    delete_profile,
    load_profiles,
    probe_keyring,
    save_profile,
)
from qmt_rpyc.exceptions import QmtError
from qmt_rpyc.proxy import DownloadTaskHandle


EXIT_USAGE = 2
EXIT_CONFIRMATION = 3
EXIT_CONNECTION = 4
EXIT_REMOTE = 5


def _emit(value, compact=False, stream=None):
    stream = stream or sys.stdout
    kwargs = {
        "ensure_ascii": False,
        "default": str,
    }
    if compact:
        kwargs["separators"] = (",", ":")
    else:
        kwargs["indent"] = 2
    print(json.dumps(value, **kwargs), file=stream)


def _connect(args):
    overrides = {
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
        "timeout": getattr(args, "timeout", None),
    }
    return QmtClient.connect_profile(args.profile, **overrides)


def _surface_descriptor(client, surface):
    if surface == "xtdata":
        return client._surface.get("xtdata", {}).get("functions", {})
    if surface == "trader":
        return client._surface.get("XtQuantTrader", {}).get("methods", {})
    if surface == "constants":
        return client._surface.get("xtconstant", {}).get("constants", {})
    raise ValueError("unknown API surface: {}".format(surface))


def _risk(surface, name, meta):
    signature = meta.get("signature", "") if isinstance(meta, dict) else ""
    if "callback" in signature:
        return "unsupported-callback"
    if surface == "trader":
        return "read" if name.startswith("query_") else "trading"
    if name.startswith("download_"):
        return "download"
    read_prefixes = (
        "get_", "query_", "is_", "list_", "get", "query", "list",
    )
    if name.startswith(read_prefixes):
        return "read"
    return "write"


def _read_request(args):
    if args.request:
        if args.request == "-":
            payload = json.load(sys.stdin)
        else:
            with open(args.request, "r", encoding="utf-8") as fp:
                payload = json.load(fp)
        if not isinstance(payload, dict):
            raise ValueError("request JSON must be an object")
        return (
            payload.get("surface"),
            payload.get("name"),
            payload.get("args", []),
            payload.get("kwargs", {}),
        )
    return (
        args.surface,
        args.name,
        json.loads(args.args),
        json.loads(args.kwargs),
    )


def _cmd_init(args):
    profile = args.profile
    existing = load_profiles().get(profile, {})
    default_host = existing.get("host", "127.0.0.1")
    default_port = existing.get("port", 18812)
    if args.non_interactive:
        host = args.host or default_host
        raw_port = args.port or default_port
    else:
        host = args.host or input(
            "Server host [{}]: ".format(default_host)
        ).strip() or default_host
        raw_port = args.port or input(
            "Server port [{}]: ".format(default_port)
        ).strip() or default_port
    secret = os.environ.get("QMT_RPYC_AUTH_KEY")
    if not secret:
        if args.non_interactive:
            raise ValueError(
                "QMT_RPYC_AUTH_KEY is required in non-interactive mode"
            )
        secret = getpass.getpass("Authentication key: ")
    if len(secret.encode("utf-8")) < 16:
        raise ValueError("authentication key must be at least 16 bytes")
    port = int(raw_port)
    if not 1 <= port <= 65535:
        raise ValueError("server port must be between 1 and 65535")
    timeout = float(args.timeout or existing.get("timeout", 30))
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    store_plaintext = args.store_plaintext
    if not store_plaintext:
        ok, detail = probe_keyring()
        if not ok:
            print(
                "System keyring unavailable: {}".format(detail),
                file=sys.stderr,
            )
            if args.non_interactive:
                raise RuntimeError(
                    "keyring unavailable; pass --store-plaintext explicitly"
                )
            answer = input(
                "Store the key in the protected config file? [y/N]: "
            ).strip().lower()
            if answer not in ("y", "yes"):
                raise RuntimeError("profile was not saved")
            store_plaintext = True

    values = {
        "host": host,
        "port": port,
        "timeout": timeout,
        "ca_certs": args.ca_certs or existing.get("ca_certs"),
        "certfile": args.certfile or existing.get("certfile"),
        "keyfile": args.keyfile or existing.get("keyfile"),
        "output": existing.get("output", "json"),
    }
    path = save_profile(
        profile, values, secret=secret, store_plaintext=store_plaintext
    )
    _emit({
        "status": "ok",
        "profile": profile,
        "config_path": str(path),
        "secret_store": "config" if store_plaintext else "keyring",
    }, args.compact)


def _cmd_profile(args):
    profiles = load_profiles()
    if args.profile_command == "list":
        _emit(sorted(profiles), args.compact)
        return
    if args.profile_command == "show":
        if args.name not in profiles:
            raise KeyError("profile {!r} does not exist".format(args.name))
        value = dict(profiles[args.name])
        if "auth_key" in value:
            value["auth_key"] = "***"
        _emit(value, args.compact)
        return
    if args.profile_command == "delete":
        if args.name not in profiles:
            raise KeyError("profile {!r} does not exist".format(args.name))
        if not args.yes:
            answer = input(
                "Delete profile {!r} and its stored credential? [y/N]: ".format(
                    args.name
                )
            ).strip().lower()
            if answer not in ("y", "yes"):
                raise RuntimeError("profile was not deleted")
        delete_profile(args.name)
        _emit({"status": "deleted", "profile": args.name}, args.compact)


def _cmd_check(args):
    with _connect(args) as client:
        health = client.health()
        result = {
            "status": "ok" if health.get("connected") else "degraded",
            "profile": args.profile,
            "package_version": client._surface.get("package_version"),
            "protocol_version": client._surface.get("protocol_version"),
            "schema_version": client._surface.get("schema_version"),
            "health": health,
            "api_counts": {
                "xtdata": len(_surface_descriptor(client, "xtdata")),
                "trader": len(_surface_descriptor(client, "trader")),
                "constants": len(_surface_descriptor(client, "constants")),
            },
        }
        _emit(result, args.compact)
        return 0 if health.get("connected") else EXIT_CONNECTION


def _cmd_api(args):
    with _connect(args) as client:
        descriptor = _surface_descriptor(client, args.surface)
        if args.api_command == "list":
            if args.surface == "constants":
                result = descriptor
            else:
                result = [
                    {
                        "name": name,
                        "signature": meta.get("signature", ""),
                        "risk": _risk(args.surface, name, meta),
                    }
                    for name, meta in sorted(descriptor.items())
                ]
            _emit(result, args.compact)
            return
        if args.name not in descriptor:
            raise KeyError(
                "{} API {!r} does not exist".format(
                    args.surface, args.name
                )
            )
        meta = descriptor[args.name]
        result = {
            "surface": args.surface,
            "name": args.name,
            "metadata": meta,
            "risk": _risk(args.surface, args.name, meta),
        }
        _emit(result, args.compact)


def _execute_call(client, surface, name, call_args, kwargs, args):
    descriptor = _surface_descriptor(client, surface)
    if name not in descriptor:
        raise KeyError(
            "{} API {!r} does not exist".format(surface, name)
        )
    meta = descriptor[name]
    risk = _risk(surface, name, meta)
    if risk == "unsupported-callback":
        raise ValueError(
            "{}.{} requires a callback and is not supported by the CLI".format(
                surface, name
            )
        )
    if risk == "download" and not getattr(args, "download_mode", False):
        raise ValueError("download_* functions require 'download start'")
    if risk == "trading" and not args.confirm_trading:
        _emit({
            "status": "confirmation_required",
            "risk": risk,
            "surface": surface,
            "name": name,
            "args": call_args,
            "kwargs": kwargs,
        }, args.compact, sys.stderr)
        return EXIT_CONFIRMATION
    if risk == "write" and not args.confirm_write:
        _emit({
            "status": "confirmation_required",
            "risk": risk,
            "surface": surface,
            "name": name,
            "args": call_args,
            "kwargs": kwargs,
        }, args.compact, sys.stderr)
        return EXIT_CONFIRMATION

    target = client.xtdata if surface == "xtdata" else client.trader
    result = getattr(target, name)(*call_args, **kwargs)
    if isinstance(result, DownloadTaskHandle):
        result = {"task_id": result.task_id, "status": "started"}
    _emit(result, args.compact)
    return 0


def _cmd_call(args):
    surface, name, call_args, kwargs = _read_request(args)
    if surface not in ("xtdata", "trader"):
        raise ValueError("surface must be 'xtdata' or 'trader'")
    if not isinstance(call_args, list) or not isinstance(kwargs, dict):
        raise ValueError("args must be a JSON array and kwargs a JSON object")
    with _connect(args) as client:
        return _execute_call(
            client, surface, name, call_args, kwargs, args
        )


def _cmd_events(args):
    event_types = [
        item.strip() for item in args.types.split(",") if item.strip()
    ]
    with _connect(args) as client:
        sub_id = client.subscribe(event_types, account_id=args.account)
        try:
            while True:
                events, dropped = client.drain_events(
                    sub_id, max_count=args.max_count
                )
                if dropped:
                    _emit(
                        {"warning": "events_dropped", "count": dropped},
                        True,
                        sys.stderr,
                    )
                for event in events:
                    _emit(event, True)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            return 0
        finally:
            client.unsubscribe(sub_id)


def _cmd_download(args):
    with _connect(args) as client:
        if args.download_command == "start":
            call_args = json.loads(args.args)
            kwargs = json.loads(args.kwargs)
            if not isinstance(call_args, list) or not isinstance(kwargs, dict):
                raise ValueError(
                    "args must be a JSON array and kwargs a JSON object"
                )
            args.download_mode = True
            return _execute_call(
                client, "xtdata", args.name, call_args, kwargs, args
            )
        if args.download_command == "status":
            _emit(client.query_download(args.task_id), args.compact)
            return 0
        handle = client.download_handle(args.task_id)
        _emit(
            handle.wait(timeout=args.wait_timeout, poll_interval=args.interval),
            args.compact,
        )
        return 0


def _cmd_self_test(args):
    with _connect(args) as client:
        with contextlib.redirect_stdout(sys.stderr):
            result = client.self_test(timeout=args.test_timeout)
        _emit(result, args.compact)
        return 0 if not result.get("failed") else EXIT_REMOTE


def _add_connection_options(parser):
    parser.add_argument("--profile", default=DEFAULT_PROFILE)
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--compact", action="store_true")


def build_parser():
    parser = argparse.ArgumentParser(prog="qmt-rpyc-client")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create or update a client profile")
    _add_connection_options(init)
    init.add_argument("--ca-certs")
    init.add_argument("--certfile")
    init.add_argument("--keyfile")
    init.add_argument("--store-plaintext", action="store_true")
    init.add_argument("--non-interactive", action="store_true")
    init.set_defaults(func=_cmd_init)

    profile = sub.add_parser("profile", help="manage profiles")
    profile.add_argument("--compact", action="store_true")
    profile_sub = profile.add_subparsers(
        dest="profile_command", required=True
    )
    profile_sub.add_parser("list")
    show = profile_sub.add_parser("show")
    show.add_argument("name")
    delete = profile_sub.add_parser("delete")
    delete.add_argument("name")
    delete.add_argument("--yes", action="store_true")
    profile.set_defaults(func=_cmd_profile)

    check = sub.add_parser("check", help="check a remote server")
    _add_connection_options(check)
    check.set_defaults(func=_cmd_check)

    api = sub.add_parser("api", help="inspect the discovered API")
    _add_connection_options(api)
    api_sub = api.add_subparsers(dest="api_command", required=True)
    api_list = api_sub.add_parser("list")
    api_list.add_argument(
        "surface", choices=("xtdata", "trader", "constants")
    )
    describe = api_sub.add_parser("describe")
    describe.add_argument("surface", choices=("xtdata", "trader"))
    describe.add_argument("name")
    api.set_defaults(func=_cmd_api)

    call = sub.add_parser("call", help="call a discovered API")
    _add_connection_options(call)
    call.add_argument("surface", nargs="?", choices=("xtdata", "trader"))
    call.add_argument("name", nargs="?")
    call.add_argument("--args", default="[]")
    call.add_argument("--kwargs", default="{}")
    call.add_argument("--request")
    call.add_argument("--confirm-trading", action="store_true")
    call.add_argument("--confirm-write", action="store_true")
    call.set_defaults(func=_cmd_call)

    events = sub.add_parser("events", help="stream event bus events")
    _add_connection_options(events)
    events.add_argument("--types", required=True)
    events.add_argument("--account")
    events.add_argument("--interval", type=float, default=1.0)
    events.add_argument("--max-count", type=int, default=100)
    events.set_defaults(func=_cmd_events)

    download = sub.add_parser("download", help="manage downloads")
    _add_connection_options(download)
    download.add_argument("--confirm-trading", action="store_true")
    download.add_argument("--confirm-write", action="store_true")
    download_sub = download.add_subparsers(
        dest="download_command", required=True
    )
    start = download_sub.add_parser("start")
    start.add_argument("name")
    start.add_argument("--args", default="[]")
    start.add_argument("--kwargs", default="{}")
    status = download_sub.add_parser("status")
    status.add_argument("task_id")
    wait = download_sub.add_parser("wait")
    wait.add_argument("task_id")
    wait.add_argument("--wait-timeout", type=float, default=300)
    wait.add_argument("--interval", type=float, default=0.5)
    download.set_defaults(func=_cmd_download)

    self_test = sub.add_parser("self-test", help="run read-only checks")
    _add_connection_options(self_test)
    self_test.add_argument("--test-timeout", type=float, default=30)
    self_test.set_defaults(func=_cmd_self_test)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        _emit(
            {"status": "error", "error_type": type(e).__name__,
             "error_message": str(e)},
            getattr(args, "compact", False),
            sys.stderr,
        )
        return EXIT_USAGE
    except QmtError as e:
        _emit(
            {"status": "error", "error_type": type(e).__name__,
             "error_message": str(e)},
            getattr(args, "compact", False),
            sys.stderr,
        )
        return EXIT_REMOTE
    except Exception as e:
        _emit(
            {"status": "error", "error_type": type(e).__name__,
             "error_message": str(e)},
            getattr(args, "compact", False),
            sys.stderr,
        )
        return EXIT_CONNECTION


if __name__ == "__main__":
    sys.exit(main())
