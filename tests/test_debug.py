"""Raw SDK inspection/calls are opt-in and separate from business contracts."""
from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.xtquant_2_0_6_1.debug import DebugGateway
from qmt_rpyc.client.debug import DebugClient, DebugError
from qmt_rpyc.contracts.errors import QmtAuthError
from qmt_rpyc.transport.codec import dumps, loads
from tests.fixtures import mock_xtquant, service
from tests.test_integration import mock_server


@pytest.fixture
def debug(service, monkeypatch):
    handler = DebugGateway(service._connection_mgr)
    monkeypatch.setattr(type(service), '_debug_handler', handler)
    client = DebugClient(SimpleNamespace(root=SimpleNamespace(debug=service.exposed_debug)))
    return client, handler


def test_disabled_by_default_before_parsing_or_sdk_execution(service):
    result = loads(service.exposed_debug('not JSON'))
    assert result['error']['type'] == 'DEBUG_DISABLED'
    assert result['error']['outcome'] == 'not_executed'


def test_authentication_is_required_even_when_debug_enabled(debug, service, monkeypatch):
    monkeypatch.setattr(type(service), '_require_auth', True)
    with pytest.raises(QmtAuthError):
        service.exposed_debug(dumps({'action': 'describe'}))


def test_raw_new_sdk_function_bypasses_contract_and_keeps_defaults(debug, monkeypatch):
    client, handler = debug
    seen = []
    def broker_extension(value, **kwargs):
        seen.append((value, kwargs))
        return {'UncontractedField': value, 'InstrumentName': 'source name', 'extra': kwargs}
    monkeypatch.setattr(handler.owners['xtdata'], 'broker_extension', broker_extension, raising=False)
    result = client.call('xtdata.broker_extension', [42], {'arbitrary': ['x', 3]})
    assert result == {'UncontractedField': 42, 'InstrumentName': 'source name', 'extra': {'arbitrary': ['x', 3]}}
    assert seen == [(42, {'arbitrary': ['x', 3]})]
    assert 'value' in client.describe('xtdata.broker_extension')['signature']


def test_describe_sdk_surface_and_constant_without_invoking_methods(debug):
    client, _ = debug
    assert 'get_option_list' in client.describe()['xtdata']['functions']
    assert client.describe('xtconstant.STOCK_BUY')['value'] == 23
    assert 'undl_code' in client.describe('xtdata.get_option_list')['signature']


def test_raw_trader_uses_account_conversion_and_preserves_fields(debug):
    client, _ = debug
    asset = client.call('trader.query_stock_asset', ['ACC1'])
    assert asset['account_id'] == 'ACC1'
    assert 'cash' in asset and 'source_account_type' not in asset


def test_raw_dataframe_is_json_safe_without_contract_projection(debug):
    client, _ = debug
    data = client.call('xtdata.get_market_data_ex', kwargs={
        'stock_list': ['600000.SH'], 'period': '1d', 'count': 2})
    assert set(data['600000.SH']) == {'columns', 'index', 'data'}
    assert data['600000.SH']['index'][0] == 20260918


@pytest.mark.parametrize('target', ['os.system', 'xtdata.__dict__', 'xtdata.os.system', 'xtdata._private'])
def test_only_direct_public_sdk_targets_are_allowed(debug, target):
    client, _ = debug
    with pytest.raises(DebugError) as caught:
        client.call(target)
    assert caught.value.outcome == 'not_executed'


def test_raw_sdk_failure_preserves_exception_and_does_not_retry(debug, monkeypatch):
    client, handler = debug
    calls = []
    def failing():
        calls.append(1)
        raise ValueError('broker detail')
    monkeypatch.setattr(handler.owners['xtdata'], 'debug_failure', failing, raising=False)
    with pytest.raises(DebugError, match='broker detail') as caught:
        client.call('xtdata.debug_failure')
    assert caught.value.error['type'] == 'ValueError'
    assert caught.value.outcome == 'unknown'
    assert calls == [1]


def test_transport_failure_is_unknown_and_never_retried():
    calls = []
    def broken(payload):
        calls.append(payload)
        raise EOFError('lost')
    client = DebugClient(SimpleNamespace(root=SimpleNamespace(debug=broken)))
    with pytest.raises(DebugError) as caught:
        client.call('xtdata.anything')
    assert caught.value.outcome == 'unknown' and len(calls) == 1


def test_real_rpc_debug_does_not_require_business_negotiation(mock_server, service, monkeypatch):
    monkeypatch.setattr(type(service), '_debug_handler', DebugGateway(service._connection_mgr))
    monkeypatch.setattr(service._dispatcher, 'negotiate', lambda *a: pytest.fail('business negotiation'))
    with DebugClient.connect('127.0.0.1', port=mock_server.port) as client:
        ticks = client.call('xtdata.get_full_tick', [['600000.SH']])
        assert 'lastPrice' in ticks['600000.SH'] and 'last_price' not in ticks['600000.SH']


def test_debug_cli_uses_raw_client_and_json_arguments(monkeypatch, capsys):
    from contextlib import contextmanager
    from qmt_rpyc.cli import client as cli
    calls = []
    @contextmanager
    def connect(*args, **kwargs):
        yield SimpleNamespace(call=lambda *a: calls.append(a) or {'Raw': 1})
    monkeypatch.setattr(cli.DebugClient, 'connect_profile', connect)
    assert cli.main(['debug', 'call', 'xtdata.example', '--args', '["A"]', '--kwargs', '{"flag":true}']) == 0
    assert loads(capsys.readouterr().out) == {'Raw': 1}
    assert calls == [('xtdata.example', ['A'], {'flag': True})]


def test_config_debug_switch_is_explicit_and_validated(monkeypatch, tmp_path):
    from qmt_rpyc.server.main import _load_config
    monkeypatch.delenv('QMT_RPYC_DEBUG', raising=False)
    assert not _load_config(tmp_path / 'absent.env')['debug']
    monkeypatch.setenv('QMT_RPYC_DEBUG', '1')
    assert _load_config(tmp_path / 'absent.env')['debug']
    monkeypatch.setenv('QMT_RPYC_DEBUG', 'typo')
    with pytest.raises(ValueError, match='boolean'):
        _load_config(tmp_path / 'absent.env')


def test_debug_handler_is_wired_only_by_explicit_startup_config(mock_xtquant, monkeypatch):
    import rpyc.utils.server
    from qmt_rpyc.server.main import start_server
    from qmt_rpyc.server.service import XtquantService
    from tests.test_main import _config
    enabled = []
    class Server:
        def __init__(self, *args, **kwargs):
            pass
        def start(self):
            service = XtquantService()
            result = loads(service.exposed_debug(dumps({'action': 'describe', 'target': 'xtconstant.STOCK_BUY'})))
            enabled.append(result['status'] == 'ok')
        def close(self):
            pass
    monkeypatch.setattr(rpyc.utils.server, 'ThreadedServer', Server)
    start_server(_config(auth_key=None, allow_insecure=True, debug=True))
    start_server(_config(auth_key=None, allow_insecure=True, debug=False))
    assert enabled == [True, False]


def test_debug_cli_trader_mutation_requires_existing_confirmation_flag(monkeypatch, capsys):
    from qmt_rpyc.cli import client as cli
    monkeypatch.setattr(cli.DebugClient, 'connect_profile', lambda *a, **kw: pytest.fail('must not connect'))
    assert cli.main(['debug', 'call', 'trader.order_stock']) == cli.EXIT_CONFIRMATION
    assert loads(capsys.readouterr().err)['status'] == 'confirmation_required'
