"""capability behavior exercised against synthetic SDK and real dispatch."""
import json
import time
from datetime import date, datetime, timezone

import pytest

from tests.fixtures import mock_xtquant, service
from qmt_rpyc.transport import codec
from qmt_rpyc.contracts.operations import CONTRACT_HASH, OPERATIONS
from qmt_rpyc.adapters.xtquant_2_0_6_1 import conversions as values


@pytest.fixture
def invoke(service, monkeypatch):
    monkeypatch.setattr(values, 'market_date', lambda: date(2026, 9, 18))
    manifest = codec.loads(service.exposed_negotiate(CONTRACT_HASH))
    assert manifest['contract_version'] == 2
    def call(operation, **payload):
        wire = codec.dumps(dict(contract_version=2, request_id='test-request', operation=operation, payload=payload))
        response = codec.loads(service.exposed_call(wire))
        assert response['request_id'] == 'test-request'
        return response
    return call


def value(invoke, operation, **payload):
    response = invoke(operation, **payload)
    assert response['status'] == 'ok', response
    return codec.decode(OPERATIONS[operation].response_type, response['data'])


def test_all_read_capabilities_have_typed_results(invoke):
    from qmt_rpyc.contracts.financials import FINANCIAL_TABLES
    cases = {
        'reference.list_sectors': {},
        'reference.get_sector_members': {'sector': '沪深A股'},
        'instruments.list_option_underlyings': {},
        'instruments.get_details': {'codes': ['600000.SH']},
        'instruments.get_trading_reference': {'codes': ['600000.SH']},
        'options.get_expiry_dates': {'underlying': '510050.SH'},
        'options.get_option_chain': {'underlying': '510050.SH', 'expiry_date': date(2026, 9, 23)},
        'options.get_contract_details': {'codes': ['10000001.SH']},
        'market.get_ticks': {'codes': ['600000.SH']},
        'market.get_market_ticks': {'markets': ['SH']},
        'market.get_daily_bars': {'codes': ['600000.SH'], 'count': 2},
        'market.get_intraday_bars': {'codes': ['600000.SH'], 'period': '1m', 'count': 2},
        'market.get_trading_dates': {'market': 'SH'},
        'reference.get_dividend_events': {'code': '600000.SH'},
        'reference.get_index_weights': {'index': '000300.SH'},
        'financials.get_reports': {'codes': ['600000.SH'], 'tables': FINANCIAL_TABLES},
        'trading.get_asset': {'account': 'ACC1'},
        'trading.list_positions': {'account': 'ACC1'},
        'trading.list_orders': {'account': 'ACC1'},
        'system.get_health': {},
        'system.get_capabilities': {},
    }
    for operation, payload in cases.items():
        result = value(invoke, operation, **payload)
        if hasattr(result, 'require_all'):
            assert result.require_all(), (operation, result)


