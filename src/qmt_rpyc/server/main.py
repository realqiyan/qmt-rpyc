"""Compose providers and run the authenticated RPC server."""
import faulthandler
import logging
import os
import sys
from pathlib import Path

from qmt_rpyc.adapters.registry import DEFAULT_ADAPTER, select_adapter
from qmt_rpyc.server.config import (
    default_server_dir,
    load_config as _load_config,
    validate_config as _validate_config,
)
from qmt_rpyc.server.logging_config import setup_logging
from qmt_rpyc.server.redaction import mask_account
from qmt_rpyc.version import __version__
from qmt_rpyc.contracts.operations import CONTRACT_VERSION

logger = logging.getLogger(__name__)
_crash_fp = None


def _enable_crash_log(log_dir):
    global _crash_fp
    try:
        path = Path(log_dir) / "crash.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        _crash_fp = path.open("a", encoding="utf-8")
        _crash_fp.write(
            "\n==== rpyc server start pid={} ====\n".format(os.getpid())
        )
        _crash_fp.flush()
        faulthandler.enable(file=_crash_fp, all_threads=True)
    except Exception:
        faulthandler.enable()


def _print_startup_info(cfg, cm):
    """Print a one-shot startup summary to stdout. No sensitive fields."""
    host = cfg["host"]
    port = cfg["port"]
    auth = "on" if cfg["auth_key"] else "off"
    tls = "on" if cfg.get("tls_keyfile") and cfg.get("tls_certfile") else "off"
    health = cm.get_health_status()
    qmt = health["connection_state"]
    account = mask_account(cfg.get("qmt_account_id", ""))

    lines = [
        "=" * 56,
        "  qmt-rpyc server",
        "=" * 56,
        "  Version  : {}".format(__version__),
        "  Contract : {}".format(CONTRACT_VERSION),
        "  Python   : {}".format(sys.executable),
        "  Package  : {}".format(Path(__file__).resolve().parents[1]),
        "  Adapter  : {}".format(cfg.get("adapter", DEFAULT_ADAPTER)),
        "  Listen   : {}:{}".format(host, port),
        "  Auth     : {}".format(auth),
        "  TLS      : {}".format(tls),
        "  SDK debug: {}".format("on" if cfg.get("debug", False) else "off"),
        "  QMT      : {}".format(qmt),
        "  Failures : {}".format(health["consecutive_failures"]),
        "  Retry at : {}".format(health["next_retry_at"] or "(none)"),
        "  Error    : {}".format(health["last_connection_error"] or "(none)"),
        "  Account  : {}".format(account),
        "  Log dir  : {}".format(cfg.get("log_dir", "logs")),
        "=" * 56,
    ]
    if cfg.get("adapter", DEFAULT_ADAPTER) == 'bigqmt':
        from qmt_rpyc.adapters.bigqmt.bridge_queue import BRIDGE_VERSION
        lines.insert(5, "  Bridge   : {}".format(BRIDGE_VERSION))
    for line in lines:
        print(line)


