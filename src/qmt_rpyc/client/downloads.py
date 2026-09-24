import math
import time
from datetime import date, datetime
from typing import Optional, Sequence, Union

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.downloads import (
    DownloadStatus,
    FinancialDownloadRequest,
    HistoryDownloadRequest,
    TaskRef,
    TaskRequest,
)
from qmt_rpyc.contracts.errors import (
    DownloadFailedError,
    ProtocolError,
)
from qmt_rpyc.contracts.financials import FINANCIAL_TABLES, FinancialTable
from qmt_rpyc.contracts.market import Period
from qmt_rpyc.transport.codec import decode, encode

from .base import _API, _sequence


class DownloadsAPI(_API):
    def start_history(self, code: str, period: Period, start: Optional[Union[date, datetime]] = None,
                       end: Optional[Union[date, datetime]] = None) -> TaskRef:
        return self._call("downloads.start_history", HistoryDownloadRequest(code, period, start, end))

    def start_financials(self, codes: Sequence[str], tables: Sequence[FinancialTable] = FINANCIAL_TABLES,
                         start: Optional[date] = None, end: Optional[date] = None) -> TaskRef:
        return self._call("downloads.start_financials", FinancialDownloadRequest(_sequence(codes), _sequence(tables), start, end))

    def start_sectors(self) -> TaskRef:
        return self._call("downloads.start_sectors", EmptyRequest())

    def start_index_weights(self) -> TaskRef:
        return self._call("downloads.start_index_weights", EmptyRequest())

    def get_task(self, task_id: str) -> DownloadStatus:
        return self._call("downloads.get_task", TaskRequest(task_id))

    def handle(self, reference: TaskRef) -> "DownloadHandle":
        return DownloadHandle(self, reference)


class DownloadHandle:
    def __init__(self, downloads: DownloadsAPI, reference: TaskRef):
        self._downloads = downloads
        self.reference = decode(TaskRef, encode(reference))

    @property
    def task_id(self):
        return self.reference.task_id

    def poll(self) -> DownloadStatus:
        status = self._downloads.get_task(self.task_id)
        if status.kind != self.reference.kind:
            raise ProtocolError("polled task kind mismatch")
        return status

    def wait(self, timeout: float = 300, poll_interval: float = 0.5) -> DownloadStatus:
        for name, number in (("timeout", timeout), ("poll_interval", poll_interval)):
            if type(number) not in (int, float) or not math.isfinite(number) or number <= 0:
                raise ValueError(name + " must be finite and positive")
        deadline = time.monotonic() + timeout
        while True:
            status = self.poll()
            if status.status in ("completed", "failed"):
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("download wait timed out; task was not cancelled")
            time.sleep(min(poll_interval, remaining))

    def require_completed(self, timeout: float = 300, poll_interval: float = 0.5) -> DownloadStatus:
        status = self.wait(timeout, poll_interval)
        if status.status != "completed":
            raise DownloadFailedError(status)
        return status
