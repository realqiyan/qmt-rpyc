"""qmt-rpyc Windows server command-line interface."""

import getpass
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from qmt_rpyc import QmtClient
from qmt_rpyc.cli.common import (
    ArgumentParser,
    add_version_argument,
    emit_interrupted,
)
from qmt_rpyc.server.config import (
    default_server_dir as server_dir,
    read_env as _read_env,
    valid_auth_key,
    write_env as _write_env,
)
from qmt_rpyc.server.environment import (
    detect_environment,
    managed_xtquant_path,
    private_ipv4_addresses,
    wire_xtquant,
)

from qmt_rpyc.server.sdk_loader import configured_sdk_path, load_sdk, validate_sdk_path

FIREWALL_RULE = "qmt-rpyc server"


def config_path(value=None):
    return Path(value).expanduser() if value else server_dir() / "config.env"


def _emit(value, compact=False, stream=None):
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            default=str,
            **({"separators": (",", ":")} if compact else {"indent": 2})
        ),
        file=stream or sys.stdout,
    )


def _confirm(prompt, default=False, assume_yes=False):
    if assume_yes:
        return True
    suffix = " [Y/n]: " if default else " [y/N]: "
    answer = input(prompt + suffix).strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


def _masked_summary(values):
    account = values.get("QMT_ACCOUNT_ID", "")
    if len(account) > 4:
        account = account[:2] + "*" * (len(account) - 4) + account[-2:]
    return {
        "host": values.get("QMT_RPYC_HOST"),
        "port": values.get("QMT_RPYC_PORT"),
        "qmt_path": values.get("QMT_PATH"),
        "xtquant_path": configured_sdk_path(values),
        "account": account or "(market-data only)",
        "auth_key": "set" if values.get("QMT_RPYC_AUTH_KEY") else "missing",
    }


def _next_command(args, command):
    parts = ["qmt-rpyc-server"]
    if args.config:
        parts.extend(["--config", str(config_path(args.config))])
    parts.append(command)
    return subprocess.list2cmdline(parts)


