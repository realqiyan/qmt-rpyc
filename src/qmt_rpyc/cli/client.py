"""CLI for the fixed, typed operation contract."""
import argparse
import getpass
import json
import os
import sys

from qmt_rpyc import QmtClient
from qmt_rpyc.cli.common import ArgumentParser, add_version_argument, emit_interrupted
from qmt_rpyc.client.debug import DebugClient, DebugError
from qmt_rpyc.config import (
    DEFAULT_PROFILE,
    delete_profile,
    load_profiles,
    probe_keyring,
    save_profile,
)
from qmt_rpyc.contracts.downloads import TaskRef
from qmt_rpyc.contracts.errors import ProtocolError, QmtAuthError, QmtError
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.cli.api_help import describe_api, list_apis, render_description, render_list
from qmt_rpyc.transport.codec import decode, encode

EXIT_USAGE = 2
EXIT_CONFIRMATION = 3
EXIT_CONNECTION = 4
EXIT_REMOTE = 5


def _emit(value, compact=False, stream=None):
    print(json.dumps(encode(value), ensure_ascii=False, indent=None if compact else 2), file=stream or sys.stdout)


def _connect(args):
    overrides = {
        "host": getattr(args, "host", None),
        "port": getattr(args, "port", None),
        "timeout": getattr(args, "timeout", None),
    }
    return QmtClient.connect_profile(args.profile, **overrides)


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
    if not secret.strip() or secret == "your-secret-key-here":
        raise ValueError(
            "authentication key must not be empty or use the placeholder"
        )
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
        "next_steps": [
            "qmt-rpyc-client check --profile {}".format(profile),
            "qmt-rpyc-client api --profile {} list market".format(profile),
        ],
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


def _add_connection_options(parser):
    parser.add_argument(
        "--profile",
        default=DEFAULT_PROFILE,
        help="client profile to use (default: %(default)s)",
    )
    parser.add_argument(
        "--host",
        help="temporarily override the profile's server host",
    )
    parser.add_argument(
        "--port",
        type=int,
        help="temporarily override the profile's server port",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="temporarily override the RPC timeout in seconds",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="emit compact single-line JSON",
    )


def _cmd_check(args):
    with _connect(args) as client:
        health = client.system.get_health()
        _emit(dict(status='ok' if health.connected else 'degraded', profile=args.profile,
                   health=health, capabilities=client.capabilities()), args.compact)
        return 0 if health.connected else EXIT_CONNECTION


def _cmd_api(args):
    if args.api_command == 'list':
        result = list_apis(args.group)
        render = render_list
    else:
        result = describe_api(args.operation)
        render = render_description
    if args.json or args.compact:
        _emit(result, args.compact)
    else:
        print(render(result))


def _read_request(args):
    if args.request:
        if args.request == '-':
            value = json.load(sys.stdin)
        else:
            with open(args.request, encoding='utf-8') as stream:
                value = json.load(stream)
        if type(value) is not dict or set(value) != {'operation', 'payload'}:
            raise ValueError('request must contain operation and payload')
        return value['operation'], value['payload']
    return args.operation, json.loads(args.payload)


def _execute(args, operation, payload):
    if type(operation) is not str or operation not in OPERATIONS:
        raise ValueError('unknown operation')
    spec = OPERATIONS[operation]
    request = decode(spec.request_type, payload)
    if spec.mutation and operation.startswith('trading.') and not args.confirm_trading:
        _emit(dict(status='confirmation_required', operation=operation), args.compact, sys.stderr)
        return EXIT_CONFIRMATION
    with _connect(args) as client:
        _emit(client._invoke(operation, request), args.compact)
    return 0


def _cmd_call(args):
    return _execute(args, *_read_request(args))


def _cmd_download(args):
    if args.download_command == 'start':
        return _execute(args, 'downloads.start_' + args.kind, json.loads(args.payload))
    with _connect(args) as client:
        status = client.downloads.get_task(args.task_id)
        if args.download_command == 'wait':
            status = client.downloads.handle(TaskRef(status.task_id, status.kind)).wait(args.wait_timeout, args.interval)
        _emit(status, args.compact)
        return EXIT_REMOTE if status.status == 'failed' else 0


def _cmd_self_test(args):
    with _connect(args) as client:
        result = client.self_test()
        _emit(result, args.compact)
        return EXIT_REMOTE if result['failed'] else 0


def _cmd_debug(args):
    if (args.debug_command == 'call' and args.target.startswith('trader.')
            and not args.target.split('.', 1)[1].startswith('query_') and not args.confirm_trading):
        _emit(dict(status='confirmation_required', target=args.target), args.compact, sys.stderr)
        return EXIT_CONFIRMATION
    try:
        with DebugClient.connect_profile(args.profile, host=args.host, port=args.port, timeout=args.timeout) as client:
            if args.debug_command == 'describe':
                result = client.describe(args.target)
            else:
                result = client.call(args.target, json.loads(args.args), json.loads(args.kwargs))
            _emit(result, args.compact)
            return 0
    except DebugError as exc:
        _emit(dict(status='error', error=exc.error), args.compact, sys.stderr)
        return EXIT_REMOTE


