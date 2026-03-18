from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_module(module_name: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module_name, "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_workflow_time_guard_module_entrypoint_imports_cleanly() -> None:
    result = _run_module("scripts.workflow_time_guard")
    assert result.returncode == 0


def test_precompute_bundle_status_module_entrypoint_imports_cleanly() -> None:
    result = _run_module("scripts.precompute_bundle_status")
    assert result.returncode == 0


def test_fetch_precompute_artifact_module_entrypoint_imports_cleanly() -> None:
    result = _run_module("scripts.fetch_precompute_artifact")
    assert result.returncode == 0