def _cmd_init(args):
    target = config_path(args.config)
    source = Path(args.import_env).expanduser() if args.import_env else None
    if source is None:
        current = Path.cwd() / ".env"
        if current.exists():
            source = current
    imported = _read_env(source) if source and source.exists() else {}
    if imported:
        if args.non_interactive and not args.yes:
            raise ValueError("Importing an existing .env non-interactively requires --yes")
        _emit({"import_candidate": str(source),
               "summary": _masked_summary(imported)}, args.compact)
        if not _confirm(
                "Import this configuration?", default=True,
                assume_yes=args.yes):
            imported = {}

    current = _read_env(target)
    values = dict(current)
    values.update(imported)
    if args.adapter:
        from qmt_rpyc.adapters.registry import select_adapter
        adapter = select_adapter(args.adapter)
    else:
        adapter = _selected_adapter(values)
    values["QMT_RPYC_ADAPTER"] = adapter.name
    detected = detect_environment() if adapter.requires_native_sdk else {}
    addresses = private_ipv4_addresses()

    default_host = (
        values.get("QMT_RPYC_HOST")
        or (addresses[0] if addresses else "127.0.0.1")
    )
    if args.non_interactive:
        host = args.host or default_host
        port = args.port or int(values.get("QMT_RPYC_PORT", 18812))
    else:
        if addresses:
            print("Detected private addresses: {}".format(
                ", ".join(addresses)
            ))
        host = args.host or input(
            "Listen address [{}]: ".format(default_host)
        ).strip() or default_host
        port = args.port or int(input(
            "Listen port [{}]: ".format(
                values.get("QMT_RPYC_PORT", 18812)
            )
        ).strip() or values.get("QMT_RPYC_PORT", 18812))

    existing_key = values.get("QMT_RPYC_AUTH_KEY")
    auth_key = os.environ.get("QMT_RPYC_AUTH_KEY") or existing_key
    generated = False
    if not valid_auth_key(auth_key):
        candidate = secrets.token_urlsafe(32)
        if args.non_interactive:
            auth_key = candidate
            generated = True
        else:
            choice = input(
                "Press Enter to generate a strong key, or type 'c' "
                "to enter a custom key: "
            ).strip().lower()
            if choice == "c":
                auth_key = getpass.getpass("Custom authentication key: ")
                if not valid_auth_key(auth_key):
                    raise ValueError(
                        "custom authentication key must not be empty or use "
                        "the placeholder"
                    )
            else:
                auth_key = candidate
                generated = True

    default_qmt_path = (
        values.get("QMT_PATH") or detected.get("qmt_path") or ""
    )
    default_account = (
        values.get("QMT_ACCOUNT_ID")
        or detected.get("account_id") or ""
    )
    if not adapter.requires_native_sdk:
        qmt_path, account = values.get("QMT_PATH", ""), values.get("QMT_ACCOUNT_ID", "")
    elif args.non_interactive:
        qmt_path = args.qmt_path or default_qmt_path
        account = (
            args.account if args.account is not None else default_account
        )
    else:
        qmt_path = args.qmt_path or input(
            "QMT userdata_mini path [{}]: ".format(default_qmt_path)
        ).strip() or default_qmt_path
        account = (
            args.account if args.account is not None else input(
                "Account ID (empty for market-data only) [{}]: ".format(
                    default_account
                )
            ).strip() or default_account
        )

    values.update({
        "QMT_RPYC_HOST": host,
        "QMT_RPYC_PORT": str(port),
        "QMT_RPYC_AUTH_KEY": auth_key,
        "QMT_PATH": qmt_path,
        "QMT_SESSION_ID": values.get("QMT_SESSION_ID", "1"),
        "QMT_ACCOUNT_ID": account,
    })
    sdk_path = args.xtquant_path if args.xtquant_path is not None else configured_sdk_path(values)
    if adapter.requires_native_sdk and not args.non_interactive and args.xtquant_path is None:
        sdk_path = input("xtquant package directory (empty uses Python imports) [{}]: ".format(sdk_path)).strip() or sdk_path
    if adapter.requires_native_sdk and sdk_path:
        validate_sdk_path(sdk_path)
    values['QMT_XTQUANT_PATH'] = sdk_path
    _write_env(target, values)

    wired = False
    xtquant_site = detected.get("xtquant_site")
    if adapter.requires_native_sdk and not sdk_path and xtquant_site and (
        args.yes or (
            not args.non_interactive and _confirm(
                "Wire detected xtquant from {}?".format(xtquant_site),
                default=True,
            )
        )
    ):
        wire_xtquant(xtquant_site)
        wired = True

    result = {
        "status": "ok",
        "config_path": str(target),
        "summary": _masked_summary(values),
        "xtquant_wired": wired,
        "next_steps": [
            _next_command(args, "check"),
            _next_command(args, "start"),
        ],
    }
    if generated:
        result["auth_key_once"] = auth_key
    _emit(result, args.compact)


def _selected_adapter(values):
    from qmt_rpyc.adapters.registry import DEFAULT_ADAPTER, select_adapter
    return select_adapter(os.environ.get("QMT_RPYC_ADAPTER", values.get("QMT_RPYC_ADAPTER", DEFAULT_ADAPTER)))