def start_server(cfg, tls=None, runtime=None):
    _validate_config(cfg)
    logger.info("Starting qmt-rpyc server %s", __version__)
    cfg = dict(cfg)
    auth_key = cfg.get("auth_key")
    if (not isinstance(auth_key, str) or not auth_key.strip()
            or auth_key == "your-secret-key-here"):
        cfg["auth_key"] = None
    host = cfg["host"]
    port = cfg["port"]
    auth_key = cfg["auth_key"]

    import socket

    from rpyc.utils.server import ThreadedServer

    adapter = select_adapter(cfg.get("adapter", DEFAULT_ADAPTER))
    if adapter.requires_native_sdk:
        from qmt_rpyc.server.sdk_loader import configured_sdk_path, load_sdk
        sdk_path = cfg.get("xtquant_path", configured_sdk_path())
        if sdk_path:
            load_sdk(sdk_path)
    ConnectionManager = adapter.connection_type()
    from qmt_rpyc.server.auth_limiter import rate_limiter
    from qmt_rpyc.server.dispatch import Dispatcher
    from qmt_rpyc.server.downloads import DownloadTaskManager
    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.transport.auth import make_server_authenticator

    config = {
        "allow_public_attrs": False,
        "allow_pickle": False,
        "sync_request_timeout": 300,
    }

    cm = None
    dm = None
    server = None
    listener_socket = None
    interrupted = False
    try:
        bridge_options = {} if adapter.requires_native_sdk else dict(
            pipe_name=cfg.get('bigqmt_pipe', 'qmt_rpyc_bridge_v1'),
            request_timeout=cfg.get('bigqmt_timeout', 30))
        cm = ConnectionManager(
            path=cfg["qmt_path"],
            session_id=cfg["qmt_session_id"],
            account_id=cfg["qmt_account_id"],
            heartbeat_interval=cfg["heartbeat_interval"],
            heartbeat_timeout=cfg["heartbeat_timeout"],
            heartbeat_max_failures=cfg["heartbeat_max_failures"],
            reconnect_max_attempts=cfg["reconnect_max_attempts"],
            **bridge_options,
        )
        dm = DownloadTaskManager(max_workers=2)

        XtquantService._require_auth = auth_key is not None
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._active_clients = 0
        XtquantService._request_gate = runtime.gate if runtime else None
        # Validate SDK imports before opening the listener. Local installation
        # failures are fatal; Trader construction and connection run separately.
        workers = int(os.environ.get('QMT_BATCH_MAX_WORKERS', '8'))
        if workers < 1:
            raise ValueError('QMT_BATCH_MAX_WORKERS must be positive')
        from qmt_rpyc.storage.policy import StorageConfig
        from qmt_rpyc.storage.repository import Repository
        from qmt_rpyc.storage.providers import decorate
        storage_config = StorageConfig.from_config(cfg, default_server_dir())
        repository = Repository(storage_config.path, storage_config.source_scope, storage_config.busy_timeout)
        source = adapter.create_providers(cm, workers)
        providers = decorate(source, repository, storage_config.policies, workers=workers,
            **adapter.storage_strategies(source))
        def health():
            result = dict(cm.get_health_status())
            result['persistent_data'] = repository.health()
            return result
        XtquantService._dispatcher = Dispatcher(providers, dm,
            health, lambda: XtquantService._active_clients,
            gate=runtime.gate if runtime else None)

        XtquantService._debug_handler = None
        if cfg.get('debug', False):
            XtquantService._debug_handler = adapter.create_debug(cm)

        authenticator = (
            make_server_authenticator(auth_key, rate_limiter)
            if auth_key is not None else None
        )

        if tls:
            import ssl
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(
                certfile=tls["certfile"],
                keyfile=tls["keyfile"],
            )
            if tls.get("ca_certs"):
                context.load_verify_locations(cafile=tls["ca_certs"])
                context.verify_mode = ssl.CERT_REQUIRED

            listener_socket = socket.socket(
                socket.AF_INET, socket.SOCK_STREAM)
            listener_socket.setsockopt(
                socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener_socket.bind((host, port))
            listener_socket.listen(5)
            listener_socket = context.wrap_socket(
                listener_socket, server_side=True)
            server = ThreadedServer(
                XtquantService, hostname=host, port=port,
                protocol_config=config, listener=listener_socket,
                authenticator=authenticator)
        else:
            server = ThreadedServer(
                XtquantService, hostname=host, port=port,
                protocol_config=config, authenticator=authenticator)

        if runtime is not None:
            runtime.attach(server, dm, cm)
        cm.start()
        _print_startup_info(cfg, cm)
        logger.info("Starting RPyC server on %s:%d", host, port)
        server.start()
    except KeyboardInterrupt:
        interrupted = True
        print("Server shutting down...")
        logger.info("Shutting down...")
    finally:
        if server is not None:
            server.close()
        elif listener_socket is not None:
            listener_socket.close()
        if dm is not None:
            dm.shutdown()
        if cm is not None:
            cm.stop()
        XtquantService._request_gate = None
        print("Server stopped.")
        logger.info("Server stopped")
    return 130 if interrupted else 0


def main(config_path=None, verbose=False, runtime=None):
    cfg = _load_config(config_path)
    _validate_config(cfg)
    setup_logging(cfg["log_dir"], verbose=verbose)
    _enable_crash_log(cfg["log_dir"])

    tls = None
    if cfg["tls_keyfile"] and cfg["tls_certfile"]:
        tls = {
            "keyfile": cfg["tls_keyfile"],
            "certfile": cfg["tls_certfile"],
            "ca_certs": cfg["tls_ca_certs"],
        }

    if runtime is None:
        return start_server(cfg, tls)
    return start_server(cfg, tls, runtime=runtime)


if __name__ == "__main__":
    sys.exit(main())
