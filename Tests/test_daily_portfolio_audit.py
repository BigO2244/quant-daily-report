from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from core.daily_portfolio_audit import (
    DailyPortfolioAuditError,
    build_daily_portfolio_audit,
)


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[str, Path]:
    trade_date = "2026-08-14"
    bundle = tmp_path / "outputs" / "precompute" / trade_date
    session = {"session_id": "session:test", "content_hash": "session-hash"}
    decisions = {"session_hash": "session-hash"}
    allocation = {"allocation_id": "allocation:test", "content_hash": "allocation-hash"}
    package = {
        "session_id": "session:test",
        "allocation_id": "allocation:test",
        "allocation_content_hash": "allocation-hash",
        "approved_target_hash": "target-hash",
    }
    _write(bundle / "session_manifest.json", session)
    _write(bundle / "sleeve_decisions.json", decisions)
    _write(bundle / "portfolio_allocation.json", allocation)
    _write(bundle / "paper_target_package.json", package)
    source_hashes = {
        name: hashlib.sha256((bundle / filename).read_bytes()).hexdigest()
        for name, filename in {
            "session": "session_manifest.json",
            "decisions": "sleeve_decisions.json",
            "allocation": "portfolio_allocation.json",
            "target": "paper_target_package.json",
        }.items()
    }
    plan_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "plans"
        / f"exact_execution_plan_{trade_date}.json"
    )
    plan = {
        "schema_version": "caerus.execution_plan.v3",
        "plan_id": "plan:test",
        "run_id": "run:test",
        "source_artifact_hashes": source_hashes,
        "sell_orders": [],
        "buy_orders": [],
    }
    plan["content_hash"] = hashlib.sha256(
        json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    _write(plan_path, plan)
    _write(
        tmp_path / "outputs" / "workflow" / trade_date / "execution.json",
        {"status": "success", "run_id": "run:test"},
    )
    as_of = "2026-08-14T23:15:00Z"
    _write(
        tmp_path / "outputs" / "ledger" / "paper" / "ownership_latest.json",
        {"as_of": as_of, "reconciliation": {"status": "PASS"}},
    )
    _write(
        tmp_path / "outputs" / "ledger" / "paper" / "valuation_latest.json",
        {"as_of": as_of, "reconciliation": {"status": "PASS"}},
    )
    _write(
        tmp_path / "outputs" / "portfolio_history" / "reporting_snapshot.json",
        {"as_of": as_of, "report_date": trade_date, "status": "PASS"},
    )
    return trade_date, plan_path


def test_daily_audit_closes_full_decision_to_report_chain(tmp_path: Path) -> None:
    trade_date, _ = _fixture(tmp_path)
    result = build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)
    assert result["status"] == "PASS"
    assert result["checks"] == {
        "decision_to_execution": "PASS",
        "execution_to_ownership": "PASS",
        "ownership_to_valuation": "PASS",
        "valuation_to_reporting": "PASS",
        "single_as_of": "PASS",
    }
    assert (
        tmp_path / "outputs" / "audit" / trade_date / "portfolio_audit.json"
    ).is_file()


def test_daily_audit_rejects_mixed_reporting_time(tmp_path: Path) -> None:
    trade_date, _ = _fixture(tmp_path)
    reporting = (
        tmp_path / "outputs" / "portfolio_history" / "reporting_snapshot.json"
    )
    _write(
        reporting,
        {
            "as_of": "2026-08-14T22:00:00Z",
            "report_date": trade_date,
            "status": "PASS",
        },
    )
    with pytest.raises(DailyPortfolioAuditError, match="reporting_as_of_mismatch"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)


def test_daily_audit_rejects_tampered_exact_plan(tmp_path: Path) -> None:
    trade_date, plan_path = _fixture(tmp_path)
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["plan_id"] = "plan:tampered"
    _write(plan_path, plan)
    with pytest.raises(DailyPortfolioAuditError, match="exact_plan_content_hash_invalid"):
        build_daily_portfolio_audit(repo_root=tmp_path, trade_date=trade_date)
