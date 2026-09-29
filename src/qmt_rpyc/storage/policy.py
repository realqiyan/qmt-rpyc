"""Configurable freshness rules, independent of rows and query planning."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Mapping
from qmt_rpyc.contracts.operations import CONTRACT_VERSION
import hashlib
import json
import math

SHANGHAI = timezone(timedelta(hours=8))
HISTORICAL = ('daily_bars', 'trading_dates', 'dividend_events', 'financials')
DAILY = ('instruments', 'option_contracts', 'option_underlyings', 'option_expiry_dates', 'adjustment_events')


def utc_now():
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Freshness:
    mode: str
    seconds: float = 0

    def __post_init__(self):
        if self.mode not in ('forever', 'day', 'ttl'):
            raise ValueError('freshness must be forever, day, or positive seconds')
        if self.mode == 'ttl' and (not math.isfinite(self.seconds) or self.seconds <= 0):
            raise ValueError('TTL must be finite and positive')

    def valid(self, updated, now):
        if updated > now:
            return False
        if self.mode == 'forever':
            return True
        if self.mode == 'day':
            return updated.astimezone(SHANGHAI).date() == now.astimezone(SHANGHAI).date()
        return (now - updated).total_seconds() < self.seconds

    @classmethod
    def parse(cls, value):
        if value in ('forever', 'day'):
            return cls(value)
        if type(value) in (int, float):
            return cls('ttl', float(value))
        raise ValueError('freshness must be forever, day, or positive seconds')


@dataclass(frozen=True)
class StorageConfig:
    path: Path
    source_scope: str
    policies: Mapping[str, Freshness]
    busy_timeout: float = 5.0

    @classmethod
    def from_config(cls, config, default_dir):
        raw = config.get('cache_policies', '{}')
        overrides = json.loads(raw) if isinstance(raw, str) else dict(raw)
        if not isinstance(overrides, dict) or set(overrides) - set(HISTORICAL + DAILY):
            raise ValueError('unknown persistent-data freshness category')
        policies = {name: Freshness('forever') for name in HISTORICAL}
        policies.update({name: Freshness('day') for name in DAILY})
        policies.update({name: Freshness.parse(value) for name, value in overrides.items()})
        adapter = config.get('adapter', 'xtquant_2.0.6.1')
        # Never include credentials or account identity in the database namespace.
        identity = [adapter, CONTRACT_VERSION, config.get('cache_source', ''), config.get('qmt_path', ''),
                    config.get('xtquant_path', ''), config.get('bigqmt_pipe', '')]
        scope = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
        return cls(Path(config.get('cache_path') or default_dir / 'data.sqlite3'), scope,
                   MappingProxyType(policies))
