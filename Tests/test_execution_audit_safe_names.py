"""
Regression tests for safe_artifact_name and execution audit filename safety.

Reproduces the GitHub Actions upload-artifact failure caused by colons in
execution_audit filenames (e.g. "2026-03-10:main:growth_engine_v4.json").

GitHub upload-artifact rejects filenames containing: : < > | * ? \r \n
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import sys

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.execution_audit import (
    safe_artifact_name,
    write_early_halt_audit,
    write_executor_audit,
    write_planner_audit,
    _AUDIT_DIR,
)

# GitHub upload-artifact forbidden characters
_FORBIDDEN = set(':<>|*?\r\n\\')


def _is_artifact_safe(name: str) -> bool:
    return not any(ch in _FORBIDDEN for ch in name)


# ---------------------------------------------------------------------------
# Unit tests for safe_artifact_name
# ---------------------------------------------------------------------------

class TestSafeArtifactName:
    def test_colon_replaced(self):
        result = safe_artifact_name("2026-03-10:main:growth_engine_v4")
        assert ":" not in result
        assert result == "2026-03-10_main_growth_engine_v4"

    def test_slash_replaced(self):
        result = safe_artifact_name("AAPL/BUY")
        assert "/" not in result
        assert result == "AAPL_BUY"

    def test_backslash_replaced(self):
        result = safe_artifact_name("some\\path")
        assert "\\" not in result

    def test_question_mark_replaced(self):
        result = safe_artifact_name("AAPL/BUY?")
        assert "?" not in result

    def test_asterisk_replaced(self):
        result = safe_artifact_name("run_*_all")
        assert "*" not in result
        assert result == "run_all"

    def test_pipe_replaced(self):
        result = safe_artifact_name("a|b")
        assert "|" not in result

    def test_angle_brackets_replaced(self):
        result = safe_artifact_name("a<b>c")
        assert "<" not in result
        assert ">" not in result

    def test_repeated_separators_collapsed(self):
        result = safe_artifact_name("a::b:::c")
        # No consecutive underscores
        assert "__" not in result
        assert result == "a_b_c"

    def test_empty_string_returns_unnamed(self):
        assert safe_artifact_name("") == "unnamed"

    def test_whitespace_only_returns_unnamed(self):
        assert safe_artifact_name("   ") == "unnamed"

    def test_normal_run_id_unchanged(self):
        # Standard run_id format must pass through unchanged
        val = "20260310T093500Z_paper_1"
        assert safe_artifact_name(val) == val

    def test_date_with_dashes_preserved(self):
        val = "2026-03-10"
        assert safe_artifact_name(val) == val

    def test_dots_preserved(self):
        result = safe_artifact_name("v1.2.3")
        assert result == "v1.2.3"

    def test_all_forbidden_chars_safe(self):
        val = "2026-03-10:main:growth_engine_v4"
        result = safe_artifact_name(val)
        assert _is_artifact_safe(result), f"Unsafe chars in: {result!r}"

    def test_exact_failure_case_from_actions(self):
        """Exact filename pattern that caused the GitHub Actions upload failure."""
        raw = "2026-03-10:main:growth_engine_v4"
        result = safe_artifact_name(raw)
        assert result == "2026-03-10_main_growth_engine_v4"
        assert _is_artifact_safe(result)


# ---------------------------------------------------------------------------
# Integration: audit write functions produce safe filenames
# ---------------------------------------------------------------------------

class TestAuditWriteSafeFilenames:
    """Verifies that every audit write path uses sanitized filenames."""

    def _run_with_audit_dir(self, tmpdir):
        """Patch _AUDIT_DIR to a temp directory for isolation."""
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = Path(tmpdir) / "execution_audit"
        return original

    def _restore_audit_dir(self, original):
        import core.execution_audit as ea
        ea._AUDIT_DIR = original

    def test_early_halt_audit_filename_is_safe(self, tmp_path):
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = tmp_path / "execution_audit"
        try:
            write_early_halt_audit(
                run_id="2026-03-10:main:growth_engine_v4",
                trade_date="2026-03-10",
                mode="ALPACA",
                halt_reason="test",
                stage="executor",
            )
            files = list((tmp_path / "execution_audit").iterdir())
            assert len(files) == 1
            name = files[0].name
            assert _is_artifact_safe(name), f"Unsafe filename: {name!r}"
            assert ":" not in name
        finally:
            ea._AUDIT_DIR = original

    def test_early_halt_preserves_raw_run_id_in_json(self, tmp_path):
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = tmp_path / "execution_audit"
        raw_run_id = "2026-03-10:main:growth_engine_v4"
        try:
            write_early_halt_audit(
                run_id=raw_run_id,
                trade_date="2026-03-10",
                mode="ALPACA",
                halt_reason="test",
                stage="executor",
            )
            files = list((tmp_path / "execution_audit").iterdir())
            data = json.loads(files[0].read_text(encoding="utf-8"))
            # Raw run_id must be inside the JSON payload
            assert data["run_id"] == raw_run_id
        finally:
            ea._AUDIT_DIR = original

    def test_executor_audit_filename_is_safe(self, tmp_path):
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = tmp_path / "execution_audit"
        try:
            write_executor_audit(
                run_id="2026-03-10:main:growth_engine_v4",
                pretrade_status="READY",
                halt_reason=None,
                submitted_count=1,
                accepted_count=1,
                rejected_count=0,
                execution_status="EXECUTED",
            )
            files = list((tmp_path / "execution_audit").iterdir())
            assert len(files) == 1
            name = files[0].name
            assert _is_artifact_safe(name), f"Unsafe filename: {name!r}"
        finally:
            ea._AUDIT_DIR = original

    def test_standard_run_id_filename_unchanged(self, tmp_path):
        """Normal run_id (no forbidden chars) must produce identical filename."""
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = tmp_path / "execution_audit"
        run_id = "20260310T093500Z_paper_1"
        try:
            write_early_halt_audit(
                run_id=run_id,
                trade_date="2026-03-10",
                mode="ALPACA",
                halt_reason="test",
                stage="executor",
            )
            files = list((tmp_path / "execution_audit").iterdir())
            assert files[0].name == f"{run_id}.json"
        finally:
            ea._AUDIT_DIR = original

    def test_executor_audit_marks_halted_run_with_submissions_as_attempted(self, tmp_path):
        import core.execution_audit as ea
        original = ea._AUDIT_DIR
        ea._AUDIT_DIR = tmp_path / "execution_audit"
        try:
            path = write_executor_audit(
                run_id="20260310T093500Z_paper_1",
                pretrade_status="READY",
                halt_reason="ENGINE_RUN_ABORTED_BEFORE_PAYLOAD:BROKER_REJECT_PDT:test",
                submitted_count=2,
                accepted_count=2,
                rejected_count=1,
                execution_status="HALTED",
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            assert payload["verdict"]["execution_attempted"] is True
            assert payload["verdict"]["orders_submitted"] == 2
            assert payload["verdict"]["orders_accepted"] == 2
        finally:
            ea._AUDIT_DIR = original
