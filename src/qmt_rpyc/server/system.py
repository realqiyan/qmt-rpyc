"""Server health and capabilities, independent of an SDK implementation."""
from datetime import datetime, timezone

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.operations import CONTRACT_VERSION
from qmt_rpyc.contracts.system import Capabilities, DownloadStats, Health
from qmt_rpyc.transport.auth import PROTOCOL_VERSION
from qmt_rpyc.version import __version__


class SystemService:
    def __init__(self, capabilities, health, downloads, active_clients):
        self.capabilities, self.health = capabilities, health
        self.downloads, self.active_clients = downloads, active_clients

    def get_capabilities(self, request: EmptyRequest) -> Capabilities:
        return self.capabilities

    def get_health(self, request: EmptyRequest) -> Health:
        raw = self.health()
        def instant(value):
            return datetime.fromisoformat(value).astimezone(timezone.utc) if value else None
        stats = DownloadStats(**self.downloads.get_stats()) if self.downloads else None
        return Health(connected=raw.get('connected', False), trader_available=raw.get('trader_available', False),
                      last_heartbeat=instant(raw.get('last_heartbeat')), heartbeat_failures=raw.get('heartbeat_failures'),
                      reconnect_attempts=raw.get('reconnect_attempts'), consecutive_failures=raw.get('consecutive_failures'),
                      uptime_seconds=float(raw['uptime_seconds']) if 'uptime_seconds' in raw else None,
                      connection_state=raw.get('connection_state', 'uninitialized'),
                      last_connection_error=raw.get('last_connection_error') or None, next_retry_at=instant(raw.get('next_retry_at')),
                      active_clients=self.active_clients(), download_tasks=stats, package_version=__version__,
                      protocol_version=PROTOCOL_VERSION, contract_version=CONTRACT_VERSION)
