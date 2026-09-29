"""SQLite business tables and atomic coverage updates. No SDK calls here."""
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from functools import cached_property
from types import MappingProxyType
from datetime import date, datetime, timezone
from pathlib import Path
from typing import get_type_hints, get_args, get_origin, Union, Literal, Optional
import logging
import sqlite3
import threading
import uuid

from qmt_rpyc.contracts import financials
from qmt_rpyc.contracts.instruments import Instrument
from qmt_rpyc.contracts.market import DailyBar
from qmt_rpyc.contracts.options import OptionContract
from qmt_rpyc.contracts.reference import DividendEvent
from qmt_rpyc.transport import codec

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Dataset:
    table: str
    model: object
    date_field: str = ''

    @cached_property
    def types(self):
        hints = get_type_hints(self.model) if is_dataclass(self.model) else {'value': self.model}
        result = {}
        for name, hint in hints.items():
            if get_origin(hint) is Union:
                args = tuple(arg for arg in get_args(hint) if arg is not type(None))
                hint = args[0] if len(args) == 1 else hint
            if get_origin(hint) is Literal:
                hint = type(get_args(hint)[0])
            result[name] = 'INTEGER' if hint in (int, bool) else 'REAL' if hint is float else (
                'TEXT' if hint in (str, date, datetime) else 'JSON')
        return MappingProxyType(result)

    @cached_property
    def columns(self):
        return tuple(f.name for f in fields(self.model)) if is_dataclass(self.model) else ('value',)


DATASETS = {
    'daily_bars': Dataset('daily_bars', DailyBar, 'trade_date'),
    'instruments': Dataset('instruments', Instrument),
    'option_contracts': Dataset('option_contracts', OptionContract),
    'option_underlyings': Dataset('option_underlyings', str),
    'option_expiry_dates': Dataset('option_expiry_dates', date),
    'trading_dates': Dataset('trading_dates', date),
    'dividend_events': Dataset('dividend_events', DividendEvent, 'event_date'),
    'Balance': Dataset('financial_balance', financials.BalanceRecord),
    'Income': Dataset('financial_income', financials.IncomeRecord),
    'CashFlow': Dataset('financial_cashflow', financials.CashFlowRecord),
    'Capital': Dataset('financial_capital', financials.CapitalRecord),
    'PershareIndex': Dataset('financial_pershare', financials.PershareIndexRecord),
}


@dataclass(frozen=True)
class Partition:
    dataset: str
    entity: str
    basis: str = ''


@dataclass(frozen=True)
class Write:
    partition: Partition
    rows: tuple
    start: date
    end: date
    reusable: bool
    updated: datetime
    state: str = ''
    invalidate_financial: bool = False


@dataclass(frozen=True)
class Snapshot:
    rows: tuple
    coverage: tuple
    state_updated: Optional[datetime] = None


def instant(text):
    return datetime.fromisoformat(text)


def ordinal(value):
    return date.fromordinal(value)


