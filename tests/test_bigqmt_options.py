"""Deployed BigQMT option shapes, without importing a native SDK or bridge."""
from datetime import date
from threading import Event

import pytest

from qmt_rpyc.adapters.bigqmt.options import OptionsAdapter
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.common import CodesRequest
from qmt_rpyc.contracts.options import ExpiryDatesRequest, OptionChainRequest


class Reader:
    def __init__(self):
        self.codes = ['10000001.SHO', '10000002.SHO', '10000003.SHO']
        self.rows = {code: dict(InstrumentID=code.split('.')[0], ExchangeID='SHO',
                               OptUndlCode='510050', OptUndlMarket='SH', optType='CALL',
                               ExpireDate=20261223, OptExercisePrice=2.8, OptUnit=10000.0)
                     for code in self.codes}
        self.rows[self.codes[1]]['optType'] = 'PUT'
        self.rows[self.codes[2]]['ExpireDate'] = 20260927

    def get_option_codes(self, underlying):
        assert underlying == '510050.SH'
        return self.codes

    def get_option_detail(self, code):
        return self.rows.get(code)

    def get_option_details(self, codes):
        return {code: self.get_option_detail(code) for code in codes}

    def get_instrument_detail(self, code):
        return dict(InstrumentID=code.split('.')[0], ExchangeID='SHO',
                    InstrumentName='source name', IsTrading=None, SettlementPrice=None)


def adapter(reader=None, **kwargs):
    return OptionsAdapter(reader or Reader(), market_date=lambda: date(2026, 9, 28), **kwargs)


def test_deployed_option_fields_and_integral_float_unit_preserve_identity():
    # CALL is observed on the target deployment; PUT is a contract-level
    # synthetic case and does not claim separate deployment verification.
    result = adapter().get_contract_details(CodesRequest(('10000002.SHO', '10000001.SHO')))
    values = result.require_all()
    assert list(values) == ['10000002.SHO', '10000001.SHO']
    assert values['10000001.SHO'].option_type == 'CALL'
    assert values['10000002.SHO'].option_type == 'PUT'
    assert values['10000001.SHO'].contract_unit == 10000
    assert type(values['10000001.SHO'].contract_unit) is int
    assert values['10000001.SHO'].expiry_date == date(2026, 12, 23)
    assert values['10000001.SHO'].underlying == '510050.SH'
    assert values['10000001.SHO'].name == 'source name'


@pytest.mark.parametrize('field,bad', [
    ('OptUnit', 10000.5), ('OptUnit', True), ('OptUnit', float('nan')),
    ('OptExercisePrice', float('inf')), ('OptExercisePrice', 0),
    ('optType', 'C'), ('optType', None), ('ExpireDate', 2026123),
    ('InstrumentID', 'different'), ('OptUndlMarket', ''),
])
def test_invalid_source_item_is_isolated_without_guessing(field, bad):
    reader = Reader()
    reader.rows['10000001.SHO'][field] = bad
    result = adapter(reader).get_contract_details(CodesRequest(('10000001.SHO', '10000002.SHO')))
    assert result.items[0].error.error_type == 'INVALID_RESULT'
    assert result.items[1].status == 'ok'


def test_missing_option_and_missing_name_have_distinct_errors():
    reader = Reader()
    reader.get_instrument_detail = lambda code: None
    result = adapter(reader).get_contract_details(CodesRequest(('MISSING.SHO', '10000001.SHO')))
    assert [i.error.error_type for i in result.items] == ['NOT_FOUND', 'MISSING_RESULT']


def test_wrong_supplemental_identity_is_rejected():
    reader = Reader()
    reader.get_instrument_detail = lambda code: {'InstrumentID': 'other', 'InstrumentName': 'other'}
    result = adapter(reader).get_contract_details(CodesRequest(('10000001.SHO',)))
    assert result.items[0].error.error_type == 'INVALID_RESULT'


def test_unexpected_source_error_is_visible_per_item_and_not_retried():
    reader = Reader()
    calls = []
    original = reader.get_option_detail
    def read(code):
        calls.append(code)
        if code == '10000001.SHO':
            raise ValueError('source exception with private details')
        return original(code)
    reader.get_option_detail = read
    result = adapter(reader).get_contract_details(CodesRequest(('10000001.SHO', '10000002.SHO')))
    assert result.items[0].error.error_type == 'SOURCE_ERROR'
    assert 'private' not in result.items[0].error.message
    assert result.items[1].status == 'ok'
    assert sorted(calls) == ['10000001.SHO', '10000002.SHO']


@pytest.mark.parametrize('category', ['NOT_CONNECTED', 'API_UNAVAILABLE'])
def test_bridge_unavailability_stays_an_operation_failure(category):
    reader = Reader()
    def read(code):
        raise ProviderError(category, '', 'unavailable')
    reader.get_option_detail = read
    with pytest.raises(ProviderError) as exc:
        adapter(reader).get_contract_details(CodesRequest(('10000001.SHO',)))
    assert exc.value.category == category


def test_bounded_parallel_reads_keep_input_order():
    reader = Reader()
    second_started = Event()
    first_started = Event()
    original = reader.get_option_detail
    def read(code):
        if code == '10000001.SHO':
            first_started.set()
            assert second_started.wait(2)
        elif code == '10000002.SHO':
            assert first_started.wait(2)
            second_started.set()
        return original(code)
    reader.get_option_detail = read
    result = adapter(reader, workers=2).get_contract_details(CodesRequest(tuple(reader.codes)))
    assert list(result.require_all()) == reader.codes


def test_discovery_only_requires_identity_owner_and_expiry_includes_today():
    reader = Reader()
    reader.rows['10000002.SHO']['ExpireDate'] = 20260928
    for row in reader.rows.values():
        for field in ('OptUnit', 'OptExercisePrice', 'optType'):
            del row[field]
    def forbidden(code):
        pytest.fail('discovery must not require full instrument records')
    reader.get_instrument_detail = forbidden
    provider = adapter(reader)
    dates = provider.get_expiry_dates(ExpiryDatesRequest('510050.SH'))
    assert dates.dates == (date(2026, 9, 28), date(2026, 12, 23))
    chain = provider.get_option_chain(OptionChainRequest('510050.SH', date(2026, 9, 28)))
    assert chain.contract_codes == ('10000002.SHO',)
    with pytest.raises(ProviderError):
        provider.get_option_chain(OptionChainRequest('510050.SH', date(2026, 9, 27)))


@pytest.mark.parametrize('failure', ['duplicate', 'wrong_owner', 'missing_expiry'])
def test_discovery_does_not_silently_return_an_incomplete_chain(failure):
    reader = Reader()
    if failure == 'duplicate':
        reader.codes.append(reader.codes[0])
    elif failure == 'wrong_owner':
        reader.rows[reader.codes[0]]['OptUndlCode'] = 'other'
    else:
        del reader.rows[reader.codes[0]]['ExpireDate']
    with pytest.raises((ValueError, KeyError)):
        adapter(reader).get_expiry_dates(ExpiryDatesRequest('510050.SH'))


def test_empty_detail_batch_does_not_read_source():
    class Forbidden:
        def __getattr__(self, name):
            pytest.fail('empty request must not access the reader')
    assert adapter(Forbidden()).get_contract_details(CodesRequest(())).items == ()
