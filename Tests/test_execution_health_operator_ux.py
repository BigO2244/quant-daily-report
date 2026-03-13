from __future__ import annotations

import json
from pathlib import Path

from core.operator_summary import format_execution_health_banner, write_operator_summary
from core.trading_day_summary import build_trading_day_summary
from scripts.print_paper_repair_actions import (
    format_paper_repair_actions,
    load_paper_repair_actions,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_execution_health_banner_includes_duplicate_and_recon_details():
    banner = format_execution_health_banner(
        {
            "run_id": "run-123",
            "duplicate_guard_status": "BLOCKED_DUPLICATE_SUBMISSION",
            "post_execution_recon_status": "UNEXPECTED_SHORT",
            "affected_symbols": ["HCA", "ROST"],
            "duplicate_fill_suspicions_count": 2,
            "repair_suggestions": ["BUY 1 HCA", "BUY 4 ROST"],
        }
    )

    assert "[EXECUTION_HEALTH]" in banner
    assert "duplicate_guard=BLOCKED_DUPLICATE_SUBMISSION" in banner
    assert "post_execution_recon=UNEXPECTED_SHORT" in banner
    assert "affected_symbols=HCA,ROST" in banner
    assert "duplicate_fill_suspicions=2" in banner
    assert "repairs=BUY 1 HCA; BUY 4 ROST" in banner


def test_operator_summary_and_trading_day_summary_include_execution_health_fields(tmp_path):
    run_root = tmp_path / "outputs" / "runs" / "run-health"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    write_operator_summary(
        run_root,
        run_id="run-health",
        trade_date="2026-03-13",
        mode="ALPACA",
        pretrade_status="READY",
        duplicate_guard_status="REMOTE_IDEMPOTENT_REPLAY",
        post_execution_recon_status="DRIFT_DETECTED",
        affected_symbols=["HCA", "ROST"],
        repair_suggestions=["BUY 1 HCA"],
        duplicate_fill_suspicions_count=1,
    )

    op_payload = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert op_payload["duplicate_guard_status"] == "REMOTE_IDEMPOTENT_REPLAY"
    assert op_payload["post_execution_recon_status"] == "DRIFT_DETECTED"
    assert op_payload["affected_symbols"] == ["HCA", "ROST"]
    assert op_payload["repair_suggestions"] == ["BUY 1 HCA"]
    assert op_payload["duplicate_fill_suspicions_count"] == 1

    _write_json(broker_dir / "pretrade_account_snapshot.json", {"cash": 1000.0, "equity": 10000.0})
    _write_json(broker_dir / "pretrade_positions.json", {"positions_count": 2})
    _write_json(broker_dir / "posttrade_account_snapshot.json", {"cash": 800.0, "equity": 9950.0})
    _write_json(broker_dir / "posttrade_positions.json", {"positions_count": 2})

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-health",
        trade_date="2026-03-13",
        workspace_root=tmp_path,
        audit_dir=tmp_path / "outputs" / "execution_audit",
    )

    broker_context = summary["broker_context"]
    assert broker_context["duplicate_guard_status"] == "REMOTE_IDEMPOTENT_REPLAY"
    assert broker_context["post_execution_recon_status"] == "DRIFT_DETECTED"
    assert broker_context["affected_symbols"] == ["HCA", "ROST"]
    assert broker_context["repair_suggestions"] == ["BUY 1 HCA"]
    assert broker_context["duplicate_fill_suspicions_count"] == 1


def test_paper_repair_helper_prints_short_repair_actions(tmp_path, monkeypatch):
    run_root = tmp_path / "outputs" / "runs" / "run-repair"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "run-repair",
            "trade_date": "2026-03-13",
            "mode": "ALPACA",
            "run_root": str(run_root),
            "status": "success",
            "created_at": "2026-03-13T15:45:00Z",
        },
    )
    _write_json(
        broker_dir / "recon_posttrade_2026-03-13.json",
        {
            "trade_date": "2026-03-13",
            "drift_status": "UNEXPECTED_SHORT",
            "affected_symbols": ["HCA", "ROST"],
            "repair_suggestions": ["BUY 1 HCA", "BUY 4 ROST"],
            "unexpected_short_positions": [
                {"symbol": "HCA", "broker_qty": -1.0},
                {"symbol": "ROST", "broker_qty": -4.0},
            ],
            "operator_message": "Unexpected short position(s) detected after execution; inspect broker orders and repair before next run.",
        },
    )

    monkeypatch.chdir(tmp_path)
    plan = load_paper_repair_actions()
    output = format_paper_repair_actions(plan)

    assert plan["drift_status"] == "UNEXPECTED_SHORT"
    assert plan["repair_suggestions"] == ["BUY 1 HCA", "BUY 4 ROST"]
    assert "affected_symbols=HCA,ROST" in output
    assert "- BUY 1 HCA" in output
    assert "- BUY 4 ROST" in output
