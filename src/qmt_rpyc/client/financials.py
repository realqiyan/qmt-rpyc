from datetime import date
from typing import Optional, Sequence

from qmt_rpyc.contracts.common import BatchResult
from qmt_rpyc.contracts.financials import (
    FINANCIAL_TABLES,
    FinancialQuery,
    FinancialReports,
    FinancialTable,
)

from .base import _API, _sequence


class FinancialsAPI(_API):
    def get_reports(self, codes: Sequence[str], tables: Sequence[FinancialTable] = FINANCIAL_TABLES,
                    start: Optional[date] = None, end: Optional[date] = None,
                    date_basis: str = "report_time") -> BatchResult[FinancialReports]:
        return self._call("financials.get_reports", FinancialQuery(_sequence(codes), _sequence(tables), start, end, date_basis))
