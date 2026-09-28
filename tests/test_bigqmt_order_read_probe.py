"""Existing-order diagnostics must never trigger writes or disclose IDs."""
import ast
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def probe(monkeypatch):
    path = Path(__file__).parents[1] / 'scripts/probe_bigqmt_order_read.py'
    source = path.read_text()
    ast.parse(source, feature_version=(3, 6))
    assert source.isascii()
    spec = importlib.util.spec_from_file_location('order_read_probe', path)
    p = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p)
    p.SIM_ACCOUNT_ID = 'private-account'
    def forbidden(*args):
        pytest.fail('read-only diagnostic must not submit, cancel or use latest order')
    for name in ('passorder', 'cancel', 'can_cancel_order', 'get_last_order_id', 'get_value_by_order_id'):
        monkeypatch.setattr(p, name, forbidden, raising=False)
    return p


def test_one_read_keeps_only_target_window_hints_and_hides_private_values(probe, monkeypatch, capsys):
    row = dict(m_strAccountID=probe.SIM_ACCOUNT_ID, m_strInstrumentID='000001', m_strExchangeID='SZ',
        m_nVolumeTotalOriginal=100, m_strInsertDate='20260928', m_strInsertTime='204801',
        m_strRemark='qp-private-marker', m_strOrderSysID='private-id', m_strOrderRef='private-ref',
        m_nOrderStatus=57, m_strErrorMsg='private-error-text', m_strSource='qmt_rpyc_probe')
    calls = []
    def read(*args):
        calls.append(args)
        return [row, dict(row, m_strInsertTime='202303'), dict(row, m_strInsertDate='20260927')]
    monkeypatch.setattr(probe, 'get_trade_detail_data', read, raising=False)
    probe.init(None)
    probe.init(None)
    probe.handlebar(None)
    assert calls == [(probe.SIM_ACCOUNT_ID, 'STOCK', 'ORDER')]
    output = capsys.readouterr().out
    assert not any(secret in output for secret in ('private-account', 'qp-private-marker', 'private-id', 'private-ref', 'private-error-text'))
    data = json.loads(output.splitlines()[0].removeprefix('QMT_RPYC_ORDER_READ '))['data']
    assert data['candidate_count'] == 1 and not data['identity_confirmed']
    assert data['candidates'][0]['m_nOrderStatus'] == 57
    assert data['candidates'][0]['fields']['m_strRemark']['starts_with_probe_prefix']


@pytest.mark.parametrize('mode', ['empty', 'exception', 'wrong_account'])
def test_empty_errors_and_account_mismatch_never_write(probe, monkeypatch, capsys, mode):
    def read(*args):
        if mode == 'exception':
            raise RuntimeError('private-native-error')
        return [] if mode == 'empty' else [{'m_strAccountID': 'other-private-account'}]
    monkeypatch.setattr(probe, 'get_trade_detail_data', read, raising=False)
    probe.init(None)
    output = capsys.readouterr().out
    assert 'private' not in output
    data = json.loads(output.splitlines()[-1].removeprefix('QMT_RPYC_ORDER_READ '))['data']
    assert data['submit_calls'] == data['cancel_calls'] == 0