class Repository:
    def __init__(self, path, scope, timeout=5):
        self.path, self.scope, self.timeout = Path(path), scope, timeout
        self._init_lock = threading.Lock()
        self._initialized = False
        self._status_lock = threading.Lock()
        self._error = None
        self.read_failures = self.write_failures = 0
        self._prepare()

    def health(self):
        with self._status_lock:
            return dict(enabled=True, degraded=self._error is not None, error=self._error,
                        read_failures=self.read_failures, write_failures=self.write_failures)

    def _failure(self, kind):
        logger.exception('Persistent data %s failed; preserving database', kind)
        with self._status_lock:
            self._error = 'persistent data ' + kind + ' failed'
            if kind == 'read':
                self.read_failures += 1
            else:
                self.write_failures += 1

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(str(self.path), timeout=self.timeout)
        try:
            connection.execute('PRAGMA foreign_keys=ON')
            yield connection
        finally:
            connection.close()

    def _prepare(self):
        try:
            with self._init_lock:
                if self._initialized:
                    return True
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.connection() as db:
                    # Refuse unknown versions; never delete or recreate a damaged database.
                    db.execute('PRAGMA journal_mode=WAL')
                    version = db.execute('PRAGMA user_version').fetchone()[0]
                    if version not in (0, SCHEMA_VERSION):
                        raise ValueError('unsupported persistent data schema version')
                    with db:
                        db.execute('CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)')
                        if version == 0:
                            db.execute('INSERT INTO schema_meta VALUES (?)', (SCHEMA_VERSION,))
                        for dataset in DATASETS.values():
                            columns = ', '.join('"' + name + '" ' + kind for name,kind in dataset.types.items())
                            db.execute('CREATE TABLE IF NOT EXISTS ' + dataset.table + ' ('
                                'source_scope TEXT NOT NULL, entity TEXT NOT NULL, basis TEXT NOT NULL, '
                                'day INTEGER NOT NULL, position INTEGER NOT NULL, generation TEXT NOT NULL, '
                                'updated_at TEXT NOT NULL, ' + columns + ', '
                                'PRIMARY KEY(source_scope, entity, basis, day, position))')
                        db.execute('CREATE TABLE IF NOT EXISTS cache_coverage ('
                            'source_scope TEXT NOT NULL, dataset TEXT NOT NULL, entity TEXT NOT NULL, basis TEXT NOT NULL, '
                            'start_day INTEGER NOT NULL, end_day INTEGER NOT NULL, reusable INTEGER NOT NULL, '
                            'generation TEXT NOT NULL, updated_at TEXT NOT NULL)')
                        db.execute('CREATE INDEX IF NOT EXISTS coverage_partition ON cache_coverage '
                                   '(source_scope,dataset,entity,basis,start_day,end_day)')
                        db.execute('CREATE TABLE IF NOT EXISTS cache_state ('
                            'source_scope TEXT NOT NULL, dataset TEXT NOT NULL, entity TEXT NOT NULL, basis TEXT NOT NULL, '
                            'state TEXT NOT NULL, updated_at TEXT NOT NULL, generation TEXT NOT NULL, '
                            'PRIMARY KEY(source_scope,dataset,entity,basis,state))')
                        db.execute('PRAGMA user_version=' + str(SCHEMA_VERSION))
                self._initialized = True
                return True
        except (sqlite3.Error, OSError, ValueError):
            self._failure('read')
            return False

    def _key(self, partition):
        return self.scope, partition.dataset, partition.entity, partition.basis

    def read(self, partition, start, end, policy, now, state='', state_policy=None):
        if not self._prepare():
            return None
        dataset = DATASETS[partition.dataset]
        try:
            with self.connection() as db:
                db.execute('BEGIN')
                coverage = db.execute('SELECT start_day,end_day,updated_at FROM cache_coverage '
                    'WHERE source_scope=? AND dataset=? AND entity=? AND basis=? AND reusable=1',
                    self._key(partition)).fetchall()
                coverage = tuple((ordinal(a), ordinal(b)) for a,b,t in coverage if policy.valid(instant(t), now))
                cols = ','.join('"' + col + '"' for col in dataset.columns)
                records = db.execute('SELECT ' + cols + ',updated_at FROM ' + dataset.table +
                    ' WHERE source_scope=? AND entity=? AND basis=? AND day BETWEEN ? AND ? ORDER BY day,position',
                    (self.scope, partition.entity, partition.basis, start.toordinal(), end.toordinal())).fetchall()
                rows = []
                for record in records:
                    if not policy.valid(instant(record[-1]), now):
                        continue
                    values = [codec.loads(value) if dataset.types[name] == 'JSON' and value is not None else value
                              for name,value in zip(dataset.columns,record[:-1])]
                    payload = dict(zip(dataset.columns, values)) if is_dataclass(dataset.model) else values[0]
                    rows.append(codec.decode(dataset.model, payload))
                updated = None
                if state:
                    value = db.execute('SELECT updated_at FROM cache_state WHERE source_scope=? AND dataset=? '
                        'AND entity=? AND basis=? AND state=?', self._key(partition) + (state,)).fetchone()
                    if value and (state_policy or policy).valid(instant(value[0]), now):
                        updated = instant(value[0])
                return Snapshot(tuple(rows), coverage, updated)
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, codec.ProtocolError):
            self._failure('read')
            return None

    def write(self, writes):
        if not writes:
            return True
        if not self._prepare():
            return False
        try:
            with self.connection() as db, db:
                db.execute('BEGIN IMMEDIATE')
                for write in writes:
                    self._write(db, write)
            return True
        except (sqlite3.Error, OSError, ValueError, TypeError, OverflowError, codec.ProtocolError):
            self._failure('write')
            return False

    def _write(self, db, write):
        p, dataset = write.partition, DATASETS[write.partition.dataset]
        key = self._key(p)
        a,b = write.start.toordinal(), write.end.toordinal()
        if a>b:
            raise ValueError('invalid storage interval')
        generation = uuid.uuid4().hex
        updated = write.updated.isoformat()
        if write.invalidate_financial:
            db.execute('DELETE FROM cache_coverage WHERE source_scope=? AND dataset=? AND entity=?', key[:3])
            db.execute('DELETE FROM cache_state WHERE source_scope=? AND dataset=? AND entity=?', key[:3])
        else:
            # Split overlapping intervals, retaining the ORIGINAL timestamp on both tails.
            previous = db.execute('SELECT rowid,start_day,end_day,reusable,generation,updated_at FROM cache_coverage '
                'WHERE source_scope=? AND dataset=? AND entity=? AND basis=? AND start_day<=? AND end_day>=?', key+(b,a)).fetchall()
            for rowid,lo,hi,reusable,old_generation,old_updated in previous:
                db.execute('DELETE FROM cache_coverage WHERE rowid=?', (rowid,))
                for left,right in ((lo,a-1),(b+1,hi)):
                    if left<=right:
                        db.execute('INSERT INTO cache_coverage VALUES (?,?,?,?,?,?,?,?,?)',
                                   key+(left,right,reusable,old_generation,old_updated))
        # Observations invalidate coverage but do not delete absent source rows.
        # A later verified replacement removes the entire covered range.
        if write.reusable:
            db.execute('DELETE FROM '+dataset.table+' WHERE source_scope=? AND entity=? AND basis=? AND day BETWEEN ? AND ?',
                       (self.scope,p.entity,p.basis,a,b))
        positions = {}
        for row in write.rows:
            payload = codec.encode(row)
            codec.decode(dataset.model, payload)
            day = row if dataset.model is date else getattr(row, dataset.date_field) if dataset.date_field else (
                getattr(row, 'm_timetag' if p.basis=='report_time' else 'm_anntime') if p.basis else write.start)
            if not write.start<=day<=write.end:
                raise ValueError('row outside storage interval')
            position = positions.get(day,0); positions[day]=position+1
            values = tuple(codec.dumps(payload[col]) if dataset.types[col] == 'JSON' else payload[col]
                           for col in dataset.columns) if isinstance(payload,dict) else (payload,)
            cols = ','.join('"'+col+'"' for col in dataset.columns)
            db.execute('INSERT OR REPLACE INTO '+dataset.table+' (source_scope,entity,basis,day,position,generation,updated_at,'+cols+') VALUES ('+
                       ','.join('?' for _ in range(7+len(values)))+')',
                       (self.scope,p.entity,p.basis,day.toordinal(),position,generation,updated)+values)
        db.execute('INSERT INTO cache_coverage VALUES (?,?,?,?,?,?,?,?,?)',key+(a,b,int(write.reusable),generation,updated))
        db.execute('DELETE FROM cache_state WHERE source_scope=? AND dataset=? AND entity=? AND basis=?',key)
        if write.state and write.reusable:
            db.execute('INSERT INTO cache_state VALUES (?,?,?,?,?,?,?)',key+(write.state,updated,generation))
