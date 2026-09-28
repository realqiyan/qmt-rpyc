from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.reader import StrategyReader
from qmt_rpyc.adapters.bigqmt.options import OptionsAdapter
from qmt_rpyc.contracts.options import ExpiryDatesRequest
from tests.test_bigqmt_options import Reader


def test_grouped_option_reads_reduce_88_pipe_exchanges_to_six():
    calls, native_calls = [], []
    runtime = StrategyRuntime(SimpleNamespace(get_option_detail_data=lambda code: native_calls.append(code) or {'InstrumentID': code}), {})
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
