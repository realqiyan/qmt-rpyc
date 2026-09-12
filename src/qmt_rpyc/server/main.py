# server/main.py
import os
import sys
import logging
import faulthandler
from pathlib import Path

import pandas
import numpy

import qmt_rpyc.server.datetime_patch

from qmt_rpyc.server.logging_config import setup_logging

logger = logging.getLogger(__name__)
_crash_fp = None


def default_server_dir():
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "qmt-rpyc"
    return Path.home() / ".qmt-rpyc"


def default_config_path():
    return default_server_dir() / "config.env"


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


def _env_int(name, default, minimum, maximum=None):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            expected = f">= {minimum}"
        else:
            expected = f"between {minimum} and {maximum}"
        raise ValueError(f"{name} must be {expected}, got {value}")
    return value


def _env_bool(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{name} must be a boolean, got {raw!r}")


def _load_config(config_path=None):
    from dotenv import load_dotenv
    path = Path(config_path) if config_path else default_config_path()
    load_dotenv(str(path), override=False)
    return {
        "host": os.environ.get("QMT_RPYC_HOST", "0.0.0.0"),
        "port": _env_int("QMT_RPYC_PORT", 18812, 1, 65535),
        "auth_key": os.environ.get("QMT_RPYC_AUTH_KEY"),
        "allow_insecure": _env_bool("QMT_RPYC_ALLOW_INSECURE"),
        "qmt_path": os.environ.get("QMT_PATH", ""),
        "qmt_session_id": _env_int("QMT_SESSION_ID", 1, 0),
        "qmt_account_id": os.environ.get("QMT_ACCOUNT_ID", ""),
        "tls_keyfile": os.environ.get("QMT_RPYC_TLS_KEY"),
        "tls_certfile": os.environ.get("QMT_RPYC_TLS_CERT"),
        "tls_ca_certs": os.environ.get("QMT_RPYC_TLS_CA"),
        "log_dir": os.environ.get(
            "QMT_RPYC_LOG_DIR", str(default_server_dir() / "logs")
        ),
        "config_path": str(path),
        "heartbeat_interval": _env_int(
            "QMT_HEARTBEAT_INTERVAL", 30, 1),
        "heartbeat_timeout": _env_int(
            "QMT_HEARTBEAT_TIMEOUT", 5, 1),
        "heartbeat_max_failures": _env_int(
            "QMT_HEARTBEAT_MAX_FAILURES", 3, 1),
        "reconnect_max_attempts": _env_int(
            "QMT_RECONNECT_MAX_ATTEMPTS", 0, 0),
    }


def _validate_config(cfg):
    auth_key = cfg.get("auth_key")
    invalid_auth_key = (
        not isinstance(auth_key, str)
        or not auth_key.strip()
        or auth_key == "your-secret-key-here"
    )
    if invalid_auth_key and not cfg.get(
            "allow_insecure", False):
        raise ValueError(
            "QMT_RPYC_AUTH_KEY must be non-empty and not use the placeholder; "
            "set "
            "QMT_RPYC_ALLOW_INSECURE=1 only for an isolated test environment")
    if bool(cfg.get("tls_keyfile")) != bool(cfg.get("tls_certfile")):
        raise ValueError(
            "QMT_RPYC_TLS_KEY and QMT_RPYC_TLS_CERT must be configured together")


def _mask_account(account_id):
    """Return a masked version of account_id for display."""
    if not account_id or len(account_id) <= 4:
        return account_id or "(none)"
    return account_id[:2] + "*" * (len(account_id) - 4) + account_id[-2:]


def _print_startup_info(cfg, cm):
    """Print a one-shot startup summary to stdout. No sensitive fields."""
    host = cfg["host"]
    port = cfg["port"]
    auth = "on" if cfg["auth_key"] else "off"
    tls = "on" if cfg.get("tls_keyfile") and cfg.get("tls_certfile") else "off"
    health = cm.get_health_status()
    qmt = health["connection_state"]
    account = _mask_account(cfg.get("qmt_account_id", ""))

    lines = [
        "=" * 56,
        "  qmt-rpyc server",
        "=" * 56,
        "  Listen   : {}:{}".format(host, port),
        "  Auth     : {}".format(auth),
        "  TLS      : {}".format(tls),
        "  QMT      : {}".format(qmt),
        "  Failures : {}".format(health["consecutive_failures"]),
        "  Retry at : {}".format(health["next_retry_at"] or "(none)"),
        "  Error    : {}".format(health["last_connection_error"] or "(none)"),
        "  Account  : {}".format(account),
        "  Log dir  : {}".format(cfg.get("log_dir", "logs")),
        "=" * 56,
    ]
    for line in lines:
        print(line)


def start_server(cfg, tls=None):
    _validate_config(cfg)
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
    from qmt_rpyc.server.service import XtquantService
    from qmt_rpyc.server.connection import ConnectionManager
    from qmt_rpyc.server.download_manager import DownloadTaskManager
    from qmt_rpyc.server.api_surface import build_api_surface
    from qmt_rpyc.server.auth_limiter import rate_limiter
    from qmt_rpyc.protocol import make_server_authenticator

    config = {
        "allow_public_attrs": True,
        "allow_pickle": True,
        "sync_request_timeout": 300,
    }

    cm = None
    dm = None
    server = None
    listener_socket = None
    interrupted = False
    try:
        cm = ConnectionManager(
            path=cfg["qmt_path"],
            session_id=cfg["qmt_session_id"],
            account_id=cfg["qmt_account_id"],
            heartbeat_interval=cfg["heartbeat_interval"],
            heartbeat_timeout=cfg["heartbeat_timeout"],
            heartbeat_max_failures=cfg["heartbeat_max_failures"],
            reconnect_max_attempts=cfg["reconnect_max_attempts"],
        )
        dm = DownloadTaskManager(max_workers=2)

        XtquantService._require_auth = auth_key is not None
        XtquantService._connection_mgr = cm
        XtquantService._download_mgr = dm
        XtquantService._active_clients = 0
        # Validate SDK imports before opening the listener. Local installation
        # failures are fatal; Trader construction and connection run separately.
        XtquantService._api_surface = build_api_surface()

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
        print("Server stopped.")
        logger.info("Server stopped")
    return 130 if interrupted else 0


def main(config_path=None, verbose=False):
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

    return start_server(cfg, tls)


if __name__ == "__main__":
    sys.exit(main())
