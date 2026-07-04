import time
import logging

from client.exceptions import _map_error

logger = logging.getLogger(__name__)


class _RemoteCallable:
    def __init__(self, client, surface, name, meta):
        self._client = client
        self._surface = surface
        self._name = name
        self._meta = meta
        self.__doc__ = meta.get("doc", "")
        self.__name__ = name

    def __call__(self, *args, **kwargs):
        return self._client._call(self._surface, self._name, args, kwargs)


class _RemoteModule:
    def __init__(self, client, surface, desc):
        self._client = client
        self._surface = surface
        for fname, meta in desc.get("functions", {}).items():
            setattr(self, fname, _RemoteCallable(client, surface, fname, meta))
        for cname, cval in desc.get("constants", {}).items():
            setattr(self, cname, cval)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _RemoteCallable(self._client, self._surface, name, {})

    def __dir__(self):
        return list(self.__dict__.keys())


class _RemoteTrader:
    def __init__(self, client, desc):
        self._client = client
        self._surface = "trader"
        for mname, meta in desc.get("methods", {}).items():
            setattr(self, mname, _RemoteCallable(client, "trader", mname, meta))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return _RemoteCallable(self._client, self._surface, name, {})


class DownloadTaskHandle:
    def __init__(self, client, task_id):
        self._client = client
        self.task_id = task_id

    def poll(self):
        resp = self._client._conn.root.query_download(self.task_id)
        if resp["status"] == "ok":
            return resp["data"]
        raise _map_error(resp)

    @property
    def is_done(self):
        task = self.poll()
        return task["status"] in ("completed", "failed")

    def wait(self, timeout=300, poll_interval=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            task = self.poll()
            if task["status"] in ("completed", "failed"):
                return task
            time.sleep(poll_interval)
        raise TimeoutError(f"download {self.task_id} not done in {timeout}s")

    def __repr__(self):
        return f"<DownloadTask {self.task_id}>"
