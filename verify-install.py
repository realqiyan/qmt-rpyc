"""Offline installation verification; never connects to QMT or reads credentials."""
import ast
import hashlib
import argparse
import json
import sys
from pathlib import Path

try:
    from qmt_rpyc import version
    from qmt_rpyc.adapters.bigqmt.bridge_queue import BRIDGE_VERSION
    from qmt_rpyc.contracts.operations import CONTRACT_HASH, CONTRACT_VERSION
except ImportError:
    print('[ERROR] Incomplete or outdated qmt-rpyc installation. '
          'Run install-server.bat from the current official bundle.', file=sys.stderr)
    raise SystemExit(1)


def verify(expected_version=None):
    installed = version.__version__
    if expected_version is not None and installed != expected_version:
        raise ValueError('Expected qmt-rpyc %s; loaded %s' % (expected_version, installed))
    # Release policy for the current 0.x series: both compatibility IDs equal MINOR.
    major, minor = installed.split('.')[:2]
    if major == '0' and (CONTRACT_VERSION != int(minor) or BRIDGE_VERSION != int(minor)):
        raise ValueError('Mixed installation: version=%s, contract=%s, bridge=%s. '
                         'Reinstall the official bundle and replace the QMT strategy.'
                         % (installed, CONTRACT_VERSION, BRIDGE_VERSION))
    return dict(version=installed, contract=CONTRACT_VERSION, bridge=BRIDGE_VERSION,
                contract_hash=CONTRACT_HASH, python=sys.executable,
                package=str(Path(version.__file__).resolve().parent))


def verify_strategy(path, installed):
    source = path.read_bytes().decode('gbk')
    values = {}
    for node in ast.parse(source, feature_version=(3, 6)).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in (
                        'RELEASE_VERSION', 'STRATEGY_BUILD', 'BRIDGE_VERSION'):
                    values[target.id] = ast.literal_eval(node.value)
    if (values.get('RELEASE_VERSION') != installed['version']
            or values.get('BRIDGE_VERSION') != installed['bridge']):
        raise ValueError('Bundled strategy does not match installed service. Extract a clean official bundle.')
    build = values.get('STRATEGY_BUILD')
    original = source.replace("STRATEGY_BUILD = " + repr(build) + "\n\n", "", 1)
    if build != hashlib.sha256(original.encode('gbk')).hexdigest()[:16]:
        raise ValueError('Bundled strategy fingerprint mismatch. Extract a clean official bundle.')
    return dict(path=str(path.resolve()), build=build)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-version')
    args = parser.parse_args(argv)
    try:
        result = verify(args.expected_version)
        strategy = Path(__file__).with_name('bigqmt_strategy.py')
        if strategy.is_file():
            result['bundled_strategy'] = verify_strategy(strategy, result)
    except (ValueError, SyntaxError, OSError) as exc:
        print('[ERROR] ' + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
