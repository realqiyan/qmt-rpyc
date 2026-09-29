"""First-install bootstrap refuses to overwrite an existing managed install."""
import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.skipif(os.name != 'nt', reason='requires Windows cmd')
def test_installer_redirects_existing_install_to_update(tmp_path):
    managed = tmp_path / 'qmt-rpyc' / 'venv' / 'Scripts'
    managed.mkdir(parents=True)
    (managed / 'qmt-rpyc-server.exe').write_bytes(b'existing installation')
    installer = Path(__file__).parents[1] / 'install-server.bat'
    result = subprocess.run(['cmd.exe', '/d', '/c', str(installer)],
                            env={**os.environ, 'LOCALAPPDATA': str(tmp_path)},
                            capture_output=True, timeout=10)
    assert result.returncode == 1
    assert b'update' in result.stdout
    assert (managed / 'qmt-rpyc-server.exe').read_bytes() == b'existing installation'
