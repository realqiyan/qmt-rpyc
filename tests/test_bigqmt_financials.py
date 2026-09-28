"""Observed scalar financial layouts and strict report-date correlation."""
import ast
from datetime import date
import json
from pathlib import Path

import pytest

from qmt_rpyc.adapters.bigqmt.financial_wire import encode_raw_financial
from qmt_rpyc.adapters.bigqmt.financials import FinancialsAdapter, convert_scalar_table, scalar_fields
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.transport.codec import decode, encode
from qmt_rpyc.contracts.financials import BalanceRecord, FINANCIAL_TABLES, FinancialQuery

REPORT = 1782748800000
ANNOUNCED = 1786723200000


def source(table='Balance', primary=REPORT):
    return {field: {primary: float(REPORT) if field.endswith('.m_timetag') else
                   float(ANNOUNCED) if field.endswith('.m_anntime') else 123.5}
            for field in scalar_fields(table)}


def wire(fields):
    return json.loads(json.dumps(encode_raw_financial({'000001.SZ': fields}), allow_nan=False))['000001.SZ']


def test_provider_defaults_to_five_core_tables_and_preserves_dates_and_units():
    calls = []
    class Reader:
        def get_scalar_financials(self, code, fields, start, end, basis):
            calls.append((code, fields, start, end, basis))
            raw = {}
            for table in FINANCIAL_TABLES:
                raw.update(source(table))
            return {code: wire(raw)}
    query = FinancialQuery(('000001.SZ',), start=date(2026, 6, 29), end=date(2026, 7, 3))
    result = FinancialsAdapter(Reader()).get_reports(query)
    value = result.items[0].value
    assert all(getattr(value, table)[0].m_timetag == date(2026, 6, 30) for table in FINANCIAL_TABLES)
    assert value.Capital[0].freeFloatCapital == 123.5
    assert calls[0][2:] == ('20260629', '20260703', 'report_time')
    assert not any(field.startswith(('SHAREHOLDER.', 'TOP10')) for field in calls[0][1])


def test_batch_keeps_input_order_with_per_item_failures_and_distinguishes_empty():
    class Reader:
        def get_scalar_financials(self, code, *args):
            if code == 'missing':
                return {}
            if code == 'wrong':
                return {'another': wire(source())}
            if code == 'exception':
                raise RuntimeError('source failed')
            if code == 'incomplete':
                return {code: {'ASHAREBALANCESHEET.m_timetag': []}}
            if code == 'empty':
                return {code: {field: [] for field in scalar_fields('Balance')}}
            return {code: wire(source())}
    codes = ('good', 'missing', 'wrong', 'exception', 'incomplete', 'empty')
    result = FinancialsAdapter(Reader(), workers=2).get_reports(FinancialQuery(codes, tables=('Balance',)))
    assert tuple(item.code for item in result.items) == codes
    assert [item.error.error_type for item in result.items[1:5]] == [
        'MISSING_RESULT', 'INVALID_RESULT', 'SOURCE_ERROR', 'INVALID_RESULT']
    assert result.items[-1].value.Balance == ()
    assert result.items[-1].value.Income is None


def test_bridge_disconnection_remains_an_operation_error():
    class Reader:
        def get_scalar_financials(self, *args):
            raise ProviderError('NOT_CONNECTED', 'financials.get_reports', 'strategy unavailable')
    with pytest.raises(ProviderError, match='strategy unavailable'):
        FinancialsAdapter(Reader()).get_reports(FinancialQuery(('000001.SZ',)))


def test_financial_client_request_and_download_defaults_agree():
    import inspect
    from qmt_rpyc.client.financials import FinancialsAPI
    from qmt_rpyc.client.downloads import DownloadsAPI
    from qmt_rpyc.contracts.downloads import FinancialDownloadRequest
    assert FinancialQuery(('x',)).tables == FINANCIAL_TABLES
    assert FinancialDownloadRequest(('x',)).tables == FINANCIAL_TABLES
    assert inspect.signature(FinancialsAPI.get_reports).parameters['tables'].default == FINANCIAL_TABLES
    assert inspect.signature(DownloadsAPI.start_financials).parameters['tables'].default == FINANCIAL_TABLES


@pytest.mark.parametrize('table', ['Balance', 'Capital', 'PershareIndex'])
def test_confirmed_tables_keep_report_and_announcement_dates_separate(table):
    # Structural fixture from the deployment, with synthetic business values.
    raw = source(table)
    result = convert_scalar_table(table, wire(raw))
    assert len(result) == 1
    assert result[0].m_timetag == date(2026, 6, 30)
    assert result[0].m_anntime == date(2026, 8, 15)
    if table == 'Capital':
        assert 'CAPITALSTRUCTURE.free_float_capital' in raw
        assert result[0].freeFloatCapital == 123.5


def test_wire_format_retains_integer_keys_and_dates_through_strict_json():
    raw = source()
    payload = wire(raw)
    assert payload['ASHAREBALANCESHEET.m_timetag'] == [[REPORT, float(REPORT)]]
    assert type(payload['ASHAREBALANCESHEET.m_timetag'][0][0]) is int
    result = convert_scalar_table('Balance', payload)[0]
    assert decode(BalanceRecord, encode(result)) == result


def test_announcement_basis_uses_announcement_key_without_overwriting_report_date():
    # Phase 6 observed the announcement key with separate report metadata.
    result = convert_scalar_table('Balance', wire(source(primary=ANNOUNCED)), 'announce_time')[0]
    assert result.m_timetag == date(2026, 6, 30)
    assert result.m_anntime == date(2026, 8, 15)
    with pytest.raises(ValueError, match='date basis'):
        convert_scalar_table('Balance', wire(source(primary=REPORT)), 'announce_time')


