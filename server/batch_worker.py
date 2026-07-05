"""Lightweight worker function for ProcessPoolExecutor-based batch calls.

This module is deliberately minimal — a ProcessPoolExecutor worker on Windows
(spawn) imports the module containing the target function.  If that module were
server.service, every worker would transitively import rpyc, pandas, numpy,
the logging config, auth_limiter, event_bus, connection manager, etc. — 50+
workers doing that simultaneously saturates disk I/O and memory.

By isolating _execute_one here, each worker only imports:
  - common.protocol (STATUS_OK constant)
  - server.serializer (serialize, which imports numpy/pandas — unavoidable)
  - xtquant (lazy, inside the function)

No RPyC, no auth, no event bus, no logging setup.
"""

from common.protocol import STATUS_OK
from server.serializer import serialize


def execute_one(name, args, kwargs):
    """Execute a single xtdata call, return {"status", "data"}.

    Receives the function *name* (string, pickle-safe) instead of the
    pybind11 function object.  Each worker process imports xtdata
    independently and therefore has its own GIL, yielding true
    parallelism for pybind11 functions that do not release the GIL.

    Exceptions propagate to the caller — the batch loop catches them.
    """
    from xtquant import xtdata as _xtdata

    fn = getattr(_xtdata, name)
    raw = fn(*args, **kwargs)
    return {"status": STATUS_OK, "data": serialize(raw)}
