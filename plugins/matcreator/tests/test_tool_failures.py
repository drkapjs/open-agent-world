"""Tool-local failure/cancellation and checkpoint contracts in the isolated Pack."""
import os
from pathlib import Path
import subprocess

import pytest

from plugins.matcreator.oaw_matcreator import native_environment


@pytest.mark.skipif(not os.environ.get('OAW_MATCREATOR_MANAGED'), reason='requires Pack orchestration dependencies')
def test_native_tool_boundary_and_interrupted_history(tmp_path):
    executable, dependencies = native_environment.read_environment()
    result = subprocess.run([executable, '-I', '-S', '-X', 'utf8',
        str(Path(__file__).with_name('tool_failure_probe.py')),
        str(native_environment.PACKAGE), str(dependencies), str(tmp_path)],
        text=True, capture_output=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'tool failure contracts passed' in result.stdout
