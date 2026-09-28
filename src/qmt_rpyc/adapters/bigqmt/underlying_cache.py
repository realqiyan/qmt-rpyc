"""Persist only successful underlying lists; refresh without blocking stale reads."""
import json
import math
import logging
import os
from pathlib import Path
import threading
import time
import tempfile

from qmt_rpyc.contracts.common import validate_identity
from qmt_rpyc.adapters.errors import ProviderError

logger = logging.getLogger(__name__)


class UnderlyingCache:
    def __init__(self, path=None, ttl=86400, retry=60, wait=20, clock=time.time):
        self.path = Path(path) if path else None
        self.ttl, self.retry, self.wait, self.clock = ttl, retry, wait, clock
        self.lock = threading.Lock()
        self.value, self.updated, self.attempt = None, 0, None
        self.error, self.refreshing = None, False
        self.done = threading.Event()
        if self.path and self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
                if data['version'] != 1 or type(data['updated']) not in (int, float) or not math.isfinite(data['updated']) or data['updated'] <= 0:
                    raise ValueError('invalid cache format')
                self.value = self.validate(data['codes'])
                self.updated = data['updated']
            except Exception:
                logger.warning('Ignoring invalid underlying cache', exc_info=True)

    @staticmethod
    def validate(codes):
        if not isinstance(codes, (list, tuple)) or len(codes) > 500:
            raise ValueError('invalid underlying list')
        for code in codes:
            validate_identity(code, 'underlying')
        if len(set(codes)) != len(codes):
            raise ValueError('duplicate underlying')
        return tuple(sorted(codes))

    def get(self, load):
        with self.lock:
            now = self.clock()
            due = self.value is None or now - self.updated >= self.ttl or now < self.updated
            if due and not self.refreshing and (self.attempt is None or now - self.attempt >= self.retry):
                self.refreshing, self.attempt = True, now
                self.done.clear()
                threading.Thread(target=self._refresh, args=(load,), daemon=True,
                                 name='bigqmt-underlyings').start()
            if self.value is not None:
                return self.value
        self.done.wait(self.wait)
        with self.lock:
            if self.value is not None:
                return self.value
            raise ProviderError('SOURCE_ERROR', '', 'underlying discovery pending or failed; no successful list cached',
                                'sdk_execution', 'not_applicable')

    def _refresh(self, load):
        try:
            value = self.validate(load())
            updated = self.clock()
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    fd, name = tempfile.mkstemp(prefix='.underlyings-', dir=self.path.parent)
                    try:
                        with os.fdopen(fd, 'w', encoding='utf-8') as f:
                            json.dump(dict(version=1, updated=updated, codes=value), f)
                        os.replace(name, self.path)
                    finally:
                        if os.path.exists(name):
                            os.unlink(name)
                except OSError:
                    logger.warning('Underlying cache persistence failed; using memory copy', exc_info=True)
            with self.lock:
                self.value, self.updated, self.error = value, updated, None
        except Exception as exc:
            logger.warning('Underlying refresh failed; retaining last successful list (%s)', type(exc).__name__)
            with self.lock:
                self.error = type(exc).__name__
        finally:
            with self.lock:
                self.refreshing = False
                self.done.set()

    def info(self):
        with self.lock:
            return dict(entries=len(self.value) if self.value is not None else None,
                        updated_at=self.updated or None, refreshing=self.refreshing,
                        stale=self.value is not None and self.clock() - self.updated >= self.ttl,
                        last_error=self.error, ttl_seconds=self.ttl)
