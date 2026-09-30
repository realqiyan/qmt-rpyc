"""Compatibility downloads; the complete QMT terminal owns data acquisition.

No QMT method is invoked. The server's normal task manager still records task
creation and completion, which do not imply downloaded or queryable data.
"""
import logging

from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.downloads import FinancialDownloadRequest, HistoryDownloadRequest


logger = logging.getLogger(__name__)
COMPATIBILITY_DOWNLOAD_REASON = (
    "Compatibility no-op: QMT manages data acquisition; "
    "task completion does not verify data readiness"
)


class DownloadAdapter:
    """Accept contract-valid download requests without a native SDK or bridge."""

    def validate_history(self, request: HistoryDownloadRequest) -> None:
        # Public request validation still applies. A no-op has no additional
        # SDK timestamp precision or connection requirements.
        return None

    def history(self, request: HistoryDownloadRequest) -> None:
        logger.info("BigQMT HISTORY: %s", COMPATIBILITY_DOWNLOAD_REASON)

    def financials(self, request: FinancialDownloadRequest) -> None:
        logger.info("BigQMT FINANCIAL: %s", COMPATIBILITY_DOWNLOAD_REASON)

    def index_weights(self, request: EmptyRequest) -> None:
        logger.info("BigQMT INDEX_WEIGHTS: %s", COMPATIBILITY_DOWNLOAD_REASON)
