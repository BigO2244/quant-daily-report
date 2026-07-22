from __future__ import annotations

import json
from pathlib import Path

import core.dynamic_daily_email as dynamic_email
import scripts.send_trading_confirmation_email as confirmation
from scripts.live_pilot_write_gate_state import main as write_gate_state_main


def _write_blocked_gate(tmp_path: Path) -> tuple[Path, dict]:
    output_root = tmp_path / "outputs" / "live_pilot"
    run_id = "2026-07-22T20260722T093601-0400_live_pilot_cron_gate"
    rc = write_gate_state_main(
        [
            "--run-id",
            run_id,
            "--trade-date",
            "2026-07-22",
            "--output-root",
            str(output_root),
            "--decision",
            "BLOCKED",
            "--block-reason",
            "live_pilot_deploy_sha_drift",
            "--running-sha",
            "a" * 40,
            "--deployed-sha",
            "b" * 40,
            "--tree-dirty",
            "False",
            "--guard-message",
            "DEPLOY DRIFT GUARD: running HEAD differs from deployed_sha",
        ]
    )
    assert rc == 0
    run_root = output_root / "runs" / run_id
    results = json.loads((run_root / "execution_results.json").read_text(encoding="utf-8"))
    return run_root, results


def test_blocked_gate_writes_confirmable_terminal_result(tmp_path: Path) -> None:
    run_root, results = _write_blocked_gate(tmp_path)
    assert (run_root / "live_pilot_gate_state.json").exists()
    assert results["status"] == "HALTED"
    assert results["operator_execution_status"] == "halted"
    assert results["halt_reason"] == "live_pilot_deploy_sha_drift"
    assert results["submitted_count"] == 0
    assert results["broker_orders_submitted"] == 0
    assert results["running_sha"] == "a" * 40
    assert results["deployed_sha"] == "b" * 40


def test_deploy_block_email_is_halted_and_actionable(tmp_path: Path, monkeypatch) -> None:
    run_root, results = _write_blocked_gate(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    subject, body_text, _body_html = confirmation._build_confirmation_email(
        results, run_root / "execution_results.json"
    )
    assert "[LIVE_PILOT]" in subject
    assert "[HALTED]" in subject
    assert "system safety block (not a broker decline): live_pilot_deploy_sha_drift" in body_text
    assert "Validate and attest the exact VM deployment" in body_text
    assert "--- Operator Action Required ---\n- None" not in body_text


def test_dynamic_account_section_reports_blocked_gate_truthfully(tmp_path: Path) -> None:
    _run_root, _results = _write_blocked_gate(tmp_path)
    payload = dynamic_email.build_live_pilot_account_payload(tmp_path)
    assert payload["status"] == "BLOCKED"
    assert payload["reconciliation_status"] == "live_pilot_deploy_sha_drift"
    assert payload["blocked_or_suppressed_buy_reason"] == "live_pilot_deploy_sha_drift"
