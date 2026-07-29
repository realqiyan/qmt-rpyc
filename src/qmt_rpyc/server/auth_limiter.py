import time
import threading
import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

_MAX_FAILURES = 5
_WINDOW_SECONDS = 60
_LOCKOUT_SECONDS = 300


class AuthRateLimiter:
    def __init__(self):
        self._failures = defaultdict(list)
        self._lockouts = {}
        self._lock = threading.Lock()

    def is_locked(self, ip):
        with self._lock:
            until = self._lockouts.get(ip)
            if until and until > time.time():
                return True
            if until:
                self._lockouts.pop(ip, None)
            return False

    def record_failure(self, ip):
        with self._lock:
            now = time.time()
            recent = [t for t in self._failures[ip] if now - t < _WINDOW_SECONDS]
            recent.append(now)
            self._failures[ip] = recent
            if len(recent) >= _MAX_FAILURES:
                self._lockouts[ip] = now + _LOCKOUT_SECONDS
                logger.warning("IP %s locked out for %ds after %d auth failures",
                               ip, _LOCKOUT_SECONDS, len(recent))

    def record_success(self, ip):
        with self._lock:
            self._failures.pop(ip, None)
            self._lockouts.pop(ip, None)


rate_limiter = AuthRateLimiter()
