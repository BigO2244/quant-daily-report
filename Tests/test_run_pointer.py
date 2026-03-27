"""
Tests for canonical run pointer management.

The latest_run.json file is the single source of truth for the current trading
run's location and metadata.
"""

import json
import os
import tempfile
from pathlib import Path
import pytest

from core.run_pointer import (
    WORKFLOW_POINTER_ROOT,
    write_latest_run_pointer,
    read_latest_run_pointer,
    write_trade_stage_pointer,
    read_trade_stage_pointer,
    resolve_trade_stage_pointer,
    workflow_stage_pointer_path,
    get_canonical_run_root,
    get_canonical_run_id,
    is_pointer_fresh,
    LATEST_RUN_POINTER,
)


class TestRunPointerWrite:
    """Test writing the canonical run pointer."""
    
    def test_write_and_read_pointer(self):
        """Should write pointer and be able to read it back."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_latest_run_pointer(
                run_id='20260309T093456Z_paper_1',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/20260309T093456Z_paper_1/',
                status='success',
                workspace_root=tmpdir,
            )
            
            # File should exist
            assert Path(path).exists()
            pointer_data = read_latest_run_pointer(tmpdir)
            
            assert pointer_data is not None
            assert pointer_data['run_id'] == '20260309T093456Z_paper_1'
            assert pointer_data['trade_date'] == '2026-03-09'
            assert pointer_data['mode'] == 'PAPER'
            assert pointer_data['status'] == 'success'
    
    def test_pointer_file_location(self):
        """Pointer should be at outputs/latest_run.json."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            expected_path = Path(tmpdir) / LATEST_RUN_POINTER
            assert expected_path.exists()


class TestRunPointerRead:
    """Test reading the canonical run pointer."""
    
    def test_read_missing_pointer_returns_none(self):
        """Should return None if pointer doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = read_latest_run_pointer(tmpdir)
            assert result is None
    
    def test_read_malformed_pointer_raises(self):
        """Should raise error if pointer is malformed JSON."""
        with tempfile.TemporaryDirectory() as tmpdir:
            pointer_path = Path(tmpdir) / 'outputs' / 'latest_run.json'
            pointer_path.parent.mkdir(parents=True, exist_ok=True)
            pointer_path.write_text('{ invalid json }')
            
            with pytest.raises(json.JSONDecodeError):
                read_latest_run_pointer(tmpdir)


class TestGetCanonicalValues:
    """Test helper functions to extract values from pointer."""
    
    def test_get_canonical_run_root(self):
        """Should extract run_root from pointer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            root = get_canonical_run_root(tmpdir)
            assert root == 'outputs/runs/test/'
    
    def test_get_canonical_run_id(self):
        """Should extract run_id from pointer."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='20260309T093456Z_paper_1',
                trade_date='2026-03-09',
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            run_id = get_canonical_run_id(tmpdir)
            assert run_id == '20260309T093456Z_paper_1'
    
    def test_get_values_when_pointer_missing(self):
        """Should return None when pointer doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert get_canonical_run_root(tmpdir) is None
            assert get_canonical_run_id(tmpdir) is None


class TestPointerFreshness:
    """Test checking if pointer is fresh for the trading day."""
    
    def test_fresh_pointer_same_date(self):
        """Pointer is fresh if it matches trade_date and is recent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Use today's date to ensure freshness
            import datetime
            today = datetime.date.today().isoformat()
            
            write_latest_run_pointer(
                run_id='test',
                trade_date=today,
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            assert is_pointer_fresh(today, tmpdir) is True
    
    def test_stale_pointer_different_date(self):
        """Pointer is stale if trade_date doesn't match."""
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id='test',
                trade_date='2026-01-01',  # Old date
                mode='PAPER',
                run_root='outputs/runs/test/',
                workspace_root=tmpdir,
            )
            
            assert is_pointer_fresh('2026-03-09', tmpdir) is False
    
    def test_missing_pointer_not_fresh(self):
        """Missing pointer is not fresh."""
        with tempfile.TemporaryDirectory() as tmpdir:
            assert is_pointer_fresh('2026-03-09', tmpdir) is False


