"""Pricing semantics at model, socket, dispatch and SDK boundaries; no live orders."""
from dataclasses import replace

import pytest

from qmt_rpyc import QmtClient
from qmt_rpyc.contracts.trading import OrderRequest
from qmt_rpyc.adapters.xtquant_2_0_6_1.factory import create_providers
from tests.fixtures import mock_xtquant, service
from tests.test_integration import mock_server
from tests.test_service import invoke

MODES = [('LIMIT', 11, 0.01), ('LATEST_PRICE', 5, None),
         ('LATEST_PRICE', 5, 0), ('LATEST_PRICE', 5, 4.131)]


@pytest.mark.parametrize('pricing,source_type,price', MODES)
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
def test_pricing_survives_socket_submission_and_query(mock_server, pricing, source_type, price, side):
    with QmtClient.connect('127.0.0.1', port=mock_server.port) as client:
        submitted = client.trading.submit_order('ACC1', '510300.SH', side, 100,
            pricing=pricing, price=price, correlation_ref='pricing-test')
        order = next(o for o in client.trading.list_orders('ACC1') if o.order_id == submitted.order_id)
        assert order.pricing == pricing
        assert order.source_price_type == source_type
        assert order.submitted_price == (price if price is not None else 0)
        assert order.side == side and order.requested_quantity == 100
        assert order.correlation_ref == 'pricing-test'


@pytest.mark.parametrize('pricing,price', [
    ('LIMIT', None), ('LIMIT', 0), ('LIMIT', -1), ('LIMIT', True),
    ('LIMIT', float('nan')), ('LIMIT', float('inf')), ('LIMIT', '0.01'), ('LIMIT', 10 ** 400),
    ('LATEST_PRICE', -1), ('LATEST_PRICE', True), ('LATEST_PRICE', '4.131'),
    ('LATEST_PRICE', float('nan')), ('LATEST_PRICE', float('inf')), ('LATEST_PRICE', 10 ** 400),
    ('MARKET_PEER_PRICE_FIRST', 0.01), ('MARKET_MINE_PRICE_FIRST', 0),
    ('UNKNOWN', None), ('FIX_PRICE', 0.01),
])
def test_model_rejects_ambiguous_pricing(pricing, price):
    with pytest.raises(ValueError):
        OrderRequest('ACC1', '510300.SH', 'BUY', 100, pricing, price)


@pytest.mark.parametrize('extra', [
    {'price': 0.01}, {'pricing': 'LIMIT'}, {'pricing': 'LIMIT', 'price': 0},
    {'pricing': 'LATEST_PRICE', 'price': -1},
    {'pricing': 'MARKET_PEER_PRICE_FIRST', 'price': 0},
    {'pricing': 'MARKET_MINE_PRICE_FIRST', 'price': 0.01},
    {'pricing': 'UNKNOWN'},
])
def test_invalid_wire_pricing_never_reaches_sdk(invoke, service, monkeypatch, extra):
    source = service._dispatcher.providers.trading.b
    monkeypatch.setattr(source, 'call', lambda *a, **kw: pytest.fail('SDK called'))
    response = invoke('trading.submit_order', account='ACC1', instrument='510300.SH',
                      side='BUY', quantity=100, **extra)
    assert response['error']['error_type'] == 'INVALID_ARGUMENTS'
    assert response['error']['outcome'] == 'not_executed'


@pytest.mark.parametrize('constant', ['FIX_PRICE', 'LATEST_PRICE'])
@pytest.mark.parametrize('value', [None, 999])
def test_bad_pricing_constant_disables_trading_capabilities(service, constant, value):
    environment = service._dispatcher.providers.trading.b.environment
    class Constants:
        def __getattr__(self, name):
            return value if name == constant else getattr(environment.constants, name)
    providers = create_providers(environment=replace(environment, constants=Constants()))
    for name in ('trading.submit_order', 'trading.list_orders'):
        capability = providers.capabilities.operations[name]
        assert not capability.available
        assert constant in capability.reason


def test_unknown_query_pricing_preserves_source_code(service):
    from qmt_rpyc.contracts.trading import OrdersRequest
    source = service._dispatcher.providers.trading.b
    trader = source.environment.connection._trader
    trader._orders[123] = dict(stock_code='510300.SH', order_type=23,
                              order_volume=100, price_type=999, price=0)
    order = service._dispatcher.providers.trading.list_orders(OrdersRequest('ACC1'))[0]
    assert order.pricing == 'UNKNOWN' and order.source_price_type == 999


@pytest.mark.parametrize('side,source_side', [('BUY', 23), ('SELL', 24)])
def test_latest_price_preserves_legacy_sdk_arguments(service, monkeypatch, side, source_side):
    adapter = service._dispatcher.providers.trading
    calls = []
    def capture(*args):
        calls.append(args)
        return 123
    monkeypatch.setattr(adapter.b, 'call', capture)
    result = adapter.submit_order(OrderRequest(
        'ACC1', '510300.SH', side, 100, 'LATEST_PRICE', 4.131, 'strategy', 'ref'))
    assert result.order_id == '123'
    assert calls == [('trader.order_stock', 'ACC1', '510300.SH', source_side,
                      100, 5, 4.131, 'strategy', 'ref')]
