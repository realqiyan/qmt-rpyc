import pytest
from qmt_rpyc.server.auth_limiter import AuthRateLimiter, _MAX_FAILURES, _LOCKOUT_SECONDS


class TestAuthRateLimiter:
    def test_not_locked_initially(self):
        limiter = AuthRateLimiter()
        assert limiter.is_locked("1.2.3.4") is False

    def test_lock_after_max_failures(self):
        limiter = AuthRateLimiter()
        for _ in range(_MAX_FAILURES):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_locked("1.2.3.4") is True

    def test_no_lock_below_threshold(self):
        limiter = AuthRateLimiter()
        for _ in range(_MAX_FAILURES - 1):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_locked("1.2.3.4") is False

    def test_success_clears_failures(self):
        limiter = AuthRateLimiter()
        for _ in range(_MAX_FAILURES - 1):
            limiter.record_failure("1.2.3.4")
        limiter.record_success("1.2.3.4")
        assert limiter.is_locked("1.2.3.4") is False
        limiter.record_failure("1.2.3.4")
        assert limiter.is_locked("1.2.3.4") is False

    def test_different_ips_independent(self):
        limiter = AuthRateLimiter()
        for _ in range(_MAX_FAILURES):
            limiter.record_failure("1.2.3.4")
        assert limiter.is_locked("1.2.3.4") is True
        assert limiter.is_locked("5.6.7.8") is False