def build_parser():
    parser = ArgumentParser(prog='qmt-rpyc-client', description='Inspect and invoke the typed QMT operation contract.')
    add_version_argument(parser)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='save a connection profile')
    _add_connection_options(init)
    for flag in ('ca-certs', 'certfile', 'keyfile'):
        init.add_argument('--' + flag)
    for flag in ('store-plaintext', 'non-interactive'):
        init.add_argument('--' + flag, action='store_true')
    init.set_defaults(func=_cmd_init)
    profile = sub.add_parser('profile', help='manage saved profiles')
    profile.add_argument('--compact', action='store_true')
    profiles = profile.add_subparsers(dest='profile_command', required=True)
    profiles.add_parser('list')
    show = profiles.add_parser('show'); show.add_argument('name')
    delete = profiles.add_parser('delete'); delete.add_argument('name'); delete.add_argument('--yes', action='store_true')
    profile.set_defaults(func=_cmd_profile)
    for command, handler in [('check', _cmd_check), ('self-test', _cmd_self_test)]:
        child = sub.add_parser(command)
        _add_connection_options(child)
        child.set_defaults(func=handler)
    api = sub.add_parser('api', help='inspect local contract definitions without connecting')
    _add_connection_options(api)
    api.add_argument('--json', action='store_true', help='emit documented JSON instead of readable text')
    apis = api.add_subparsers(dest='api_command', required=True)
    listing = apis.add_parser('list'); listing.add_argument('group', nargs='?')
    describe = apis.add_parser('describe'); describe.add_argument('operation')
    for child in (listing, describe):
        child.add_argument('--json', action='store_true', default=argparse.SUPPRESS, help='emit documented JSON')
        child.add_argument('--compact', action='store_true', default=argparse.SUPPRESS, help='emit compact JSON')
    api.set_defaults(func=_cmd_api)
    call = sub.add_parser('call', help='invoke an operation with a typed JSON payload')
    _add_connection_options(call)
    call.add_argument('operation', nargs='?')
    call.add_argument('--payload', default='{}')
    call.add_argument('--request', help='JSON file with operation and payload; - reads stdin')
    call.add_argument('--confirm-trading', action='store_true')
    call.set_defaults(func=_cmd_call)
    download = sub.add_parser('download', help='start, inspect or wait for a download')
    _add_connection_options(download)
    commands = download.add_subparsers(dest='download_command', required=True)
    start = commands.add_parser('start')
    start.add_argument('kind', choices=('history', 'financials', 'sectors', 'index_weights'))
    start.add_argument('--payload', default='{}')
    status = commands.add_parser('status'); status.add_argument('task_id')
    wait = commands.add_parser('wait'); wait.add_argument('task_id')
    wait.add_argument('--wait-timeout', type=float, default=300)
    wait.add_argument('--interval', type=float, default=.5)
    download.set_defaults(func=_cmd_download, confirm_trading=False)
    debug = sub.add_parser('debug', help='inspect or call the raw SDK (server opt-in required)')
    _add_connection_options(debug)
    debug_sub = debug.add_subparsers(dest='debug_command', required=True)
    describe = debug_sub.add_parser('describe', help='inspect deployed signatures or constants')
    describe.add_argument('target', nargs='?', help='omit to inspect all SDK surfaces')
    raw = debug_sub.add_parser('call', help='forward an SDK call once, without contract projection')
    raw.add_argument('target', help='xtdata.NAME or trader.NAME')
    raw.add_argument('--args', default='[]', help='JSON positional arguments')
    raw.add_argument('--kwargs', default='{}', help='JSON keyword arguments')
    raw.add_argument('--confirm-trading', action='store_true')
    debug.set_defaults(func=_cmd_debug)
    return parser


def main(argv=None):
    try:
        args = build_parser().parse_args(argv)
        return args.func(args) or 0
    except KeyboardInterrupt:
        return emit_interrupted()
    except (QmtError, ProtocolError) as exc:
        value = dict(status='error', message=str(exc))
        if isinstance(exc, QmtError):
            value['error'] = exc.error
        _emit(value, stream=sys.stderr)
        return EXIT_REMOTE
    except (OSError, QmtAuthError) as exc:
        _emit(dict(status='error', message=str(exc)), stream=sys.stderr)
        return EXIT_CONNECTION
    except (ValueError, KeyError, RuntimeError) as exc:
        _emit(dict(status='error', message=str(exc)), stream=sys.stderr)
        return EXIT_USAGE
