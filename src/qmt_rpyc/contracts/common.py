"""Common requests and results."""
from dataclasses import dataclass
from typing import Generic, Literal, Mapping, Tuple, TypeVar, Union


def validate_identity(value, name):
    if type(value) is not str or not value or value.strip() != value:
        raise ValueError(name + " must be nonempty without surrounding whitespace")


def validate_nonnegative(value, *names):
    for name in names:
        number = getattr(value, name)
        if number is not None and number < 0:
            raise ValueError(name + " must be nonnegative")


T = TypeVar("T")


@dataclass(frozen=True)
class ItemError:
    error_type: Literal["NOT_FOUND", "NOT_OPTION", "MISSING_RESULT", "INVALID_RESULT", "SOURCE_ERROR"]
    message: str


@dataclass(frozen=True)
class OperationError:
    error_type: Literal["INVALID_ARGUMENTS", "CONTRACT_MISMATCH", "API_UNAVAILABLE", "NOT_CONNECTED", "SOURCE_ERROR", "INVALID_RESULT", "TRANSPORT_ERROR", "TASK_NOT_FOUND", "INTERNAL_ERROR"]
    message: str
    operation: str
    contract_version: int
    phase: Literal["pre_execution", "dispatch", "sdk_execution", "result_validation", "transport"]
    outcome: Literal["not_executed", "not_applicable", "unknown"]
    request_id: str


@dataclass(frozen=True)
class Success(Generic[T]):
    code: str
    value: T
    status: Literal["ok"] = "ok"

    def __post_init__(self):
        validate_identity(self.code, "code")


@dataclass(frozen=True)
class Failure:
    code: str
    error: ItemError
    status: Literal["error"] = "error"

    def __post_init__(self):
        validate_identity(self.code, "code")


@dataclass(frozen=True)
class BatchResult(Generic[T]):
    items: Tuple[Union[Success[T], Failure], ...]

    def require_all(self) -> Mapping[str, T]:
        from .errors import BatchIncompleteError
        if any(isinstance(item, Failure) for item in self.items):
            raise BatchIncompleteError(self)
        codes = [item.code for item in self.items]
        if len(set(codes)) != len(codes):
            raise ValueError("duplicate batch identity")
        return {item.code: item.value for item in self.items}

    def __post_init__(self):
        codes = [item.code for item in self.items]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate result identities")


MAX_CODES = 500


def validate_codes(codes):
    if len(codes) > MAX_CODES:
        raise ValueError("at most 500 codes per request")
    if any(not code or code.strip() != code for code in codes):
        raise ValueError("codes must be nonempty without surrounding whitespace")
    if len(set(codes)) != len(codes):
        raise ValueError("duplicate codes")


def validate_window(start, end, count=None):
    if count is not None and (type(count) is not int or count <= 0):
        raise ValueError("count must be a positive integer")
    if count is not None and start is not None:
        raise ValueError("count cannot be combined with start")
    if start is not None and end is not None and start > end:
        raise ValueError("start must not exceed end")


@dataclass(frozen=True)
class EmptyRequest:
    pass


@dataclass(frozen=True)
class CodesRequest:
    codes: Tuple[str, ...]

    def __post_init__(self):
        validate_codes(self.codes)
