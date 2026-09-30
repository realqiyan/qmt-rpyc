#!/usr/bin/env python3
"""Read-only BigQMT price-derivation verification via the existing debug profile.

No downloads, subscriptions, trading, deployment changes or failed-call retries.
Prints comparison summaries; raw source samples remain outside the repository.

Besides checking that local derivation reproduces every source adjustment mode,
it re-reads the same closed interval as two adjacent halves: the persistent
cache splices ranges, so the source must return the same rows whatever the
query boundary is. The split point defaults to the interval midpoint.
"""
import argparse
from datetime import date, datetime, timedelta, timezone
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from qmt_rpyc.client.debug import DebugClient
from qmt_rpyc.contracts.market import DailyBar
from qmt_rpyc.contracts.reference import DividendEvent
from qmt_rpyc.storage.adjustment import SampledBigQmtAdjustment, UnsupportedDerivation
from qmt_rpyc.storage.policy import SHANGHAI

FIELDS = ('open', 'high', 'low', 'close', 'preClose', 'volume', 'amount',
          'openInterest', 'suspendFlag', 'settelementPrice')
MODES = ('none', 'front', 'back', 'front_ratio', 'back_ratio')
DAY = timedelta(days=1)


def events_from_raw(raw):
    if not isinstance(raw, dict) or raw.get('type') != 'mapping':
        raise ValueError('unexpected event envelope')
    result = []
    for stamp, values in raw['items']:
        instant = datetime.fromtimestamp(stamp / 1000, timezone.utc)
        result.append(DividendEvent(instant.astimezone(SHANGHAI).date(), instant,
                                   values[6], *values[:6]))
    return tuple(result)


def bars_from_raw(raw, code):
    table = raw[code]
    result = []
    for stamp, values in zip(table['index'], table['data']):
        row = dict(zip(table['columns'], values))
        day = datetime.strptime(str(stamp), '%Y%m%d').date()
        result.append(DailyBar(day, None, *(row[name] for name in FIELDS)))
    return tuple(result)


def read_bars(client, code, mode, fill, start, end):
    raw = client.call('context.get_market_data_ex', kwargs=dict(
        fields=list(FIELDS), stock_code=[code], period='1d',
        start_time=start.strftime('%Y%m%d'), end_time=end.strftime('%Y%m%d'),
        count=-1, dividend_type=mode, fill_data=fill, subscribe=False))
    return bars_from_raw(raw, code)


def row_dates(rows):
    return tuple(row.trade_date for row in rows)


def compare_rows(actual, expected, label):
    """Field-by-field comparison; returns the largest absolute numeric difference."""
    maximum = 0.
    for left, right in zip(actual, expected):
        for name in DailyBar.__dataclass_fields__:
            a, b = getattr(left, name), getattr(right, name)
            if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                maximum = max(maximum, abs(a - b))
                if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
                    raise ValueError(label + ' differs at ' + name)
            elif a != b:
                raise ValueError(label + ' differs at ' + name)
    return maximum


def verify_derivation(client, code, start, end, events, raw):
    report = []
    for mode in MODES:
        try:
            derived = SampledBigQmtAdjustment().derive(raw, events, mode)
        except UnsupportedDerivation:
            report.append(dict(mode=mode, status='source_fallback'))
            continue
        expected = read_bars(client, code, mode, False, start, end)
        if not expected:
            report.append(dict(mode=mode, status='no_sample'))
            continue
        if row_dates(expected) != row_dates(derived):
            raise ValueError('row identities differ for ' + mode)
        report.append(dict(mode=mode, status='matched', rows=len(expected),
                           max_absolute_error=compare_rows(derived, expected, 'local derivation ' + mode)))
    return report


def verify_boundaries(client, code, start, end, split):
    """Compare one closed interval with the same interval read as two halves."""
    report = []
    for fill in (False, True):
        for mode in MODES:
            whole = read_bars(client, code, mode, fill, start, end)
            halves = read_bars(client, code, mode, fill, start, split) + read_bars(
                client, code, mode, fill, split + DAY, end)
            entry = dict(mode=mode, fill_data=fill, rows=len(whole),
                         row_identities_match=row_dates(halves) == row_dates(whole))
            if entry['row_identities_match']:
                entry['max_absolute_error'] = compare_rows(
                    halves, whole, 'split boundary ' + mode + '/' + str(fill))
            report.append(entry)
    return report


def verify(client, code, start, end, split):
    for target in ('context.get_divid_factors', 'context.get_market_data_ex'):
        if not client.describe(target).get('call_allowed'):
            raise ValueError('required read-only source method is unavailable')
    before = client.call('context.get_divid_factors', [code])
    events = events_from_raw(before)
    unfilled = read_bars(client, code, 'none', False, start, end)
    filled = read_bars(client, code, 'none', True, start, end)
    comparisons = verify_derivation(client, code, start, end, events, unfilled)
    boundaries = verify_boundaries(client, code, start, end, split)
    after = client.call('context.get_divid_factors', [code])
    if before != after:
        raise ValueError('source events changed during verification')
    return dict(comparisons=comparisons, boundaries=boundaries,
                fill_changes_result=unfilled != filled)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--code', required=True)
    parser.add_argument('--start', required=True, type=date.fromisoformat)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    parser.add_argument('--split', type=date.fromisoformat,
                        help='boundary between the two halves (default: the midpoint)')
    args = parser.parse_args()
    today = datetime.now(SHANGHAI).date()
    if not args.start <= args.end < today or (args.end-args.start).days > 90:
        parser.error('choose an ended interval of at most 90 days')
    split = args.split or args.start + (args.end - args.start) // 2
    if not args.start <= split < args.end:
        parser.error('choose a split inside the interval, leaving two non-empty halves')
    with DebugClient.connect_profile(args.profile) as client:
        print(json.dumps(verify(client, args.code, args.start, args.end, split),
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
