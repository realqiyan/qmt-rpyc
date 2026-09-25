"""Explicit SDK selection before any native xtquant imports."""
import importlib
import importlib.util
import logging
import os
from pathlib import Path
import sys

logger = logging.getLogger(__name__)


def configured_sdk_path(values=None):
    return os.environ.get('QMT_XTQUANT_PATH', (values or {}).get('QMT_XTQUANT_PATH', ''))


def validate_sdk_path(value):
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError('QMT_XTQUANT_PATH must be an absolute xtquant package directory')
    path = path.resolve(strict=True)
    if not path.is_dir() or not (path / '__init__.py').is_file():
        raise ValueError('QMT_XTQUANT_PATH must be the xtquant package directory containing __init__.py: {}'.format(path))
    return path


def load_sdk(value=None):
    """Bind only xtquant, leaving unrelated broker site-packages off sys.path."""
    value = configured_sdk_path() if value is None else value
    if getattr(sys.modules.get('xtquant'), '__qmt_load_failed__', False):
        raise RuntimeError('configured xtquant failed to load; restart the process')
    if not value:
        return importlib.import_module('xtquant')
    path = validate_sdk_path(value)
    loaded = sys.modules.get('xtquant')
    if loaded is not None:
        origin = getattr(loaded, '__file__', None)
        if origin and Path(origin).resolve() == path / '__init__.py':
            return loaded
        raise RuntimeError('xtquant is already loaded from another location; restart the process')
    if any(name.startswith('xtquant.') for name in sys.modules):
        raise RuntimeError('xtquant submodules are already loaded; restart the process')
    logger.info('Configured SDK directory: %s; resolved=%s', value, path)
    spec = importlib.util.spec_from_file_location(
        'xtquant', path / '__init__.py', submodule_search_locations=[str(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['xtquant'] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        # Never permit a later call to reuse a partly initialized native SDK.
        # Keep the failed package bound; SDK switching requires a fresh process.
        logger.exception('Failed to load configured SDK from %s; restart required', path)
        module.__qmt_load_failed__ = True
        raise
    return module
