from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.rebuild_execution_timeline import rebuild_execution_timeline


REPO_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_minimal_run(root: Path, run_id: str = "run-123") -> Path:
    run_root = root / "outputs" / "runs" / run_id
    _write_json(
        run_root / "operator_summary.json",
        {
            "run_id": run_id,
            "trade_date": "2026-05-28",
            "terminal_status": "success",
            "operator_execution_status": "executed",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": run_id,
            "trade_date": "2026-05-28",
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
            "submitted_count": 1,
            "submitted_buy_count": 1,
        },
    )
    return run_root


def test_successful_write_for_minimal_fixture(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _write_minimal_run(tmp_path)

    payload = rebuild_execution_timeline(repo_root=tmp_path, run_id="run-123")

    assert payload["status"] == "OK"
    assert payload["written"] is True
    assert (run_root / "execution_timeline.json").exists()
    assert (run_root / "execution_timeline.md").exists()
    timeline = json.loads((run_root / "execution_timeline.json").read_text(encoding="utf-8"))
    assert timeline["provenance"]["execution_source"] == "planned_payload_exact"


def test_timeline_surfaces_cash_gate_diagnostics(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _write_minimal_run(tmp_path, "run-cash-gate")
    diagnostics = {
        "schema_version": "cash_gate_diagnostics.v1",
        "buying_power_before_sells": 0.0,
        "estimated_sell_proceeds_submitted": 100.0,
        "buying_power_after_sell_submission": 0.0,
        "buying_power_after_posttrade_repoll": 950.0,
        "buy_notional_submitted_immediate": 0.0,
        "buy_notional_skipped_or_deferred_due_to_cash": 900.0,
        "raw_cash_gate_triggered": True,
        "raw_cash_gate_reason": "buy_blocked_pending_sells_required_for_cash",
        "temporary_cash_shortfall_later_resolved": True,
        "raw_cash_gate_despite_final_reconciled_success": True,
        "sell_phase_poll_observations": [
            {
                "poll": 1,
                "account_buying_power": 0.0,
                "orders": [{"order_id": "run:AAA:SELL", "status": "ACCEPTED"}],
            }
        ],
        "posttrade_sell_fill_observations": [
            {
                "order_id": "run:AAA:SELL",
                "status": "FILLED",
                "seconds_to_fill": 4.0,
            }
        ],
    }
    payload = json.loads((run_root / "execution_payload.json").read_text(encoding="utf-8"))
    payload.update(
        {
            "submitted_sell_count": 1,
            "submitted_buy_count": 0,
            "sell_phase_status": "TIMEOUT",
            "buy_phase_block_reason": "buy_blocked_pending_sells_required_for_cash",
            "cash_gate_diagnostics": diagnostics,
        }
    )
    _write_json(run_root / "execution_payload.json", payload)

    result = rebuild_execution_timeline(repo_root=tmp_path, run_id="run-cash-gate")

    assert result["status"] == "OK"
    timeline = json.loads((run_root / "execution_timeline.json").read_text(encoding="utf-8"))
    assert timeline["cash_gate_diagnostics"]["raw_cash_gate_triggered"] is True
    buy_completion = next(
        event for event in timeline["events"] if event["checkpoint"] == "buy_phase_completion"
    )
    assert (
        buy_completion["details"]["cash_gate_diagnostics"][
            "buy_notional_skipped_or_deferred_due_to_cash"
        ]
        == 900.0
    )
    reconciliation = next(
        event for event in timeline["events"] if event["checkpoint"] == "reconciliation_state"
    )
    assert (
        reconciliation["details"]["cash_gate_diagnostics"][
            "temporary_cash_shortfall_later_resolved"
        ]
        is True
    )


def test_latest_resolution_from_latest_run_json(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _write_minimal_run(tmp_path, "run-latest")
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-latest",
            "trade_date": "2026-05-28",
            "run_root": str(run_root),
            "status": "success",
        },
    )

    payload = rebuild_execution_timeline(repo_root=tmp_path, latest=True)

    assert payload["status"] == "OK"
    assert payload["run_id"] == "run-latest"
    assert (run_root / "execution_timeline.json").exists()


def test_missing_latest_run_json(tmp_path: Path) -> None:
    payload = rebuild_execution_timeline(repo_root=tmp_path, latest=True)

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["reason"] == "latest_run_missing"
    assert payload["written"] is False


def test_missing_run_directory(tmp_path: Path) -> None:
    payload = rebuild_execution_timeline(repo_root=tmp_path, run_id="missing-run")

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["reason"] == "run_directory_missing"
    assert payload["written"] is False


def test_missing_required_artifacts(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-incomplete"
    run_root.mkdir(parents=True)

    payload = rebuild_execution_timeline(repo_root=tmp_path, run_id="run-incomplete")

    assert payload["status"] == "NEEDS_OPERATOR"
    assert payload["reason"] == "required_source_artifacts_missing"
    assert sorted(payload["missing_required_artifacts"]) == [
        "execution_payload.json",
        "operator_summary.json",
    ]


def test_no_overwrite_without_force(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _write_minimal_run(tmp_path)
    (run_root / "execution_timeline.json").write_text("existing\n", encoding="utf-8")

    payload = rebuild_execution_timeline(repo_root=tmp_path, run_id="run-123")

    assert payload["status"] == "REFUSED"
    assert payload["reason"] == "timeline_exists"
    assert (run_root / "execution_timeline.json").read_text(encoding="utf-8") == "existing\n"


def test_force_overwrites_existing_timeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = _write_minimal_run(tmp_path)
    (run_root / "execution_timeline.json").write_text("existing\n", encoding="utf-8")

    payload = rebuild_execution_timeline(repo_root=tmp_path, run_id="run-123", force=True)

    assert payload["status"] == "OK"
    assert payload["forced"] is True
    assert "execution_lifecycle_timeline.v1" in (run_root / "execution_timeline.json").read_text(encoding="utf-8")


def test_direct_script_invocation_from_repo_root(tmp_path: Path) -> None:
    _write_minimal_run(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rebuild_execution_timeline.py",
            "--repo-root",
            str(tmp_path),
            "--run-id",
            "run-123",
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "OK"
