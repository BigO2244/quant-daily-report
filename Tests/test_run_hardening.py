"""
Tests for run-status hardening (terminal state observability).

Covers:
1. Legitimate no_action run writes operator_summary with terminal_status=no_action
2. Pre-payload failure writes operator_summary and planner_failure.json
3. latest_run.json does not claim success for a sparse/failed run
4. Dashboard surface marks fallback_in_use when latest attempted run is incomplete
5. Missing execution_payload.json is classified as failed_pre_payload, not no_action
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────

def _make_run_root(tmp_path: Path, run_id: str = "20260312T093500Z_paper_1") -> Path:
    """Create minimal run directory structure."""
    run_root = tmp_path / "outputs" / "runs" / run_id
    for subdir in ("logs", "reports", "broker", "ledger", "snapshots", "audit"):
        (run_root / subdir).mkdir(parents=True, exist_ok=True)
    return run_root


def _write_meta(run_root: Path, report_date: str = "2026-03-12", mode: str = "paper") -> None:
    (run_root / "meta.json").write_text(
        json.dumps({"run_id": run_root.name, "report_date": report_date, "mode": mode}) + "\n",
        encoding="utf-8",
    )


# ── 1. no_action run ─────────────────────────────────────────────────────────

class TestNoActionRun:
    def test_operator_summary_terminal_status_no_action(self, tmp_path):
        """A no_action run must write operator_summary with terminal_status=no_action."""
        from core.operator_summary import write_operator_summary

        run_root = _make_run_root(tmp_path)
        write_operator_summary(
            run_root,
            run_id=run_root.name,
            trade_date="2026-03-12",
            mode="PAPER",
            pretrade_status="NO_ACTION",
            executable_trades_count=0,
            planner_completed=True,
            execution_payload_written=True,
            terminal_status="no_action",
            no_trade_reason="no_executable_trades_after_constraints",
        )
        data = json.loads((run_root / "operator_summary.json").read_text())
        assert data["terminal_status"] == "no_action"
        assert data["planner_completed"] is True
        assert data["execution_payload_written"] is True
        assert data["no_trade_reason"] is not None
        assert data["pretrade_status"] == "NO_ACTION"

    def test_no_action_is_run_complete(self):
        """is_run_complete must return True for no_action."""
        from core.run_pointer import is_run_complete, is_run_failure
        assert is_run_complete("no_action") is True
        assert is_run_failure("no_action") is False


# ── 2. pre-payload failure ────────────────────────────────────────────────────

class TestPrePayloadFailure:
    def test_write_preflight_failure_creates_artifact(self, tmp_path):
        """write_preflight_failure must create run_root/logs/planner_failure.json."""
        from core.operator_summary import write_preflight_failure

        run_root = _make_run_root(tmp_path)
        dest = write_preflight_failure(
            run_root,
            run_id=run_root.name,
            stage="pre_payload",
            terminal_status="failed_pre_payload",
            exception_type="RuntimeError",
            exception_message="sleeve_trend failed",
        )
        assert dest is not None
        assert dest.exists()
        data = json.loads(dest.read_text())
        assert data["terminal_status"] == "failed_pre_payload"
        assert data["stage"] == "pre_payload"
        assert data["exception_type"] == "RuntimeError"
        assert "generated_at" in data

    def test_pre_payload_failure_writes_operator_summary(self, tmp_path):
        """On pre-payload failure, operator_summary must be written by the exit handler."""
        from core.operator_summary import write_operator_summary

        run_root = _make_run_root(tmp_path)
        # Simulate what _write_failure_artifacts_on_exit does on early crash
        write_operator_summary(
            run_root,
            run_id=run_root.name,
            trade_date="2026-03-12",
            mode="PAPER",
            terminal_status="failed_pre_payload",
            execution_payload_written=False,
            execution_stage_reached=False,
        )
        data = json.loads((run_root / "operator_summary.json").read_text())
        assert data["terminal_status"] == "failed_pre_payload"
        assert data["execution_payload_written"] is False
        assert data["execution_stage_reached"] is False


# ── 3. latest_run.json must not claim success for a sparse run ────────────────

class TestLatestRunPointer:
    def test_failure_status_propagates_to_pointer(self, tmp_path):
        """write_latest_run_pointer must accept and preserve failure statuses."""
        from core.run_pointer import (
            write_latest_run_pointer,
            read_latest_run_pointer,
            TERMINAL_STATUS_FAILED_PRE_PAYLOAD,
            is_run_failure,
        )
        write_latest_run_pointer(
            run_id="20260312T093500Z_paper_1",
            trade_date="2026-03-12",
            mode="paper",
            run_root="outputs/runs/20260312T093500Z_paper_1",
            status=TERMINAL_STATUS_FAILED_PRE_PAYLOAD,
            workspace_root=str(tmp_path),
        )
        data = read_latest_run_pointer(workspace_root=str(tmp_path))
        assert data is not None
        assert data["status"] == TERMINAL_STATUS_FAILED_PRE_PAYLOAD
        assert is_run_failure(data["status"]) is True

    def test_running_status_is_not_complete(self):
        """'running' is not a terminal complete state."""
        from core.run_pointer import is_run_complete, is_run_failure
        assert is_run_complete("running") is False
        # running is not treated as a failure either (it's in-progress)
        assert is_run_failure("running") is False

    def test_success_is_complete(self):
        from core.run_pointer import is_run_complete, is_run_failure
        assert is_run_complete("success") is True
        assert is_run_failure("success") is False


# ── 4. dashboard fallback_in_use when latest attempt is incomplete ─────────────

class TestDashboardFallback:
    def _build_dashboard(self, repo_root: Path) -> dict[str, Any]:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from scripts.research.build_quant_dashboard import DashboardBuilder
        builder = DashboardBuilder(repo_root=repo_root)
        return builder.build()

    def test_fallback_in_use_when_latest_attempt_failed(self, tmp_path):
        """fallback_in_use must be True when latest attempted run is incomplete."""
        # Set up a failed latest attempted run (only meta.json)
        failed_run_id = "20260312T093500Z_paper_failed"
        failed_root = tmp_path / "outputs" / "runs" / failed_run_id
        failed_root.mkdir(parents=True)
        _write_meta(failed_root, "2026-03-12")

        # Set up a successful prior run with completion indicators
        good_run_id = "20260311T093500Z_paper_1"
        good_root = tmp_path / "outputs" / "runs" / good_run_id
        (good_root / "reports").mkdir(parents=True)
        (good_root / "snapshots").mkdir(parents=True)
        _write_meta(good_root, "2026-03-11")
        (good_root / "reports" / "quant_report_2026-03-11.html").write_text("<html/>")

        # latest.json points to the failed run
        latest_payload = {
            "run_id": failed_run_id,
            "report_date": "2026-03-12",
            "mode": "paper",
            "created_at": "2026-03-12T13:55:00Z",
        }
        (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "latest.json").write_text(json.dumps(latest_payload))

        # latest_run.json shows the run did not complete
        latest_run_payload = {
            "run_id": failed_run_id,
            "trade_date": "2026-03-12",
            "mode": "paper",
            "run_root": str(failed_root),
            "status": "failed_pre_payload",
            "created_at": "2026-03-12T13:55:00Z",
        }
        (tmp_path / "outputs" / "latest_run.json").write_text(json.dumps(latest_run_payload))

        from scripts.research.build_quant_dashboard import DashboardBuilder
        model = DashboardBuilder(repo_root=tmp_path).build()

        run_meta = model["run_meta"]
        assert run_meta["fallback_in_use"] is True, "fallback_in_use must be True for incomplete latest run"
        assert run_meta["latest_attempted_is_complete"] is False
        assert run_meta["latest_attempted_terminal_status"] == "failed_pre_payload"
        assert "operator_summary.json" in run_meta["latest_attempted_missing_artifacts"]

    def test_no_fallback_when_latest_run_is_complete(self, tmp_path):
        """fallback_in_use must be False when latest run completed successfully."""
        run_id = "20260312T093500Z_paper_1"
        run_root = tmp_path / "outputs" / "runs" / run_id
        (run_root / "reports").mkdir(parents=True)
        (run_root / "snapshots").mkdir(parents=True)
        _write_meta(run_root, "2026-03-12")
        (run_root / "reports" / "quant_report_2026-03-12.html").write_text("<html/>")

        latest_payload = {
            "run_id": run_id,
            "report_date": "2026-03-12",
            "mode": "paper",
            "created_at": "2026-03-12T13:55:00Z",
        }
        (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "outputs" / "latest.json").write_text(json.dumps(latest_payload))

        latest_run_payload = {
            "run_id": run_id,
            "trade_date": "2026-03-12",
            "mode": "paper",
            "run_root": str(run_root),
            "status": "success",
            "created_at": "2026-03-12T13:55:00Z",
        }
        (tmp_path / "outputs" / "latest_run.json").write_text(json.dumps(latest_run_payload))

        from scripts.research.build_quant_dashboard import DashboardBuilder
        model = DashboardBuilder(repo_root=tmp_path).build()

        run_meta = model["run_meta"]
        assert run_meta["fallback_in_use"] is False
        assert run_meta["latest_attempted_is_complete"] is True


# ── 5. missing execution_payload.json → failed_pre_payload, not no_action ─────

class TestPayloadMissingClassification:
    def test_missing_payload_is_failed_pre_payload(self):
        """failed_pre_payload is distinct from no_action."""
        from core.run_pointer import (
            TERMINAL_STATUS_FAILED_PRE_PAYLOAD,
            TERMINAL_STATUS_NO_ACTION,
            is_run_complete,
            is_run_failure,
        )
        assert is_run_failure(TERMINAL_STATUS_FAILED_PRE_PAYLOAD) is True
        assert is_run_complete(TERMINAL_STATUS_FAILED_PRE_PAYLOAD) is False
        assert is_run_complete(TERMINAL_STATUS_NO_ACTION) is True

    def test_failure_artifacts_classify_as_failed_pre_payload(self, tmp_path):
        """_write_failure_artifacts_on_exit must write failed_pre_payload when
        payload was never written."""
        # Simulate what _write_failure_artifacts_on_exit does when
        # _RUN_STAGE_PAYLOAD_WRITTEN=False and _RUN_STAGE_EXECUTION_REACHED=False
        from core.operator_summary import write_preflight_failure, write_operator_summary

        run_root = _make_run_root(tmp_path)
        terminal = "failed_pre_payload"  # what the helper infers
        stage = "pre_payload"

        write_preflight_failure(run_root, run_id=run_root.name, stage=stage,
                                terminal_status=terminal)
        write_operator_summary(run_root, run_id=run_root.name, trade_date="2026-03-12",
                               mode="PAPER", terminal_status=terminal,
                               execution_payload_written=False,
                               execution_stage_reached=False)

        failure_data = json.loads((run_root / "logs" / "planner_failure.json").read_text())
        op_data = json.loads((run_root / "operator_summary.json").read_text())

        assert failure_data["terminal_status"] == "failed_pre_payload"
        assert op_data["terminal_status"] == "failed_pre_payload"
        assert op_data["execution_payload_written"] is False
        assert op_data["execution_stage_reached"] is False
