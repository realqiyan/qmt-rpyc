from types import SimpleNamespace

import pandas as pd
import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import StrategyRuntime
from qmt_rpyc.adapters.bigqmt.cache import DiscoveryCache
from qmt_rpyc.adapters.bigqmt.debug import DebugGateway
from qmt_rpyc.client.debug import DebugClient, DebugError


def client(context=None, global_api=None):
    runtime = StrategyRuntime(context or SimpleNamespace(), global_api or {})
    cache = DiscoveryCache()
    cache.get('old', lambda: ('cached',))
    connection = SimpleNamespace(transport=SimpleNamespace(request=runtime.dispatch,
        diagnostics=lambda: {'business_capacity': 3, 'heartbeat_capacity': 1}), discovery_cache=cache)
    gateway = DebugGateway(connection)
    return DebugClient(SimpleNamespace(root=SimpleNamespace(debug=gateway))), cache


def test_native_debug_keeps_arguments_and_dataframe_shape_without_contract_projection():
    calls = []
    def get_market_data_ex(fields=None, stock_code=None):
        calls.append((fields, stock_code))
        return pd.DataFrame({'new_field': [1.0, float('nan')]}, index=['same', 'same'])
    debug, _ = client(SimpleNamespace(get_market_data_ex=get_market_data_ex))
    value = debug.call('context.get_market_data_ex', kwargs={'stock_code': ['000001.SZ']})
    assert calls == [(None, ['000001.SZ'])]
    assert value == dict(type='DataFrame', columns=['new_field'], index=['same', 'same'], data=[[1.0], [None]])


def test_metadata_inspects_unknown_members_but_calls_only_explicit_read_allowlist():
    calls = []
    debug, _ = client(SimpleNamespace(new_native_method=lambda: calls.append('unexpected')),
                      {'passorder': lambda: calls.append('order')})
    assert not debug.describe('context.new_native_method')['call_allowed']
    surface = debug.describe()
    assert 'global.passorder' in surface['targets'] and 'bridge.cache_info' in surface['bridge_targets']
    for target in ('global.passorder', 'context.new_native_method', 'context.__class__', 'context.get_full_tick.__call__'):
        with pytest.raises(DebugError) as error:
            debug.call(target)
        assert error.value.outcome == 'not_executed'
    assert calls == []


def test_debug_raw_integer_keys_and_cache_controls():
    debug, cache = client(global_api={'get_history_index_weight': lambda code: {1782748800000: ['code']}})
    assert debug.call('global.get_history_index_weight', ['index']) == dict(type='mapping', items=[[1782748800000, ['code']]])
    assert debug.call('bridge.cache_info')['entries'] == 1
    assert debug.call('bridge.clear_cache')['entries'] == 0
    assert debug.call('bridge.transport')['heartbeat_capacity'] == 1


def test_native_debug_failure_is_not_retried():
    calls = []
    def read(*args):
        calls.append(1)
        raise ValueError('source failure')
    debug, _ = client(SimpleNamespace(get_full_tick=read))
    with pytest.raises(DebugError) as error:
        debug.call('context.get_full_tick', [['000001.SZ']])
    assert error.value.phase == 'sdk_execution' and error.value.outcome == 'unknown'
    assert calls == [1]
