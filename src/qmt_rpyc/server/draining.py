"""Reversible admission pause for orderly local maintenance."""
from contextlib import contextmanager
import threading
import time


class DrainingError(RuntimeError):
    pass


class RequestGate:
    def __init__(self):
        self._condition = threading.Condition()
        self._active = 0
        self._replies = 0
        self._draining = False

    @contextmanager
    def admit(self):
        with self._condition:
            if self._draining:
                raise DrainingError('Server is draining for local maintenance')
            self._active += 1
        try:
            yield
        finally:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def reply(self):
        with self._condition:
            self._replies += 1
        try:
            yield
        finally:
            with self._condition:
                self._replies -= 1
                self._condition.notify_all()

    def _wait_idle(self, deadline):
        with self._condition:
            while self._active or self._replies:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Requests did not finish before stop timeout')
                self._condition.wait(remaining)

    def drain(self, downloads, timeout):
        deadline = time.monotonic() + timeout
        with self._condition:
            if self._draining:
                raise DrainingError('Server is already draining')
            self._draining = True
        try:
            self._wait_idle(deadline)
            while downloads is not None:
                stats = downloads.get_stats()
                if not stats['pending'] and not stats['running']:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Downloads did not finish before stop timeout')
                time.sleep(min(.05, remaining))
            self._wait_idle(deadline)
        except BaseException:
            with self._condition:
                self._draining = False
                self._condition.notify_all()
            raise
