"""Conservative reusable-range evidence for QMT provider results.

The source calendar is authoritative only for an explicitly bounded past query.
A bar range additionally requires one *actual* bar for every calendar session.
Financial/event completeness has no deployed attestation and remains observed.
"""
import logging
from qmt_rpyc.contracts.market import TradingDatesRequest
from qmt_rpyc.storage.coverage import ConservativeEvidence, Evidence
from qmt_rpyc.storage.policy import SHANGHAI, utc_now

logger = logging.getLogger(__name__)


class QmtCoverage(ConservativeEvidence):
    def __init__(self, market, clock=utc_now):
        self.market, self.clock = market, clock

    def market_for(self, code):
        # Explicit exchange identities, not suffix rewriting (SHO/SZO are not SH/SZ).
        market = code.rsplit('.',1)[-1]
        return market if market in ('SH','SZ') else None

    def sessions(self, market, start, end, calendar):
        """Sessions of a closed window, from the bridge when it offers its stored one."""
        if calendar is not None:
            return tuple(calendar())
        return tuple(self.market.get_trading_dates(TradingDatesRequest(market,start,end)))

    def assess(self, dataset, request, rows, start, end, calendar=None):
        today = self.clock().astimezone(SHANGHAI).date()
        bounded = request.start is not None and request.end is not None and getattr(request,'count',None) is None
        if not bounded or end >= today:
            return Evidence(False, 'open, counted, or unfinished interval')
        if dataset == 'trading_dates':
            valid = bool(rows) and tuple(rows) == tuple(sorted(set(rows))) and all(start <= d <= end for d in rows)
            return Evidence(valid, 'bounded historical source calendar' if valid else 'empty or invalid calendar')
        if dataset == 'daily_bars':
            if request.adjustment != 'none' or request.fill_data:
                return Evidence(False, 'only actual unadjusted bars may establish coverage')
            market = self.market_for(request.codes[0])
            if market is None:
                return Evidence(False, 'unverified market identity')
            try:
                sessions = self.sessions(market,start,end,calendar)
            except Exception:
                logger.warning('Cannot obtain calendar evidence for bars', exc_info=True)
                return Evidence(False, 'calendar evidence unavailable')
            dates = tuple(row.trade_date for row in rows)
            valid = bool(sessions) and tuple(sessions) == tuple(sorted(set(sessions))) and dates == tuple(sessions)
            return Evidence(valid, 'actual bar for every source calendar session' if valid else 'unknown missing sessions')
        return super().assess(dataset, request, rows, start, end, calendar)


class BigQmtCoverage(QmtCoverage):
    def assess(self, dataset, request, rows, start, end, calendar=None):
        if dataset == 'dividend_events':
            # ReferenceAdapter reads the complete explicit event-pair collection
            # before applying request dates locally, validating even excluded rows.
            # This is source-view coverage, not proof of all real-world events.
            valid = (len({row.source_event_at for row in rows}) == len(rows)
                     and all(start <= row.event_date <= end for row in rows))
            return Evidence(valid, 'validated full source event collection, filtered locally')
        return super().assess(dataset, request, rows, start, end, calendar)
