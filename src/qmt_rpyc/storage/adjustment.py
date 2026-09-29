"""Pure local price derivation. Adapter policy decides which cases are verified."""
from dataclasses import replace
import math

PRICE_FIELDS = ('open', 'high', 'low', 'close', 'previous_close')


class UnsupportedDerivation(ValueError):
    pass


class AdjustmentPolicy:
    """Cash/bonus affine transforms and event-ratio products; no guessed gugai."""
    def supports(self, events, mode):
        return mode == 'none'

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


class SampledBigQmtAdjustment(AdjustmentPolicy):
    """Ordinary cash, bonus and rights events verified against deployed samples."""
    def supports(self, events, mode):
        if mode == 'none':
            return True
        return all(event.source_gugai == 0 for event in events)


class FillPolicy:
    """A source must validate gap-filling before synthetic bars may be emitted."""
    def derive(self, rows, sessions, fill):
        if not fill:
            return tuple(rows)
        if tuple(row.trade_date for row in rows) == tuple(sessions):
            return tuple(rows)
        raise UnsupportedDerivation('suspension filling is not verified for this source')
