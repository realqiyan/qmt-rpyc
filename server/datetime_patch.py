"""
Monkey-patch for datetime.fromtimestamp / utcfromtimestamp to prevent
CPython C assertion crash: "Assertion failed: u < 1000000".

Root cause:
  CPython's _datetimemodule.c computes microseconds as:
      us = (timestamp - (time_t)timestamp) * 1_000_000
  IEEE 754 float precision can cause us >= 1_000_000,
  triggering assert(u < 1000000) which calls abort() — uncatchable.

  This happens inside xtquant SDK when it deserializes market data timestamps
  (millisecond precision) via datetime.fromtimestamp().

Fix:
  On Python < 3.12: Replace datetime.datetime in the datetime module
  with a subclass (_SafeDatetime) that overrides fromtimestamp /
  utcfromtimestamp to split the timestamp into integer seconds +
  microseconds manually, clamp microseconds to [0, 999999] with proper
  carry, then call the original fromtimestamp with integer seconds only
  (always safe) and set microseconds separately.

  On Python >= 3.12: Do nothing — the C assertion bug is already fixed
  upstream (https://bugs.python.org/issue44831).

IMPORTANT:
  This module MUST be imported before any xtquant import on Python < 3.12.
"""
import datetime as _dt
import sys
import logging

logger = logging.getLogger(__name__)

_NEEDS_PATCH = sys.version_info < (3, 12)
_PATCHED = False

# Save references to the ORIGINAL C datetime class and its classmethods.
_OrigDatetime = _dt.datetime
_orig_fromtimestamp = _OrigDatetime.fromtimestamp
_orig_utcfromtimestamp = getattr(_OrigDatetime, "utcfromtimestamp", None)


def _clamp_timestamp(timestamp):
    """Split a float timestamp into (int_seconds, microseconds) with carry.

    Returns (ts_int, us) where 0 <= us < 1_000_000.

    This avoids the C assertion crash by never letting a float with
    a problematic fractional part reach datetime.fromtimestamp().
    """
    ts_int = int(timestamp)
    us = round((timestamp - ts_int) * 1_000_000)

    if us >= 1_000_000:
        ts_int += us // 1_000_000
        us = us % 1_000_000
    elif us < 0:
        # Negative microseconds (from negative timestamps): borrow from seconds
        ts_int -= ((-us - 1) // 1_000_000) + 1
        us = us % 1_000_000

    return ts_int, us


class _SafeDatetime(_OrigDatetime):
    """datetime subclass with safe fromtimestamp / utcfromtimestamp."""

    @classmethod
    def fromtimestamp(cls, timestamp, tz=None):
        ts_int, us = _clamp_timestamp(timestamp)

        # Call original C fromtimestamp with integer seconds — always safe
        # because there's no fractional part to trigger the assertion.
        if tz is not None:
            dt = _orig_fromtimestamp(ts_int, tz=tz)
        else:
            dt = _orig_fromtimestamp(ts_int)

        # Set the correct microseconds (properly clamped)
        dt = dt.replace(microsecond=us)

        # The C replace() always returns a datetime.datetime instance,
        # not the subclass, so reconstruct if cls != _OrigDatetime.
        if type(dt) is not cls:
            return cls(dt.year, dt.month, dt.day,
                       dt.hour, dt.minute, dt.second,
                       dt.microsecond, dt.tzinfo)
        return dt

    if _orig_utcfromtimestamp is not None:
        @classmethod
        def utcfromtimestamp(cls, timestamp):
            ts_int, us = _clamp_timestamp(timestamp)
            dt = _orig_utcfromtimestamp(ts_int)
            dt = dt.replace(microsecond=us)
            if type(dt) is not cls:
                return cls(dt.year, dt.month, dt.day,
                           dt.hour, dt.minute, dt.second,
                           dt.microsecond, dt.tzinfo)
            return dt


def apply():
    """Apply the monkey-patch. Idempotent.

    On Python >= 3.12: no-op.
    On Python < 3.12: Replaces datetime.datetime in the datetime module.
    """
    global _PATCHED

    if not _NEEDS_PATCH:
        logger.debug("datetime patch: skipped (Python %d.%d — bug fixed upstream)",
                     sys.version_info.major, sys.version_info.minor)
        return

    if _PATCHED:
        return

    _dt.datetime = _SafeDatetime
    sys.modules['datetime'].datetime = _SafeDatetime

    _PATCHED = True
    logger.info("datetime patch: applied (Python %d.%d — prevents "
                "'u < 1000000' C assertion crash from float precision)",
                sys.version_info.major, sys.version_info.minor)


# Auto-apply on import — must happen before xtquant is loaded on Python < 3.12
apply()