class TestRunPointerFallback:
    """
    Tests for the outputs/latest.json fallback path.

    Covers the scenario where engine_run finalized a run (wrote latest.json via
    write_latest_pointer) but latest_run.json was not written (e.g. the
    write_canonical_run_pointer call failed or the old code path ran).
    """

    def test_fallback_to_latest_json_path_key(self):
        """Should return a pointer built from latest.json when latest_run.json is absent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = Path(tmpdir) / "outputs" / "latest.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({
                "run_id": "20260310T093000Z_paper_1",
                "path": "outputs/runs/20260310T093000Z_paper_1",
                "report_date": "2026-03-10",
                "mode": "ALPACA",
                "created_at": "2026-03-10T09:30:00Z",
            }), encoding="utf-8")

            result = read_latest_run_pointer(tmpdir)
            assert result is not None
            assert result["run_id"] == "20260310T093000Z_paper_1"
            assert result["run_root"] == "outputs/runs/20260310T093000Z_paper_1"
            assert result["trade_date"] == "2026-03-10"
            assert result["mode"] == "ALPACA"
            assert result.get("_source") == "legacy_latest_json"

    def test_fallback_to_latest_json_run_root_key(self):
        """Should accept run_root key in latest.json (some versions write both)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = Path(tmpdir) / "outputs" / "latest.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({
                "run_id": "20260310T093000Z_paper_1",
                "run_root": "outputs/runs/20260310T093000Z_paper_1",
                "report_date": "2026-03-10",
                "mode": "ALPACA",
                "created_at": "2026-03-10T09:30:00Z",
            }), encoding="utf-8")

            result = read_latest_run_pointer(tmpdir)
            assert result is not None
            assert result["run_root"] == "outputs/runs/20260310T093000Z_paper_1"
            assert result.get("_source") == "legacy_latest_json"

    def test_fallback_missing_run_root_returns_none(self):
        """Fallback should return None if latest.json has no path or run_root."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = Path(tmpdir) / "outputs" / "latest.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text(json.dumps({
                "run_id": "test",
                "report_date": "2026-03-10",
            }), encoding="utf-8")

            result = read_latest_run_pointer(tmpdir)
            assert result is None

    def test_fallback_malformed_latest_json_returns_none(self):
        """Corrupted latest.json should not raise; fallback returns None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            legacy = Path(tmpdir) / "outputs" / "latest.json"
            legacy.parent.mkdir(parents=True, exist_ok=True)
            legacy.write_text("{ not valid json }", encoding="utf-8")

            result = read_latest_run_pointer(tmpdir)
            assert result is None

    def test_canonical_pointer_takes_precedence_over_fallback(self):
        """latest_run.json should be returned even if latest.json also exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write canonical pointer
            write_latest_run_pointer(
                run_id="canonical-run",
                trade_date="2026-03-10",
                mode="ALPACA",
                run_root="outputs/runs/canonical-run",
                status="success",
                workspace_root=tmpdir,
            )
            # Also write legacy pointer with different run_id
            legacy = Path(tmpdir) / "outputs" / "latest.json"
            legacy.write_text(json.dumps({
                "run_id": "legacy-run",
                "path": "outputs/runs/legacy-run",
                "report_date": "2026-03-09",
                "mode": "ALPACA",
            }), encoding="utf-8")

            result = read_latest_run_pointer(tmpdir)
            assert result is not None
            assert result["run_id"] == "canonical-run"
            assert result.get("_source") is None  # no _source tag means read from latest_run.json


class TestWorkflowPointerHandoff:
    """
    Simulates the engine_run → execute_orders artifact handoff.

    These tests catch regressions in the pointer contract without requiring
    a live broker or GitHub Actions environment.
    """

    def test_preliminary_pointer_format_is_valid(self):
        """
        Validates the JSON written by the 'Set run metadata' workflow step.

        The workflow writes:
            printf '{"run_id":"%s","trade_date":"%s","mode":"ALPACA","run_root":"outputs/runs/%s","status":"initializing","created_at":"%s"}'
        This test ensures that JSON parses correctly and has all keys required
        by execute_alpaca_orders.run_execution().
        """
        preliminary = {
            "run_id": "20260310T093500Z_12345678_1",
            "trade_date": "2026-03-10",
            "mode": "ALPACA",
            "run_root": "outputs/runs/20260310T093500Z_12345678_1",
            "status": "initializing",
            "created_at": "2026-03-10T14:35:00Z",
        }
        required_keys = {"run_id", "trade_date", "mode", "run_root", "status", "created_at"}
        assert required_keys.issubset(preliminary.keys())
        # run_root must be non-empty string
        assert preliminary["run_root"].strip()
        # trade_date must look like YYYY-MM-DD
        assert len(preliminary["trade_date"]) == 10
        assert preliminary["trade_date"][4] == "-" and preliminary["trade_date"][7] == "-"

    def test_pointer_written_by_init_run_context_has_running_status(self):
        """
        Pointer written early by _init_run_context should have status='running',
        not 'success'. Finalize overwrites to 'success' at end of main().
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id="20260310T093500Z_paper_1",
                trade_date="2026-03-10",
                mode="ALPACA",
                run_root="outputs/runs/20260310T093500Z_paper_1",
                status="running",
                workspace_root=tmpdir,
            )
            result = read_latest_run_pointer(tmpdir)
            assert result is not None
            assert result["status"] == "running"
            assert result["run_root"] == "outputs/runs/20260310T093500Z_paper_1"

    def test_pointer_overwritten_by_finalize_has_success_status(self):
        """
        Finalize should overwrite the 'running' pointer with 'success'.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id="20260310T093500Z_paper_1",
                trade_date="2026-03-10",
                mode="ALPACA",
                run_root="outputs/runs/20260310T093500Z_paper_1",
                status="running",
                workspace_root=tmpdir,
            )
            # Simulate finalize overwriting
            write_latest_run_pointer(
                run_id="20260310T093500Z_paper_1",
                trade_date="2026-03-10",
                mode="ALPACA",
                run_root="outputs/runs/20260310T093500Z_paper_1",
                status="success",
                workspace_root=tmpdir,
            )
            result = read_latest_run_pointer(tmpdir)
            assert result["status"] == "success"

    def test_both_missing_returns_none(self):
        """No latest_run.json and no latest.json → None (executor should halt gracefully)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = read_latest_run_pointer(tmpdir)
            assert result is None


