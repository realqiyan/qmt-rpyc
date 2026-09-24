"""Authenticated RPyC transport and connection lifecycle."""
import logging
import socket

import rpyc
from rpyc.core.stream import SocketStream

from qmt_rpyc.contracts.errors import NotConnectedError, QmtAuthError
from qmt_rpyc.transport.auth import SocketAuthError, authenticate_client_socket

logger = logging.getLogger(__name__)

class RpycTransport:
    def __init__(self, connection):
        self.connection = connection

    @classmethod
    def connect(cls, host, port=18812, auth_key=None,
                    timeout=30, tls_config=None, **protocol_config):
        config = {
            "allow_public_attrs": False,
            "allow_pickle": False,
            "sync_request_timeout": timeout,
            **protocol_config,
        }
        sock = None
        conn = None
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            if tls_config:
                import ssl
                context = ssl.create_default_context(
                    cafile=tls_config.get("ca_certs"))
                if tls_config.get("certfile"):
                    context.load_cert_chain(
                        certfile=tls_config["certfile"],
                        keyfile=tls_config.get("keyfile"))
                sock = context.wrap_socket(sock, server_hostname=host)
            if auth_key is not None:
                authenticate_client_socket(sock, auth_key)
            conn = rpyc.connect_stream(SocketStream(sock), config=config)
            sock = None
            return cls(conn)
        except SocketAuthError as e:
            if conn is not None:
                conn.close()
            elif sock is not None:
                sock.close()
            raise QmtAuthError(str(e)) from e
        except Exception:
            try:
                if conn is not None:
                    conn.close()
                elif sock is not None:
                    sock.close()
            except Exception:
                logger.warning(
                    "Failed to close RPyC connection after connect error",
                    exc_info=True,
                )
            raise

    @property
    def root(self):
        if self.connection is None:
            raise NotConnectedError("client is closed")
        return self.connection.root

    def close(self):
        connection, self.connection = self.connection, None
        if connection is not None:
            try:
                connection.close()
            except Exception:
                logger.warning("Failed to close RPyC connection", exc_info=True)
