"""Bounded successful-discovery cache with single-flight refresh per key."""
from collections import OrderedDict
from concurrent.futures import Future
import threading
import time


class DiscoveryCache:
    def __init__(self, ttl=300, capacity=32, clock=time.monotonic):
        if ttl <= 0 or type(capacity) is not int or capacity < 1:
            raise ValueError('cache bounds must be positive')
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.lock = threading.Lock()
        self.values, self.pending = OrderedDict(), {}
        self.generation = 0
        self.hits = self.misses = 0

    def get(self, key, load):
        with self.lock:
            now = self.clock()
            for old_key, (expires, _) in list(self.values.items()):
                if expires <= now:
                    del self.values[old_key]
            if key in self.values:
                self.hits += 1
                self.values.move_to_end(key)
                return self.values[key][1]
            generation = self.generation
            flight_key = generation, key
            future = self.pending.get(flight_key)
            leader = future is None
            if leader:
                # Pending entries are bounded too; unrelated overflow calls
                # can load normally but cannot enlarge the cache bookkeeping.
                future = Future()
                tracked = len(self.pending) < self.capacity
                if tracked:
                    self.pending[flight_key] = future
                self.misses += 1
        if not leader:
            return future.result()
        try:
            value = load()
            with self.lock:
                if generation == self.generation:
                    self.values[key] = self.clock() + self.ttl, value
                    self.values.move_to_end(key)
                    while len(self.values) > self.capacity:
                        self.values.popitem(last=False)
            future.set_result(value)
            return value
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            if tracked:
                with self.lock:
                    self.pending.pop(flight_key, None)

    def clear(self):
        with self.lock:
            self.generation += 1
            self.values.clear()

    def info(self):
        with self.lock:
            return dict(ttl_seconds=self.ttl, capacity=self.capacity, entries=len(self.values),
                        refreshing=len(self.pending), hits=self.hits, misses=self.misses)
