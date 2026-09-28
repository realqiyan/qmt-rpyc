"""Public simplification must preserve native SDK positional conventions."""
import pytest

from qmt_rpyc.contracts.trading import OrderRequest
from tests.fixtures import mock_xtquant, service
from tests.test_service import invoke


@pytest.mark.parametrize('pricing,price,native_price', [('LIMIT', .1, 11), ('LATEST_PRICE', 4.2, 5)])
@pytest.mark.parametrize('side,native_side', [('BUY', 23), ('SELL', 24)])
def test_removed_strategy_slot_does_not_shift_native_remark(service, monkeypatch, pricing, price, native_price, side, native_side):
    adapter = service._dispatcher.providers.trading
    calls = []
    def call(*args):
        calls.append(args)
        return 123
    monkeypatch.setattr(adapter.b, 'call', call)
    result = adapter.submit_order(OrderRequest('ACC1', '510300.SH', side, 100, pricing, price, 'unique-ref'))
    assert result.order_id == '123'
    assert calls == [('trader.order_stock', 'ACC1', '510300.SH', native_side,
                      100, native_price, price, '', 'unique-ref')]


def test_obsolete_strategy_argument_is_rejected_before_sdk(invoke, service, monkeypatch):
    monkeypatch.setattr(service._dispatcher.providers.trading.b, 'call',
                        lambda *a, **kw: pytest.fail('obsolete request reached SDK'))
    result = invoke('trading.submit_order', account='ACC1', instrument='510300.SH',
                    side='BUY', quantity=100, pricing='LIMIT', price=.1, strategy_name='old')
    assert result['error']['error_type'] == 'INVALID_ARGUMENTS'
    assert result['error']['outcome'] == 'not_executed'
