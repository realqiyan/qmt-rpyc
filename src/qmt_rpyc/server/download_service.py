"""Task submission and public download lifecycle."""
from datetime import datetime, timezone

from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.adapters.interfaces import DownloadProvider
from qmt_rpyc.contracts.common import EmptyRequest, OperationError
from qmt_rpyc.contracts.downloads import (
    DownloadProgress,
    DownloadStatus,
    FinancialDownloadRequest,
    HistoryDownloadRequest,
    TaskRef,
    TaskRequest,
)
from qmt_rpyc.contracts.operations import CONTRACT_VERSION


class DownloadService:
    def __init__(self, provider: DownloadProvider, manager):
        self.provider, self.manager = provider, manager

    def _submit(self, method, kind, request):
        if self.manager is None:
            raise ProviderError('API_UNAVAILABLE', '', 'download manager unavailable')
        task_id = self.manager.submit(method, function_name=kind, _args=(request,))
        return TaskRef(task_id, kind)

    def start_history(self, request: HistoryDownloadRequest) -> TaskRef:
        # Contract accepts aware instants; the provider validates its precision
        # synchronously before a task can be accepted.
        self.provider.validate_history(request)
        return self._submit(self.provider.history, 'HISTORY', request)

    def start_financials(self, request: FinancialDownloadRequest) -> TaskRef:
        return self._submit(self.provider.financials, 'FINANCIAL', request)

    def start_sectors(self, request: EmptyRequest) -> TaskRef:
        return self._submit(self.provider.sectors, 'SECTORS', request)

    def start_index_weights(self, request: EmptyRequest) -> TaskRef:
        return self._submit(self.provider.index_weights, 'INDEX_WEIGHTS', request)

    def get_task(self, request: TaskRequest) -> DownloadStatus:
        task = self.manager.get_task(request.task_id)
        if task is None:
            raise ProviderError('TASK_NOT_FOUND', 'downloads.get_task', 'task is unknown or no longer retained')
        status = task['status']
        progress = task['progress']
        if progress is not None:
            progress = DownloadProgress(progress['total'], progress['finished'], progress['stockcode'] or None, progress['message'])
        error = None
        if status == 'failed':
            error = OperationError('SOURCE_ERROR', 'download task failed; see server diagnostics',
                                   'downloads.get_task', CONTRACT_VERSION, 'sdk_execution', 'unknown', '')
        completed = datetime.fromisoformat(task['completed_at']).astimezone(timezone.utc) if task['completed_at'] else None
        return DownloadStatus(task['task_id'], task['function_name'], status,
                              datetime.fromisoformat(task['submitted_at']).astimezone(timezone.utc), completed, progress, error)
