from __future__ import annotations

import json
from pathlib import Path

from core.execution_lifecycle_timeline import (
    build_execution_lifecycle_timeline,
    write_execution_lifecycle_timeline,
)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_execution_lifecycle_timeline_synthesizes_run_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = tmp_path / "outputs" / "runs" / "run-123"
    trade_date = "2026-05-28"

    _write_json(
        tmp_path / "outputs" / "precompute" / trade_date / "contract.json",
        {
            "created_at": "2026-05-28T11:00:00+00:00",
            "source_run_id": "2026-05-28:main:growth_engine_v4",
            "status": "complete",
            "validated_for_execution": True,
            "workflow_stage": "precompute",
            "summary": {"execution_eligible_trades_count": 2},
        },
    )
    _write_json(
        tmp_path / "outputs" / "workflow" / trade_date / "execution_bundle_validation.json",
        {"status": "OK", "validated_at": "2026-05-28T13:35:00+00:00"},
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-123",
            "trade_date": trade_date,
            "execution_status": "READY",
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
            "submitted_count": 2,
            "submitted_buy_count": 1,
            "submitted_sell_count": 1,
            "sell_phase_status": "COMPLETE",
            "sell_phase_completion_reason": "all_sells_submitted",
            "buy_phase_planned": 1,
            "buy_phase_submitted": 1,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-123",
            "trade_date": trade_date,
            "status": "EXECUTED",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "broker_responses": [
                {"ticker": "ELV", "side": "SELL", "submitted_at": "2026-05-28T13:35:10Z"},
                {"ticker": "ABNB", "side": "BUY", "submitted_at": "2026-05-28T13:35:20Z"},
            ],
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "run_id": "run-123",
            "trade_date": trade_date,
            "terminal_status": "success",
            "operator_execution_status": "executed",
            "first_submit_et": "2026-05-28T09:35:10-04:00",
            "actual_execution_start_et": "2026-05-28T09:35:00-04:00",
            "post_execution_recon_status": "WARN",
            "post_execution_recon_path": str(run_root / "broker" / f"recon_posttrade_{trade_date}.json"),
        },
    )
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "status": "WARN",
            "findings": [{"code": "cash_target_drift", "severity": "WARN"}],
        },
    )
    _write_json(
        run_root / "broker" / f"recon_posttrade_{trade_date}.json",
        {"drift_status": "WARN", "affected_symbols": ["ABNB"], "repair_suggestions": []},
    )

    json_path, md_path = write_execution_lifecycle_timeline(
        run_root=run_root,
        trade_date=trade_date,
        run_id="run-123",
    )

    timeline = json.loads(json_path.read_text(encoding="utf-8"))
    checkpoints = {event["checkpoint"]: event for event in timeline["events"]}

    assert md_path.exists()
    assert timeline["schema_version"] == "execution_lifecycle_timeline.v1"
    assert timeline["provenance"]["execution_source"] == "planned_payload_exact"
    assert checkpoints["precompute_completed"]["status"] == "OK"
    assert checkpoints["bundle_validation"]["status"] == "OK"
    assert checkpoints["sell_phase_start"]["status"] == "OBSERVED"
    assert checkpoints["buy_phase_start"]["status"] == "OBSERVED"
    assert checkpoints["integrity_findings"]["details"]["finding_codes"] == ["cash_target_drift"]
    assert checkpoints["terminal_status"]["status"] == "success"
    assert "`planned_payload_exact`" in md_path.read_text(encoding="utf-8")


def test_execution_lifecycle_timeline_includes_equality_gate_when_present(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = tmp_path / "outputs" / "runs" / "run-123"
    trade_date = "2026-05-28"
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-123",
            "trade_date": trade_date,
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-05-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
        },
    )
    _write_json(
        run_root / "equality_gate.json",
        {
            "decision": "WOULD_HALT_HASH_MISMATCH",
            "would_block": True,
            "hashes_equal": False,
            "pricing_asof_match": True,
            "execution_source": "planned_payload_exact",
            "timestamp_utc": "2026-05-28T13:34:59Z",
        },
    )

    timeline = build_execution_lifecycle_timeline(
        run_root=run_root,
        trade_date=trade_date,
        run_id="run-123",
    )
    checkpoints = {event["checkpoint"]: event for event in timeline["events"]}

    assert timeline["event_count"] == 16
    assert checkpoints["equality_gate_observe"]["status"] == "WARN"
    assert checkpoints["equality_gate_observe"]["details"]["decision"] == "WOULD_HALT_HASH_MISMATCH"
    assert checkpoints["equality_gate_observe"]["details"]["note"] == "observe-only; submission unaffected"


def test_execution_lifecycle_timeline_is_deterministic_for_missing_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    run_root = tmp_path / "outputs" / "runs" / "run-missing"
    run_root.mkdir(parents=True)

    first = build_execution_lifecycle_timeline(
        run_root=run_root,
        trade_date="2026-05-28",
        run_id="run-missing",
    )
    second = build_execution_lifecycle_timeline(
        run_root=run_root,
        trade_date="2026-05-28",
        run_id="run-missing",
    )

    assert first == second
    assert first["event_count"] == 15
    assert first["events"][0]["status"] == "UNAVAILABLE"
