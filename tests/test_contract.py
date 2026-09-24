"""Business contract regressions using recorded deployment data and changed SDKs."""
import copy
import inspect
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qmt_rpyc.contract import (
    CONTRACT_HASH, ContractFailure, bind, manifest, project_result, signature, specification,
)
from qmt_rpyc.server.adapters import AdapterRegistry, ContractDispatcher, SdkAdapter, SdkEnvironment, signature_problem
from qmt_rpyc.server.adapters.baseline import BaselineV1Adapter
from tests import _xtquant_mock as sdk

DATA = Path(__file__).parents[1] / 'docs' / 'api'


def dispatcher(xtdata=None, registry=None, connection=None):
    env = SdkEnvironment(xtdata or sdk.xtdata, sdk.XtQuantTrader, sdk.xtconstant, connection)
    return ContractDispatcher(env, registry or AdapterRegistry([BaselineV1Adapter()]))


def test_recorded_responses_project_without_changing_values():
    samples = json.loads((DATA / 'contract-v1-rpc-samples-20260923.json').read_text())['calls']
    for sample in samples:
        api = 'xtdata.' + sample['api']
        params = bind(api, sample['args'], sample['kwargs'])
        value = copy.deepcopy(sample['result'])
        if sample['api'] == 'get_option_detail_data':
            instrument = next(c['result'] for c in samples if c['api'] == 'get_instrument_detail' and c['args'][0] == sample['args'][0])
            value['InstrumentName'] = instrument['InstrumentName']
        result = project_result(api, value, params)
        if sample['api'] in ('get_full_tick', 'get_trading_dates', 'get_market_data_ex', 'get_divid_factors', 'get_index_weight'):
            assert result == value
        # Input sample remains unchanged, including SDK-only fields.
        assert value == sample['result'] or sample['api'] == 'get_option_detail_data'


def test_contract_signature_defaults_are_independent_of_sdk():
    evidence = json.loads((DATA / 'contract-v1-sources.json').read_text())
    for api in specification()['methods']:
        surface, name = api.split('.')
        assert str(signature(api)) == evidence[surface][name]['signature'].replace('(self, ', '(')
    assert len(specification()['methods']) == 22
    assert len(specification()['constants']) == 16
    assert all(v['available'] for v in dispatcher().surface()['capabilities'].values())


def test_signature_mismatch_warns_but_only_disables_affected_dependency(caplog):
    changed = copy.copy(sdk.xtdata)
    changed.get_instrument_detail = lambda stock_code: {}
    d = dispatcher(changed)
    caps = d.surface()['capabilities']
    assert not caps['xtdata.get_instrument_detail']['available']
    assert not caps['xtdata.get_option_detail_data']['available']
    assert caps['xtdata.get_full_tick']['available']
    assert 'signature mismatch' in caplog.text
    failure = d.call('xtdata.get_instrument_detail', ['600000.SH'], {})
    assert failure['error_type'] == 'APIUnavailable'
    assert failure['outcome'] == 'not_executed'
    assert d.call('xtdata.get_full_tick', [['600000.SH']], {})['status'] == 'ok'
    assert d.surface()['contract_hash'] == CONTRACT_HASH


class RenamedTickAdapter(SdkAdapter):
    """Synthetic second MiniQMT API; production support requires its own evidence."""
    adapter_id = 'synthetic-renamed-tick-v1'
    priority = 10

    def supports(self, api):
        return api == 'xtdata.get_full_tick'

    def probe(self, api, environment):
        expected = inspect.Signature([inspect.Parameter('symbols', inspect.Parameter.POSITIONAL_OR_KEYWORD)])
        return signature_problem(getattr(environment.xtdata, 'fetch_ticks', None), expected)

    def invoke(self, api, parameters, environment):
        rows = environment.xtdata.fetch_ticks(symbols=parameters['code_list'])
        return {code: {**{k: v for k, v in row.items() if k != 'last'}, 'lastPrice': row['last']}
                for code, row in rows.items()}


def test_same_v1_contract_across_two_sdk_shapes():
    def fetch_ticks(symbols):
        return {code: {**{k: v for k, v in row.items() if k != 'lastPrice'}, 'last': row['lastPrice']}
                for code, row in sdk.xtdata.get_full_tick(symbols).items()}
    newer = copy.copy(sdk.xtdata)
    newer.get_full_tick = None
    newer.fetch_ticks = fetch_ticks
    registry = AdapterRegistry([BaselineV1Adapter(), RenamedTickAdapter()])
    original = dispatcher(registry=registry)
    adapted = dispatcher(newer, registry)
    assert original.call('xtdata.get_full_tick', [['600000.SH']], {}) == adapted.call('xtdata.get_full_tick', [['600000.SH']], {})
    assert original.surface()['contract_hash'] == adapted.surface()['contract_hash']
    assert adapted.surface()['capabilities']['xtdata.get_full_tick']['adapter_id'] == 'synthetic-renamed-tick-v1'
    assert original.surface()['capabilities']['xtdata.get_full_tick']['adapter_id'] == 'xtquant-baseline-20260923-v1'


