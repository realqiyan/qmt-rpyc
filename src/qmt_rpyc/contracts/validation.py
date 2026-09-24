
from qmt_rpyc.contracts.common import BatchResult, Success
from qmt_rpyc.contracts.downloads import DownloadStatus, TaskRef
from qmt_rpyc.contracts.errors import (
    ProtocolError,
)
from qmt_rpyc.contracts.financials import FINANCIAL_TABLES
from qmt_rpyc.contracts.market import MarketTicks
from qmt_rpyc.contracts.operations import OPERATIONS
from qmt_rpyc.contracts.system import Capabilities


def validate_result(operation, request, result):
    if operation in ("reference.list_sectors", "reference.get_sector_members",
                     "instruments.list_option_underlyings"):
        if any(not identity or identity.strip() != identity for identity in result):
            raise ProtocolError("result identities must be nonempty without surrounding whitespace")
        if result != tuple(sorted(set(result))):
            raise ProtocolError("result identities must be unique and sorted")
    if isinstance(result, Capabilities) and set(result.operations) != set(OPERATIONS):
        raise ProtocolError("capability operation set mismatch")
    if isinstance(result, BatchResult):
        if tuple(item.code for item in result.items) != request.codes:
            raise ProtocolError("batch response identities/order differ from request")
    if isinstance(result, MarketTicks):
        codes = tuple(item.code for item in result.items)
        if codes != tuple(sorted(codes)):
            raise ProtocolError("market ticks are not sorted")
    if operation == "market.get_trading_dates" and result != tuple(sorted(set(result))):
        raise ProtocolError("trading dates are not unique and sorted")
    if operation == "financials.get_reports":
        for item in result.items:
            if isinstance(item, Success):
                for table in FINANCIAL_TABLES:
                    if (getattr(item.value, table) is None) == (table in request.tables):
                        raise ProtocolError("financial requested-table presence mismatch")
    if isinstance(result, TaskRef):
        expected = {"downloads.start_history": "HISTORY", "downloads.start_financials": "FINANCIAL",
                    "downloads.start_sectors": "SECTORS", "downloads.start_index_weights": "INDEX_WEIGHTS"}[operation]
        if result.kind != expected:
            raise ProtocolError("download task kind mismatch")
    if isinstance(result, DownloadStatus) and result.task_id != request.task_id:
        raise ProtocolError("download task identity mismatch")
    if operation == "trading.get_asset" and result is not None and result.account != request.account:
        raise ProtocolError("asset account mismatch")
    if operation in ("trading.list_orders", "trading.list_positions") and any(item.account != request.account for item in result):
        raise ProtocolError("trading response account mismatch")
