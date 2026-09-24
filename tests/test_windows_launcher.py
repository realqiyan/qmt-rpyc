"""Exercise the batch launcher under real Windows cmd without starting QMT."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(os.name != 'nt', reason='requires Windows cmd')


@pytest.mark.parametrize('source_checkout', [False, True])
def test_managed_install_starts_without_source_venv(tmp_path, source_checkout):
    checkout = tmp_path / 'checkout with spaces'
    checkout.mkdir()
    launcher = checkout / 'start-rpyc.bat'
    shutil.copyfile(Path(__file__).parents[1] / 'start-rpyc.bat', launcher)
    if source_checkout:
        (checkout / 'pyproject.toml').write_text('[project]\n')
    local = tmp_path / 'local app data'
    managed = local / 'qmt-rpyc'
    managed.mkdir(parents=True)
    (managed / 'qmt-rpyc-server.bat').write_bytes(
        b'@echo off\r\necho MANAGED_ARGS=%*\r\nexit /b 7\r\n')
    result = subprocess.run(
        ['cmd.exe', '/d', '/c', str(launcher)],
        env={**os.environ, 'LOCALAPPDATA': str(local)},
        input=b'\r\n', capture_output=True, timeout=15,
    )
    assert b'MANAGED_ARGS=start' in result.stdout
    assert result.returncode == 7
