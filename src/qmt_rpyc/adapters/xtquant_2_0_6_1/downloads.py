"""Native SDK download operations, independent of task lifecycle."""
from qmt_rpyc.contracts.common import EmptyRequest
from qmt_rpyc.contracts.downloads import (
    FinancialDownloadRequest,
    HistoryDownloadRequest,
)

from .conversions import sdk_range
from .source import SdkSource


class DownloadAdapter:
    def __init__(self, source: SdkSource):
        self.source = source

    def validate_history(self, request: HistoryDownloadRequest) -> None:
        from qmt_rpyc.adapters.errors import ProviderError
        try:
            sdk_range(request.start, request.end, request.period != '1d')
        except ValueError as exc:
            raise ProviderError('INVALID_ARGUMENTS', 'downloads.start_history', str(exc)) from exc

    def history(self, request: HistoryDownloadRequest) -> None:
        start, end = sdk_range(request.start, request.end, request.period != '1d')
        self.source.call('download_history_data', request.code, request.period, start, end)

    def financials(self, request: FinancialDownloadRequest) -> None:
        start, end = sdk_range(request.start, request.end)
        self.source.call('download_financial_data', list(request.codes), list(request.tables), start, end)

    def sectors(self, request: EmptyRequest) -> None:
        self.source.call('download_sector_data')

    def index_weights(self, request: EmptyRequest) -> None:
        self.source.call('download_index_weight')
