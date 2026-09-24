from datetime import date
from typing import get_args, get_type_hints

from qmt_rpyc.adapters.errors import ItemFailure
from qmt_rpyc.contracts.common import BatchResult
from qmt_rpyc.contracts.financials import (
    FINANCIAL_TABLES,
    FinancialQuery,
    FinancialReports,
)

from . import conversions as v
from .source import SdkSource


class FinancialsAdapter:
    def __init__(self, source: SdkSource):
        self.b = source

    def get_reports(self, r: FinancialQuery) -> BatchResult[FinancialReports]:
        if not r.codes:
            return BatchResult(())
        start, end = v.sdk_range(r.start, r.end)
        source = self.b.call('get_financial_data', list(r.codes), list(r.tables), start, end, r.date_basis)
        if not isinstance(source, dict) or set(source) - set(r.codes):
            raise ValueError('invalid financial result identities')
        schemas = get_type_hints(FinancialReports)
        def one(code):
            if code not in source:
                raise ItemFailure('MISSING_RESULT', 'source omitted financial reports')
            result = {table: None for table in FINANCIAL_TABLES}
            for table in r.tables:
                validated = source[code][table]
                record_type = get_args(get_args(schemas[table])[0])[0]
                columns = get_type_hints(record_type)
                records = []
                for _, row in v.rows(validated):
                    records.append({field: v.day(value) if columns[field] is date
                                    else (None if value is None else v.number(value))
                                    for field, value in ((field, row[field]) for field in columns)})
                result[table] = records
            return result
        return self.b.batch(r.codes, FinancialReports, one)
