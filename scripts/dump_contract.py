#!/usr/bin/env python3
"""Generate the contract manifest from Python declarations (no SDK or connection)."""
import argparse
import json
from pathlib import Path

from qmt_rpyc.contracts.operations import CONTRACT_HASH, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='write UTF-8 JSON instead of stdout')
    args = parser.parse_args()
    payload = json.dumps({'contract_hash': CONTRACT_HASH, **manifest()}, ensure_ascii=False, indent=2) + '\n'
    if args.output is None:
        print(payload, end='')
    else:
        args.output.write_text(payload, encoding='utf-8')


if __name__ == '__main__':
    main()
