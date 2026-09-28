import ast
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from qmt_rpyc.adapters.bigqmt.bridge_runtime import BAR_FIELDS, StrategyRuntime, frame, plain


def test_embedded_sources_parse_as_python36_without_native_sdk_imports():
    root = Path('src/qmt_rpyc/adapters/bigqmt')
    for name in ('bridge_runtime.py', 'financial_wire.py'):
        tree = ast.parse((root / name).read_text(), feature_version=(3, 6))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith('xtquant') for alias in node.names)


def test_native_frames_keep_duplicates_and_scalar_types_for_external_validation():
    value = pd.DataFrame({'volume': [np.int64(1), np.int64(2)], 'close': [np.nan, 3.0]}, index=['20260101'] * 2)
    assert frame(value) == {'columns': ['volume', 'close'], 'index': ['20260101'] * 2, 'data': [[1, None], [2, 3.0]]}
    assert plain(np.int64(1782748800000)) == 1782748800000
    with pytest.raises(ValueError):
        plain(float('inf'))
    with pytest.raises(ValueError):
        plain({1782748800000: 1})


def test_native_daily_call_has_explicit_daily_period_and_no_subscription():
    calls = []
    def read(*args):
        calls.append(args)
        return {'000001.SZ': pd.DataFrame({'volume': [100]}, index=['20260928'])}
    runtime = StrategyRuntime(SimpleNamespace(get_market_data_ex=read), {})
    result = runtime.dispatch('daily_bars', dict(codes=['000001.SZ'], start='20260928', end='', count=1, adjustment='front_ratio', fill_data=False))
    assert calls == [(list(BAR_FIELDS), ['000001.SZ'], '1d', '20260928', '', 1, 'front_ratio', False, False)]
    assert result['000001.SZ']['data'] == [[100]]


def test_runtime_rejects_worker_execution_and_write_or_arbitrary_operations():
    runtime = StrategyRuntime(SimpleNamespace(), {})
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match='strategy thread'):
            pool.submit(runtime.dispatch, 'ping', {}).result()
    for operation in ('passorder', 'download_history_data', '__getattr__', 'eval'):
        with pytest.raises(ValueError, match='allowlist'):
            runtime.dispatch(operation, {})
    with pytest.raises(ValueError, match='arguments'):
        runtime.dispatch('ping', {'unused': True})


def test_raw_financial_timestamp_keys_survive_runtime_serialization():
    def read(*args):
        assert args == (['ASHAREBALANCESHEET.tot_assets'], ['000001.SZ'], '20260629', '20260703', 'report_time')
        return {'000001.SZ': {'ASHAREBALANCESHEET.tot_assets': {1782748800000: 6028785000000.0}}}
    runtime = StrategyRuntime(SimpleNamespace(get_raw_financial_data=read), {})
    result = runtime.dispatch('financials', dict(code='000001.SZ', fields=['ASHAREBALANCESHEET.tot_assets'], start='20260629', end='20260703', date_basis='report_time'))
    assert result['000001.SZ']['ASHAREBALANCESHEET.tot_assets'] == [[1782748800000, 6028785000000.0]]
