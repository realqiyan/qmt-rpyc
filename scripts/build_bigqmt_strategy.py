#!/usr/bin/env python3
"""Build the release's default standalone BigQMT strategy (GBK, Python 3.6)."""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from qmt_rpyc.adapters.bigqmt.strategy import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    data = build().encode('gbk')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(data)
    print(args.output)


if __name__ == '__main__':
    main()
