"""Boundary-equivalence verification tooling, driven by a synthetic debug client."""
from datetime import date, datetime, timedelta, timezone

import pytest

from scripts.verify_persistent_data import FIELDS, MODES, verify

CODE = '600000.SH'
START, END = date(2026, 9, 21), date(2026, 9, 25)
SPLIT = date(2026, 9, 23)
WEEK = tuple(START + timedelta(days=offset) for offset in range(5))
# 2026-09-21 12:00 in Shanghai, so the sampled event date does not depend on this machine.
MILLIS = int(datetime(2026, 9, 21, 4, tzinfo=timezone.utc).timestamp() * 1000)
DR, RATIO = 1.5, 2.
PRICE_COLUMNS = (0, 1, 2, 3, 4)
# The single event is dated on the first row, so back modes apply it to every row
# and front modes to none: each source mode output stays self-consistent.
FACTORS = {'none': 1., 'front': 1., 'back': RATIO, 'front_ratio': 1., 'back_ratio': DR}


def as_date(value):
    return datetime.strptime(str(value), '%Y%m%d').date()


def raw_bars(days, close=10.):
    return dict(columns=list(FIELDS), index=[int(day.strftime('%Y%m%d')) for day in days],
                data=[[close] * len(FIELDS) for day in days])


class Debug:
    """A closed-interval source; its filling depends on the query start, as observed."""
    def __init__(self, days, fill_from_start=False, wrong_column=None):
        self.days, self.fill_from_start = tuple(days), fill_from_start
        self.wrong_column = wrong_column
        self.calls = []

    def describe(self, target):
        return {'call_allowed': True}

    def call(self, name, args=None, kwargs=None):
        self.calls.append(name)
        if name == 'context.get_divid_factors':
            return {'type': 'mapping', 'items': [[MILLIS, [0., 1., 0., 0., 0., 0., DR]]]}
        start, end = as_date(kwargs['start_time']), as_date(kwargs['end_time'])
        window = [day for day in self.days if start <= day <= end]
        if kwargs['fill_data'] and self.fill_from_start and start not in self.days:
            window = [start] + [day for day in window if day > start]
        factor = FACTORS[kwargs['dividend_type']]
        data = [[value * factor if column in PRICE_COLUMNS else value
                 for column, value in enumerate(row)] for row in raw_bars(window)['data']]
        if self.wrong_column is not None and kwargs['dividend_type'] != 'none' and data:
            data[0][self.wrong_column] += 0.5
        return {CODE: dict(raw_bars(window), data=data)}


def test_a_consistent_source_matches_derivation_and_both_boundaries():
    report = verify(Debug(WEEK), CODE, START, END, SPLIT)
    assert [entry['status'] for entry in report['comparisons']] == ['matched'] * len(MODES)
    assert all(entry['max_absolute_error'] == 0 for entry in report['comparisons'])
    assert all(entry['row_identities_match'] and entry['max_absolute_error'] == 0
               for entry in report['boundaries'])
    assert report['fill_changes_result'] is False


def test_a_start_dependent_fill_is_reported_not_raised():
    # 2026-09-24 is suspended, so only a window starting there invents a row.
    suspended = (START, START + timedelta(days=1), START + timedelta(days=2), END)
    report = verify(Debug(suspended, fill_from_start=True), CODE, START, END, SPLIT)
    filled = [entry for entry in report['boundaries'] if entry['fill_data']]
    assert filled and not any(entry['row_identities_match'] for entry in filled)
    assert all(entry['row_identities_match'] for entry in report['boundaries'] if not entry['fill_data'])


@pytest.mark.parametrize('column', [0, 3])
def test_a_local_derivation_mismatch_fails_verification(column):
    with pytest.raises(ValueError, match='local derivation'):
        verify(Debug(WEEK, wrong_column=column), CODE, START, END, SPLIT)


def test_an_unavailable_source_method_fails_before_reading():
    class Unavailable(Debug):
        def describe(self, target):
            return {'call_allowed': target != 'context.get_divid_factors'}
    with pytest.raises(ValueError, match='unavailable'):
        verify(Unavailable(WEEK), CODE, START, END, SPLIT)
