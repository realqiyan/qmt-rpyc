import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from qmt_rpyc.adapters.bigqmt.bridge_queue import BRIDGE_VERSION, BridgeQueue, wire_dump
from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.factory import create_providers
from qmt_rpyc.adapters.bigqmt.transport import PipeTransport
from qmt_rpyc.adapters.errors import ProviderError
from qmt_rpyc.adapters.registry import select_adapter
from qmt_rpyc.contracts.common import EmptyRequest


def transport():
    bridge = BridgeQueue(StrategyRuntime(SimpleNamespace(), {}))
    calls = []
    def exchange(name, raw, deadline):
        calls.append(raw)
        ticket = bridge.submit(raw)
        bridge.pump()
        return ticket.poll()
    return PipeTransport(exchange_fn=exchange), bridge, calls


def test_negotiation_and_missing_native_method_are_distinct():
    channel, bridge, calls = transport()
    with pytest.raises(ProviderError, match='not connected'):
        channel.request('instrument', {'code': '000001.SZ'})
    assert calls == []
    assert not channel.request('ping', {})['read_only']
    assert channel.instance == bridge.instance
    with pytest.raises(ProviderError) as error:
        channel.request('instrument', {'code': '000001.SZ'})
    assert error.value.category == 'API_UNAVAILABLE'
    assert len(calls) == 2


@pytest.mark.parametrize('fault', ['wrong_id', 'wrong_instance', 'disconnect', 'malformed'])
def test_exchange_failures_never_replay_a_request(fault):
    channel, bridge, calls = transport()
    channel.request('ping', {})
    original = channel.exchange
    def exchange(*args):
        raw = original(*args)
        if fault == 'disconnect':
            raise OSError('response lost after execution')
        if fault == 'malformed':
            return b'null'
        response = json.loads(raw)
        response['id' if fault == 'wrong_id' else 'instance'] = '0' * 32
        return wire_dump(response)
    channel.exchange = exchange
    with pytest.raises(ProviderError):
        channel.request('instrument', {'code': '000001.SZ'})
    assert len(calls) == 2


def test_bigqmt_factory_capabilities_and_downloads_do_not_need_bridge():
    channel, _, calls = transport()
    adapter = select_adapter('bigqmt')
    assert not adapter.requires_native_sdk
    providers = create_providers(SimpleNamespace(transport=channel))
    assert len(providers.capabilities.operations) == 24
    assert providers.capabilities.operations['trading.submit_order'].available
    providers.downloads.index_weights(EmptyRequest())
    assert calls == []
    assert providers.trading.transport is channel


def test_standalone_strategy_is_python36_and_has_no_package_dependency(tmp_path):
    target = tmp_path / 'strategy.py'
    subprocess.run([sys.executable, 'scripts/build_bigqmt_strategy.py', '--output', str(target)], check=True)
    import ast
    raw = target.read_bytes()
    assert raw.startswith(b'# coding: gbk')
    assert 'QMT 策略文件' in raw.decode('gbk')
    compile(raw, str(target), 'exec')
    tree = ast.parse(raw.decode('gbk'), feature_version=(3, 6))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not node.level and not node.module.startswith(('qmt_rpyc', 'xtquant'))
        if isinstance(node, ast.Import):
            assert not any(alias.name.startswith(('qmt_rpyc', 'xtquant')) for alias in node.names)
    # Importing the artifact itself does not load WinDLL or start native I/O.
    namespace = {'__name__': 'generated_strategy'}
    exec(compile(tree, str(target), 'exec'), namespace)
    assert namespace['_service'] is None
    from qmt_rpyc.version import __version__
    import hashlib
    assert namespace['RELEASE_VERSION'] == __version__
    from qmt_rpyc.contracts.operations import CONTRACT_VERSION
    assert namespace['BRIDGE_VERSION'] == CONTRACT_VERSION == int(__version__.split('.')[1])
    build = namespace['STRATEGY_BUILD']
    source = raw.decode('gbk')
    original = source.replace("STRATEGY_BUILD = " + repr(build) + "\n\n", "", 1)
    assert build == hashlib.sha256(original.encode('gbk')).hexdigest()[:16]
    assert 'strategy_revision=' not in source
    assert 'bridge_protocol=' not in source
    assert 'version=' in source and 'build=' in source


def test_mismatched_strategy_reports_expected_and_received_protocol_without_retry():
    channel, bridge, calls = transport()
    channel.request('ping', {})
    original = channel.exchange
    def outdated(*args):
        response = json.loads(original(*args))
        response['version'] = 4
        return wire_dump(response)
    channel.exchange = outdated
    with pytest.raises(ProviderError, match='service expects 9, strategy reports 4'):
        channel.request('ping', {})
    assert len(calls) == 2
    assert channel.instance is None


def test_legacy_pipe_close_explains_release_check_without_claiming_a_known_mismatch():
    calls = []
    def closed(*args):
        calls.append(args)
        raise OSError(109, 'pipe ended')
    channel = PipeTransport(exchange_fn=closed)
    with pytest.raises(ProviderError, match='pipe closed by strategy; check'):
        channel.request('ping', {})
    assert len(calls) == 1


def test_default_transport_reuses_one_persistent_client(monkeypatch):
    import qmt_rpyc.adapters.bigqmt.transport as module
    created = []
    class FakeClient:
        def __init__(self, name):
            created.append(self)
            self.calls = []
        def exchange(self, raw, deadline):
            self.calls.append(raw)
            request = json.loads(raw)
            return wire_dump(dict(version=BRIDGE_VERSION, instance='1' * 32, id=request['id'],
                                  result={'runtime': 'bigqmt', 'read_only': False}))
    monkeypatch.setattr(module, 'PipeClient', FakeClient)
    channel = PipeTransport()
    channel.request('ping', {})
    channel.request('ping', {})
    assert len(created) == 1
    assert len(created[0].calls) == 2
