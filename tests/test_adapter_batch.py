"""Both adapters retain the same batch isolation and failure semantics."""
from collections import Counter

import pytest

from qmt_rpyc.adapters.bigqmt.conversions import batch as bigqmt_batch
from qmt_rpyc.adapters.errors import ItemFailure, ProviderError
from qmt_rpyc.adapters.xtquant_2_0_6_1.source import SdkSource


@pytest.fixture(params=['bigqmt', 'xtquant'])
def batch(request):
    if request.param == 'bigqmt':
        return bigqmt_batch
    source = object.__new__(SdkSource)
    source.workers = 2
    return source.batch


def test_batch_preserves_order_and_isolates_errors_without_retries(batch):
    calls = []
    def read(code):
        calls.append(code)
        if code == 'missing':
            return None
        if code == 'invalid':
            return 'not an integer'
        if code == 'rejected':
            raise ItemFailure('MISSING_RESULT', 'required field missing')
        if code == 'failed':
            raise ProviderError('SOURCE_ERROR', '', 'native failure')
        return 42
    codes = ('ok', 'missing', 'invalid', 'rejected', 'failed')
    result = batch(codes, int, read)
    assert tuple(item.code for item in result.items) == codes
    assert result.items[0].value == 42
    assert tuple(item.error.error_type for item in result.items[1:]) == (
        'NOT_FOUND', 'INVALID_RESULT', 'MISSING_RESULT', 'SOURCE_ERROR')
    assert result.items[3].error.message == 'required field missing'
    assert Counter(calls) == Counter(codes)


@pytest.mark.parametrize('category', ['NOT_CONNECTED', 'API_UNAVAILABLE'])
def test_batch_connection_and_capability_errors_still_propagate(batch, category):
    calls = []
    def read(code):
        calls.append(code)
        raise ProviderError(category, '', 'unavailable')
    with pytest.raises(ProviderError) as caught:
        batch(('one',), int, read)
    assert caught.value.category == category
    assert calls == ['one']


def test_empty_batch_never_reads_source(batch):
    def read(code):
        pytest.fail('empty batch must not read')
    assert batch((), int, read).items == ()
