# server/main.py
import os
import sys
import logging
import faulthandler
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
_CRASH_LOG = os.path.join(_HERE, "..", "logs", "crash.log")
try:
    os.makedirs(os.path.dirname(_CRASH_LOG), exist_ok=True)
    _crash_fp = open(_CRASH_LOG, "a", encoding="utf-8")
    _crash_fp.write("\n==== rpyc server start pid={} ====\n".format(os.getpid()))
    _crash_fp.flush()
    faulthandler.enable(file=_crash_fp, all_threads=True)
except Exception:
    faulthandler.enable()

import pandas
import numpy

import server.datetime_patch

from server.logging_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def _load_config():
    # 加载项目根目录 .env 文件到 os.environ（已存在的环境变量不会被覆盖）
    from dotenv import load_dotenv
    _PROJECT_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))
    return {
        "host": os.environ.get("QMT_RPYC_HOST", "0.0.0.0"),
        "port": int(os.environ.get("QMT_RPYC_PORT", "18812")),
        "auth_key": os.environ.get("QMT_RPYC_AUTH_KEY"),
        "qmt_path": os.environ.get("QMT_PATH", ""),
        "qmt_session_id": int(os.environ.get("QMT_SESSION_ID", "1")),
        "qmt_account_id": os.environ.get("QMT_ACCOUNT_ID", ""),
        "tls_keyfile": os.environ.get("QMT_RPYC_TLS_KEY"),
        "tls_certfile": os.environ.get("QMT_RPYC_TLS_CERT"),
        "tls_ca_certs": os.environ.get("QMT_RPYC_TLS_CA"),
        "log_dir": os.environ.get("QMT_RPYC_LOG_DIR", "logs"),
        "heartbeat_interval": int(os.environ.get("QMT_HEARTBEAT_INTERVAL", "30")),
        "heartbeat_timeout": int(os.environ.get("QMT_HEARTBEAT_TIMEOUT", "5")),
        "heartbeat_max_failures": int(os.environ.get("QMT_HEARTBEAT_MAX_FAILURES", "3")),
        "reconnect_max_attempts": int(os.environ.get("QMT_RECONNECT_MAX_ATTEMPTS", "0")),
    }


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
    qmt = "connected" if cm.is_connected else "not connected"
    account = _mask_account(cfg.get("qmt_account_id", ""))

    lines = [
        "=" * 56,
        "  qmt-rpyc server",
        "=" * 56,
        "  Listen   : {}:{}".format(host, port),
        "  Auth     : {}".format(auth),
        "  TLS      : {}".format(tls),
        "  QMT      : {}".format(qmt),
        "  Account  : {}".format(account),
        "  Log dir  : {}".format(cfg.get("log_dir", "logs")),
        "=" * 56,
    ]
    for line in lines:
        print(line)


def start_server(cfg, tls=None):
    host = cfg["host"]
    port = cfg["port"]
    auth_key = cfg["auth_key"]

    import socket
    from rpyc.utils.server import ThreadedServer
    from server.service import XtquantService
    from server.connection import ConnectionManager
    from server.download_manager import DownloadTaskManager
    from server.api_surface import build_api_surface

    config = {
        "allow_public_attrs": True,
        "allow_pickle": True,
        "sync_request_timeout": 300,
    }

    cm = ConnectionManager(
        path=cfg["qmt_path"],
        session_id=cfg["qmt_session_id"],
        account_id=cfg["qmt_account_id"],
        heartbeat_interval=cfg["heartbeat_interval"],
        heartbeat_timeout=cfg["heartbeat_timeout"],
        heartbeat_max_failures=cfg["heartbeat_max_failures"],
        reconnect_max_attempts=cfg["reconnect_max_attempts"],
    )
    cm.start()

    dm = DownloadTaskManager(max_workers=2)

    XtquantService._auth_key = auth_key
    XtquantService._require_auth = auth_key is not None
    XtquantService._connection_mgr = cm
    XtquantService._download_mgr = dm
    try:
        XtquantService._api_surface = build_api_surface()
    except ImportError:
        logger.error("Cannot build API surface -- xtquant not available")
        XtquantService._api_surface = {}

    for t in threading.enumerate():
        if t is not threading.current_thread() and not t.daemon:
            t.daemon = True

    if tls:
        import ssl
        listener_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener_socket.bind((host, port))
        listener_socket.listen(5)
        ssl_sock = ssl.wrap_socket(
            listener_socket,
            keyfile=tls.get("keyfile"),
            certfile=tls.get("certfile"),
            ca_certs=tls.get("ca_certs"),
            cert_reqs=ssl.CERT_REQUIRED if tls.get("ca_certs") else ssl.CERT_NONE,
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
        )
        server = ThreadedServer(
            XtquantService, hostname=host, port=port,
            protocol_config=config, listener=ssl_sock)
    else:
        server = ThreadedServer(
            XtquantService, hostname=host, port=port,
            protocol_config=config)

    _print_startup_info(cfg, cm)

    try:
        logger.info("Starting RPyC server on %s:%d", host, port)
        server.start()
    except KeyboardInterrupt:
        print("Server shutting down...")
        logger.info("Shutting down...")
    finally:
        dm.shutdown()
        cm.stop()
        server.close()
        print("Server stopped.")
        logger.info("Server stopped")


def main():
    cfg = _load_config()

    tls = None
    if cfg["tls_keyfile"] and cfg["tls_certfile"]:
        tls = {
            "keyfile": cfg["tls_keyfile"],
            "certfile": cfg["tls_certfile"],
            "ca_certs": cfg["tls_ca_certs"],
        }

    start_server(cfg, tls)


if __name__ == "__main__":
    main()
