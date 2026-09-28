"""Read-only trading investigation must not emit private record values."""
import ast
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def probe():
    path = Path(__file__).parents[1] / 'scripts/probe_bigqmt_trading.py'
    ast.parse(path.read_text(), feature_version=(3, 6))
    assert path.read_text().isascii()
    spec = importlib.util.spec_from_file_location('trading_probe', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def records(output):
    return [json.loads(line.removeprefix('QMT_RPYC_TRADE_PROBE ')) for line in output.splitlines()]


def test_blank_account_does_not_query_or_mutate(probe, monkeypatch, capsys):
    def forbidden(*args):
        pytest.fail('unconfigured strategy must not call QMT')
    monkeypatch.setattr(probe, 'get_trade_detail_data', forbidden, raising=False)
    probe.init(None)
    assert records(capsys.readouterr().out)[-1]['event'] == 'configuration_required'


def test_only_three_reads_hide_private_values_but_preserve_types_and_id_relations(probe, monkeypatch, capsys):
    calls = []
    probe.SIM_ACCOUNT_ID = 'synthetic-private-account'
    def read(account, account_type, kind):
        calls.append((account, account_type, kind))
        return [SimpleNamespace(m_strAccountID=account, m_strInstrumentID='private-instrument',
            m_nOrderID=246891357, m_strOrderSysID='246891357', m_strRemark='private-remark',
            m_dBalance=9753197531.25, m_nOrderStatus=50, m_nOrderPriceType=11,
            m_strInsertTime='12:34:56')] * 3
    monkeypatch.setattr(probe, 'get_trade_detail_data', read, raising=False)
    def forbidden(*args):
        pytest.fail('read-only probe must not trade or fetch latest order')
    for name in ('passorder', 'cancel', 'get_last_order_id', 'can_cancel_order', 'get_value_by_order_id'):
        monkeypatch.setattr(probe, name, forbidden, raising=False)
    probe.init(None)
    output = capsys.readouterr().out
    assert calls == [(probe.SIM_ACCOUNT_ID, 'STOCK', kind) for kind in ('ACCOUNT', 'POSITION', 'ORDER')]
    for secret in (probe.SIM_ACCOUNT_ID, 'private-instrument', 'private-remark', '246891357', '9753197531.25', '12:34:56'):
        assert secret not in output
    results = [r['data'] for r in records(output) if r['event'] == 'read_result']
    assert all(len(r['records']) == 2 and r['truncated'] for r in results)
    row = results[-1]['records'][0]
    assert row['fields']['m_strAccountID']['matches_requested_account'] is True
    assert row['fields']['m_nOrderStatus']['enum_value'] == 50
    assert row['fields']['m_nOrderID']['type'] == 'int'
    assert row['identity_comparisons'][0] == {'left': 'm_nOrderID', 'right': 'm_strOrderSysID', 'same_text': True}
    assert records(output)[-1]['data']['mutation_calls'] == 0


def test_native_and_property_errors_are_visible_without_private_messages(probe, monkeypatch, capsys):
    probe.SIM_ACCOUNT_ID = 'synthetic'
    class Row:
        @property
        def m_strInstrumentID(self):
            raise ValueError('private-property-text')
    def read(account, kind, detail):
        if detail == 'ACCOUNT':
            raise RuntimeError('private-error-text')
        return [Row()]
    monkeypatch.setattr(probe, 'get_trade_detail_data', read, raising=False)
    probe.init(None)
    output = capsys.readouterr().out
    assert 'private-property-text' not in output and 'private-error-text' not in output
    results = [r['data'] for r in records(output) if r['event'] == 'read_result']
    assert results[0]['error_type'] == 'RuntimeError'
    assert results[1]['records'][0]['fields']['m_strInstrumentID']['error_type'] == 'ValueError'


def test_empty_queries_are_reported_without_claiming_field_compatibility(probe, monkeypatch, capsys):
    probe.SIM_ACCOUNT_ID = 'synthetic'
    monkeypatch.setattr(probe, 'get_trade_detail_data', lambda *args: [], raising=False)
    probe.init(None)
    results = [r['data'] for r in records(capsys.readouterr().out) if r['event'] == 'read_result']
    assert all(r['count'] == 0 and r['records'] == [] for r in results)
