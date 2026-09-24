"""Timestamp precision and standard datetime identity across server entry points."""
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from qmt_rpyc.adapters.xtquant_2_0_6_1.conversions import instant
from qmt_rpyc.transport.codec import decode, encode


@pytest.mark.parametrize('milliseconds, expected', [
    (-1, '1969-12-31T23:59:59.999000Z'),
    (0, '1970-01-01T00:00:00.000000Z'),
    (1700000000999, '2023-11-14T22:13:20.999000Z'),
    (1700000001000, '2023-11-14T22:13:21.000000Z'),
])
def test_millisecond_boundaries_round_trip(milliseconds, expected):
    value = instant(milliseconds, milliseconds=True)
    assert type(value) is datetime
    assert value.tzinfo is timezone.utc
    assert encode(value) == expected
    assert decode(datetime, expected) == value


@pytest.mark.parametrize('codec_first', [True, False])
def test_server_and_sdk_dump_keep_datetime_identity(codec_first):
    root = Path(__file__).parents[1]
    code = '''
import datetime
import runpy
original = datetime.datetime
if CODEC_FIRST:
    from qmt_rpyc.transport.codec import encode, decode
import qmt_rpyc.server.main
runpy.run_path('scripts/dump_api_surface.py', run_name='diagnostic_import_test')
assert datetime.datetime is original
from qmt_rpyc.transport.codec import encode, decode
from qmt_rpyc.adapters.xtquant_2_0_6_1.conversions import instant
value = instant(1700000000999, milliseconds=True)
assert type(value) is original
assert encode(value) == '2023-11-14T22:13:20.999000Z'
assert decode(original, encode(value)) == value
'''.replace('CODEC_FIRST', repr(codec_first))
    subprocess.run([sys.executable, '-c', code], check=True, cwd=root,
                   env={**os.environ, 'PYTHONPATH': str(root / 'src')})
