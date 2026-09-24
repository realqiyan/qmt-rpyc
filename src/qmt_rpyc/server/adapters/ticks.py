"""Normalize the redundant tick display time from the SDK's Unix milliseconds."""
from datetime import datetime, timedelta

from qmt_rpyc.contract import project, specification

_TICK_TIME_SCHEMA = specification()['models']['tick']['fields']['time']


def normalize_tick_times(ticks):
    if not isinstance(ticks, dict):
        return ticks
    return {code: _normalize_tick(tick) for code, tick in ticks.items()}


def _normalize_tick(tick):
    if not isinstance(tick, dict):
        return tick
    try:
        project(tick.get('timetag'), 'timetag')
        return tick
    except ValueError:
        pass
    # Validate units before conversion. Never interpret a seconds value as
    # milliseconds, use the wall clock, or change prices to mask missing data.
    try:
        milliseconds = project(tick.get('time'), _TICK_TIME_SCHEMA)
        local = datetime(1970, 1, 1) + timedelta(milliseconds=milliseconds, hours=8)
    except (ValueError, TypeError, OverflowError):
        # The normal result validator reports the offending required field.
        return tick
    tag = local.strftime('%Y%m%d %H:%M:%S')
    if milliseconds % 1000:
        tag += '.{:03d}'.format(milliseconds % 1000)
    return {**tick, 'timetag': tag}
