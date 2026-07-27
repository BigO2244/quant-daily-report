"""Hermetic tests for LIVE_PILOT confirmation discovery + dedupe.

Pins the fix for the 2026-07-10 confirm-timing race where a real armed submit
went unreported because the confirm cron grabbed the last-sorted (DRY) run dir.
"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.live_pilot_confirm_discover import (
    append_sent_ledger,
    discover_pending,
    is_terminal,
    read_sent_ledger,
)


def _make_run(runs_root: Path, run_id: str, status: str | None) -> Path:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if status is not None:
        (run_dir / "execution_results.json").write_text(
            json.dumps({"run_id": run_id, "status": status}), encoding="utf-8"
        )
    return run_dir


def test_two_runs_same_date_both_pending_then_each_confirmed_once(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "state" / "ledger.jsonl"
    dry = "2026-07-10T093604-0400_live_pilot_cron_dry"
    submit = "2026-07-10T100930-0400_live_pilot_cron_submit"
    _make_run(runs, dry, "DRY_RUN_NO_SUBMISSION")
    _make_run(runs, submit, "FAILED_RECONCILIATION")

    result = discover_pending("2026-07-10", runs, ledger)
    assert result["has_any_run"] is True
    assert result["terminal_count"] == 2
    assert result["pending_count"] == 2
    ids = {r["run_id"] for r in result["pending"]}
    assert ids == {dry, submit}
    # Name-sort == chronological: dry (09:36) precedes submit (10:09).
    assert result["pending"][0]["run_id"] == dry
    assert result["pending"][1]["run_id"] == submit

    # Confirm the dry run -> only the submit remains pending (the exact 07-10 gap).
    append_sent_ledger(ledger, run_id=dry, run_root=str(runs / dry),
                       trade_date="2026-07-10", status="DRY_RUN_NO_SUBMISSION")
    result2 = discover_pending("2026-07-10", runs, ledger)
    assert result2["pending_count"] == 1
    assert result2["pending"][0]["run_id"] == submit

    # Confirm the submit -> nothing pending; both confirmed exactly once.
    append_sent_ledger(ledger, run_id=submit, run_root=str(runs / submit),
                       trade_date="2026-07-10", status="FAILED_RECONCILIATION")
    result3 = discover_pending("2026-07-10", runs, ledger)
    assert result3["pending_count"] == 0
    assert result3["already_sent_count"] == 2


def test_no_runs_reports_has_any_false_for_alert_path(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir(parents=True)
    ledger = tmp_path / "ledger.jsonl"
    result = discover_pending("2026-07-11", runs, ledger)
    assert result["has_any_run"] is False
    assert result["terminal_count"] == 0
    assert result["pending_count"] == 0


def test_already_confirmed_run_not_resent(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    run_id = "2026-07-12T093604-0400_live_pilot_cron_dry"
    _make_run(runs, run_id, "DRY_RUN_NO_SUBMISSION")
    append_sent_ledger(ledger, run_id=run_id, run_root=str(runs / run_id),
                       trade_date="2026-07-12", status="DRY_RUN_NO_SUBMISSION")
    result = discover_pending("2026-07-12", runs, ledger)
    assert result["pending_count"] == 0
    assert result["already_sent_count"] == 1


def test_append_ledger_is_idempotent(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    assert append_sent_ledger(ledger, run_id="r1", run_root="x", trade_date="d", status="s") is True
    assert append_sent_ledger(ledger, run_id="r1", run_root="x", trade_date="d", status="s") is False
    assert read_sent_ledger(ledger) == {"r1"}
    # Only one physical line written despite two calls.
    assert len([ln for ln in ledger.read_text().splitlines() if ln.strip()]) == 1


def test_confirmation_receipt_records_post_refresh_truth_and_hash(tmp_path: Path) -> None:
    ledger = tmp_path / "ledger.jsonl"
    append_sent_ledger(
        ledger,
        run_id="r-final",
        run_root="outputs/live_pilot/runs/r-final",
        trade_date="2026-07-27",
        status="SUBMITTED",
        discovered_status="SUBMITTED_UNFILLED",
        reconciliation_status="CLEAN",
        display_status="EXECUTED",
        results_sha256="abc123",
    )

    receipt = json.loads(ledger.read_text(encoding="utf-8"))
    assert receipt["schema_version"] == "live_pilot_confirmation_receipt.v1"
    assert receipt["discovered_status"] == "SUBMITTED_UNFILLED"
    assert receipt["status_at_send"] == "SUBMITTED"
    assert receipt["reconciliation_status"] == "CLEAN"
    assert receipt["display_status"] == "EXECUTED"
    assert receipt["results_sha256"] == "abc123"


def test_running_run_is_not_terminal_and_not_pending(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    _make_run(runs, "2026-07-13T093604-0400_live_pilot_cron_submit", "running")
    # A run dir with no execution_results.json at all is also not terminal.
    _make_run(runs, "2026-07-13T090000-0400_live_pilot_cron_dry", None)
    result = discover_pending("2026-07-13", runs, ledger)
    assert result["terminal_count"] == 0
    assert result["pending_count"] == 0
    # There IS a run dir with results (the running one), so has_any_run is True.
    assert result["has_any_run"] is True


def test_is_terminal_classification() -> None:
    assert is_terminal({"status": "DRY_RUN_NO_SUBMISSION"}) is True
    assert is_terminal({"status": "FAILED_RECONCILIATION"}) is True
    assert is_terminal({"status": "CLEAN"}) is True
    assert is_terminal({"status": "SUBMITTED"}) is True
    assert is_terminal({"status": "BLOCKED"}) is True
    assert is_terminal({"status": "running"}) is False
    assert is_terminal({"status": ""}) is False
    assert is_terminal({}) is False
    assert is_terminal(None) is False


def test_other_date_runs_are_ignored(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    _make_run(runs, "2026-07-09T093604-0400_live_pilot_cron_submit", "FAILED_RECONCILIATION")
    _make_run(runs, "2026-07-10T093604-0400_live_pilot_cron_dry", "DRY_RUN_NO_SUBMISSION")
    result = discover_pending("2026-07-10", runs, ledger)
    assert result["terminal_count"] == 1
    assert result["pending"][0]["run_id"].startswith("2026-07-10T")


def test_later_blocked_pointer_cannot_be_masked_by_dry_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    dry = "2026-07-22T093606-0400_live_pilot_cron_dry"
    gate = "2026-07-22T093601-0400_live_pilot_cron_gate"
    _make_run(runs, dry, "DRY_RUN_NO_SUBMISSION")
    pointer = tmp_path / "workflow" / "2026-07-22" / "live_pilot_execution.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps(
            {
                "run_id": gate,
                "run_root": str(runs / gate),
                "status": "blocked",
                "status_message": "live_pilot_deploy_sha_drift",
            }
        ),
        encoding="utf-8",
    )

    result = discover_pending("2026-07-22", runs, ledger)
    assert result["terminal_count"] == 1
    assert result["pending"][0]["run_id"] == dry
    assert result["unconfirmable_count"] == 1
    assert result["unconfirmable"][0]["run_id"] == gate


def test_blocked_pointer_is_confirmable_when_gate_result_exists(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    ledger = tmp_path / "ledger.jsonl"
    gate = "2026-07-22T093601-0400_live_pilot_cron_gate"
    _make_run(runs, gate, "HALTED")
    pointer = tmp_path / "workflow" / "2026-07-22" / "live_pilot_execution.json"
    pointer.parent.mkdir(parents=True)
    pointer.write_text(
        json.dumps({"run_id": gate, "run_root": str(runs / gate), "status": "blocked"}),
        encoding="utf-8",
    )
    result = discover_pending("2026-07-22", runs, ledger)
    assert result["unconfirmable_count"] == 0
    assert result["pending"][0]["run_id"] == gate