@pytest.mark.parametrize('period', ['1w', '1wk', '1mon', '60m'])
def test_period_rejected_before_sdk_or_download_submission(period):
    for api, args in [('xtdata.get_market_data_ex', []), ('xtdata.download_history_data', ['600000.SH'])]:
        response = dispatcher().call(api, args, {'period': period})
        assert response['error_type'] == 'InvalidArguments'
        assert response['phase'] == 'pre_execution'


def test_result_projection_filters_extras_and_rejects_missing_fields():
    params = bind('xtdata.get_full_tick', [['600000.SH']], {})
    value = sdk.xtdata.get_full_tick(['600000.SH'])
    value['600000.SH']['newSdkField'] = {'arbitrary': 'data'}
    result = project_result('xtdata.get_full_tick', value, params)
    assert 'newSdkField' not in result['600000.SH']
    del value['600000.SH']['lastPrice']
    with pytest.raises(ContractFailure, match='lastPrice') as exc:
        project_result('xtdata.get_full_tick', value, params)
    assert exc.value.category == 'InvalidResult'


def test_changed_sdk_default_is_unavailable_not_silently_used():
    changed = copy.copy(sdk.xtdata)
    changed.get_trading_dates = lambda market, start_time='', end_time='', count=10: []
    d = dispatcher(changed)
    assert d.call('xtdata.get_trading_dates', ['SH'], {})['error_type'] == 'APIUnavailable'


def test_nullable_empty_and_malformed_tables():
    params = bind('xtdata.get_market_data_ex', [], {'field_list': ['open']})
    good = {'600000.SH': {'columns': ['open', 'extra'], 'index': [20260918], 'data': [[None, 2]]}}
    assert project_result('xtdata.get_market_data_ex', good, params)['600000.SH']['data'] == [[None]]
    bad = {'600000.SH': {'columns': ['open'], 'index': [20260918], 'data': [[1, 2]]}}
    with pytest.raises(ContractFailure):
        project_result('xtdata.get_market_data_ex', bad, params)
    financial = {'600000.SH': {'Income': {'columns': [], 'index': [], 'data': []}}}
    assert project_result('xtdata.get_financial_data', financial,
                          bind('xtdata.get_financial_data', [['600000.SH'], ['Income']], {})) == financial


@pytest.mark.parametrize('result', [None, True, 0, '123'])
def test_invalid_order_result_is_unknown_outcome_and_not_retried(result):
    manager = SimpleNamespace(call_trader_method=Mock(return_value={'status': 'ok', 'data': result}))
    response = dispatcher(connection=manager).call('trader.order_stock', ['A', '600000.SH', 23, 100, 5, 10.0], {})
    assert response['error_type'] == 'InvalidResult'
    assert response['outcome'] == 'unknown'
    assert response['phase'] == 'result_validation'
    assert manager.call_trader_method.call_count == 1


def test_trader_disconnected_proven_not_executed():
    response = dispatcher().call('trader.cancel_order_stock', ['A', 123], {})
    assert response['outcome'] == 'not_executed'
    assert response['phase'] == 'pre_execution'


@pytest.mark.parametrize('api,args,expected', [
    ('trader.query_stock_orders', ['A'], 'not_applicable'),
    ('trader.cancel_order_stock', ['A', 123], 'unknown'),
])
def test_trader_execution_error_distinguishes_reads_from_mutations(api, args, expected):
    from qmt_rpyc.exceptions import OutcomeUnknownError, QmtError
    manager = SimpleNamespace(call_trader_method=Mock(return_value={
        'status': 'error', 'error_type': 'RuntimeError', 'error_message': 'SDK failed',
        'phase': 'sdk_execution', 'outcome': 'unknown',
    }))
    response = dispatcher(connection=manager).call(api, args, {})
    assert response['outcome'] == expected
    assert isinstance(QmtError.from_response(response), OutcomeUnknownError) == (expected == 'unknown')
    assert manager.call_trader_method.call_count == 1


def test_contract_defaults_not_mutated_between_requests():
    first = bind('xtdata.get_market_data_ex', [], {})
    first['stock_list'].append('600000.SH')
    assert bind('xtdata.get_market_data_ex', [], {})['stock_list'] == []


def test_client_ignores_discovered_extra_methods_and_rejects_wrong_contract():
    from qmt_rpyc import QmtClient
    from qmt_rpyc.exceptions import ContractError
    surface = manifest()
    surface['xtdata']['functions']['new_sdk_method'] = {}
    c = QmtClient()
    c._conn = SimpleNamespace(root=SimpleNamespace(get_api_surface=lambda versions: surface))
    c._init_surface()
    assert not hasattr(c.xtdata, 'new_sdk_method')
    assert not hasattr(c.trader, 'connect')
    assert c.contract_version == 1
    surface['contract_hash'] = 'changed'
    with pytest.raises(ContractError):
        c._init_surface()


