from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.operator_summary import write_operator_summary
from core.trading_day_summary import build_trading_day_summary
from scripts.research.build_quant_dashboard import DashboardBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_operator_summary_persists_broker_snapshot_status(tmp_path):
    run_root = tmp_path / "outputs" / "runs" / "run-obs"
    run_root.mkdir(parents=True, exist_ok=True)

    write_operator_summary(
        run_root,
        run_id="run-obs",
        trade_date="2026-03-12",
        mode="ALPACA",
        pretrade_status="READY",
        planner_completed=True,
        broker_pretrade_snapshot_ok=True,
        broker_posttrade_snapshot_ok=True,
        broker_authoritative_state=True,
    )

    payload = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert payload["broker_pretrade_snapshot_ok"] is True
    assert payload["broker_posttrade_snapshot_ok"] is True
    assert payload["broker_authoritative_state"] is True


def test_trading_day_summary_includes_broker_context(tmp_path):
    run_root = tmp_path / "outputs" / "runs" / "run-obs"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_root / "operator_summary.json",
        {
            "broker_authoritative_state": True,
            "broker_pretrade_snapshot_ok": True,
            "broker_posttrade_snapshot_ok": True,
            "confirmation_email_sent": False,
        },
    )
    _write_json(
        broker_dir / "pretrade_account_snapshot.json",
        {"cash": 1000.0, "equity": 10000.0},
    )
    _write_json(
        broker_dir / "pretrade_positions.json",
        {"positions_count": 2},
    )
    _write_json(
        broker_dir / "posttrade_account_snapshot.json",
        {"cash": 820.0, "equity": 10010.0},
    )
    _write_json(
        broker_dir / "posttrade_positions.json",
        {"positions_count": 1},
    )

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-obs",
        trade_date="2026-03-12",
        workspace_root=tmp_path,
        audit_dir=tmp_path / "outputs" / "execution_audit",
    )

    assert summary["broker_context"]["broker_authoritative_state"] is True
    assert summary["broker_context"]["pretrade_positions_count"] == 2
    assert summary["broker_context"]["posttrade_positions_count"] == 1
    assert summary["broker_context"]["posttrade_cash"] == 820.0


def test_trading_day_summary_includes_execution_integrity_fields(tmp_path):
    run_root = tmp_path / "outputs" / "runs" / "run-obs"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        run_root / "operator_summary.json",
        {
            "broker_authoritative_state": True,
            "broker_pretrade_snapshot_ok": True,
            "broker_posttrade_snapshot_ok": True,
            "duplicate_guard_status": "BLOCKED_DUPLICATE_SUBMISSION",
            "post_execution_recon_status": "UNEXPECTED_SHORT",
            "affected_symbols": ["HCA", "ROST"],
            "repair_suggestions": ["BUY 1 HCA", "BUY 4 ROST"],
            "duplicate_fill_suspicions_count": 2,
            "confirmation_email_sent": False,
        },
    )
    _write_json(broker_dir / "pretrade_account_snapshot.json", {"cash": 1000.0, "equity": 10000.0})
    _write_json(broker_dir / "pretrade_positions.json", {"positions_count": 2})
    _write_json(broker_dir / "posttrade_account_snapshot.json", {"cash": 820.0, "equity": 10010.0})
    _write_json(broker_dir / "posttrade_positions.json", {"positions_count": 1})

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-obs",
        trade_date="2026-03-12",
        workspace_root=tmp_path,
        audit_dir=tmp_path / "outputs" / "execution_audit",
    )

    broker_context = summary["broker_context"]
    assert broker_context["duplicate_guard_status"] == "BLOCKED_DUPLICATE_SUBMISSION"
    assert broker_context["post_execution_recon_status"] == "UNEXPECTED_SHORT"
    assert broker_context["affected_symbols"] == ["HCA", "ROST"]
    assert broker_context["repair_suggestions"] == ["BUY 1 HCA", "BUY 4 ROST"]
    assert broker_context["duplicate_fill_suspicions_count"] == 2


def test_dashboard_builder_uses_posttrade_account_snapshot_as_authoritative(tmp_path):
    run_id = "run-obs"
    _write_json(
        tmp_path / "outputs" / "runs" / run_id / "broker" / "posttrade_account_snapshot.json",
        {"cash": 820.0, "equity": 10010.0, "buying_power": 820.0},
    )

    builder = DashboardBuilder(repo_root=tmp_path)
    snapshot, mode = builder._artifact_broker_snapshot(run_id=run_id, report_date="2026-03-12")

    assert mode == "authoritative_artifact"
    assert snapshot is not None
    assert snapshot["trust_level"] == "authoritative"
    assert "posttrade_account_snapshot.json" in snapshot["source"]


def test_dashboard_builder_surfaces_execution_integrity_from_trading_day_summary(tmp_path):
    run_id = "run-obs"
    _write_json(
        tmp_path / "outputs" / "trading_day_summary.json",
        {
            "broker_context": {
                "duplicate_guard_status": "REMOTE_IDEMPOTENT_REPLAY",
                "post_execution_recon_status": "DRIFT_DETECTED",
                "affected_symbols": ["HCA", "ROST"],
                "repair_suggestions": ["BUY 1 HCA"],
                "duplicate_fill_suspicions_count": 1,
            }
        },
    )

    builder = DashboardBuilder(repo_root=tmp_path)
    model = builder.build()

    integrity = model["execution_integrity"]
    assert integrity["duplicate_guard_status"] == "REMOTE_IDEMPOTENT_REPLAY"
    assert integrity["post_execution_recon_status"] == "DRIFT_DETECTED"
    assert integrity["affected_symbols"] == ["HCA", "ROST"]
    assert integrity["repair_suggestions"] == ["BUY 1 HCA"]
    assert integrity["duplicate_fill_suspicions_count"] == 1
    assert integrity["visible"] is True
