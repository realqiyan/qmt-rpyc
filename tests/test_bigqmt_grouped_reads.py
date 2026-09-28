from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.reader import StrategyReader
from qmt_rpyc.adapters.bigqmt.options import OptionsAdapter
from qmt_rpyc.contracts.options import ExpiryDatesRequest
from tests.test_bigqmt_options import Reader


def test_grouped_option_reads_reduce_88_pipe_exchanges_to_six():
    calls, native_calls = [], []
    runtime = StrategyRuntime(SimpleNamespace(get_option_detail_data=lambda code: native_calls.append(code) or {'InstrumentID': code}, get_instrumentdetail=lambda code: {'InstrumentID':code, 'InstrumentName':'name'}), {})
    def request(operation, arguments):
        calls.append((operation, arguments))
        return runtime.dispatch(operation, arguments)
    reader = StrategyReader(SimpleNamespace(request=request))
    codes = ['option' + str(i) for i in range(88)]
    result = reader.get_option_details(codes)
    assert list(result) == native_calls == codes
    assert len(calls) == 6
    assert max(len(args['codes']) for op, args in calls) == 16


def test_grouped_weight_request_preserves_zero_and_finite_source_units():
    runtime = StrategyRuntime(SimpleNamespace(get_weight_in_index=lambda index, code: .433 if code == 'a' else 0), {})
    reader = StrategyReader(SimpleNamespace(request=runtime.dispatch))
    assert reader.get_index_weights('index', ['a', 'b']) == {'a': .433, 'b': 0}


def test_grouped_native_limit_is_checked_before_any_source_call():
    calls = []
    runtime = StrategyRuntime(SimpleNamespace(get_option_detail_data=lambda code: calls.append(code)), {})
    with pytest.raises(ValueError, match='16'):
        runtime.dispatch('option_details', {'codes': [str(i) for i in range(17)]})
    assert calls == []


@pytest.mark.parametrize('response', [{}, {'unexpected': {}}])
def test_missing_or_extra_group_results_are_not_silently_accepted(response):
    reader = StrategyReader(SimpleNamespace(request=lambda *args: response))
    with pytest.raises(ValueError, match='identities'):
        reader.get_option_details(['a'])


def test_incomplete_discovery_fails_instead_of_returning_partial_expiries():
    reader = Reader()
    reader.get_option_details = lambda codes: {codes[0]: reader.rows[codes[0]]}
    with pytest.raises(ValueError, match='incomplete'):
        OptionsAdapter(reader).get_expiry_dates(ExpiryDatesRequest('510050.SH'))


def test_complete_chain_details_use_two_pipe_requests_not_44():
    from qmt_rpyc.contracts.common import CodesRequest
    calls = []
    def option(code):
        return dict(InstrumentID=code.split('.')[0], OptUndlCode='510300', OptUndlMarket='SH',
                    optType='CALL', ExpireDate=20261028, OptExercisePrice=4.0, OptUnit=10000.0)
    def instrument(code):
        return dict(InstrumentID=code.split('.')[0], InstrumentName='native option name')
    runtime = StrategyRuntime(SimpleNamespace(get_option_detail_data=option, get_instrumentdetail=instrument), {})
    def request(operation, arguments):
        calls.append((operation, arguments))
        return runtime.dispatch(operation, arguments)
    adapter = OptionsAdapter(StrategyReader(SimpleNamespace(request=request)))
    codes = tuple('%08d.SHO' % i for i in range(22))
    result = adapter.get_contract_details(CodesRequest(codes)).require_all()
    assert list(result) == list(codes)
    assert all(row.name == 'native option name' for row in result.values())
    assert [op for op, _ in calls] == ['option_details', 'option_details']
    assert [len(args['codes']) for _, args in calls] == [16, 6]


def test_grouped_contract_native_error_does_not_discard_other_contracts():
    from qmt_rpyc.contracts.common import CodesRequest
    source = Reader()
    def instrument(code):
        if code == source.codes[0]:
            raise ValueError('private source error')
        return source.get_instrument_detail(code)
    runtime = StrategyRuntime(SimpleNamespace(get_option_detail_data=source.get_option_detail,
                                               get_instrumentdetail=instrument), {})
    adapter = OptionsAdapter(StrategyReader(SimpleNamespace(request=runtime.dispatch)))
    result = adapter.get_contract_details(CodesRequest(tuple(source.codes[:2])))
    assert result.items[0].error.error_type == 'SOURCE_ERROR'
    assert 'private' not in result.items[0].error.message
    assert result.items[1].value.name == 'source name'


def test_old_strategy_option_record_can_still_resolve_supplemental_identity():
    from qmt_rpyc.contracts.common import CodesRequest
    source = Reader()
    def request(operation, args):
        if operation == 'option_details':
            return source.get_option_details(args['codes'])
        assert operation == 'instrument'
        return source.get_instrument_detail(args['code'])
    adapter = OptionsAdapter(StrategyReader(SimpleNamespace(request=request)))
    assert adapter.get_contract_details(CodesRequest(tuple(source.codes))).require_all()
