"""Typed QMT bridge client and server."""
from typing import TYPE_CHECKING

from .version import __version__

if TYPE_CHECKING:
    from .client import QmtClient

__all__ = ["QmtClient", "__version__"]

def __getattr__(name):
    if name == "QmtClient":
        from .client import QmtClient
        return QmtClient
    raise AttributeError(name)
