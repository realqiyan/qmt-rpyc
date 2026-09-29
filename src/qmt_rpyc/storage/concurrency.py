"""Per-entity serialization; never hold a database transaction around source IO."""
from contextlib import contextmanager
import threading


class PartitionLocks:
    def __init__(self):
        self._guard = threading.Lock()
        self._entries = {}

    @contextmanager
    def hold(self, key):
        with self._guard:
            lock, users = self._entries.get(key, (threading.RLock(), 0))
            self._entries[key] = lock, users + 1
        try:
            with lock:
                yield
        finally:
            with self._guard:
                _, users = self._entries[key]
                if users == 1:
                    del self._entries[key]
                else:
                    self._entries[key] = lock, users - 1
