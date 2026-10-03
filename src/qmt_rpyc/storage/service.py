"""Read-through application service over typed providers and business repositories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from functools import partial
import logging

from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import BatchResult, CachedCodesRequest, Failure, ItemError, Success
from qmt_rpyc.contracts.financials import FINANCIAL_TABLES, FinancialReports
from qmt_rpyc.contracts.instruments import KnownDate
from qmt_rpyc.contracts.market import DailyBarSeries, TradingDatesRequest
from qmt_rpyc.contracts.options import ExpiryDates
from qmt_rpyc.contracts.reference import DividendQuery
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.contracts.validation import validate_result
from qmt_rpyc.transport import codec

from .adjustment import AdjustmentPolicy, FillPolicy, UnsupportedDerivation
from .concurrency import PartitionLocks
from .coverage import ConservativeEvidence, gaps, covered, covered_suffix
from .policy import SHANGHAI, utc_now
from .repository import Partition, Write

logger = logging.getLogger(__name__)
FULL_START, FULL_END = date.min, date.max
DAY = timedelta(days=1)
# An unset daily-bar beginning is a window of this length, anchored at the request end.
DAYS_PER_YEAR = 365


@dataclass(frozen=True)
class QueryTime:
    """One acquisition boundary shared by cache checks and dependency writes."""
    started: datetime

    @property
    def today(self):
        return self.started.astimezone(SHANGHAI).date()


@dataclass(frozen=True)
class History:
    """Verified rows for the closed part of a request, and what backs them."""
    start: date
    rows: tuple
    writes: tuple
    complete: bool


class PersistentData:
    def __init__(self, source, repository, policies, workers=8, clock=utc_now,
                 evidence=None, adjustment=None, fill=None):
        self.source, self.repository, self.policies = source, repository, policies
        self.workers, self.clock = workers, clock
        self.evidence = evidence or ConservativeEvidence()
        self.adjustment = adjustment or AdjustmentPolicy()
        self.fill = fill or FillPolicy()
        self.locks = PartitionLocks()

    def call(self, operation, method, request):
        capability = self.source.capabilities.operations[operation]
        if not capability.available:
            raise ProviderError('API_UNAVAILABLE', operation, capability.reason)
        result = codec.decode(OPERATIONS[operation].response_type, codec.encode(method(request)))
        validate_result(operation, request, result)
        return result

    def batch(self, request, one):
        if not request.codes:
            return BatchResult(())
        def run(code):
            try:
                with self.locks.hold(('security', code)):
                    value = one(code)
                    return value if isinstance(value, Failure) else Success(code, value)
            except ProviderError:
                logger.warning('Persistent query source failure for one item', exc_info=True)
                return Failure(code, ItemError('SOURCE_ERROR', 'source unavailable for requested data'))
            except (ValueError, TypeError, KeyError, codec.ProtocolError):
                logger.warning('Persistent query result validation failed', exc_info=True)
                return Failure(code, ItemError('INVALID_RESULT', 'source result does not satisfy the contract'))
            except Exception:
                logger.exception('Persistent query failed for one item')
                return Failure(code, ItemError('SOURCE_ERROR', 'source item failed'))
        with ThreadPoolExecutor(max_workers=min(self.workers, len(request.codes))) as pool:
            return BatchResult(tuple(pool.map(run, request.codes)))

    def read(self, partition, start, end, category, state='', state_category=None, context=None):
        return self.repository.read(partition, start, end, self.policies[category],
                                    context.started if context else self.clock(),
                                    state, self.policies[state_category] if state_category else None)

    def snapshot(self, partition, category, refresh, load, unpack, pack):
        with self.locks.hold((partition.dataset, partition.entity)):
            if not refresh:
                cached = self.read(partition, FULL_START, FULL_END, category, 'complete')
                if cached is not None and cached.state_updated is not None:
                    try:
                        return pack(cached.rows)
                    except (ValueError, IndexError):
                        logger.warning("Cached collection no longer satisfies its query semantics", exc_info=True)
            # Start time is conservative if a source call crosses midnight.
            started = self.clock()
            value = load()
            if isinstance(value, Failure):
                return value
            rows = tuple(unpack(value))
            self.repository.write((Write(partition, rows, FULL_START, FULL_END, True, started, 'complete'),))
            return value

    def details(self, request, contracts=False):
        dataset = 'option_contracts' if contracts else 'instruments'
        operation = 'options.get_contract_details' if contracts else 'instruments.get_details'
        method = self.source.options.get_contract_details if contracts else self.source.instruments.get_details
        if not request.codes:
            return BatchResult(())
        # Preserve the source's grouped reads (notably BigQMT option records).
        # Sorted acquisition avoids deadlocks between overlapping batches.
        with ExitStack() as stack:
            for code in sorted(request.codes):
                stack.enter_context(self.locks.hold(('security', code)))
            results, missing = {}, []
            for code in request.codes:
                cached = None if request.refresh else self.read(
                    Partition(dataset,code),FULL_START,FULL_END,dataset,'complete')
                if cached is not None and cached.state_updated is not None and len(cached.rows) == 1:
                    results[code] = Success(code,cached.rows[0])
                else:
                    missing.append(code)
            if missing:
                started = self.clock()
                query = replace(request,codes=tuple(missing),refresh=True)
                try:
                    loaded = self.call(operation,method,query)
                except (ValueError, TypeError, KeyError, codec.ProtocolError):
                    logger.warning('Persistent batch source validation failed',exc_info=True)
                    loaded = BatchResult(tuple(Failure(code,ItemError('INVALID_RESULT','source result failed validation'))
                                               for code in missing))
                except Exception:
                    logger.exception('Persistent batch source unavailable')
                    loaded = BatchResult(tuple(Failure(code,ItemError('SOURCE_ERROR','source unavailable for requested data'))
                                               for code in missing))
                writes = []
                for item in loaded.items:
                    results[item.code] = item
                    if isinstance(item,Success):
                        writes.append(Write(Partition(dataset,item.code),(item.value,),FULL_START,FULL_END,
                                            True,started,'complete'))
                self.repository.write(writes)
            return BatchResult(tuple(results[code] for code in request.codes))

    def underlyings(self, request):
        return self.snapshot(Partition('option_underlyings', ''), 'option_underlyings', request.refresh,
            lambda: self.call('instruments.list_option_underlyings', self.source.instruments.list_option_underlyings,
                              replace(request, refresh=True)), tuple, tuple)

    def expiry_dates(self, request):
        today = self.clock().astimezone(SHANGHAI).date()
        def pack(rows):
            return ExpiryDates(today, rows)
        return self.snapshot(Partition('option_expiry_dates', request.underlying), 'option_expiry_dates', request.refresh,
            lambda: self.call('options.get_expiry_dates', self.source.options.get_expiry_dates, replace(request, refresh=True)),
            lambda result: result.dates, pack)

    def bounds(self, request, context=None):
        today = (context or QueryTime(self.clock())).today
        return request.start or FULL_START, request.end or (FULL_END if isinstance(request, DividendQuery) else today)

    @staticmethod
    def proven_end(coverage):
        """Last day the cache proves, or None when it proves nothing at all."""
        return max((hi for _, hi in coverage), default=None)

    def live_split(self, start, end, today, proven):
        """Boundary between what the cache proves and what must be read again.

        Only the current Shanghai day is still open, so everything the cache
        proves is history and the remainder is read from the source once. A
        boundary the cache does not reach leaves nothing to splice, and the
        caller keeps the exact original request instead.
        """
        if end != today:
            return end, None
        history_end = min(end - DAY, proven) if proven is not None else None
        if history_end is None or history_end < start:
            return None, None
        return history_end, history_end + DAY

    def calendar(self, request, start, end, context=None):
        """Source sessions for evidence, reusing closed calendar coverage when valid."""
        market = self.evidence.market_for(request.codes[0])
        if market is None or start > end:
            return None
        try:
            return self.trading_dates(TradingDatesRequest(market, start, end), context)
        except (ProviderError, ValueError, TypeError, KeyError, codec.ProtocolError):
            # Evidence that cannot get the calendar simply proves nothing; the
            # caller still answers from the source.
            logger.warning('Cannot reuse the stored calendar as bar evidence', exc_info=True)
            return None
        except Exception:
            logger.exception('Unexpected failure while reading the stored calendar')
            return None

    def range_read(self, partition, request, category, load, row_date, *, state='', state_category=None, context=None):
        """Return rows and staged write; callers can atomically publish dependencies."""
        context = context or QueryTime(self.clock())
        start, end = self.bounds(request, context)
        if not request.refresh:
            cached = self.read(partition, start, end, category, state, state_category, context)
            if cached is not None and not gaps(start, end, cached.coverage) and (not state or cached.state_updated):
                rows = tuple(row for row in cached.rows if start <= row_date(row) <= end)
                count = getattr(request, 'count', None)
                return (rows[-count:] if count else rows), None
        rows = tuple(load())
        if any(not start <= row_date(row) <= end for row in rows):
            raise ValueError('source result outside query interval')
        if category == 'trading_dates':
            return rows, self.historical_write(partition, request, rows, start, end, context, row_date)
        proof = self.evidence.assess(partition.dataset, request, rows, start, end)
        return rows, Write(partition, rows, start, end, proof.reusable, context.started, state)

    def historical_write(self, partition, request, rows, start, end, context, row_date):
        """Only days closed when acquisition began may enter historical storage."""
        end = min(end, context.today - DAY)
        if start > end:
            return None
        closed = tuple(row for row in rows if row_date(row) <= end)
        query = replace(request, end=end) if request.end is not None else request
        calendar = partial(self.calendar, query, start, end, context) if partition.dataset == 'daily_bars' else None
        proof = self.evidence.assess(partition.dataset, query, closed, start, end, calendar)
        return Write(partition, closed, start, end, proof.reusable, context.started)

    def trading_dates(self, request, context=None):
        with self.locks.hold(('calendar', request.market)):
            context = context or QueryTime(self.clock())
            today = context.today
            if request.end and request.end > today:
                raise ProviderError('INVALID_ARGUMENTS', 'market.get_trading_dates', 'future calendar coverage is not verified')
            start, end = self.bounds(request, context)
            if request.refresh or request.count is not None or request.start is None or end != today:
                # Open and counted calendars may not infer a beginning from the
                # earliest stored day, so they keep the original source path.
                return self._closed_dates(request, context)
            partition = Partition('trading_dates', request.market)
            cached = self.read(partition, start, end, 'trading_dates', context=context)
            history_end, tail_start = self.live_split(
                start, end, today, self.proven_end(cached.coverage) if cached else None)
            if history_end is None:
                return self._closed_dates(request, context)
            rows = self._closed_dates(replace(request, end=history_end), context)
            tail, writes = self._tail_dates(request, partition, tail_start, end, context)
            self.repository.write(writes)
            return tuple(sorted(set(rows) | set(tail)))

    def _closed_dates(self, request, context):
        rows, write = self.range_read(Partition('trading_dates', request.market), request, 'trading_dates',
            lambda: self.call('market.get_trading_dates', self.source.market.get_trading_dates,
                              replace(request, end=request.end or context.today)),
            lambda day: day, context=context)
        self.repository.write((write,) if write else ())
        return rows

    def _tail_dates(self, request, partition, start, end, context):
        """The unproven calendar remainder; only its closed days are persisted."""
        query = replace(request, start=start, end=end, count=None)
        rows = self.call('market.get_trading_dates', self.source.market.get_trading_dates, query)
        if any(not start <= day <= end for day in rows):
            raise ValueError('source result outside query interval')
        closed = tuple(day for day in rows if day < context.today)
        if not closed:
            return rows, ()
        closed_end = max(closed)
        write = self.historical_write(partition, query, closed, start, closed_end, context, lambda day: day)
        if write is None or not write.reusable:
            return rows, ()
        return rows, (write,)

    def dividends(self, request):
        with self.locks.hold(('security', request.code)):
            rows, write = self.range_read(Partition('dividend_events', request.code), request, 'dividend_events',
                lambda: self.call('reference.get_dividend_events', self.source.reference.get_dividend_events, request),
                lambda row: row.event_date)
            self.repository.write((write,) if write else ())
            return rows

    def financials(self, request):
        def one(code):
            query = replace(request, codes=(code,))
            start, end = self.bounds(query)
            values = {table: None for table in FINANCIAL_TABLES}
            hit = not request.refresh
            if hit:
                for table in request.tables:
                    cached = self.read(Partition(table, code, request.date_basis), start, end, 'financials')
                    if cached is None or gaps(start, end, cached.coverage):
                        hit = False
                        break
                    values[table] = cached.rows
            if hit:
                return FinancialReports(**values)
            started = self.clock()
            item = self.call('financials.get_reports', self.source.financials.get_reports, query).items[0]
            if isinstance(item, Failure):
                return item
            writes = []
            for table in request.tables:
                rows = getattr(item.value, table)
                field = 'm_timetag' if request.date_basis == 'report_time' else 'm_anntime'
                if any(not start <= getattr(row,field) <= end for row in rows):
                    raise ValueError('financial source returned records outside query')
                proof = self.evidence.assess(table, query, rows, start, end)
                writes.append(Write(Partition(table,code,request.date_basis), rows,start,end,
                                    proof.reusable,started,invalidate_financial=True))
            self.repository.write(writes)
            return item.value
        return self.batch(request, one)

    def daily_bars(self, request):
        """Answer one security at a time against its own proven cache boundary."""
        # A counted request has no beginning to resolve, so it needs no listing date.
        listed = self.listing_dates(request.codes) if request.count is None else {}
        def one(code):
            context = QueryTime(self.clock())
            query = self.normalized(replace(request, codes=(code,)), code, context.today, listed)
            return self._daily(query, context)
        return self.batch(request, one)

    def listing_dates(self, codes):
        """Listing dates the source reports, for the beginning clamp. Absent means the
        requested beginning is kept rather than guessed."""
        return {item.code: item.value.listed_date.value
                for item in self.details(CachedCodesRequest(tuple(codes))).items
                if isinstance(item, Success) and isinstance(item.value.listed_date, KnownDate)}

    def normalized(self, request, code, today, listed):
        """Resolve a daily-bar beginning into a window the bridge can verify.

        An unset beginning becomes one year before the request end, because an open
        beginning is never reusable. A beginning earlier than the reported listing date
        starts at the listing instead, where the source has no fabricated rows.
        """
        if request.count is not None:
            return replace(request, end=request.end or today)
        end = request.end or today
        start = request.start or end - timedelta(days=DAYS_PER_YEAR)
        first = listed.get(code)
        return replace(request, start=first if first is not None and start < first <= end else start, end=end)

    def raw_write(self, request, rows, started):
        context = QueryTime(started)
        rows = tuple(row for row in rows if row.trade_date < context.today)
        start, end = self.bounds(request, context)
        if request.start is None and rows:
            # Prove only the returned suffix, never claim a historical beginning.
            start = rows[0].trade_date
        evidence_request = replace(request, start=start, end=end, count=None) if rows else request
        return self.historical_write(Partition('daily_bars', request.codes[0]), evidence_request,
                                     rows, start, end, context, lambda row: row.trade_date)

    def derivable(self, request, events, start, end):
        """Whether the bridge can reproduce this request's own result locally.

        Check formula support for the supplied window, not data completeness.
        Closed count queries supply their actual suffix start after selecting rows.
        Front's session completeness and filling are checked in _derive.
        """
        return self.adjustment.supports(self.relevant_events(request, events, start, end), request.adjustment)

    def may_derive(self, request, events, start, end, event_proven):
        """Whether local derivation is allowed from the events read for this request.

        An explicit ``event_cutoff`` is the caller's opt-in: derive from the events
        read for this request even when the source does not attest reusable coverage,
        because the source's own adjustment reads the same data. Without a cutoff the
        bridge keeps requiring reusable event coverage. An event the formula cannot
        reproduce (a relevant gugai) still fails to derive in either case.
        """
        if not self.derivable(request, events, start, end):
            return False
        return request.event_cutoff is not None or event_proven

    def relevant_events(self, request, events, start, end):
        """Apply the cutoff, then select front events after start or back events through end.

        Front includes events after the query end to preserve the latest anchor.
        For a cached count suffix, start is its first selected bar's date.
        """
        events = self._cut_events(request, events)
        if request.adjustment.startswith('front'):
            return tuple(event for event in events if event.event_date > start)
        return tuple(event for event in events if event.event_date <= end)

    def _cut_events(self, request, events):
        """Drop events after an explicit cutoff so a historical request stays causal."""
        cutoff = request.event_cutoff
        if cutoff is None:
            return events
        return tuple(event for event in events if event.event_date <= cutoff)

    @staticmethod
    def _cutoff_failure(code):
        return Failure(code, ItemError(
            'SOURCE_ERROR', 'event_cutoff requires a locally derivable adjustment'))

    def _daily(self, request, context):
        """Verified history up to what the cache proves, then the unproven remainder."""
        code = request.codes[0]
        today = context.today
        start, end = self.bounds(request, context)
        events, event_writes, event_proven = self._adjustment_events(request, code, context)
        if end > today or (request.count is None and request.start is None):
            return self._source_daily(request, events, event_proven, event_writes, context)
        partition = Partition('daily_bars', code)
        cached = None if request.refresh else self.read(partition, start, end, 'daily_bars', context=context)
        history_end, tail_start = self.live_split(
            start, end, today, self.proven_end(cached.coverage) if cached else None)
        if history_end is None:
            return self._source_daily(request, events, event_proven, event_writes, context)
        # Closed count queries resolve their actual suffix start in _history_bars;
        # _answer checks support against that boundary instead of date.min.
        if tail_start is not None and not self.derivable(request, events, start, end):
            # An adjustment this bridge cannot reproduce locally, so the still-open day
            # and the whole window keep the source's own factors in one call.
            return self._source_daily(request, events, event_proven, event_writes, context)
        tail, tail_writes = (), ()
        if tail_start is not None:
            tail, tail_writes = self._tail_bars(request, partition, tail_start, end, context)
            if isinstance(tail, Failure):
                return tail
            if request.count is not None:
                # The unproven remainder is read whole; a counted request keeps
                # only its newest bars before splicing the confirmed history.
                tail = tail[-request.count:]
        history = self._history_bars(request, partition, start, history_end, tail, cached, context)
        if isinstance(history, Failure):
            return history
        return self._answer(request, code, history.start, end, history.rows + tail,
                            history.complete, event_proven, events,
                            event_writes + tail_writes + history.writes, context)

    def _adjustment_events(self, request, code, context):
        """Event dependency rows, the write that carries them, and their verdict."""
        if request.adjustment == 'none':
            return (), (), True
        event_query = DividendQuery(code, refresh=request.refresh)
        events, event_write = self.range_read(Partition('dividend_events', code), event_query, 'dividend_events',
            lambda: self.call('reference.get_dividend_events', self.source.reference.get_dividend_events, event_query),
            lambda row: row.event_date, state='adjustment', state_category='adjustment_events', context=context)
        return events, ((event_write,) if event_write else ()), (event_write is None or event_write.reusable)

    def _history_bars(self, request, partition, start, end, tail, cached, context):
        """Closed-window rows from the given snapshot, with the writes that back them."""
        coverage = cached.coverage if cached else ()
        # Open/count queries cannot infer a beginning from the first stored bar,
        # and rows outside this window belong to the tail read.
        rows = tuple(row for row in cached.rows if start <= row.trade_date <= end
                     and covered(row.trade_date, coverage)) if cached else ()
        if request.count is not None and cached:
            # The live tail already answers part of the count.
            needed = request.count - len(tail)
            if needed <= 0:
                return History(tail[0].trade_date, (), (), True)
            suffix = covered_suffix(end, coverage)
            available = tuple(row for row in rows if suffix is not None and row.trade_date >= suffix)
            if len(available) >= needed:
                chosen = available[-needed:]
                return History(chosen[0].trade_date, chosen, (), True)
        missing = gaps(start, end, coverage)
        if not missing or request.start is None:
            return History(start, rows, (), not missing)
        merged = {row.trade_date: row for row in rows}
        staged = []
        for lo,hi in missing:
            query = replace(request, start=lo, end=hi, count=None, adjustment='none', fill_data=False,
                            event_cutoff=None)
            item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, query).items[0]
            if isinstance(item, Failure):
                return item
            raw = item.value.rows
            write = self.historical_write(partition, query, raw, lo, hi, context, lambda row: row.trade_date)
            if write is not None:
                staged.append(write)
            if write is None or not write.reusable:
                return History(start, rows, tuple(staged), False)
            merged.update((row.trade_date,row) for row in raw)
        return History(start, tuple(merged[day] for day in sorted(merged)), tuple(staged), True)

    def _tail_bars(self, request, partition, start, end, context):
        """The unproven remainder, read once; only its closed part is persisted."""
        query = replace(request, start=start, end=end, count=None, adjustment='none', fill_data=False,
                        event_cutoff=None)
        item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, query).items[0]
        if isinstance(item, Failure):
            return item, ()
        rows = item.value.rows
        if any(not start <= row.trade_date <= end for row in rows):
            raise ValueError('source result outside query interval')
        closed = tuple(row for row in rows if row.trade_date < context.today)
        if not closed:
            return rows, ()
        closed_end = max(row.trade_date for row in closed)
        write = self.historical_write(partition, query, closed, start, closed_end, context, lambda row: row.trade_date)
        if write is None or not write.reusable:
            return rows, ()
        return rows, (write,)

    def _answer(self, request, code, start, end, rows, history_complete, event_proven, events, writes, context):
        """Derive locally once every dependency is verified; otherwise ask the source."""
        if history_complete and self.may_derive(request, events, start, end, event_proven):
            try:
                return self._derive(request, code, start, end, rows, events, writes, context)
            except UnsupportedDerivation:
                if request.event_cutoff is not None:
                    return self._cutoff_failure(code)
                return self._source_answer(request, writes)
        if request.event_cutoff is not None:
            return self._cutoff_failure(code)
        if (request.adjustment == 'front'
                and self.relevant_events(request, events, start, end)
                and any(write.partition.dataset == 'daily_bars' and not write.reusable for write in writes)):
            # A gap response already failed the coverage check. Reading the same
            # raw window again cannot prove front safe; keep the original query.
            return self._source_answer(request, writes)
        # Unknown filling/event cases use the exact original query; no partial concatenation.
        return self._source_daily(request, events, event_proven, writes, context)

    def _derive(self, request, code, start, end, rows, events, writes, context):
        sessions = None
        if request.adjustment == 'front' and self.relevant_events(request, events, start, end):
            # Native front can depend on the query beginning when events fall in a
            # gap between actual bars. Filled rows cannot prove that gap harmless.
            sessions = self.calendar(request, start, end, context)
            if sessions is None or tuple(row.trade_date for row in rows) != sessions:
                raise UnsupportedDerivation('front requires an actual bar for every source session')
        if request.fill_data:
            market = self.evidence.market_for(code)
            if market is None:
                raise UnsupportedDerivation('market mapping not verified')
        # The source fills after adjusting, so a halt crossing an ex-dividend date
        # keeps the adjusted series continuous; the same order is used here.
        adjusted = self.adjustment.derive(rows, self._cut_events(request, events), request.adjustment)
        if request.fill_data:
            if sessions is None:
                sessions = self.trading_dates(TradingDatesRequest(market, start, end), context)
            adjusted = self.fill.derive(adjusted, sessions, True)
        if request.count is not None:
            # A counted request counts filled rows too, so the trim follows the filling.
            adjusted = adjusted[-request.count:]
        result = codec.decode(DailyBarSeries, codec.encode(
            DailyBarSeries(adjusted, request.adjustment)))
        self.repository.write(writes)
        return result

    def _source_daily(self, request, events, event_proven, writes, context):
        """Answer from the source.

        The unadjusted series is the only shape this bridge stores, so a request it can
        reproduce locally reads that series and derives the answer in memory;
        anything else keeps the exact original request. The source path is also the
        only one that may answer an open interval.
        """
        today = context.today
        raw_request = replace(request, adjustment='none', fill_data=False, event_cutoff=None)
        start, end = self.bounds(raw_request, context)
        # A future bound is never provable, so it keeps the source's own answer.
        derivable = end <= today and self.may_derive(request, events, start, end, event_proven)
        if request.event_cutoff is not None and not derivable:
            return self._cutoff_failure(request.codes[0])
        if not (derivable or request.refresh):
            return self._source_answer(request, writes)
        raw = self.call('market.get_daily_bars', self.source.market.get_daily_bars, raw_request).items[0]
        if isinstance(raw, Failure):
            return raw
        write = self.raw_write(raw_request, raw.value.rows, context.started)
        staged = writes + ((write,) if write else ())
        if derivable:
            if request.count is not None and raw.value.rows:
                # A counted read has no beginning of its own: its span is the rows it
                # returned, which is what the filling and the trim then work over.
                start = raw.value.rows[0].trade_date
            try:
                return self._derive(request, request.codes[0], start, end, raw.value.rows, events, staged, context)
            except UnsupportedDerivation:
                if request.event_cutoff is not None:
                    return self._cutoff_failure(request.codes[0])
        return self._source_answer(request, staged)

    def _source_answer(self, request, writes):
        """Whatever the source returns for the request as asked, with staged writes."""
        item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, request).items[0]
        if isinstance(item, Failure):
            return item
        self.repository.write(writes)
        return item.value
