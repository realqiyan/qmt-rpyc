"""BigQMT no-op downloads retain the existing public task lifecycle."""
from datetime import date
import logging

from qmt_rpyc.adapters.bigqmt.downloads import DownloadAdapter
from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.downloads import (
    DownloadStatus,
    FinancialDownloadRequest,
    HistoryDownloadRequest,
    TaskRequest,
)
from qmt_rpyc.server.download_service import DownloadService
from qmt_rpyc.server.downloads import DownloadTaskManager
from qmt_rpyc.transport import codec


def test_compatibility_downloads_complete_as_real_tasks_without_source_or_progress(caplog):
    provider = DownloadAdapter()
    manager = DownloadTaskManager(max_workers=2)
    service = DownloadService(provider, manager)
    caplog.set_level(logging.INFO, logger="qmt_rpyc.adapters.bigqmt.downloads")
    try:
        tasks = [
            service.start_history(HistoryDownloadRequest("000001.SZ", "1d", date(2026, 1, 1))),
            service.start_financials(FinancialDownloadRequest(("000001.SZ",))),
            service.start_sectors(EmptyRequest()),
            service.start_index_weights(EmptyRequest()),
        ]
        manager._executor.shutdown(wait=True)
        assert len({task.task_id for task in tasks}) == 4
        assert [task.kind for task in tasks] == ["HISTORY", "FINANCIAL", "SECTORS", "INDEX_WEIGHTS"]
        for task in tasks:
            result = service.get_task(TaskRequest(task.task_id))
            decoded = codec.decode(DownloadStatus, codec.encode(result))
            assert decoded.task_id == task.task_id
            assert decoded.kind == task.kind
            assert decoded.status == "completed"
            assert decoded.completed_at is not None
            assert decoded.error is decoded.progress is decoded.result is None
        assert sum("Compatibility no-op" in record.message for record in caplog.records) == 4
    finally:
        manager.shutdown()
