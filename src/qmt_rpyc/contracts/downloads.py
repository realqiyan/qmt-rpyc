"""Downloads requests and results."""
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional, Tuple, Union

from .common import (
    OperationError,
    validate_codes,
    validate_identity,
    validate_nonnegative,
    validate_window,
)
from .financials import FINANCIAL_TABLES, FinancialTable
from .market import Period

TaskKind = Literal["HISTORY", "FINANCIAL", "SECTORS", "INDEX_WEIGHTS"]


@dataclass(frozen=True)
class TaskRef:
    task_id: str
    kind: TaskKind

    def __post_init__(self):
        validate_identity(self.task_id, "task_id")


@dataclass(frozen=True)
class DownloadProgress:
    total: Optional[int]
    finished: Optional[int]
    instrument: Optional[str]
    message: str

    def __post_init__(self):
        validate_identity(self.instrument, "instrument")
        validate_nonnegative(self, "total", "finished")


@dataclass(frozen=True)
class DownloadStatus:
    task_id: str
    kind: TaskKind
    status: Literal["pending", "running", "completed", "failed"]
    submitted_at: datetime
    completed_at: Optional[datetime]
    progress: Optional[DownloadProgress]
    error: Optional[OperationError]
    result: None = None

    def __post_init__(self):
        validate_identity(self.task_id, "task_id")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("failed task requires an error; other states forbid it")
        if (self.status in ("completed", "failed")) != (self.completed_at is not None):
            raise ValueError("terminal task requires completion time")


@dataclass(frozen=True)
class HistoryDownloadRequest:
    code: str
    period: Period
    start: Optional[Union[date, datetime]] = None
    end: Optional[Union[date, datetime]] = None

    def __post_init__(self):
        validate_identity(self.code, "code")
        expected = date if self.period == "1d" else datetime
        for boundary in (self.start, self.end):
            if boundary is not None and type(boundary) is not expected:
                raise ValueError("daily download requires dates; intraday requires aware instants")
        validate_window(self.start, self.end)


@dataclass(frozen=True)
class FinancialDownloadRequest:
    codes: Tuple[str, ...]
    tables: Tuple[FinancialTable, ...] = FINANCIAL_TABLES
    start: Optional[date] = None
    end: Optional[date] = None

    def __post_init__(self):
        validate_codes(self.codes)
        validate_window(self.start, self.end)
        if not self.tables or len(set(self.tables)) != len(self.tables):
            raise ValueError("financial tables must be nonempty and unique")


@dataclass(frozen=True)
class TaskRequest:
    task_id: str

    def __post_init__(self):
        validate_identity(self.task_id, "task_id")