@pytest.mark.parametrize('table,values', [
    ('Income', {'s_fa_eps_basic': 1.24, 's_fa_eps_diluted': 1.24}),
    ('CashFlow', {'cash_cash_equ_end_period': 289251000000.}),
])
def test_phase_six_optional_metrics_are_read_from_their_own_table(table, values):
    raw = source(table)
    prefix = scalar_fields(table)[0].split('.')[0]
    for name, value in values.items():
        raw[prefix + '.' + name] = {REPORT: value}
    result = convert_scalar_table(table, wire(raw))[0]
    assert all(getattr(result, name) == value for name, value in values.items())


@pytest.mark.parametrize('table,omitted', [
    ('Income', ('s_fa_eps_basic', 's_fa_eps_diluted')),
    ('CashFlow', ('cash_cash_equ_end_period',)),
])
def test_unprovided_optional_metrics_are_null_not_cross_table_guesses(table, omitted):
    raw = source(table)
    raw = {k: v for k, v in raw.items() if k.rsplit('.', 1)[1] not in omitted}
    result = convert_scalar_table(table, wire(raw))[0]
    assert all(getattr(result, name) is None for name in omitted)
    assert result.m_timetag == date(2026, 6, 30)


def test_optional_null_nan_and_missing_point_are_distinct_from_missing_dates():
    raw = source()
    raw['ASHAREBALANCESHEET.tot_assets'][REPORT] = float('nan')
    raw['ASHAREBALANCESHEET.tot_liab'][REPORT] = None
    raw['ASHAREBALANCESHEET.cap_stk'] = {}
    result = convert_scalar_table('Balance', wire(raw))[0]
    assert result.tot_assets is result.tot_liab is result.cap_stk is None
    raw['ASHAREBALANCESHEET.m_anntime'] = {}
    with pytest.raises(ValueError, match='different record keys'):
        convert_scalar_table('Balance', wire(raw))


def test_values_join_by_keys_not_field_iteration_order():
    earlier_report = REPORT - 86400000
    raw = source()
    for field, points in raw.items():
        points[earlier_report] = (float(earlier_report) if field.endswith('.m_timetag') else
                                  float(ANNOUNCED) if field.endswith('.m_anntime') else 1.)
    raw['ASHAREBALANCESHEET.tot_assets'] = {earlier_report: 2., REPORT: 3.}
    result = convert_scalar_table('Balance', wire(raw))
    assert [r.m_timetag for r in result] == [date(2026, 6, 29), date(2026, 6, 30)]
    assert [r.tot_assets for r in result] == [2., 3.]


def test_orphan_points_and_duplicate_wire_keys_do_not_silently_disappear():
    raw = source()
    raw['ASHAREBALANCESHEET.tot_assets'][REPORT + 1] = 9.
    with pytest.raises(ValueError, match='no matching date'):
        convert_scalar_table('Balance', wire(raw))
    payload = wire(source())
    payload['ASHAREBALANCESHEET.tot_assets'].append([REPORT, 9.])
    with pytest.raises(ValueError, match='duplicate'):
        convert_scalar_table('Balance', payload)


@pytest.mark.parametrize('bad', [None, True, float('nan'), 1786723200000.5, '20260815'])
def test_required_date_never_uses_report_key_as_fallback(bad):
    raw = source()
    raw['ASHAREBALANCESHEET.m_anntime'][REPORT] = bad
    with pytest.raises((ValueError, TypeError)):
        convert_scalar_table('Balance', wire(raw))


def test_missing_date_field_is_not_an_empty_report():
    raw = source()
    del raw['ASHAREBALANCESHEET.m_anntime']
    with pytest.raises(KeyError):
        convert_scalar_table('Balance', wire(raw))
    assert convert_scalar_table('Balance', {name: [] for name in scalar_fields('Balance')}) == ()


@pytest.mark.parametrize('bad', [True, '123', [1, 2], {'holder': 1}, float('inf')])
def test_scalar_encoder_rejects_non_scalar_values_instead_of_flattening_them(bad):
    with pytest.raises(ValueError):
        encode_raw_financial({'000001.SZ': {'FIELD': {REPORT: bad}}})


def test_stringified_time_key_is_not_guessed_back_into_an_integer():
    with pytest.raises(ValueError):
        encode_raw_financial({'000001.SZ': {'FIELD': {str(REPORT): 1.}}})
    payload = wire(source())
    payload['ASHAREBALANCESHEET.tot_assets'][0][0] = str(REPORT)
    with pytest.raises(ValueError):
        convert_scalar_table('Balance', payload)


@pytest.mark.parametrize('table', ['TOP10HOLDER', 'TOP10FLOWHOLDER'])
def test_observed_last_holder_only_dict_cannot_enter_scalar_report_encoding(table):
    # Phase 7 returned only rank 10 despite the specialized API's ten holders.
    raw = {table + '.' + field: {REPORT: value} for field, value in {
        'declareDate': float(ANNOUNCED), 'endDate': float(REPORT),
        'quantity': 62523366., 'ratio': .32, 'rank': 10.,
    }.items()}
    with pytest.raises(ValueError, match='row-preserving'):
        encode_raw_financial({'000001.SZ': raw})


def test_numpy_scalars_are_converted_without_importing_numpy_in_strategy_module():
    np = pytest.importorskip('numpy')
    assert encode_raw_financial({'A': {'F': {np.int64(REPORT): np.float64(2.)}}}) == {'A': {'F': [[REPORT, 2.]]}}
    script = Path(__file__).parents[1] / 'src/qmt_rpyc/adapters/bigqmt/financial_wire.py'
    ast.parse(script.read_text(), feature_version=(3, 6))
    assert script.read_text().isascii()
