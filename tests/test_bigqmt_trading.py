from types import SimpleNamespace
from dataclasses import asdict
import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.bridge_queue import BridgeQueue
from qmt_rpyc.adapters.bigqmt.transport import PipeTransport
from qmt_rpyc.adapters.bigqmt.trading import TradingAdapter
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.contracts.trading import AccountRequest, OrdersRequest, OrderRequest, CancelRequest, ByOrderId, ByExchangeOrderId

ACCOUNT = 'synthetic'


def order(**updates):
    row = dict(m_strAccountID=ACCOUNT, m_strInstrumentID='510300', m_strExchangeID='SH',
               m_strOrderRef='0000123', m_strOrderSysID='SYS-A7', m_strInsertDate='20260928',
               m_strInsertTime='142301', m_nOpType=23, m_nOrderPriceType=55, m_dLimitPrice=.1,
               m_nVolumeTotalOriginal=100, m_nVolumeTraded=0, m_dTradedPrice=0., m_nOrderStatus=50,
               m_strErrorMsg='', m_strSource='test', m_strRemark='marker')
    row.update(updates)
    return row


def setup_bridge(mode='visible'):
    rows, calls = [], []
    account = dict(m_strAccountID=ACCOUNT, m_nBrokerType=2, m_dAvailable=10., m_dFrozenCash=2.,
                   m_dStockValue=30., m_dBalance=42.)
    def query(a, t, k):
        assert (a, t) == (ACCOUNT, 'STOCK')
        return [account] if k == 'ACCOUNT' else rows if k == 'ORDER' else []
    def submit(*args):
        calls.append(args)
        if mode == 'throw':
            raise RuntimeError('unknown after dispatch')
        if mode != 'invisible':
            rows.append(order(m_strRemark=args[9], m_strSource=args[7], m_nOrderStatus=57 if mode == 'rejected' else 50))
        return 0
    api = dict(get_trade_detail_data=query, passorder=submit,
               can_cancel_order=lambda *a: True,
               get_value_by_order_id=lambda *a: rows[0], cancel=lambda *a: calls.append(a) or True)
    runtime = StrategyRuntime(SimpleNamespace(), api)
    bridge = BridgeQueue(runtime)
    def exchange(name, raw, deadline):
        ticket = bridge.submit(raw); bridge.pump(); return ticket.poll()
    transport = PipeTransport(exchange_fn=exchange)
    transport.request('ping', {})
    return TradingAdapter(transport, observation_seconds=.01, sleep=lambda t: None), rows, calls, api, runtime


def request():
    return OrderRequest(ACCOUNT, '510300.SH', 'BUY', 100, 'LIMIT', .1, 'marker')


def test_native_zero_is_not_identity_and_leading_zero_reference_is_preserved():
    p, rows, calls, api, runtime = setup_bridge()
    result = p.submit_order(request())
    assert result.order_id == '0000123'
    assert len(calls) == 1
    assert calls[0][:10] == (23, 1101, ACCOUNT, '510300.SH', 11, .1, 100, '', 2, 'marker')
    assert p.list_orders(OrdersRequest(ACCOUNT))[0].pricing == 'UNKNOWN'
    with pytest.raises(ProviderError) as error:
        p.submit_order(request())
    assert error.value.outcome == 'not_executed'
    assert len(calls) == 1


@pytest.mark.parametrize('mode', ['invisible', 'throw'])
def test_unknown_submission_is_never_retried(mode):
    p, rows, calls, api, runtime = setup_bridge(mode)
    with pytest.raises(ProviderError) as error:
        p.submit_order(request())
    assert error.value.outcome == 'unknown'
    assert len(calls) == 1


def test_observed_rejection_uses_order_evidence():
    p, *_ = setup_bridge('rejected')
    result = p.submit_order(request())
    assert result.status == 'rejected'


def test_asset_and_order_identity_translation():
    p, rows, calls, api, runtime = setup_bridge()
    assert p.get_asset(AccountRequest(ACCOUNT)).total_asset == 42
    assert p.list_positions(AccountRequest(ACCOUNT)) == ()
    rows.append(order(m_nOpType=2147483647))
    value = p.list_orders(OrdersRequest(ACCOUNT))[0]
    assert value.order_id == '0000123' and value.exchange_order_id == 'SYS-A7'
    assert value.side == 'UNKNOWN'
    assert value.submitted_at.utcoffset().total_seconds() == 28800


