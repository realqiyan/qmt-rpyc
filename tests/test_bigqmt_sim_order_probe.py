"""Simulated mutation investigation: correlation and at-most-once boundaries."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def rig(monkeypatch):
    path = Path(__file__).parents[1] / 'scripts/probe_bigqmt_sim_order.py'
    text = path.read_text()
    ast.parse(text, feature_version=(3, 6))
    assert text.isascii()
    spec = importlib.util.spec_from_file_location('sim_order_probe', path)
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    p.SIM_ACCOUNT_ID = 'private-account'
    p.SIMULATION_CONFIRMED = True
    p.LIMIT_PRICE = 7.21
    calls, orders = [], []
    def read(account, account_type, kind):
        if kind == 'ACCOUNT':
            return [dict(m_strAccountID=account, m_nBrokerType=2)]
        return orders
    monkeypatch.setattr(p, 'get_trade_detail_data', read, raising=False)
    def submit(*args):
        calls.append(('submit', args))
        orders.append(dict(m_strAccountID=args[2], m_strInstrumentID='000001', m_strExchangeID='SZ',
            m_strRemark=args[9], m_strOrderRef='0000012345', m_strOrderSysID='private-id',
            m_nVolumeTotalOriginal=100, m_nVolumeTraded=0, m_nOrderStatus=50, m_nOrderPriceType=50))
        return 0
    monkeypatch.setattr(p, 'passorder', submit, raising=False)
    monkeypatch.setattr(p, 'get_value_by_order_id', lambda *args: orders[-1], raising=False)
    monkeypatch.setattr(p, 'can_cancel_order', lambda *args: True, raising=False)
    monkeypatch.setattr(p, 'cancel', lambda *args: calls.append(('cancel', args)) or True, raising=False)
    context = SimpleNamespace(run_time=lambda *args: None)
    return p, context, calls, orders


def test_unarmed_or_wrong_account_type_never_submits(rig, monkeypatch):
    p, context, calls, orders = rig
    p.SIMULATION_CONFIRMED = False
    p.init(context)
    p.sim_order_timer(context)
    assert calls == []
    p._STATE.clear()
    p.SIMULATION_CONFIRMED = True
    monkeypatch.setattr(p, 'get_trade_detail_data', lambda *args: [dict(m_strAccountID=p.SIM_ACCOUNT_ID, m_nBrokerType=3)])
    p.init(context)
    p.sim_order_timer(context)
    assert calls == []


@pytest.mark.parametrize('field,value,reason', [
    ('SIM_ACCOUNT_ID', 123456, 'must_be_nonempty_text'),
    ('SIM_ACCOUNT_ID', ' private-account ', 'remove_surrounding_whitespace'),
    ('SIMULATION_CONFIRMED', 'True', 'must_be_boolean_True_after_checking_simulated_account'),
    ('LIMIT_PRICE', '7.21', 'must_be_number_without_quotes'),
    ('LIMIT_PRICE', None, 'must_be_number_without_quotes'),
    ('LIMIT_PRICE', True, 'must_be_number_without_quotes'),
    ('LIMIT_PRICE', float('nan'), 'must_be_finite_and_positive'),
    ('LIMIT_PRICE', 0, 'must_be_finite_and_positive'),
    ('LIMIT_PRICE', 10 ** 400, 'must_be_finite_and_positive'),
])
def test_configuration_names_only_the_invalid_field_without_values(rig, monkeypatch, capsys, field, value, reason):
    p, context, calls, orders = rig
    setattr(p, field, value)
    def forbidden(*args):
        pytest.fail('invalid configuration must not call QMT')
    monkeypatch.setattr(p, 'get_trade_detail_data', forbidden)
    p.init(context)
    p.sim_order_timer(context)
    output = capsys.readouterr().out
    rows = [json.loads(line.removeprefix('QMT_RPYC_SIM_ORDER ')) for line in output.splitlines()]
    assert rows[-1]['event'] == 'configuration_required'
    assert rows[-1]['data'] == {'fields': [field], 'reasons': {field: reason},
                                'submit_calls': 0, 'cancel_calls': 0}
    assert 'private-account' not in output and '7.21' not in output
    assert not calls


def test_one_submission_and_cancel_with_private_ids_kept_local(rig, capsys):
    p, context, calls, orders = rig
    p.init(context)
    p.init(context)  # QMT may reenter initialization; never reset write guards.
    for _ in range(10):
        p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit', 'cancel']
    submit = calls[0][1]
    assert submit[:7] == (23, 1101, p.SIM_ACCOUNT_ID, '000001.SZ', 11, 7.21, 100)
    assert len(submit[9]) < 24
    assert calls[1][1] == ('private-id', p.SIM_ACCOUNT_ID, 'STOCK', context)
    assert p._STATE['done'] is True
    output = capsys.readouterr().out
    for value in ('private-id', p.SIM_ACCOUNT_ID, '0000012345', submit[9], '7.21'):
        assert value not in output
    assert 'post_cancel_samples_collected_not_a_success_assertion' in output


@pytest.mark.parametrize('damage', ['ambiguous', 'instrument', 'quantity', 'baseline', 'lookup'])
def test_uncertain_identity_never_cancels(rig, monkeypatch, damage):
    p, context, calls, orders = rig
    p.init(context)
    p.sim_order_timer(context)
    if damage == 'ambiguous':
        orders.append(dict(orders[0]))
    elif damage == 'instrument':
        orders[0]['m_strInstrumentID'] = '600000'
    elif damage == 'quantity':
        orders[0]['m_nVolumeTotalOriginal'] = 200
    elif damage == 'baseline':
        p._STATE['before_refs'].add(orders[0]['m_strOrderRef'])
    else:
        monkeypatch.setattr(p, 'get_value_by_order_id', lambda *args: dict(orders[0], m_strOrderSysID='unrelated'))
    for _ in range(3):
        p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit']
    assert p._STATE['done']


def test_write_exceptions_never_retry_and_only_correlated_order_may_be_cancelled(rig, monkeypatch):
    p, context, calls, orders = rig
    native_submit = p.passorder
    def submit(*args):
        native_submit(*args)
        raise RuntimeError('private-exception')
    def cancel(*args):
        calls.append(('cancel', args))
        raise RuntimeError('private-exception')
    monkeypatch.setattr(p, 'passorder', submit)
    monkeypatch.setattr(p, 'cancel', cancel)
    p.init(context)
    for _ in range(10):
        p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit', 'cancel']


def test_missing_remark_or_no_cancelability_times_out_without_replay(rig, monkeypatch):
    p, context, calls, orders = rig
    p.init(context)
    p.sim_order_timer(context)
    orders[0]['m_strRemark'] = 'unrelated'
    p.sim_order_timer(context)
    p._STATE['deadline'] = 0
    p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit']
    assert p._STATE['done']


def test_correlated_identity_change_never_triggers_another_cancel(rig):
    p, context, calls, orders = rig
    p.init(context)
    p.sim_order_timer(context)
    p.sim_order_timer(context)
    orders[0]['m_strOrderSysID'] = 'another-id'
    for _ in range(5):
        p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit', 'cancel']
    assert p._STATE['done']


def test_zero_return_without_matching_order_stays_unknown_and_never_retries(rig, monkeypatch, capsys):
    p, context, calls, orders = rig
    def signal_only(*args):
        calls.append(('submit', args))
        return 0
    monkeypatch.setattr(p, 'passorder', signal_only)
    p.init(context)
    p.sim_order_timer(context)
    for _ in range(5):
        p.sim_order_timer(context)
    p._STATE['deadline'] = 0
    p.sim_order_timer(context)
    assert [name for name, _ in calls] == ['submit']
    output = capsys.readouterr().out
    rows = [json.loads(line.removeprefix('QMT_RPYC_SIM_ORDER ')) for line in output.splitlines()]
    assert sum(row['event'] == 'query_progress' for row in rows) == 1
    result = rows[-1]['data']
    assert result['reason'] == 'observation_timeout_no_write_retry'
    assert result['order_reads'] == 5 and result['last_order_count'] == 0
    assert result['last_marker_matches'] == 0 and result['cancel_calls'] == 0
