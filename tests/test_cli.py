"""CLI exercises the same typed contract as the Python client."""
import json
from contextlib import contextmanager
from types import SimpleNamespace
import pytest
from qmt_rpyc.cli import client as cli
from qmt_rpyc.contracts.common import BatchResult
from qmt_rpyc.contracts.downloads import TaskRef


def test_api_inspection_does_not_connect(monkeypatch, capsys):
    monkeypatch.setattr(cli, '_connect', lambda args: pytest.fail('network call'))
    assert cli.main(['api', 'list', 'options', '--json']) == 0
    operations = json.loads(capsys.readouterr().out)
    assert set(operations) == {'options.get_expiry_dates', 'options.get_option_chain', 'options.get_contract_details'}
    assert cli.main(['api', 'describe', 'market.get_ticks', '--json']) == 0
    assert json.loads(capsys.readouterr().out)['request']['model'] == 'CodesRequest'


def test_call_validates_request_and_encodes_model(monkeypatch, capsys):
    calls = []
    @contextmanager
    def connect(args):
        def invoke(operation, request):
            calls.append((operation, request))
            return BatchResult(())
        yield SimpleNamespace(_invoke=invoke)
    monkeypatch.setattr(cli, '_connect', connect)
    assert cli.main(['call', 'market.get_ticks', '--payload', '{"codes":[]}']) == 0
    assert json.loads(capsys.readouterr().out) == {'items': []}
    assert calls[0][1].codes == ()
    assert cli.main(['call', 'market.get_ticks', '--payload', '{"codes":["A","A"]}']) != 0
    assert len(calls) == 1


def test_trading_requires_confirmation_before_connecting(monkeypatch, capsys):
    monkeypatch.setattr(cli, '_connect', lambda args: pytest.fail('must not connect'))
    payload = json.dumps(dict(account='test', instrument='600000.SH', side='BUY', quantity=100, pricing='LIMIT', price=1))
    assert cli.main(['call', 'trading.submit_order', '--payload', payload]) == cli.EXIT_CONFIRMATION
    assert json.loads(capsys.readouterr().err)['status'] == 'confirmation_required'


def test_request_file_and_download_creation(monkeypatch, tmp_path, capsys):
    seen = []
    @contextmanager
    def connect(args):
        def invoke(operation, request):
            seen.append(operation)
            return TaskRef('task', 'SECTORS')
        yield SimpleNamespace(_invoke=invoke)
    monkeypatch.setattr(cli, '_connect', connect)
    request = tmp_path / 'request.json'
    request.write_text('{"operation":"downloads.start_sectors","payload":{}}')
    assert cli.main(['call', '--request', str(request)]) == 0
    assert json.loads(capsys.readouterr().out) == {'task_id': 'task', 'kind': 'SECTORS'}
    assert seen == ['downloads.start_sectors']


def test_profile_initialization_retains_secret_storage_controls(monkeypatch, capsys):
    saved = []
    monkeypatch.setenv('QMT_RPYC_AUTH_KEY', 'synthetic-secret')
    monkeypatch.setattr(cli, 'load_profiles', lambda: {})
    monkeypatch.setattr(cli, 'save_profile', lambda *a, **kw: saved.append((a, kw)) or '/tmp/profile')
    assert cli.main(['init', '--non-interactive', '--store-plaintext']) == 0
    output = capsys.readouterr().out
    assert 'synthetic-secret' not in output
    assert saved[0][1]['secret'] == 'synthetic-secret'