@pytest.mark.parametrize('target', [ByOrderId('0000123'), ByExchangeOrderId('SH', 'SYS-A7')])
def test_cancel_resolves_exact_sysid_and_only_reports_request_acceptance(target):
    p, rows, calls, api, runtime = setup_bridge()
    rows.append(order())
    result = p.cancel_order(CancelRequest(ACCOUNT, target))
    assert result.status == 'succeeded'
    assert calls[0][:3] == ('SYS-A7', ACCOUNT, 'STOCK')


def test_cancel_ambiguous_lookup_mismatch_and_nonboolean_fail_closed():
    p, rows, calls, api, runtime = setup_bridge()
    rows.extend([order(), order()])
    r = CancelRequest(ACCOUNT, ByOrderId('0000123'))
    with pytest.raises(ProviderError) as e:
        p.cancel_order(r)
    assert e.value.outcome == 'not_executed' and not calls
    rows.pop()
    api['get_value_by_order_id'] = lambda *a: order(m_strOrderSysID='other')
    with pytest.raises(ProviderError) as e:
        p.cancel_order(r)
    assert e.value.outcome == 'not_executed' and not calls
    api['get_value_by_order_id'] = lambda *a: rows[0]
    api['cancel'] = lambda *a: calls.append(a) or 0
    with pytest.raises(ProviderError) as e:
        p.cancel_order(r)
    assert e.value.outcome == 'unknown' and len(calls) == 1


def test_post_execution_disconnect_is_unknown_and_not_replayed():
    p, rows, calls, api, runtime = setup_bridge()
    exchange = p.transport.exchange
    def disconnect(*args):
        exchange(*args)
        raise OSError('lost response')
    p.transport.exchange = disconnect
    with pytest.raises(ProviderError) as e:
        p.submit_order(request())
    assert e.value.outcome == 'unknown' and len(calls) == 1


def test_expired_preflight_does_not_submit():
    p, rows, calls, api, runtime = setup_bridge()
    args = asdict(request());args['marker'] = args.pop('correlation_ref')
    result = runtime.trading.execute('trade_submit', args, deadline=0)
    assert result['outcome'] == 'not_executed' and not calls


def test_latest_sell_native_arguments_and_unavailable_account_prevent_writes():
    p, rows, calls, api, runtime = setup_bridge('invisible')
    r = OrderRequest(ACCOUNT, '510300.SH', 'SELL', 100, 'LATEST_PRICE', None, 'sell-marker')
    with pytest.raises(ProviderError) as e:
        p.submit_order(r)
    assert e.value.outcome == 'unknown'
    assert calls[0][:7] == (24, 1101, ACCOUNT, '510300.SH', 5, 0, 100)
    api['get_trade_detail_data'] = lambda *a: [dict(m_strAccountID='wrong', m_nBrokerType=2)]
    with pytest.raises(ProviderError) as e:
        p.submit_order(request())
    assert e.value.outcome == 'not_executed' and len(calls) == 1


def test_ambiguous_correlated_orders_do_not_claim_submission_success():
    p, rows, calls, api, runtime = setup_bridge()
    original = api['passorder']
    def duplicate(*args):
        value = original(*args)
        rows.append(dict(rows[0], m_strOrderRef='another'))
        return value
    api['passorder'] = duplicate
    with pytest.raises(ProviderError) as e:
        p.submit_order(request())
    assert e.value.outcome == 'unknown' and len(calls) == 1


def test_local_signal_plus_unique_broker_record_returns_broker_identity():
    p, rows, calls, api, runtime = setup_bridge()
    original = api['passorder']
    def dual(*args):
        result = original(*args)
        rows.insert(0, dict(rows[0], m_strOrderRef='local-reference', m_strOrderSysID=''))
        return result
    api['passorder'] = dual
    assert p.submit_order(request()).order_id == '0000123'
    assert len(calls) == 1


def test_local_signal_without_broker_record_stays_unknown():
    p, rows, calls, api, runtime = setup_bridge()
    original = api['passorder']
    def local(*args):
        result = original(*args)
        rows[0]['m_strOrderSysID'] = ''
        return result
    api['passorder'] = local
    with pytest.raises(ProviderError) as e:
        p.submit_order(request())
    assert e.value.outcome == 'unknown' and len(calls) == 1
