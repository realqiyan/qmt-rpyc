"""Read-through application service over typed providers and business repositories."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date, timedelta
from functools import partial
import logging

from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import BatchResult, Failure, ItemError, Success
from qmt_rpyc.contracts.financials import FINANCIAL_TABLES, FinancialReports
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


@dataclass(frozen=True)
class History:
    """Verified rows for the closed part of a request, and what backs them."""
    start: date
    rows: tuple
    writes: tuple
    complete: bool
    # True once the closed part was read from the source, so a later source
    # fallback on refresh does not fetch the raw bars again to seed the cache.
    fetched: bool = False


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

    def read(self, partition, start, end, category, state='', state_category=None):
        return self.repository.read(partition, start, end, self.policies[category], self.clock(),
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

    def bounds(self, request):
        today = self.clock().astimezone(SHANGHAI).date()
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

    def calendar(self, request, start, end):
        """Sessions of a closed window from the stored calendar, for evidence."""
        market = self.evidence.market_for(request.codes[0])
        if market is None or start > end:
            return None
        try:
            return self.trading_dates(TradingDatesRequest(market, start, end))
        except (ProviderError, ValueError, TypeError, KeyError, codec.ProtocolError):
            # Evidence that cannot get the calendar simply proves nothing; the
            # caller still answers from the source.
            logger.warning('Cannot reuse the stored calendar as bar evidence', exc_info=True)
            return None
        except Exception:
            logger.exception('Unexpected failure while reading the stored calendar')
            return None

    def range_read(self, partition, request, category, load, row_date, *, state='', state_category=None):
        """Return rows and staged write; callers can atomically publish dependencies."""
        start, end = self.bounds(request)
        if not request.refresh:
            cached = self.read(partition, start, end, category, state, state_category)
            if cached is not None and not gaps(start, end, cached.coverage) and (not state or cached.state_updated):
                rows = tuple(row for row in cached.rows if start <= row_date(row) <= end)
                count = getattr(request, 'count', None)
                return (rows[-count:] if count else rows), None
        started = self.clock()
        rows = tuple(load())
        if any(not start <= row_date(row) <= end for row in rows):
            raise ValueError('source result outside query interval')
        proof = self.evidence.assess(partition.dataset, request, rows, start, end)
        return rows, Write(partition, rows, start, end, proof.reusable, started, state)

    def trading_dates(self, request):
        with self.locks.hold(('calendar', request.market)):
            today = self.clock().astimezone(SHANGHAI).date()
            if request.end and request.end > today:
                raise ProviderError('INVALID_ARGUMENTS', 'market.get_trading_dates', 'future calendar coverage is not verified')
            start, end = self.bounds(request)
            if request.refresh or request.count is not None or request.start is None or end != today:
                # Open and counted calendars may not infer a beginning from the
                # earliest stored day, so they keep the original source path.
                return self._closed_dates(request)
            partition = Partition('trading_dates', request.market)
            cached = self.read(partition, start, end, 'trading_dates')
            history_end, tail_start = self.live_split(
                start, end, today, self.proven_end(cached.coverage) if cached else None)
            if history_end is None:
                return self._closed_dates(request)
            rows = self._closed_dates(replace(request, end=history_end))
            tail, writes = self._tail_dates(request, partition, tail_start, end, today)
            self.repository.write(writes)
            return tuple(sorted(set(rows) | set(tail)))

    def _closed_dates(self, request):
        rows, write = self.range_read(Partition('trading_dates', request.market), request, 'trading_dates',
            lambda: self.call('market.get_trading_dates', self.source.market.get_trading_dates, request), lambda day: day)
        self.repository.write((write,) if write else ())
        return rows

    def _tail_dates(self, request, partition, start, end, today):
        """The unproven calendar remainder; only its closed days are persisted."""
        query = replace(request, start=start, end=end, count=None)
        started = self.clock()
        rows = self.call('market.get_trading_dates', self.source.market.get_trading_dates, query)
        if any(not start <= day <= end for day in rows):
            raise ValueError('source result outside query interval')
        closed = tuple(day for day in rows if day < today)
        if not closed:
            return rows, ()
        closed_end = max(closed)
        proof = self.evidence.assess('trading_dates', replace(query, end=closed_end), closed, start, closed_end)
        if not proof.reusable:
            return rows, ()
        return rows, (Write(partition, closed, start, closed_end, proof.reusable, started),)

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
        today = self.clock().astimezone(SHANGHAI).date()
        return self.batch(request, lambda code: self._daily(replace(request, codes=(code,)), today))

    def raw_write(self, request, rows, started):
        today = self.clock().astimezone(SHANGHAI).date()
        rows = tuple(row for row in rows if row.trade_date < today)
        start, end = self.bounds(request)
        end = min(end, today - timedelta(days=1))
        if request.start is None and rows:
            # Prove only the returned suffix, never claim a historical beginning.
            start = rows[0].trade_date
        if start > end:
            return None
        evidence_request = replace(request, start=start, end=end, count=None) if rows else request
        proof = self.evidence.assess('daily_bars', evidence_request, rows, start, end,
                                     partial(self.calendar, request, start, end))
        return Write(Partition('daily_bars', request.codes[0]), rows, start, end, proof.reusable, started)

    def _daily(self, request, today):
        """Verified history up to what the cache proves, then the unproven remainder."""
        code = request.codes[0]
        start, end = self.bounds(request)
        events, event_writes, event_proven = self._adjustment_events(request, code)
        if end > today or (request.count is None and request.start is None):
            # An unprovable future bound or an open range: the exact original
            # request, which may answer an open interval.
            return self._source_daily(request, event_writes)
        partition = Partition('daily_bars', code)
        cached = None if request.refresh else self.read(partition, start, end, 'daily_bars')
        history_end, tail_start = self.live_split(
            start, end, today, self.proven_end(cached.coverage) if cached else None)
        if history_end is None:
            # Nothing proven inside this window, or a refreshed request that would
            # have to splice: the exact original request, which also seeds the cache.
            return self._source_daily(request, event_writes)
        if tail_start is not None and (request.adjustment != 'none' or request.fill_data):
            # Only raw bars are safe to splice across the still-open day: the
            # source's own factors anchor today's adjusted or filled bar, so the
            # whole request stays on the source path.
            return self._source_daily(request, event_writes)
        tail, tail_writes = (), ()
        if tail_start is not None:
            tail, tail_writes = self._tail_bars(request, partition, tail_start, end, today)
            if isinstance(tail, Failure):
                return tail
            if request.count is not None:
                # The unproven remainder is read whole; a counted request keeps
                # only its newest bars before splicing the confirmed history.
                tail = tail[-request.count:]
        history = self._history_bars(request, partition, start, history_end, tail, cached)
        if isinstance(history, Failure):
            return history
        return self._answer(request, code, history.start, end, history.rows + tail,
                            history.complete and event_proven, events,
                            event_writes + tail_writes + history.writes, history.fetched)

    def _adjustment_events(self, request, code):
        """Event dependency rows, the write that carries them, and their verdict."""
        if request.adjustment == 'none':
            return (), (), True
        event_query = DividendQuery(code, refresh=request.refresh)
        events, event_write = self.range_read(Partition('dividend_events', code), event_query, 'dividend_events',
            lambda: self.call('reference.get_dividend_events', self.source.reference.get_dividend_events, event_query),
            lambda row: row.event_date, state='adjustment', state_category='adjustment_events')
        return events, ((event_write,) if event_write else ()), (event_write is None or event_write.reusable)

    def _history_bars(self, request, partition, start, end, tail, cached):
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
            query = replace(request, start=lo, end=hi, count=None, adjustment='none', fill_data=False)
            started = self.clock()
            item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, query).items[0]
            if isinstance(item, Failure):
                return item
            raw = item.value.rows
            proof = self.evidence.assess('daily_bars', query, raw, lo, hi,
                                         partial(self.calendar, query, lo, hi))
            staged.append(Write(partition, raw,lo,hi,proof.reusable,started))
            if not proof.reusable:
                return History(start, rows, tuple(staged), False, True)
            merged.update((row.trade_date,row) for row in raw)
        return History(start, tuple(merged[day] for day in sorted(merged)), tuple(staged), True, True)

    def _tail_bars(self, request, partition, start, end, today):
        """The unproven remainder, read once; only its closed part is persisted."""
        query = replace(request, start=start, end=end, count=None, adjustment='none', fill_data=False)
        started = self.clock()
        item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, query).items[0]
        if isinstance(item, Failure):
            return item, ()
        rows = item.value.rows
        if any(not start <= row.trade_date <= end for row in rows):
            raise ValueError('source result outside query interval')
        closed = tuple(row for row in rows if row.trade_date < today)
        if not closed:
            return rows, ()
        closed_end = max(row.trade_date for row in closed)
        proof = self.evidence.assess('daily_bars', replace(query, end=closed_end), closed, start, closed_end,
                                     partial(self.calendar, query, start, closed_end))
        if not proof.reusable:
            return rows, ()
        return rows, (Write(partition, closed, start, closed_end, proof.reusable, started),)

    def _answer(self, request, code, start, end, rows, complete, events, writes, fetched):
        """Derive locally once every dependency is verified; otherwise ask the source."""
        relevant = tuple(event for event in events if
            (event.event_date > start if request.adjustment.startswith('front') else event.event_date <= end))
        if complete and self.adjustment.supports(relevant, request.adjustment):
            try:
                return self._derive(request, code, start, end, rows, events, writes)
            except UnsupportedDerivation:
                pass
        # Unknown filling/event cases use the exact original query; no partial concatenation.
        return self._source_daily(request, writes, fetched)

    def _derive(self, request, code, start, end, rows, events, writes):
        if request.fill_data:
            market = self.evidence.market_for(code)
            if market is None:
                raise UnsupportedDerivation('market mapping not verified')
            sessions = self.trading_dates(TradingDatesRequest(market, start, end))
            rows = self.fill.derive(rows, sessions, True)
        result = codec.decode(DailyBarSeries, codec.encode(
            DailyBarSeries(self.adjustment.derive(rows,events,request.adjustment),request.adjustment)))
        self.repository.write(writes)
        return result

    def _source_daily(self, request, writes=(), fetched=False):
        """The exact original request; the only path that may answer an open interval."""
        started = self.clock()
        raw_request = replace(request, adjustment='none', fill_data=False)
        if request.refresh and not fetched and (request.adjustment != 'none' or request.fill_data):
            item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, raw_request).items[0]
            if isinstance(item, Failure):
                return item
            write = self.raw_write(raw_request, item.value.rows, started)
            writes = writes + ((write,) if write else ())
        item = self.call('market.get_daily_bars', self.source.market.get_daily_bars, request).items[0]
        if isinstance(item, Failure):
            return item
        if request.adjustment == 'none' and not request.fill_data:
            write = self.raw_write(raw_request, item.value.rows, started)
            writes = writes + ((write,) if write else ())
        self.repository.write(writes)
        return item.value