def _validate_values(values):
    errors = []
    adapter = None
    try:
        adapter = _selected_adapter(values)
    except ValueError as exc:
        errors.append(str(exc))
    auth_key = values.get("QMT_RPYC_AUTH_KEY", "")
    if not valid_auth_key(auth_key):
        errors.append(
            "QMT_RPYC_AUTH_KEY is missing or still uses the placeholder"
        )
    if (adapter is None or adapter.requires_native_sdk) and not values.get("QMT_PATH"):
        errors.append("QMT_PATH is missing")
    try:
        port = int(values.get("QMT_RPYC_PORT", "18812"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        errors.append("QMT_RPYC_PORT must be between 1 and 65535")
    if bool(values.get("QMT_RPYC_TLS_KEY")) != bool(
            values.get("QMT_RPYC_TLS_CERT")):
        errors.append("TLS key and certificate must be configured together")
    return errors


def _cmd_check(args):
    path = config_path(args.config)
    values = _read_env(path)
    checks = []

    def add(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    add("config", path.exists(), str(path))
    errors = _validate_values(values)
    add("config_values", not errors, "; ".join(errors))
    version_ok = sys.version_info[:2] in ((3, 10), (3, 11))
    add("python", version_ok, sys.version.split()[0])
    try:
        adapter = _selected_adapter(values)
    except ValueError:
        adapter = None
    if adapter is None:
        xtquant_ok = False
        add('adapter', False, 'Invalid adapter selection')
    elif not adapter.requires_native_sdk:
        xtquant_ok = True
        add("strategy_bridge_mode", True, "Independent of xtquant and MiniQMT; checking the local strategy pipe")
    else:
        detected = detect_environment()
        add("MiniQMT", bool(detected.get("miniqmt")),
            "XtMiniQmt.exe is not running" if not detected.get("miniqmt") else "")
        try:
            xtquant = load_sdk(configured_sdk_path(values))
            xtquant_ok = True
            origin = getattr(xtquant, "__file__", None)
            detail = str(Path(origin).resolve()) if origin else "module path unavailable"
        except Exception as e:
            xtquant_ok = False
            detail = str(e)
        add("xtquant", xtquant_ok, detail)
    connected = False
    if not errors and xtquant_ok:
        try:
            from qmt_rpyc.adapters.registry import DEFAULT_ADAPTER, select_adapter
            adapter = select_adapter(os.environ.get("QMT_RPYC_ADAPTER", values.get("QMT_RPYC_ADAPTER", DEFAULT_ADAPTER)))
            ConnectionManager = adapter.connection_type()
            manager = ConnectionManager(
                path=values.get("QMT_PATH", ""),
                session_id=int(values.get("QMT_SESSION_ID", "1")),
                account_id=values.get("QMT_ACCOUNT_ID", ""),
                **({} if adapter.requires_native_sdk else {
                    "pipe_name": os.environ.get("QMT_RPYC_BIGQMT_PIPE", values.get("QMT_RPYC_BIGQMT_PIPE", "qmt_rpyc_bridge_v1")),
                    "request_timeout": int(os.environ.get("QMT_RPYC_BIGQMT_TIMEOUT", values.get("QMT_RPYC_BIGQMT_TIMEOUT", "30"))),
                }),
            )
            try:
                # probe() blocks for one real attempt; start() only schedules
                # a background connection and would always report success.
                connected = manager.probe()
            finally:
                manager.stop()
        except Exception as e:
            detail = "{}: {}".format(type(e).__name__, e)
        else:
            detail = "" if connected else "initial QMT connection failed"
    else:
        detail = "skipped because configuration or xtquant failed"
    add("qmt_connection", connected, detail)

    port_ok = False
    if not errors:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.bind((
                values.get("QMT_RPYC_HOST", "127.0.0.1"),
                int(values.get("QMT_RPYC_PORT", "18812")),
            ))
            port_ok = True
            detail = ""
        except OSError as e:
            detail = str(e)
        finally:
            listener.close()
    else:
        detail = "skipped because configuration failed"
    add("listen_port", port_ok, detail)

    ok = all(item["ok"] for item in checks)
    _emit({"status": "ok" if ok else "failed", "checks": checks},
          args.compact)
    return 0 if ok else 1


def _cmd_start(args):
    from qmt_rpyc.cli import processes
    if args.foreground:
        from qmt_rpyc.server.managed import run
        return run(str(config_path(args.config).resolve()), args.verbose, foreground=True)
    with processes.command_lock():
        result = processes.launch(config_path(args.config), args.verbose)
    _emit(result, args.compact)
    return 0


def _cmd_stop(args):
    from qmt_rpyc.cli import processes
    with processes.command_lock():
        result = processes.stop(args.timeout)
    _emit(result, args.compact)
    return 0


def _cmd_restart(args):
    from qmt_rpyc.cli import processes
    with processes.command_lock():
        state = processes.read_state()
        path = args.config or state.get('config') or config_path()
        environment = dict(os.environ)
        # Reproduce original overrides, rather than unrelated shell overrides.
        for key in list(environment):
            if key.startswith(('QMT_', 'QMT_RPYC_')):
                del environment[key]
        environment.update(state.get('overrides', {}))
        processes.stop(args.timeout)
        result = processes.launch(path, state.get('verbose', False), environment=environment)
    _emit(result, args.compact)
    return 0


def _client_from_server_config(path):
    values = _read_env(path)
    tls = None
    server_cert = values.get("QMT_RPYC_TLS_CERT")
    if server_cert:
        if values.get("QMT_RPYC_TLS_CA"):
            raise RuntimeError(
                "status cannot probe an mTLS server without a distinct client "
                "certificate; use qmt-rpyc-client check with a TLS profile"
            )
        tls = {"ca_certs": server_cert}
    host = values.get("QMT_RPYC_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    return QmtClient.connect(
        host,
        port=int(values.get("QMT_RPYC_PORT", "18812")),
        auth_key=values.get("QMT_RPYC_AUTH_KEY"),
        timeout=3,
        tls_config=tls,
    )


def _cmd_status(args):
    from qmt_rpyc.cli import processes
    result = processes.status()
    if result.get('rpc_ready'):
        state = processes.read_state()
        try:
            with _client_from_server_config(state.get('config') or config_path(args.config)) as client:
                from qmt_rpyc.transport.codec import encode
                result['health'] = encode(client.system.get_health())
        except Exception as exc:
            result['health_error'] = str(exc)
    _emit(result, args.compact)
    return 0


def _cmd_update(args):
    from qmt_rpyc.cli.update import execute
    _emit(execute(args, server=True, config=str(config_path(args.config).resolve())), args.compact)
    return 0


def _cmd_uninstall(args):
    if not args.yes:
        if not _confirm(
                "Uninstall the managed qmt-rpyc environment?",
                default=False):
            return 1
    root = server_dir()
    venv = root / "venv"
    launcher = root / "qmt-rpyc-server.bat"
    if sys.platform == "win32":
        script = Path(os.environ.get("TEMP", str(root))) / (
            "qmt-rpyc-uninstall-{}.bat".format(os.getpid())
        )
        lines = [
            "@echo off",
            "ping 127.0.0.1 -n 3 >nul",
            'rmdir /s /q "{}"'.format(venv),
            'del /q "{}" 2>nul'.format(launcher),
        ]
        if args.purge:
            lines.append('rmdir /s /q "{}"'.format(root))
        lines.append('del /q "%~f0"')
        script.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
        subprocess.Popen(
            ["cmd.exe", "/c", str(script)],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
    else:
        if venv.exists():
            shutil.rmtree(venv)
        if launcher.exists():
            launcher.unlink()
        if args.purge and root.exists():
            shutil.rmtree(root)
    _emit({"status": "uninstall_scheduled", "purge": args.purge},
          args.compact)
    return 0


def _cmd_firewall(args):
    if sys.platform != "win32":
        raise RuntimeError("Windows firewall commands require Windows")
    values = _read_env(config_path(args.config))
    port = str(values.get("QMT_RPYC_PORT", "18812"))
    if args.firewall_command == "add":
        command = [
            "netsh", "advfirewall", "firewall", "add", "rule",
            "name={}".format(FIREWALL_RULE),
            "dir=in", "action=allow", "protocol=TCP",
            "localport={}".format(port), "profile=private",
        ]
    else:
        command = [
            "netsh", "advfirewall", "firewall", "delete", "rule",
            "name={}".format(FIREWALL_RULE),
        ]
    subprocess.run(command, check=True)
    _emit({"status": "ok", "command": args.firewall_command,
           "port": port}, args.compact)


def _cmd_xtquant(args):
    path = managed_xtquant_path()
    if args.xtquant_command == "check":
        try:
            values = _read_env(config_path(args.config))
            xtquant = load_sdk(configured_sdk_path(values))
            version = getattr(xtquant, "__version__", None)
            ok = True
            origin = getattr(xtquant, "__file__", None)
            detail = str(Path(origin).resolve()) if origin else "module path unavailable"
        except Exception as e:
            ok = False
            version = None
            detail = str(e)
        _emit({"status": "ok" if ok else "failed", "path": detail,
               "version": version}, args.compact)
        return 0 if ok else 1
    values = _read_env(config_path(args.config))
    if configured_sdk_path(values):
        raise ValueError('QMT_XTQUANT_PATH is configured; edit that setting instead of repairing an unused link')
    detected = detect_environment()
    site = args.source or detected.get("xtquant_site")
    if not site:
        raise RuntimeError("could not locate xtquant in MiniQMT")
    target = wire_xtquant(site)
    _emit({"status": "ok", "target": str(target)}, args.compact)


def _cmd_api_dump(args):
    from qmt_rpyc.adapters.registry import DEFAULT_ADAPTER, select_adapter
    values = _read_env(config_path(args.config))
    adapter = select_adapter(os.environ.get("QMT_RPYC_ADAPTER", values.get("QMT_RPYC_ADAPTER", DEFAULT_ADAPTER)))
    load_sdk(configured_sdk_path(values))
    surface = adapter.discover()
    if args.without_docs:
        for section, key in (
            ("xtdata", "functions"),
            ("XtQuantTrader", "methods"),
        ):
            for meta in surface.get(section, {}).get(key, {}).values():
                meta.pop("doc", None)
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "schema_version": surface.get("schema_version"),
        "runtime": {
            "platform": sys.platform,
            "python_version": sys.version.split()[0],
        },
        "api_surface": surface,
    }
    output = Path(args.output or "api_surface.json")
    if output.exists() and not args.force:
        raise FileExistsError(
            "{} already exists; pass --force".format(output)
        )
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _emit({"status": "ok", "output": str(output)}, args.compact)


def build_parser():
    parser = ArgumentParser(
        prog="qmt-rpyc-server",
        description=(
            "Configure, validate, and run the qmt-rpyc server beside a "
            "Windows QMT/MiniQMT installation."
        ),
        epilog="""Getting started (Windows, Python 3.10 or 3.11):
  qmt-rpyc-server init
  qmt-rpyc-server check
  qmt-rpyc-server start

'start' runs in the foreground. Press Ctrl-C to shut down cleanly.
Global options must appear before COMMAND. Run
'qmt-rpyc-server COMMAND --help' for command-specific details.""",
    )
    add_version_argument(parser)
    parser.add_argument(
        "--config",
        help=(
            "server config.env path; must appear before COMMAND "
            "(default: platform application-data directory)"
        ),
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact single-line JSON; must appear before COMMAND",
    )
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        title="commands",
        metavar="COMMAND",
    )

    init = sub.add_parser(
        "init",
        help="run the server configuration guide",
        description=(
            "Create or update config.env. The guide can import .env from the "
            "current directory, detect MiniQMT and xtquant, and generate or "
            "accept a shared authentication key."
        ),
        epilog="""Examples:
  qmt-rpyc-server init
  qmt-rpyc-server init --host 192.168.1.20 --account ACCOUNT_ID
  qmt-rpyc-server --config D:\\qmt\\config.env init --import-env D:\\qmt\\.env
  qmt-rpyc-server init --non-interactive --yes""",
    )
    init.add_argument(
        "--import-env",
        help="import initial values from this .env file",
    )
    init.add_argument("--adapter", choices=("xtquant_2.0.6.1", "bigqmt"),
                      help="persist the QMT adapter selection")
    init.add_argument("--host", help="server listen address")
    init.add_argument("--port", type=int, help="server listen port")
    init.add_argument(
        "--qmt-path",
        help="QMT userdata_mini directory",
    )
    init.add_argument("--xtquant-path", help="absolute xtquant package directory; overrides automatic SDK import")
    init.add_argument(
        "--account",
        help="stock account ID; omit for market-data-only operation",
    )
    init.add_argument(
        "--yes",
        action="store_true",
        help="accept safe defaults and detected configuration",
    )
    init.add_argument(
        "--non-interactive",
        action="store_true",
        help="do not prompt; importing an existing .env also requires --yes",
    )
    init.set_defaults(func=_cmd_init)

    check = sub.add_parser(
        "check",
        help="run server configuration and QMT connection checks",
        description=(
            "Validate config.env, Python, MiniQMT, xtquant, the initial QMT "
            "connection, and availability of the listen address."
        ),
        epilog="""Run this before each manual start:
  qmt-rpyc-server check

If configuration is missing or invalid, run:
  qmt-rpyc-server init""",
    )
    check.set_defaults(func=_cmd_check)
    start = sub.add_parser(
        "start",
        help="start the server in the background",
        description=(
            "Start one background server for this Python environment. "
            "Use --foreground to debug in the current console."
        ),
        epilog="""Example:
  qmt-rpyc-server start

Use qmt-rpyc-server stop to drain requests and stop the background service.
For console debugging: qmt-rpyc-server start --foreground""",
    )
    start.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose server logging",
    )
    start.add_argument("--foreground", action="store_true", help="run in the current console")
    start.set_defaults(func=_cmd_start)
    for name, handler in (("stop", _cmd_stop), ("restart", _cmd_restart)):
        child = sub.add_parser(name, help=name + " the local server",
                               description=name.capitalize() + " the local server after draining requests.")
        child.add_argument("--timeout", type=float, default=60, help="drain timeout in seconds (default: 60)")
        child.set_defaults(func=handler)
    status = sub.add_parser(
        "status",
        help="query the health of a running local server",
        description=(
            "Report local process state even when RPC is unavailable, "
            "and include live health when available."
        ),
        epilog="""Example:
  qmt-rpyc-server status""",
    )
    status.set_defaults(func=_cmd_status)
    path = sub.add_parser(
        "config-path",
        help="print the effective server configuration path",
        description="Print the config.env path selected by global options.",
    )
    path.set_defaults(func=lambda args: _emit(
        {"config_path": str(config_path(args.config))}, args.compact
    ))

    update = sub.add_parser(
        "update", help="upgrade this local environment without starting the server",
        description="Drain and stop the local server, install and verify the target package, then print next steps.",
        epilog="Examples:\n  qmt-rpyc-server update\n  qmt-rpyc-server update --pre\n  qmt-rpyc-server update --version 0.9.0",
    )
    from qmt_rpyc.cli.update import add_arguments
    add_arguments(update)
    update.set_defaults(func=_cmd_update)

    uninstall = sub.add_parser(
        "uninstall",
        help="remove the managed server environment",
        description=(
            "Remove the managed virtual environment and launcher. "
            "Configuration and logs remain unless --purge is supplied."
        ),
        epilog="""Examples:
  qmt-rpyc-server uninstall
  qmt-rpyc-server uninstall --purge --yes""",
    )
    uninstall.add_argument(
        "--purge",
        action="store_true",
        help="also remove managed configuration and logs",
    )
    uninstall.add_argument(
        "--yes",
        action="store_true",
        help="uninstall without asking for confirmation",
    )
    uninstall.set_defaults(func=_cmd_uninstall)

    firewall = sub.add_parser(
        "firewall",
        help="manage the explicit Windows private-network firewall rule",
        description=(
            "Add or remove the qmt-rpyc inbound TCP rule for Windows private "
            "networks. Administrator privileges are normally required."
        ),
        epilog="""Examples (run in an elevated terminal):
  qmt-rpyc-server firewall add
  qmt-rpyc-server firewall remove""",
    )
    firewall_sub = firewall.add_subparsers(
        dest="firewall_command",
        required=True,
        title="firewall commands",
        metavar="COMMAND",
    )
    firewall_sub.add_parser(
        "add",
        help="allow the configured TCP port on private networks",
        description=(
            "Add the qmt-rpyc inbound TCP rule for the configured port."
        ),
    )
    firewall_sub.add_parser(
        "remove",
        help="remove the qmt-rpyc firewall rule",
        description="Remove the qmt-rpyc Windows firewall rule.",
    )
    firewall.set_defaults(func=_cmd_firewall)

    xtquant = sub.add_parser(
        "xtquant",
        help="check or repair access to the deployed xtquant SDK",
        description=(
            "Inspect the managed xtquant link or wire the SDK discovered in "
            "the local MiniQMT installation."
        ),
        epilog="""Examples:
  qmt-rpyc-server xtquant check
  qmt-rpyc-server xtquant repair""",
    )
    xtquant_sub = xtquant.add_subparsers(
        dest="xtquant_command",
        required=True,
        title="xtquant commands",
        metavar="COMMAND",
    )
    xtquant_sub.add_parser(
        "check",
        help="verify that xtquant can be imported",
        description="Report the imported xtquant location and version.",
    )
    repair = xtquant_sub.add_parser(
        "repair",
        help="wire the MiniQMT xtquant package into this environment",
        description=(
            "Create or repair the managed link to MiniQMT's xtquant package."
        ),
    )
    repair.add_argument(
        "--source",
        help="site-packages directory containing xtquant; otherwise detect it",
    )
    xtquant.set_defaults(func=_cmd_xtquant)

    api = sub.add_parser(
        "api",
        help="inspect the local deployed xtquant API surface",
        description=(
            "Discover the broker-customized local xtquant API and export its "
            "metadata for diagnostics or compatibility review."
        ),
        epilog="""Example:
  qmt-rpyc-server api dump --output api_surface.json""",
    )
    api_sub = api.add_subparsers(
        dest="api_command",
        required=True,
        title="API commands",
        metavar="COMMAND",
    )
    dump = api_sub.add_parser(
        "dump",
        help="write the discovered API surface to JSON",
        description=(
            "Discover the local API surface and write a portable JSON report."
        ),
    )
    dump.add_argument(
        "--output",
        help="output JSON path",
    )
    dump.add_argument(
        "--without-docs",
        action="store_true",
        help="omit function and method documentation",
    )
    dump.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing output file",
    )
    api.set_defaults(func=_cmd_api_dump)
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except KeyboardInterrupt:
        return emit_interrupted(
            _emit, getattr(args, "compact", False)
        )
    except ModuleNotFoundError as e:
        missing = (e.name or "").split(".", 1)[0]
        payload = {
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": str(e),
        }
        if missing in ("dotenv", "numpy", "pandas", "psutil"):
            payload["hint"] = (
                "Install server dependencies with: python -m pip install "
                "\"qmt-rpyc[server]\". The server requires Python 3.10 or 3.11."
            )
        _emit(payload, getattr(args, "compact", False), sys.stderr)
        return 1
    except Exception as e:
        _emit({
            "status": "error",
            "error_type": type(e).__name__,
            "error_message": str(e),
        }, getattr(args, "compact", False), sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
