"""Pure local price derivation. Adapter policy decides which cases are verified."""
from dataclasses import replace
from datetime import datetime, time, timezone
import math

from .policy import SHANGHAI

PRICE_FIELDS = ('open', 'high', 'low', 'close', 'previous_close')


class UnsupportedDerivation(ValueError):
    pass


class AdjustmentPolicy:
    """Cash/bonus affine transforms and event-ratio products; no guessed gugai.

    Each mode was compared with the source's own output before being served locally.
    Front differs where a halt spans an ex-dividend date: the source answers such a
    window differently depending on where it starts, so no local formula matches both.
    The query service checks front's session completeness before using this
    formula; this additional check does not apply to front_ratio. Relevant
    gugai events stay on the source path. One policy serves every
    adapter, because they read the same QMT data.
    """
    VERIFIED_MODES = ('none', 'front', 'back', 'back_ratio', 'front_ratio')

    def supports(self, events, mode):
        """Formula support for already selected relevant events; no coverage check."""
        return mode in self.VERIFIED_MODES and all(event.source_gugai == 0 for event in events)

    def derive(self, rows, events, mode):
        if mode == 'none':
            return tuple(rows)
        if not rows:
            return ()
        boundary = min(row.trade_date for row in rows) if mode.startswith('front') else max(row.trade_date for row in rows)
        relevant_events = tuple(event for event in events if
            (event.event_date > boundary if mode.startswith('front') else event.event_date <= boundary))
        if not self.supports(relevant_events, mode):
            raise UnsupportedDerivation('adjustment has not been verified for this source')
        result = []
        for row in rows:
            relevant = [event for event in events if
                        (event.event_date > row.trade_date if mode.startswith('front') else event.event_date <= row.trade_date)]
            relevant.sort(key=lambda event: event.event_date, reverse=mode.startswith('back'))
            values = {name: getattr(row, name) for name in PRICE_FIELDS}
            for event in relevant:
                ratio = 1 + event.stock_bonus + event.stock_gift + event.allotment_quantity
                offset = event.allotment_price * event.allotment_quantity - event.interest
                if ratio <= 0 or not math.isfinite(ratio) or event.dr <= 0 or not math.isfinite(event.dr):
                    raise UnsupportedDerivation('invalid event factor')
                for name, value in values.items():
                    if value is None:
                        continue
                    if mode == 'front':
                        values[name] = (value + offset) / ratio
                    elif mode == 'back':
                        values[name] = value * ratio - offset
                    elif mode == 'front_ratio':
                        values[name] = value / event.dr
                    elif mode == 'back_ratio':
                        values[name] = value * event.dr
                    else:
                        raise UnsupportedDerivation('unknown adjustment')
            result.append(replace(row, **values))
        return tuple(result)


class FillPolicy:
    """The source's own filling rule, for sessions inside the window that have no bar.

    The fabricated row repeats the previous close as its whole range, with no volume,
    turnover, previous close or settlement price, the previous open interest, the
    suspension flag set and the session's Shanghai midnight as its timestamp.
    Adjustment runs first so an ex-dividend date inside a halt stays continuous.
    A window whose first session has no bar keeps the source path instead.
    """
    def derive(self, rows, sessions, fill):
        if not fill:
            return tuple(rows)
        known = {row.trade_date: row for row in rows}
        result, previous = [], None
        for day in sessions:
            row = known.get(day)
            if row is None:
                if previous is None:
                    raise UnsupportedDerivation('a window beginning without a bar is not filled locally')
                row = replace(previous, trade_date=day, source_time=midnight(day), previous_close=0.0,
                              open=previous.close, high=previous.close, low=previous.close,
                              volume=0, turnover=0.0, source_suspension_flag=1, settlement_price=0.0)
            previous = row
            result.append(row)
        return tuple(result)


def midnight(day):
    """The Shanghai midnight a daily bar carries as its source time, in UTC."""
    return datetime.combine(day, time.min, SHANGHAI).astimezone(timezone.utc)