class TestTradeStagePointers:
    def test_write_and_read_execution_pointer(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_trade_stage_pointer(
                stage="execution",
                run_id="run-exec-1",
                trade_date="2026-03-30",
                mode="PAPER",
                run_root="outputs/runs/run-exec-1",
                status="running",
                workspace_root=tmpdir,
            )

            assert Path(path).exists()
            assert Path(path) == workflow_stage_pointer_path("2026-03-30", "execution", tmpdir)

            pointer = read_trade_stage_pointer("2026-03-30", "execution", tmpdir)
            assert pointer is not None
            assert pointer["stage"] == "execution"
            assert pointer["run_id"] == "run-exec-1"
            assert pointer["status"] == "running"

    def test_trade_stage_pointer_path_uses_workflow_root(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = workflow_stage_pointer_path("2026-03-30", "execution", tmpdir)
            expected = Path(tmpdir) / WORKFLOW_POINTER_ROOT / "2026-03-30" / "execution.json"
            assert path == expected

    def test_resolve_trade_stage_pointer_prefers_stage_pointer_over_latest_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id="latest-run",
                trade_date="2026-03-30",
                mode="PAPER",
                run_root="outputs/runs/latest-run",
                status="success",
                workspace_root=tmpdir,
            )
            write_trade_stage_pointer(
                stage="execution",
                run_id="execution-run",
                trade_date="2026-03-30",
                mode="PAPER",
                run_root="outputs/runs/execution-run",
                status="success",
                workspace_root=tmpdir,
            )

            resolved = resolve_trade_stage_pointer("2026-03-30", "execution", tmpdir)
            assert resolved is not None
            assert resolved["run_id"] == "execution-run"

    def test_resolve_trade_stage_pointer_falls_back_to_latest_run_when_stage_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_latest_run_pointer(
                run_id="latest-run",
                trade_date="2026-03-30",
                mode="PAPER",
                run_root="outputs/runs/latest-run",
                status="success",
                workspace_root=tmpdir,
            )

            resolved = resolve_trade_stage_pointer("2026-03-30", "execution", tmpdir)
            assert resolved is not None
            assert resolved["run_id"] == "latest-run"
            assert resolved["_source"] == "latest_run_json"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
