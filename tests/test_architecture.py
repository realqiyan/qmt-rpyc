"""Dependency boundaries and installed public model isolation."""
import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).parents[1] / 'src' / 'qmt_rpyc'


def imports(path):
    for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            yield from (name.name for name in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            yield node.module


def test_contracts_are_independent_of_runtime_implementations():
    for path in (ROOT / 'contracts').glob('*.py'):
        for module in imports(path):
            assert module.split('.')[0] in {'dataclasses', 'datetime', 'typing', 'types', 'hashlib', 'json', 'math', 're', 'collections'} or module.startswith('qmt_rpyc.contracts'), (path, module)


def test_client_does_not_import_server_or_sdk():
    for directory in ('client', 'transport'):
        for path in (ROOT / directory).glob('*.py'):
            assert not any(module.startswith(('qmt_rpyc.server', 'qmt_rpyc.adapters', 'xtquant')) for module in imports(path)), path


def test_dispatcher_has_no_concrete_sdk_dependency():
    for path in (ROOT / 'server').glob('*.py'):
        if path.name == 'main.py':
            continue  # composition root
        assert not any(module.startswith(('xtquant', 'qmt_rpyc.adapters.xtquant')) for module in imports(path)), path


def test_model_import_does_not_load_network_or_sdk():
    subprocess.run([sys.executable, '-c', '''
import sys
from qmt_rpyc.contracts.options import OptionContract
assert 'rpyc' not in sys.modules
assert 'qmt_rpyc.client' not in sys.modules
assert 'xtquant' not in sys.modules
'''], check=True)


def test_rpc_exposes_fixed_calls_and_one_isolated_debug_entry():
    from qmt_rpyc.server.service import XtquantService
    assert {name for name in vars(XtquantService) if name.startswith('exposed_')} == {'exposed_negotiate', 'exposed_call', 'exposed_debug'}
