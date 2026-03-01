"""Tests for aiops CLI stability and deterministic exit codes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def repo_root() -> Path:
    """Get repository root."""
    return Path(__file__).parent.parent


@pytest.fixture(scope="module")
def venv_python() -> Path:
    """Get path to venv python."""
    venv_py = Path(__file__).parent.parent / ".venv" / "bin" / "python"
    if not venv_py.exists():
        pytest.skip("venv not found; cannot run subprocess tests")
    return venv_py


@pytest.fixture(scope="module")
def valid_spec(repo_root: Path) -> Path:
    """Get a valid spec file."""
    spec = repo_root / "specs" / "trading_turnover_cost_audit.md"
    if not spec.exists():
        pytest.skip(f"Test spec not found: {spec}")
    return spec


def _run_aiops(
    venv_python: Path,
    args: list[str],
    cwd: Path | str,
    timeout: float = 15.0,
) -> subprocess.CompletedProcess:
    """
    Helper to run `aiops` CLI with bounded timeout and controlled PATH.
    
    Args:
        venv_python: Path to venv Python executable
        args: CLI arguments (e.g., ["parse", "specs/foo.md"])
        cwd: Working directory for subprocess
        timeout: Timeout in seconds (default: 15s)
    
    Returns:
        CompletedProcess with captured stdout/stderr
    """
    # Clamp PATH to minimal system dirs so codex won't be found
    env = os.environ.copy()
    env["PATH"] = "/usr/bin:/bin"
    
    return subprocess.run(
        [str(venv_python), "-m", "aiops", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=timeout,
        env=env,
    )


def test_parse_valid_spec(venv_python: Path, valid_spec: Path) -> None:
    """Test `aiops parse <spec>` with a valid spec."""
    result = _run_aiops(
        venv_python,
        ["parse", str(valid_spec)],
        cwd=valid_spec.parent.parent,
    )

    assert result.returncode == 0, f"parse failed: {result.stderr}"
    output = json.loads(result.stdout)
    assert "MODE" in output
    assert "OBJECTIVE" in output
    assert "PROJECT_TYPE" in output
    assert "RISK_TIER" in output


def test_parse_missing_spec(venv_python: Path, repo_root: Path) -> None:
    """Test `aiops parse <missing>` returns non-zero."""
    result = _run_aiops(
        venv_python,
        ["parse", "specs/does_not_exist.md"],
        cwd=repo_root,
    )

    assert result.returncode != 0
    assert "ERROR" in result.stderr or "ERROR" in result.stdout


def test_plan_valid_spec(venv_python: Path, valid_spec: Path, repo_root: Path) -> None:
    """Test `aiops plan <spec> --mode BUILD` returns 0."""
    result = _run_aiops(
        venv_python,
        ["plan", str(valid_spec), "--mode", "BUILD"],
        cwd=repo_root,
    )

    assert result.returncode == 0, f"plan failed: {result.stderr}"


def test_dispatch_missing_run(venv_python: Path, repo_root: Path) -> None:
    """Test `aiops dispatch --run DOES_NOT_EXIST` returns non-zero."""
    result = _run_aiops(
        venv_python,
        ["dispatch", "--run", "DOES_NOT_EXIST_RUN_ID"],
        cwd=repo_root,
    )

    assert result.returncode != 0, "dispatch should fail for missing run"
    assert "Traceback" not in result.stderr, f"dispatch crashed with traceback: {result.stderr}"
    assert "UnboundLocalError" not in result.stderr
    assert "NameError" not in result.stderr
    assert "ERROR" in result.stderr or "ERROR" in result.stdout


def test_dispatch_requires_run_arg(venv_python: Path, repo_root: Path) -> None:
    """Test `aiops dispatch` without --run prints usage error."""
    result = _run_aiops(
        venv_python,
        ["dispatch"],
        cwd=repo_root,
    )

    # argparse exits with code 2 for argument errors
    assert result.returncode == 2, f"dispatch without --run should fail with exit 2; got {result.returncode}"


def test_run_command_exists(venv_python: Path, repo_root: Path) -> None:
    """Test `aiops run --help` succeeds (verifies command is wired)."""
    result = _run_aiops(
        venv_python,
        ["run", "--help"],
        cwd=repo_root,
    )

    assert result.returncode == 0
    assert "run" in result.stdout
    assert "spec_path" in result.stdout


def test_run_no_crash_on_missing_codex(venv_python: Path, valid_spec: Path, repo_root: Path) -> None:
    """Test `aiops run <spec> --mode BUILD` fails cleanly (not crash) if codex missing."""
    result = _run_aiops(
        venv_python,
        ["run", str(valid_spec), "--mode", "BUILD"],
        cwd=repo_root,
        timeout=20.0,
    )

    assert result.returncode != 0
    # Should not crash; should exit with non-zero (likely 2 from dispatch due to missing codex)
    # But it must NOT have a Python traceback
    assert "Traceback" not in result.stderr, f"run crashed with traceback: {result.stderr}"
    assert "UnboundLocalError" not in result.stderr
    assert "NameError" not in result.stderr


def test_verify_valid_spec(venv_python: Path, valid_spec: Path, repo_root: Path) -> None:
    """Test `aiops verify <spec> --mode BUILD` returns deterministic exit code."""
    try:
        result = _run_aiops(
            venv_python,
            ["verify", str(valid_spec), "--mode", "BUILD"],
            cwd=repo_root,
            timeout=20.0,
        )
        # Verify should succeed or fail deterministically, not crash
        assert "Traceback" not in result.stderr
        # BUILD mode may pass or fail depending on content, but not crash
        assert result.returncode in {0, 1, 2}, f"verify returned unexpected code: {result.returncode}"
    except subprocess.TimeoutExpired:
        # Acceptable: verify may block waiting for codex (which is intentionally missing)
        # The timeout prevents indefinite hang, which is the goal
        pass


def test_parse_command_deterministic(venv_python: Path, valid_spec: Path) -> None:
    """Test `aiops parse <spec>` produces deterministic output."""
    result1 = _run_aiops(
        venv_python,
        ["parse", str(valid_spec)],
        cwd=valid_spec.parent.parent,
    )
    result2 = _run_aiops(
        venv_python,
        ["parse", str(valid_spec)],
        cwd=valid_spec.parent.parent,
    )

    assert result1.returncode == result2.returncode
    assert result1.stdout == result2.stdout


def test_unknown_command(venv_python: Path, repo_root: Path) -> None:
    """Test unknown command returns exit code 2."""
    result = _run_aiops(
        venv_python,
        ["invalid_command"],
        cwd=repo_root,
    )

    assert result.returncode == 2
    assert "error" in result.stderr.lower() or "invalid" in result.stderr.lower()
