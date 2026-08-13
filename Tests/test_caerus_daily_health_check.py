from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.execution_attempt_registry import AttemptRecord, SELECTION_SCHEMA_VERSION
from core.failure_semantics import TerminalOutcome
from core.orchestrator_state import STAGES, append_orchestrator_transition
from core.strategy_registry import active_shadow_security_selection_ids
from scripts.caerus_daily_health_check import build_health_check, render_console, write_artifacts


TRADE_DATE = "2026-04-28"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True))


def _write_base_artifacts(root: Path, *, reconciliation: dict | None = None, vix: dict | None = None) -> None:
    _write_json(
        root
        / "outputs"
        / "health"
        / "caerus_dashboard_refresh"
        / "latest"
        / "refresh_status.json",
        {
            "schema_version": "caerus.dashboard_refresh_health.v1",
            "trade_date": TRADE_DATE,
            "status": "SUCCESS",
            "exit_code": 0,
            "reason_codes": [],
            "dashboard_published": True,
        },
    )
    _write_json(
        root / "outputs" / "operational_drag" / TRADE_DATE / "operational_drag.json",
        {
            "trade_date": TRADE_DATE,
            "available": True,
            "decision_grade": True,
            "current_date_status": "CLEAN",
            "reason_codes": [],
        },
    )
    shadow_latest = root / "outputs" / "shadow_candidates" / "latest"
    shadow_latest.mkdir(parents=True, exist_ok=True)
    (shadow_latest / "comparison.md").write_text(
        "\n".join(
            [
                "# Shadow Candidates Comparison",
                "",
                "## Trade Date",
                f"- {TRADE_DATE}",
                "",
                "## Executive Summary",
                "- Decision-useful chain.",
                "",
                "## Performance Scoreboard",
                "| Strategy | Data Status |",
                "|---|---|",
                "| Caerus Polaris | OK |",
                "| Caerus Orion | OK |",
                "| Caerus Lyra | OK |",
                "| SPY | OK |",
                "",
                "## Relative Performance",
                "- Polaris excess vs SPY: 0.00%",
                "",
                "## Chain Health",
                "- Any NO_DATA: NO",
            ]
        )
    )
    _write_json(
        shadow_latest / "shadow_evaluation.json",
        {
            "trade_date": TRADE_DATE,
            "benchmark_symbol": "SPY",
            "strategies": {
                **{
                    strategy_id: {
                        "data_status": "OK",
                        "status": "OK",
                        "rolling_count_of_valid_days": 6,
                    }
                    for strategy_id in active_shadow_security_selection_ids()
                },
                "spy_benchmark": {
                    "data_status": "OK",
                    "status": "OK",
                    "rolling_count_of_valid_days": 6,
                },
            },
        },
    )
    _write_json(
        root / "outputs" / "vix_regime" / "regime_current.json",
        vix or {"date": TRADE_DATE, "vix": 21.5, "regime": "ELEVATED", "source": "fixture", "fallback_used": False},
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "daily_snapshot.json",
        {"trade_date": TRADE_DATE, "market_analyzer": {"vix": 21.5, "regime": "ELEVATED"}},
    )
    _write_json(
        root / "outputs" / "precompute" / TRADE_DATE / "signals.json",
        {
            "snapshot_date": TRADE_DATE,
            "strategy_identity": {
                "live_strategy_id": "growth_engine_v4",
                "shadow_baseline_strategy": "caerus_polaris",
                "live_tracks_shadow_baseline": False,
            },
            "signals": [{"ticker": "AAA", "target_weight": 1.0}],
        },
    )
    _write_json(
        root / "outputs" / "reconciliation" / "live_vs_shadow" / "latest" / "live_vs_shadow_reconciliation.json",
        reconciliation
        or {
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "RECONCILED",
            "status": "RECONCILED",
            "reason_codes": ["RETURNS_RECONCILED", "HOLDINGS_RECONCILED"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
            "strategy_alignment": {
                "live_strategy_id": "growth_engine_v4",
                "shadow_baseline_strategy": "caerus_polaris",
                "status": "ALIGNED",
            },
        },
    )
    run_root = root / "outputs" / "runs" / "run-health"
    _write_json(
        root / "outputs" / "latest_run.json",
        {
            "run_id": "run-health",
            "trade_date": TRADE_DATE,
            "mode": "PAPER",
            "run_root": str(run_root),
            "status": "success",
            "workflow_stage": "execution",
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "trade_date": TRADE_DATE,
            "terminal_status": "success",
            "operator_execution_status": "executed",
            "execution_integrity_status": "OK",
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "trade_date": TRADE_DATE,
            "execution_source": "planned_payload_exact",
            "planning_price_basis": "PREV_CLOSE",
            "pricing_asof": "2026-04-27",
            "execution_price_requirement": "PRECOMPUTE_VALIDATED",
            "price_freshness_scope": "precompute_bundle",
        },
    )
    _write_json(
        run_root / "execution_timeline.json",
        {
            "trade_date": TRADE_DATE,
            "event_count": 15,
            "provenance": {
                "execution_source": "planned_payload_exact",
                "planning_price_basis": "PREV_CLOSE",
                "pricing_asof": "2026-04-27",
                "execution_price_requirement": "PRECOMPUTE_VALIDATED",
                "price_freshness_scope": "precompute_bundle",
            },
        },
    )
    _write_json(run_root / "audit" / "execution_integrity.json", {"status": "OK", "findings": []})
    _write_json(
        run_root / "equality_gate.json",
        {
            "decision": "WOULD_PROCEED",
            "would_block": False,
            "hashes_equal": True,
            "pricing_asof_match": True,
            "execution_source": "planned_payload_exact",
        },
    )


def _with_content_hash(payload: dict) -> dict:
    hashed = dict(payload)
    hashed["content_hash"] = hashlib.sha256(
        json.dumps(
            hashed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return hashed


def _write_choice2_artifacts(root: Path) -> None:
    _write_base_artifacts(root)
    run_id = "run-health"
    run_root = root / "outputs" / "runs" / run_id
    pointer = {
        "stage": "execution",
        "run_id": run_id,
        "trade_date": TRADE_DATE,
        "mode": "PAPER",
        "run_root": str(run_root),
        "status": "success",
        "substatus": None,
        "created_at": "2026-04-28T20:00:00Z",
    }
    _write_json(root / "outputs" / "workflow" / TRADE_DATE / "execution.json", pointer)
    _write_json(
        root / "outputs" / "latest_run.json",
        {**pointer, "workflow_stage": "execution"},
    )

    selection_path = (
        root
        / "outputs"
        / "paper_lane"
        / "execution_attempts"
        / TRADE_DATE
        / "selection.json"
    )
    state_root = root / "outputs" / "paper_lane" / "orchestrator_state"
    workflow_plan_id = "plan:health:run-health"
    transition = None
    for index, stage in enumerate(STAGES):
        transition = append_orchestrator_transition(
            state_root,
            trade_date=TRADE_DATE,
            plan_id=workflow_plan_id,
            stage=stage,
            status="PASS",
            recorded_at=f"2026-04-28T20:00:{index:02d}Z",
            artifact_refs=(f"fixture:{stage.lower()}",),
        )
    assert transition is not None
    operator = {
        "schema_version": "live_pilot_operator_summary.v1",
        "run_id": run_id,
        "trade_date": TRADE_DATE,
        "mode": "PAPER",
        "terminal_status": "SUBMITTED",
        "terminal_outcome": "RECONCILED_SUCCESS",
        "reason_code": None,
        "reconciliation_status": "RECONCILED",
        "execution_source": "exact_execution_plan_v3",
        "execution_integrity_status": "OK",
        "canonical_economic_verification_status": "RECONCILED",
        "attempt_registry_status": "RESOLVED",
        "attempt_registry_selection": str(selection_path),
        "orchestrator_state_status": "PASS",
        "orchestrator_state_hash": transition.content_hash,
        "orchestrator_state_root": str(state_root),
        "orchestrator_workflow_plan_id": workflow_plan_id,
        "run_root": str(run_root),
    }
    _write_json(run_root / "operator_summary.json", operator)
    _write_json(run_root / "live_pilot_operator_summary.json", operator)
    _write_json(
        run_root / "execution_payload.json",
        {
            "schema_version": "caerus.execution_payload.v2",
            "run_id": run_id,
            "trade_date": TRADE_DATE,
            "mode": "PAPER",
            "execution_source": "exact_execution_plan_v3",
            "price_freshness_scope": "fresh_broker_state_at_authorization",
        },
    )
    terminal_surface = {
        "run_id": run_id,
        "trade_date": TRADE_DATE,
        "mode": "PAPER",
        "terminal_status": "SUBMITTED",
        "terminal_outcome": "RECONCILED_SUCCESS",
        "reconciliation_status": "RECONCILED",
    }
    _write_json(run_root / "execution_results.json", terminal_surface)
    _write_json(
        run_root / "execution_timeline.json",
        {
            **terminal_surface,
            "schema_version": "caerus.execution_lifecycle_timeline.v2",
            "provenance": {
                "execution_source": "exact_execution_plan_v3",
                "price_freshness_scope": "fresh_broker_state_at_authorization",
            },
        },
    )
    _write_json(
        run_root / "canonical_economic_verification.json",
        _with_content_hash(
            {
                "schema_version": "caerus.canonical_economic_verification.v1",
                "trade_date": TRADE_DATE,
                "status": "RECONCILED",
                "reconciled": True,
                "economic_reconciliation": {"status": "RECONCILED"},
                "sleeve_attribution_reconciliation": {"status": "RECONCILED"},
            }
        ),
    )

    attempt = AttemptRecord(
        attempt_id=run_id,
        trade_date=TRADE_DATE,
        run_id=run_id,
        lane="paper",
        sequence=1,
        terminal_outcome=TerminalOutcome.RECONCILED_SUCCESS,
        recorded_at="2026-04-28T20:00:00Z",
        run_root=str(run_root),
        submitted_count=1,
        filled_count=1,
    ).with_content_hash()
    attempt_path = selection_path.parent / "attempts" / f"{run_id}.json"
    _write_json(attempt_path, attempt.to_dict())
    _write_json(
        selection_path,
        {
            "schema_version": SELECTION_SCHEMA_VERSION,
            "trade_date": TRADE_DATE,
            "status": "RESOLVED",
            "selected_attempt_id": run_id,
            "selected_attempt_hash": attempt.content_hash,
            "unresolved_submission_attempt_ids": [],
            "attempt_count": 1,
            "attempt_hashes": [attempt.content_hash],
            "generated_at": "2026-04-28T20:00:00Z",
            "reason": "latest_resolved_nonfailure_attempt_selected",
        },
    )


def test_strategy_identity_warns_when_live_target_does_not_track_approved_strategy(
    tmp_path: Path,
) -> None:
    _write_base_artifacts(tmp_path)
    signals_path = (
        tmp_path / "outputs" / "precompute" / TRADE_DATE / "signals.json"
    )
    signals = json.loads(signals_path.read_text(encoding="utf-8"))
    signals["strategy_identity"].update(
        {
            "execution_target_strategy_id": "growth_engine_v4",
            "live_pilot_governed_strategy_id": "caerus_orion",
            "live_pilot_mapping_status": "NOT_TRACKING_GOVERNED_STRATEGY",
            "live_pilot_tracks_approved_strategy": False,
        }
    )
    _write_json(signals_path, signals)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    identity = next(
        check for check in payload["checks"] if check["name"] == "Strategy identity"
    )
    assert identity["status"] == "YELLOW"
    assert "LIVE_PILOT_STRATEGY_TARGET_MISMATCH" in identity["reason_codes"]


def _status(payload: dict, name: str) -> str:
    return next(check["status"] for check in payload["checks"] if check["name"] == name)


def _check(payload: dict, name: str) -> dict:
    return next(check for check in payload["checks"] if check["name"] == name)


def test_green_case(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "GREEN"
    assert payload["recommended_action"] == "HOLD_NO_ACTION"
    assert _status(payload, "VIX/regime") == "GREEN"
    assert _status(payload, "Broker/NAV refresh service") == "GREEN"
    assert _status(payload, "NAV/operational-drag reconciliation") == "GREEN"
    assert _status(payload, "Shadow performance report") == "GREEN"
    assert _status(payload, "Execution timeline provenance") == "GREEN"
    assert _status(payload, "Choice 2 terminal evidence") == "GREEN"
    assert "EXPLICIT_NON_CHOICE2_COMPATIBILITY" in _check(
        payload, "Choice 2 terminal evidence"
    )["reason_codes"]
    assert payload["equality_gate_observe"]["status"] == "ok"
    assert "Caerus Daily Health Check" in render_console(payload)


def test_choice2_terminal_evidence_is_green_when_canonical_chain_agrees(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "GREEN"
    assert check["reason_codes"] == []
    assert "terminal_status=SUBMITTED" in check["summary"]
    assert "attempt_selection_status=RESOLVED" in check["summary"]


def test_choice2_missing_orchestrator_state_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    operator_path = tmp_path / "outputs" / "runs" / "run-health" / "operator_summary.json"
    operator = json.loads(operator_path.read_text())
    operator.pop("orchestrator_state_root")
    _write_json(operator_path, operator)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    check = next(row for row in payload["checks"] if row["name"] == "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ORCHESTRATOR_STATE_IDENTITY_MISSING" in check["reason_codes"]


def test_choice2_two_phase_running_pointer_can_pass_prepublication_health(
    tmp_path: Path,
) -> None:
    _write_choice2_artifacts(tmp_path)
    pointer_path = tmp_path / "outputs" / "workflow" / TRADE_DATE / "execution.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer.update(
        {
            "status": "running",
            "substatus": "paper_posttrade_verification_started",
        }
    )
    _write_json(pointer_path, pointer)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "GREEN"
    assert "CHOICE2_POINTER_TERMINAL_STATUS_MISMATCH" not in check["reason_codes"]


def test_choice2_arbitrary_running_pointer_remains_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    pointer_path = tmp_path / "outputs" / "workflow" / TRADE_DATE / "execution.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer.update({"status": "running", "substatus": "unrecognized_state"})
    _write_json(pointer_path, pointer)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_POINTER_TERMINAL_STATUS_MISMATCH" in check["reason_codes"]


def test_choice2_missing_canonical_execution_pointer_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    (tmp_path / "outputs" / "workflow" / TRADE_DATE / "execution.json").unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_EXECUTION_POINTER_MISSING_OR_UNREADABLE" in check["reason_codes"]


def test_choice2_pointer_run_id_mismatch_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    pointer_path = tmp_path / "outputs" / "workflow" / TRADE_DATE / "execution.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["run_id"] = "wrong-run"
    _write_json(pointer_path, pointer)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_OPERATOR_RUN_ID_MISMATCH" in check["reason_codes"]
    assert "CHOICE2_SELECTED_ATTEMPT_RUN_ID_MISMATCH" in check["reason_codes"]


def test_choice2_run_local_terminal_mismatch_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    results_path = tmp_path / "outputs" / "runs" / "run-health" / "execution_results.json"
    results = json.loads(results_path.read_text())
    results["terminal_status"] = "FAILED_RECONCILIATION"
    _write_json(results_path, results)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_RESULTS_TERMINAL_STATUS_MISMATCH" in check["reason_codes"]


def test_choice2_missing_economic_verification_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    (
        tmp_path
        / "outputs"
        / "runs"
        / "run-health"
        / "canonical_economic_verification.json"
    ).unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ECONOMIC_VERIFICATION_MISSING_OR_UNREADABLE" in check["reason_codes"]


def test_choice2_tampered_economic_verification_hash_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    economic_path = (
        tmp_path
        / "outputs"
        / "runs"
        / "run-health"
        / "canonical_economic_verification.json"
    )
    economic = json.loads(economic_path.read_text())
    economic["economic_reconciliation"]["status"] = "TAMPERED"
    _write_json(economic_path, economic)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ECONOMIC_VERIFICATION_HASH_MISMATCH" in check["reason_codes"]


def test_choice2_economic_date_or_status_mismatch_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    economic_path = (
        tmp_path
        / "outputs"
        / "runs"
        / "run-health"
        / "canonical_economic_verification.json"
    )
    economic = json.loads(economic_path.read_text())
    economic["trade_date"] = "2026-04-27"
    economic["status"] = "FAILED_RECONCILIATION"
    economic["reconciled"] = False
    economic.pop("content_hash")
    _write_json(economic_path, _with_content_hash(economic))

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ECONOMIC_VERIFICATION_DATE_MISMATCH" in check["reason_codes"]
    assert "CHOICE2_ECONOMIC_VERIFICATION_NOT_RECONCILED" in check["reason_codes"]
    assert "CHOICE2_OPERATOR_ECONOMIC_STATUS_MISMATCH" in check["reason_codes"]


def test_choice2_missing_attempt_selection_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "execution_attempts"
        / TRADE_DATE
        / "selection.json"
    ).unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ATTEMPT_SELECTION_MISSING_OR_UNREADABLE" in check["reason_codes"]


def test_choice2_failed_attempt_selection_status_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    selection_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "execution_attempts"
        / TRADE_DATE
        / "selection.json"
    )
    selection = json.loads(selection_path.read_text())
    selection["status"] = "FAILED"
    _write_json(selection_path, selection)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_ATTEMPT_SELECTION_NOT_RESOLVED" in check["reason_codes"]
    assert "CHOICE2_OPERATOR_ATTEMPT_STATUS_MISMATCH" in check["reason_codes"]


def test_choice2_selected_attempt_hash_mismatch_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    selection_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "execution_attempts"
        / TRADE_DATE
        / "selection.json"
    )
    selection = json.loads(selection_path.read_text())
    selection["selected_attempt_hash"] = "0" * 64
    _write_json(selection_path, selection)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_SELECTED_ATTEMPT_HASH_MISMATCH" in check["reason_codes"]


def test_choice2_tampered_selected_attempt_is_red(tmp_path: Path) -> None:
    _write_choice2_artifacts(tmp_path)
    attempt_path = (
        tmp_path
        / "outputs"
        / "paper_lane"
        / "execution_attempts"
        / TRADE_DATE
        / "attempts"
        / "run-health.json"
    )
    attempt = json.loads(attempt_path.read_text())
    attempt["filled_count"] = 0
    _write_json(attempt_path, attempt)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = _check(payload, "Choice 2 terminal evidence")
    assert check["status"] == "RED"
    assert "CHOICE2_SELECTED_ATTEMPT_HASH_OR_SCHEMA_INVALID" in check["reason_codes"]


def test_broker_nav_refresh_failure_blocks_false_green(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path
        / "outputs"
        / "health"
        / "caerus_dashboard_refresh"
        / "latest"
        / "refresh_status.json",
        {
            "schema_version": "caerus.dashboard_refresh_health.v1",
            "trade_date": TRADE_DATE,
            "status": "FAILED",
            "exit_code": 1,
            "reason_codes": ["alpaca_auth_failed", "nav_artifact_stale"],
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert payload["overall_status"] == "RED"
    check = next(
        item
        for item in payload["checks"]
        if item["name"] == "Broker/NAV refresh service"
    )
    assert check["status"] == "RED"
    assert "alpaca_auth_failed" in check["reason_codes"]


def test_missing_broker_nav_refresh_health_blocks_false_green(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    (
        tmp_path
        / "outputs"
        / "health"
        / "caerus_dashboard_refresh"
        / "latest"
        / "refresh_status.json"
    ).unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert payload["overall_status"] == "RED"
    assert _status(payload, "Broker/NAV refresh service") == "RED"


def test_material_operational_drag_gap_blocks_false_green(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path
        / "outputs"
        / "operational_drag"
        / TRADE_DATE
        / "operational_drag.json",
        {
            "trade_date": TRADE_DATE,
            "available": True,
            "decision_grade": False,
            "current_date_status": "MATERIAL_GAP",
            "reason_codes": ["planned_buys_without_submissions"],
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert payload["overall_status"] == "RED"
    check = next(
        item
        for item in payload["checks"]
        if item["name"] == "NAV/operational-drag reconciliation"
    )
    assert "planned_buys_without_submissions" in check["reason_codes"]


def test_historical_operational_drag_caveats_do_not_make_current_date_red(
    tmp_path: Path,
) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path
        / "outputs"
        / "operational_drag"
        / TRADE_DATE
        / "operational_drag.json",
        {
            "trade_date": TRADE_DATE,
            "available": True,
            "decision_grade": True,
            "current_date_status": "current_date_available_with_historical_caveats",
            "current_date_reason_codes": ["actual_nav_from_live_overlay"],
            "historical_reason_codes": ["planned_buys_without_submissions"],
            "material_reason_codes": ["planned_buys_without_submissions"],
            "current_date_health": {
                "requested_date": TRADE_DATE,
                "reaches_requested_date": True,
                "current_date_material_reason_codes": [],
                "blocking_components": [],
            },
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert _status(payload, "NAV/operational-drag reconciliation") == "GREEN"


def test_current_operational_drag_caveats_remain_red(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path
        / "outputs"
        / "operational_drag"
        / TRADE_DATE
        / "operational_drag.json",
        {
            "trade_date": TRADE_DATE,
            "available": True,
            "decision_grade": False,
            "current_date_status": "current_date_available_with_caveats",
            "current_date_reason_codes": ["reconciliation_not_clean"],
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    check = next(
        item
        for item in payload["checks"]
        if item["name"] == "NAV/operational-drag reconciliation"
    )
    assert check["status"] == "RED"
    assert "reconciliation_not_clean" in check["reason_codes"]


def test_canonical_operational_drag_status_cross_checks_current_health(
    tmp_path: Path,
) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path
        / "outputs"
        / "operational_drag"
        / TRADE_DATE
        / "operational_drag.json",
        {
            "trade_date": TRADE_DATE,
            "available": True,
            "decision_grade": True,
            "current_date_status": "current_date_available_with_historical_caveats",
            "current_date_reason_codes": [],
            "reason_codes": ["planned_buys_without_submissions"],
            "current_date_health": {
                "requested_date": TRADE_DATE,
                "reaches_requested_date": True,
                "current_date_material_reason_codes": [
                    "planned_buys_without_submissions"
                ],
                "blocking_components": ["intended"],
            },
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert _status(payload, "NAV/operational-drag reconciliation") == "RED"


def test_equality_gate_divergence_blocks_universal_green(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    _write_json(
        tmp_path / "outputs" / "runs" / "run-health" / "equality_gate.json",
        {
            "decision": "WOULD_HALT_HASH_MISMATCH",
            "would_block": True,
            "hashes_equal": False,
            "pricing_asof_match": True,
            "execution_source": "planned_payload_exact",
        },
    )

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)

    assert payload["overall_status"] == "RED"
    assert _status(payload, "Execution equality") == "RED"
    assert payload["equality_gate_observe"]["status"] == "divergence_observed"
    assert payload["equality_gate_observe"]["decision"] == "WOULD_HALT_HASH_MISMATCH"


def test_execution_timeline_missing_is_yellow_operator_visibility(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    (tmp_path / "outputs" / "runs" / "run-health" / "execution_timeline.json").unlink()

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    check = next(item for item in payload["checks"] if item["name"] == "Execution timeline provenance")

    assert payload["overall_status"] == "YELLOW"
    assert check["status"] == "YELLOW"
    assert "EXECUTION_TIMELINE_MISSING" in check["reason_codes"]
    assert "timeline_present=false" in check["summary"]


def test_yellow_not_aligned_case(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "NOT_ALIGNED",
            "status": "NOT_ALIGNED",
            "reason_codes": ["DIFFERENT_STRATEGY_PATH"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Live vs shadow reconciliation") == "YELLOW"
    assert payload["recommended_action"] == "HOLD_MONITOR"


def test_yellow_not_comparable_explicit_reasons(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "generated_at": "2026-04-28T20:00:00Z",
            "classification": "NOT_COMPARABLE",
            "status": "NOT_COMPARABLE",
            "reason_codes": ["INSUFFICIENT_HISTORY", "BENCHMARK_MISSING"],
            "live_strategy_id": "growth_engine_v4",
            "shadow_baseline_strategy": "caerus_polaris",
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Live vs shadow reconciliation") == "YELLOW"


def test_aligned_initializing_is_green_only_with_lineage_and_attainment(tmp_path: Path) -> None:
    _write_base_artifacts(
        tmp_path,
        reconciliation={
            "trade_date": TRADE_DATE,
            "classification": "ALIGNED_INITIALIZING",
            "status": "ALIGNED_INITIALIZING",
            "reason_codes": [
                "INSUFFICIENT_HISTORY",
                "IMMUTABLE_LINEAGE_VERIFIED",
                "TARGET_ATTAINED",
                "PERFORMANCE_HISTORY_INITIALIZING",
            ],
            "live_strategy_id": "caerus_orion",
            "shadow_baseline_strategy": "caerus_orion",
            "strategy_alignment": {
                "live_strategy_id": "caerus_orion",
                "shadow_baseline_strategy": "caerus_orion",
                "status": "ALIGNED",
            },
            "immutable_lineage": {"verified": True},
        },
    )
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert _status(payload, "Live vs shadow reconciliation") == "GREEN"
    assert _status(payload, "Strategy identity") == "GREEN"
    assert payload["overall_status"] == "GREEN"


def test_yellow_price_cache_stale_from_shadow_sidecars(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    shadow_latest = tmp_path / "outputs" / "shadow_candidates" / "latest"
    (shadow_latest / "comparison.md").write_text(
        "\n".join(
            [
                "# Shadow Candidates Comparison",
                "## Executive Summary",
                "- Chain health: NO_DATA",
                "## Performance Scoreboard",
                "| Strategy | Data Status |",
                "|---|---|",
                "| Caerus Polaris | NO_DATA |",
                "| Caerus Orion | NO_DATA |",
                "| Caerus Lyra | NO_DATA |",
                "| SPY | NO_DATA |",
                "## Chain Health",
                "- Any NO_DATA: YES",
            ]
        )
    )
    _write_json(shadow_latest / "comparison.json", {"trade_date": TRADE_DATE, "status": "NO_DATA", "reason_code": "PRICE_CACHE_STALE"})
    _write_json(
        tmp_path / "outputs" / "shadow_candidates" / TRADE_DATE / "shadow_performance.json",
        {"trade_date": TRADE_DATE, "data_status": "NO_DATA", "data_reason": "PRICE_CACHE_STALE", "strategies": {}},
    )
    evaluation = json.loads((shadow_latest / "shadow_evaluation.json").read_text())
    for row in evaluation["strategies"].values():
        row["data_status"] = "NO_DATA"
        row.pop("data_reason", None)
    _write_json(shadow_latest / "shadow_evaluation.json", evaluation)

    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "YELLOW"
    assert _status(payload, "Shadow artifacts") == "YELLOW"
    assert _status(payload, "Shadow performance report") == "YELLOW"


def test_red_missing_shadow_latest(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    (tmp_path / "outputs" / "shadow_candidates" / "latest" / "comparison.md").unlink()
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "RED"
    assert _status(payload, "Shadow artifacts") == "RED"
    assert payload["recommended_action"] == "INVESTIGATE_BEFORE_TRADING_CHANGES"


def test_red_ambiguous_unknown_regime(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path, vix={"date": TRADE_DATE, "vix": "?", "regime": "UNKNOWN"})
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    assert payload["overall_status"] == "RED"
    assert _status(payload, "VIX/regime") == "RED"


def test_latest_publishing(tmp_path: Path) -> None:
    _write_base_artifacts(tmp_path)
    payload = build_health_check(root=tmp_path, trade_date=TRADE_DATE)
    dated_json, dated_md, latest_json, latest_md = write_artifacts(payload, root=tmp_path)

    assert dated_json == tmp_path / "outputs" / "health" / "caerus_daily_health_check" / TRADE_DATE / "health_check.json"
    assert dated_md.exists()
    assert latest_json.exists()
    assert latest_md.exists()
    latest_payload = json.loads(latest_json.read_text())
    assert latest_payload["trade_date"] == TRADE_DATE
    assert latest_payload["overall_status"] == "GREEN"
