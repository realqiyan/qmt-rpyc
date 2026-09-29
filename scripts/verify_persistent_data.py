#!/usr/bin/env python3
"""Read-only BigQMT price-derivation verification via the existing debug profile.

No downloads, subscriptions, trading, deployment changes or failed-call retries.
Prints comparison summaries; raw source samples remain outside the repository.
"""
import argparse
from datetime import date, datetime, timezone
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


def verify(client, code, start, end):
    for target in ('context.get_divid_factors', 'context.get_market_data_ex'):
        if not client.describe(target).get('call_allowed'):
            raise ValueError('required read-only source method is unavailable')
    before = client.call('context.get_divid_factors', [code])
    events = events_from_raw(before)
    source = {}
    for fill in (False, True):
        for mode in MODES:
            raw = client.call('context.get_market_data_ex', kwargs=dict(
                fields=list(FIELDS), stock_code=[code], period='1d',
                start_time=start.strftime('%Y%m%d'), end_time=end.strftime('%Y%m%d'),
                count=-1, dividend_type=mode, fill_data=fill, subscribe=False))
            source[fill, mode] = bars_from_raw(raw, code)
    after = client.call('context.get_divid_factors', [code])
    if before != after:
        raise ValueError('source events changed during verification')
    policy = SampledBigQmtAdjustment()
    report = []
    for mode in MODES:
        try:
            derived = policy.derive(source[False, 'none'], events, mode)
        except UnsupportedDerivation:
            report.append(dict(mode=mode, status='source_fallback'))
            continue
        expected = source[False, mode]
        if not expected:
            report.append(dict(mode=mode, status='no_sample'))
            continue
        if tuple(row.trade_date for row in expected) != tuple(row.trade_date for row in derived):
            raise ValueError('row identities differ')
        maximum = 0.
        for actual, wanted in zip(derived, expected):
            for name in DailyBar.__dataclass_fields__:
                a, b = getattr(actual, name), getattr(wanted, name)
                if isinstance(a, (int, float)) and isinstance(b, (int, float)):
                    maximum = max(maximum, abs(a-b))
                    if not math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
                        raise ValueError('source/local mismatch: ' + mode + '/' + name)
                elif a != b:
                    raise ValueError('source/local mismatch: ' + mode + '/' + name)
        report.append(dict(mode=mode, status='matched', rows=len(expected), max_absolute_error=maximum))
    return dict(comparisons=report, fill_changes_result=source[False,'none'] != source[True,'none'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', default='default')
    parser.add_argument('--code', required=True)
    parser.add_argument('--start', required=True, type=date.fromisoformat)
    parser.add_argument('--end', required=True, type=date.fromisoformat)
    args = parser.parse_args()
    today = datetime.now(SHANGHAI).date()
    if not args.start <= args.end < today or (args.end-args.start).days > 90:
        parser.error('choose an ended interval of at most 90 days')
    with DebugClient.connect_profile(args.profile) as client:
        print(json.dumps(verify(client,args.code,args.start,args.end),ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
