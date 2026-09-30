"""Coverage algebra and source evidence: absence is never inferred from min/max."""
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

DAY = timedelta(days=1)


def gaps(start, end, intervals):
    cursor = start
    missing = []
    for lo, hi in sorted(intervals):
        if hi < cursor or lo > end:
            continue
        if lo > cursor:
            missing.append((cursor, lo - DAY))
        if hi >= end:
            return tuple(missing)
        cursor = max(cursor, hi + DAY)
    if cursor <= end:
        missing.append((cursor, end))
    return tuple(missing)


@dataclass(frozen=True)
class Evidence:
    reusable: bool
    reason: str


class CoverageEvidence(Protocol):
    """Verifies reusable intervals. `calendar` lazily supplies source sessions.

    It is a zero-argument callable returning the sessions of the assessed window,
    so evidence that does not need a calendar never pays for one, and evidence
    that does need it can reuse the calendar the bridge already stores.
    """
    def assess(self, dataset, request, rows, start, end, calendar=None) -> Evidence: ...


class ConservativeEvidence:
    """Default for sources without a verified completeness signal.

    Concrete records prove their own existence, not the absence of other records.
    A complete trading calendar plus an actual bar for EVERY expected session
    is sufficient for unfilled bars. Missing/suspended sessions stay unknown.
    Source-specific proofs can be injected without changing repositories.
    """
    def market_for(self, code):
        return None

    def assess(self, dataset, request, rows, start, end, calendar=None):
        return Evidence(False, 'source does not attest interval coverage')


def covered(day, intervals):
    """Whether a continuously covered interval contains day."""
    return any(lo <= day <= hi for lo, hi in intervals)


def covered_suffix(end, intervals):
    """Earliest bound continuously covered through end, or None."""
    cursor = end
    found = False
    for lo,hi in sorted(intervals, reverse=True):
        if lo > cursor or hi < cursor:
            continue
        found = True
        if lo == date.min:
            return lo
        cursor = lo - DAY
    return cursor + DAY if found else None