def test_transport_failure_after_order_send_is_unknown():
    from qmt_rpyc import QmtClient
    from qmt_rpyc.exceptions import OutcomeUnknownError
    call = Mock(side_effect=TimeoutError('no acknowledgement'))
    client = QmtClient()
    client._conn = SimpleNamespace(root=SimpleNamespace(call_trader=call))
    with pytest.raises(OutcomeUnknownError) as exc:
        client._call('trader', 'order_stock', ['A', '600000.SH', 23, 100, 5, 10.0], {})
    assert exc.value.phase == 'transport'
    assert call.call_count == 1


def test_recorded_dr_ratios_match_sdk_forward_ratio_prices():
    evidence = json.loads((DATA / 'contract-v1-rpc-dividends-20260923.json').read_text())['calls']
    import math
    compared = 0
    for offset in (0, 3):
        factors = evidence[offset]['result']
        values = {day: row[factors['columns'].index('dr')] for day, row in zip(factors['index'], factors['data'])}
        raw = next(iter(evidence[offset + 1]['result'].values()))
        adjusted = next(iter(evidence[offset + 2]['result'].values()))
        assert raw['index'] == adjusted['index']
        for day, row, adj in zip(raw['index'], raw['data'], adjusted['data']):
            if row[0] and adj[0]:
                expected = math.prod(v for date, v in values.items() if date > str(day))
                assert row[0] / adj[0] == pytest.approx(expected, rel=1e-14)
                compared += 1
    assert compared == 1311


def test_order_millisecond_drift_is_rejected_instead_of_misread_as_seconds():
    raw = vars(sdk.XtOrder(order_time=1789696800000))
    with pytest.raises(ContractFailure, match='Unix seconds'):
        project_result('trader.query_stock_orders', [raw], {'account': 'A', 'cancelable_only': False})


def test_sdk_constant_drift_disables_only_dependent_endpoints():
    changed = SimpleNamespace(**{k:v for k,v in specification()['constants'].items()})
    changed.STOCK_BUY = 999
    d = ContractDispatcher(SdkEnvironment(sdk.xtdata, sdk.XtQuantTrader, changed, None), AdapterRegistry([BaselineV1Adapter()]))
    caps = d.surface()['capabilities']
    assert not caps['trader.order_stock']['available']
    assert not caps['trader.query_stock_orders']['available']
    assert caps['trader.query_stock_asset']['available']


def test_signature_annotations_do_not_disable_compatible_methods():
    def get_full_tick(code_list: list) -> dict:
        return {}
    assert signature_problem(get_full_tick, signature('xtdata.get_full_tick')) is None


def test_adapter_ambiguity_is_an_unavailable_capability():
    first, second = BaselineV1Adapter(), BaselineV1Adapter()
    second.adapter_id = 'another-baseline'
    d = dispatcher(registry=AdapterRegistry([first, second]))
    assert d.call('xtdata.get_full_tick', [[]], {})['error_type'] == 'APIUnavailable'
    assert 'ambiguous' in d.surface()['capabilities']['xtdata.get_full_tick']['reason']


@pytest.mark.parametrize('tag', ['', None, '20260918 15:00:00.5', '2026-09-18 15:00:00'])
def test_tick_display_time_is_derived_from_valid_milliseconds(tag):
    raw = sdk.xtdata.get_full_tick(['OPTION.SHO', 'ETF.SH'])
    raw['OPTION.SHO'].update(time=1789714800500, timetag=tag)
    before = copy.deepcopy(raw)
    def get_full_tick(code_list):
        return {code: raw[code] for code in code_list}
    changed = copy.copy(sdk.xtdata)
    changed.get_full_tick = get_full_tick
    response = dispatcher(changed).call('xtdata.get_full_tick', [list(raw)], {})
    assert response['status'] == 'ok'
    projected = response['data']
    assert projected['OPTION.SHO']['timetag'] == '20260918 15:00:00.500'
    assert projected['OPTION.SHO']['time'] == 1789714800500
    assert projected['OPTION.SHO']['lastPrice'] == raw['OPTION.SHO']['lastPrice']
    assert projected['ETF.SH'] == raw['ETF.SH']
    assert raw == before


@pytest.mark.parametrize('time_value', [None, 0, 1789714800, '1789714800500'])
def test_tick_time_fallback_does_not_invent_or_guess_timestamps(time_value):
    from qmt_rpyc.server.adapters.ticks import normalize_tick_times
    raw = sdk.xtdata.get_full_tick(['OPTION.SHO'])
    raw['OPTION.SHO'].update(time=time_value, timetag='')
    with pytest.raises(ContractFailure):
        project_result('xtdata.get_full_tick', normalize_tick_times(raw), {})


def test_huge_integer_never_overflows_type_validation():
    assert bind('xtdata.get_market_data_ex', [], {'count': 10 ** 400})['count'] == 10 ** 400
    with pytest.raises(ContractFailure) as exc:
        project_result('xtdata.get_trading_dates', [10 ** 400], {})
    assert exc.value.category == 'InvalidResult'
