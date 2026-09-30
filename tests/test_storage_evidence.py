"""What a QMT source may claim reusable, and what it never may."""
from datetime import date, datetime, timedelta, timezone

import pytest

from qmt_rpyc.adapters.storage_evidence import BigQmtCoverage, QmtCoverage
from qmt_rpyc.contracts.market import DailyBar, DailyBarsQuery, TradingDatesRequest
from qmt_rpyc.contracts.reference import DividendEvent, DividendQuery

NOW = datetime(2026, 9, 29, 1, tzinfo=timezone.utc)
TODAY = date(2026, 9, 29)
START, END = date(2026, 9, 21), date(2026, 9, 25)
SESSIONS = tuple(START + timedelta(days=day) for day in range(5)) + (date(2026, 9, 28), TODAY)


def bar(day):
    return DailyBar(day, None, 10., 10., 10., 10., 10., 100, 1000., 0, 0, 0.)


def event(day, moment, dr=1.25):
    return DividendEvent(day, moment, dr, 0., 0., 0., 0., 0., 0.)


class Calendar:
    """A source calendar that enumerates its sessions for a bounded query."""
    def __init__(self, days):
        self.days = tuple(days)

    def get_trading_dates(self, request):
        return tuple(day for day in self.days
                     if (not request.start or day >= request.start)
                     and (not request.end or day <= request.end))


def coverage(clock=lambda: NOW):
    return QmtCoverage(Calendar(SESSIONS), clock=clock)


def closed_bars(end=END):
    return tuple(bar(day) for day in SESSIONS if day <= end)


def test_market_identity_is_explicit_and_never_rewritten():
    assert (coverage().market_for('600000.SH'), coverage().market_for('600000.SZ')) == ('SH', 'SZ')
    assert coverage().market_for('600000.SHO') is None


@pytest.mark.parametrize('query,window', [
    (DailyBarsQuery(('600000.SH',), START, TODAY, fill_data=False), (START, TODAY)),
    (DailyBarsQuery(('600000.SH',), START, TODAY + timedelta(days=1), fill_data=False), (START, TODAY + timedelta(days=1))),
    (DailyBarsQuery(('600000.SH',), None, END, fill_data=False), (START, END)),
    (DailyBarsQuery(('600000.SH',), end=END, count=5, fill_data=False), (START, END)),
])
def test_open_counted_and_unfinished_intervals_are_never_reusable(query, window):
    # Every session in the interval has an actual bar: only the shape rules the claim out.
    start, end = window
    assert not coverage().assess('daily_bars', query, closed_bars(end), start, end).reusable


def test_a_bar_for_every_source_session_proves_a_closed_window():
    query = DailyBarsQuery(('600000.SH',), START, END, fill_data=False)
    assert coverage().assess('daily_bars', query, closed_bars(), START, END).reusable


def test_a_missing_session_never_proves_a_closed_window():
    # 2026-09-28 is a source session, so a bar set that skips it proves nothing.
    query = DailyBarsQuery(('600000.SH',), START, date(2026, 9, 28), fill_data=False)
    assert not coverage().assess('daily_bars', query, closed_bars(), START, date(2026, 9, 28)).reusable


@pytest.mark.parametrize('adjustment,fill_data', [('back', False), ('none', True)])
def test_derived_bars_never_establish_coverage(adjustment, fill_data):
    query = DailyBarsQuery(('600000.SH',), START, END, adjustment=adjustment, fill_data=fill_data)
    assert not coverage().assess('daily_bars', query, closed_bars(), START, END).reusable


def test_the_unfinished_boundary_follows_shanghai_midnight():
    query = DailyBarsQuery(('600000.SH',), START, TODAY, fill_data=False)
    rows = closed_bars(TODAY)
    assert not coverage().assess('daily_bars', query, rows, START, TODAY).reusable
    # 2026-09-30 00:30 in Shanghai: the same interval is now closed history.
    after_midnight = lambda: datetime(2026, 9, 29, 16, 30, tzinfo=timezone.utc)
    assert coverage(after_midnight).assess('daily_bars', query, rows, START, TODAY).reusable


def test_calendar_coverage_requires_a_bounded_valid_source_answer():
    bounded = TradingDatesRequest('SH', START, END)
    days = tuple(day for day in SESSIONS if day <= END)
    assert coverage().assess('trading_dates', bounded, days, START, END).reusable
    assert not coverage().assess('trading_dates', bounded, (), START, END).reusable
    assert not coverage().assess('trading_dates', bounded, days[::-1], START, END).reusable
    assert not coverage().assess('trading_dates', TradingDatesRequest('SH', None, END), days, START, END).reusable
    assert not coverage().assess('trading_dates', TradingDatesRequest('SH', START, TODAY),
                                 tuple(SESSIONS), START, TODAY).reusable


def test_a_supplied_calendar_replaces_the_source_read():
    class Unreachable(Calendar):
        def get_trading_dates(self, request):
            raise AssertionError('the supplied calendar must be used instead')

    query = DailyBarsQuery(('600000.SH',), START, END, fill_data=False)
    supplied = QmtCoverage(Unreachable(SESSIONS), clock=lambda: NOW)
    days = tuple(day for day in SESSIONS if day <= END)
    assert supplied.assess('daily_bars', query, closed_bars(), START, END, lambda: days).reusable
    # A stale or incomplete supplied calendar proves nothing, as an incomplete read would.
    assert not supplied.assess('daily_bars', query, closed_bars(), START, END, lambda: days[:2]).reusable
    assert not supplied.assess('daily_bars', query, closed_bars(), START, END, lambda: ()).reusable


def test_unknown_datasets_fall_back_to_the_conservative_default():
    query = DividendQuery('600000.SH', START, END)
    assert not coverage().assess('Balance', query, closed_bars(), START, END).reusable


def test_event_collection_coverage_requires_distinct_source_timestamps():
    query = DividendQuery('600000.SH', START, END)
    events = BigQmtCoverage(Calendar(SESSIONS), clock=lambda: NOW)
    distinct = (event(START, NOW), event(END, NOW + timedelta(seconds=1)))
    assert events.assess('dividend_events', query, distinct, START, END).reusable
    assert not events.assess('dividend_events', query, (event(START, NOW), event(END, NOW)), START, END).reusable
    assert not events.assess('dividend_events', query, (event(END + timedelta(days=1), NOW),), START, END).reusable
