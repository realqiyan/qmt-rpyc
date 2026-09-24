"""System requests and results."""
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Mapping, Optional

from .common import validate_nonnegative


@dataclass(frozen=True)
class DownloadStats:
    total: int
    pending: int
    running: int
    completed: int
    failed: int

    def __post_init__(self):
        validate_nonnegative(self, "total", "pending", "running", "completed", "failed")


@dataclass(frozen=True)
class Health:
    connected: bool
    trader_available: bool
    last_heartbeat: Optional[datetime]
    heartbeat_failures: Optional[int]
    reconnect_attempts: Optional[int]
    consecutive_failures: Optional[int]
    uptime_seconds: Optional[float]
    connection_state: Literal["uninitialized", "disconnected", "stopped", "connected", "exhausted", "waiting_retry", "connecting"]
    last_connection_error: Optional[str]
    next_retry_at: Optional[datetime]
    active_clients: int
    download_tasks: Optional[DownloadStats]
    package_version: str
    protocol_version: int
    contract_version: int

    def __post_init__(self):
        validate_nonnegative(self, "active_clients", "heartbeat_failures", "reconnect_attempts", "consecutive_failures", "uptime_seconds")


@dataclass(frozen=True)
class Capability:
    available: bool
    adapter_id: Optional[str]
    reason: Optional[str]


@dataclass(frozen=True)
class Capabilities:
    operations: Mapping[str, Capability]
