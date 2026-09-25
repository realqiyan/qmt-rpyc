"""Startup diagnostics identify loaded SDK modules, not configured SDK candidates."""
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

from qmt_rpyc.adapters.xtquant_2_0_6_1.factory import _log_sdk_origins


def test_logs_loaded_python_and_native_sdk_paths(monkeypatch, tmp_path, caplog):
    loaded = tmp_path / 'loaded-sdk'
    for name, filename in [('xtquant', '__init__.py'), ('xtquant.xtdata', 'xtdata.py'),
                           ('xtquant.xttrader', 'xttrader.py'), ('xtquant.xttype', 'xttype.py'),
                           ('xtquant.xtpythonclient', 'xtpythonclient.pyd')]:
        monkeypatch.setitem(sys.modules, name, SimpleNamespace(__file__=str(loaded / filename)))
    monkeypatch.setenv('QMT_PATH', str(tmp_path / 'different-qmt-installation'))
    with caplog.at_level(logging.INFO):
        _log_sdk_origins()
    assert sys.executable in caplog.text
    for filename in ('__init__.py', 'xtdata.py', 'xttrader.py', 'xttype.py', 'xtpythonclient.pyd'):
        assert str((loaded / filename).resolve()) in caplog.text
    assert 'different-qmt-installation' not in caplog.text


def test_missing_module_path_does_not_break_startup(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, 'xtquant', SimpleNamespace())
    with caplog.at_level(logging.INFO):
        _log_sdk_origins()
    assert 'SDK module xtquant: path unavailable' in caplog.text


def test_unresolvable_path_retains_import_origin(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, 'xtquant', SimpleNamespace(__file__='sdk/__init__.py'))
    def fail_resolve(self):
        raise OSError('unavailable target')
    monkeypatch.setattr(Path, 'resolve', fail_resolve)
    with caplog.at_level(logging.INFO):
        _log_sdk_origins()
    assert 'file=sdk/__init__.py; cannot resolve path: unavailable target' in caplog.text