def test_discovery_uses_only_required_metadata(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    monkeypatch.setattr(sdk, 'get_option_detail_data', lambda optioncode: {
        'OptUndlCode': '510050', 'OptUndlMarket': 'SH', 'ExpireDate': '20260923'})
    assert value(invoke, 'options.get_expiry_dates', underlying='510050.SH').dates == (date(2026, 9, 23),)
    assert value(invoke, 'options.get_contract_details', codes=['10000001.SH']).items[0].status == 'error'
    monkeypatch.setattr(sdk, 'get_option_detail_data', lambda optioncode: {'ExpireDate': '20260923'})
    assert invoke('options.get_expiry_dates', underlying='510050.SH')['status'] == 'error'


def test_batch_missing_and_invalid_are_isolated(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    original = sdk.get_full_tick
    def ticks(code_list):
        data = original(code_list)
        data.pop('MISSING.SH', None)
        if 'BAD.SH' in data:
            data['BAD.SH']['lastPrice'] = float('nan')
        return data
    monkeypatch.setattr(sdk, 'get_full_tick', ticks)
    result = value(invoke, 'market.get_ticks', codes=['600000.SH', 'MISSING.SH', 'BAD.SH'])
    assert [item.status for item in result.items] == ['ok', 'error', 'error']
    assert result.items[1].error.error_type == 'MISSING_RESULT'
    assert result.items[2].error.error_type == 'INVALID_RESULT'
    assert result.items[0].value.observed_at.tzinfo == timezone.utc


def test_invalid_requests_do_not_execute(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    monkeypatch.setattr(sdk, 'get_full_tick', lambda codes: pytest.fail('SDK called'))
    assert invoke('market.get_ticks', codes=['A', 'A'])['error']['outcome'] == 'not_executed'
    assert value(invoke, 'market.get_ticks', codes=[]).items == ()
    assert invoke('market.get_daily_bars', codes=['A'], count=0)['error']['error_type'] == 'INVALID_ARGUMENTS'
    assert invoke('options.get_option_chain', underlying='510050.SH', expiry_date=date(2026, 9, 17))['error']['outcome'] == 'not_executed'


def test_all_download_kinds_and_status(invoke):
    cases = [('history', {'code': '600000.SH', 'period': '1d'}),
             ('financials', {'codes': ['600000.SH']}), ('sectors', {}), ('index_weights', {})]
    for kind, params in cases:
        ref = value(invoke, 'downloads.start_' + kind, **params)
        deadline = time.monotonic() + 2
        while True:
            status = value(invoke, 'downloads.get_task', task_id=ref.task_id)
            if status.status in ('completed', 'failed'):
                break
            assert time.monotonic() < deadline
            time.sleep(.01)
        assert status.status == 'completed'
        assert status.result is None
        assert status.kind == ref.kind
    assert invoke('downloads.get_task', task_id='no-such-task')['error']['error_type'] == 'TASK_NOT_FOUND'


def test_submission_query_and_both_cancellations(invoke):
    result = value(invoke, 'trading.submit_order', account='ACC1', instrument='600000.SH',
                   side='BUY', quantity=100, pricing="LIMIT", price=10.0)
    assert result.status == 'submitted'
    orders = value(invoke, 'trading.list_orders', account='ACC1')
    assert orders[0].order_id == result.order_id
    assert orders[0].status == 'REPORTED'
    for target in [dict(kind='order_id', order_id=result.order_id),
                   dict(kind='exchange_order_id', market='SH', exchange_order_id='00001')]:
        assert value(invoke, 'trading.cancel_order', account='ACC1', target=target).status == 'succeeded'


def test_post_submission_invalid_result_is_unknown(invoke, service, monkeypatch):
    source = service._dispatcher.providers.trading.b
    original = source.call
    def call(api, *args, **kwargs):
        if api == 'trader.order_stock':
            return 0
        return original(api, *args, **kwargs)
    monkeypatch.setattr(source, 'call', call)
    result = invoke('trading.submit_order', account='ACC1', instrument='600000.SH', side='BUY', quantity=100, pricing="LIMIT", price=10.0)
    assert result['status'] == 'error'
    assert result['error']['outcome'] == 'unknown'


def test_hash_mismatch_prevents_invocation(service):
    assert codec.loads(service.exposed_negotiate('wrong'))['status'] == 'error'
    assert codec.loads(service.exposed_call('{}'))['error']['outcome'] == 'not_executed'


def test_plain_instrument_does_not_require_optional_extension(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    original = sdk.get_instrument_detail
    def detail(stock_code, iscomplete=False):
        row = original(stock_code, iscomplete)
        row.pop('ExtendInfo', None)
        return row
    monkeypatch.setattr(sdk, 'get_instrument_detail', detail)
    instrument = value(invoke, 'instruments.get_details', codes=['600000.SH']).require_all()['600000.SH']
    assert instrument.delivery_end_date is None
    assert instrument.created_date.raw == '0'


def test_option_detail_validates_only_its_public_fields(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    original = sdk.get_option_detail_data
    def detail(optioncode):
        row = original(optioncode)
        # Not part of OptionContract; its failure must not remove valid terms.
        row.pop('UpStopPrice')
        if optioncode == 'BAD.SHO':
            row['OptUnit'] = 10000.5
        return row
    monkeypatch.setattr(sdk, 'get_option_detail_data', detail)
    result = value(invoke, 'options.get_contract_details', codes=['GOOD.SHO', 'BAD.SHO'])
    assert result.items[0].status == 'ok'
    assert result.items[0].value.name == 'TestStock'
    assert result.items[1].error.error_type == 'INVALID_RESULT'


def test_supplemental_source_failure_is_per_instrument(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    original = sdk.get_option_detail_data
    def detail(optioncode):
        if optioncode == 'BAD.SHO':
            raise RuntimeError('synthetic metadata failure')
        return original(optioncode)
    monkeypatch.setattr(sdk, 'get_option_detail_data', detail)
    batch = value(invoke, 'instruments.get_details', codes=['GOOD.SHO', 'BAD.SHO'])
    assert [i.status for i in batch.items] == ['ok', 'error']
    assert batch.items[1].error.error_type == 'SOURCE_ERROR'


def test_fractional_download_boundary_rejected_before_submission(invoke, service, monkeypatch):
    monkeypatch.setattr(service.__class__._download_mgr, 'submit', lambda *a, **kw: pytest.fail('submitted'))
    result = invoke('downloads.start_history', code='600000.SH', period='1m',
                    start=datetime(2026, 9, 18, 1, 30, 0, 1, tzinfo=timezone.utc))
    assert result['error']['outcome'] == 'not_executed'
    assert result['error']['error_type'] == 'INVALID_ARGUMENTS'


def test_future_calendar_is_not_reported_as_known_empty(invoke):
    result = invoke('market.get_trading_dates', market='SH', end=date(2026, 9, 19))
    assert result['error']['error_type'] == 'INVALID_ARGUMENTS'
    assert result['error']['outcome'] == 'not_executed'


def test_discovery_rejects_short_source_date(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    monkeypatch.setattr(sdk, 'get_option_detail_data', lambda optioncode: {
        'OptUndlCode': '510050', 'OptUndlMarket': 'SH', 'ExpireDate': '2026111'})
    assert invoke('options.get_expiry_dates', underlying='510050.SH')['status'] == 'error'


def test_wrong_source_contract_identity_is_not_attached_to_requested_code(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    original = sdk.get_option_detail_data
    def detail(optioncode):
        row = original(optioncode)
        if optioncode == 'WRONG.SHO':
            row['InstrumentID'] = 'OTHER'
        return row
    monkeypatch.setattr(sdk, 'get_option_detail_data', detail)
    result = value(invoke, 'options.get_contract_details', codes=['GOOD.SHO', 'WRONG.SHO'])
    assert result.items[0].status == 'ok'
    assert result.items[1].error.error_type == 'INVALID_RESULT'


def test_source_identity_sets_reject_empty_names(invoke, service, monkeypatch):
    sdk = service._dispatcher.providers.market.b.environment.xtdata
    monkeypatch.setattr(sdk, 'get_sector_list', lambda: [''])
    assert invoke('reference.list_sectors')['status'] == 'error'


def test_alternative_provider_uses_identical_contract_without_sdk():
    from dataclasses import replace
    from types import SimpleNamespace
    from qmt_rpyc.server.dispatch import Dispatcher
    from qmt_rpyc.adapters.interfaces import Providers
    from qmt_rpyc.contracts.trading import Asset
    from qmt_rpyc.contracts.system import Capabilities, Capability
    class AlternativeTrading:
        def get_asset(self, request):
            return Asset(request.account, 2, 12.5, 0.0, 0.0, 12.5)
        def list_positions(self, request): return ()
        def list_orders(self, request): return ()
        def submit_order(self, request): raise AssertionError('unsupported')
        def cancel_order(self, request): raise AssertionError('unsupported')
    class Unavailable:
        def __getattr__(self, name):
            def fail(request): raise AssertionError('unsupported')
            return fail
    unavailable = Unavailable()
    capabilities = Capabilities({name: Capability(name == 'trading.get_asset',
                                'synthetic' if name == 'trading.get_asset' else None,
                                None if name == 'trading.get_asset' else 'unsupported') for name in OPERATIONS})
    providers = Providers(unavailable, unavailable, unavailable, unavailable, unavailable,
                          AlternativeTrading(), unavailable, capabilities)
    dispatcher = Dispatcher(providers)
    request = dict(contract_version=2, request_id='alternate', operation='trading.get_asset', payload={'account': 'test'})
    result = codec.loads(dispatcher.call(codec.dumps(request)))
    assert codec.decode(OPERATIONS['trading.get_asset'].response_type, result['data']).cash == 12.5
    request.update(operation='reference.list_sectors', payload={})
    assert codec.loads(dispatcher.call(codec.dumps(request)))['error']['error_type'] == 'API_UNAVAILABLE'


def test_signature_drift_disables_only_dependent_operations(mock_xtquant, monkeypatch):
    import sys
    from qmt_rpyc.adapters.xtquant_2_0_6_1.factory import create_providers
    monkeypatch.setattr(sys.modules['xtquant.xtdata'], 'get_sector_list', lambda required: [])
    capabilities = create_providers().capabilities.operations
    assert not capabilities['reference.list_sectors'].available
    assert 'signature mismatch' in capabilities['reference.list_sectors'].reason
    assert capabilities['market.get_ticks'].available
    assert capabilities['options.get_expiry_dates'].available


@pytest.mark.parametrize('bad', [True, -1.0, None, 0])
def test_invalid_source_submission_is_never_a_known_rejection(invoke, service, monkeypatch, bad):
    source = service._dispatcher.providers.trading.b
    monkeypatch.setattr(source, 'call', lambda *args, **kwargs: bad)
    result = invoke('trading.submit_order', account='ACC1', instrument='600000.SH', side='BUY', quantity=100, pricing="LIMIT", price=1.)
    assert result['status'] == 'error'
    assert result['error']['outcome'] == 'unknown'


def test_tick_timestamp_units_are_not_silently_reinterpreted(invoke, service, monkeypatch):
    source = service._dispatcher.providers.market.b.environment.xtdata
    original = source.get_full_tick
    def ticks(code_list):
        result = original(code_list)
        result['BAD.SH']['time'] //= 1000
        return result
    monkeypatch.setattr(source, 'get_full_tick', ticks)
    batch = value(invoke, 'market.get_ticks', codes=['GOOD.SH', 'BAD.SH'])
    assert [item.status for item in batch.items] == ['ok', 'error']
    assert batch.items[1].error.error_type == 'INVALID_RESULT'
