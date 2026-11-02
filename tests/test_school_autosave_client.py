"""Run dependency-free JS behavior tests as part of the existing pytest contract."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_autosave_queue_behavior():
    node=shutil.which('node')
    if not node: pytest.skip('Node.js not installed; browser/queue verification still required for UI changes')
    root=Path(__file__).resolve().parents[1]
    result=subprocess.run([node,'--test',str(root/'tests/school_autosave.test.cjs')],cwd=root,
                          capture_output=True,text=True,timeout=30)
    assert result.returncode==0, result.stdout+result.stderr
