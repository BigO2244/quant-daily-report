from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.recovery.drift_analysis import analyze_portfolio_drift
from core.recovery.eventual_settlement import assess_eventual_settlement
from core.recovery.execution_timeline import TimelineEventType, reconstruct_execution_timeline
from core.recovery.incident_package import build_incident_package
from core.recovery.interrupted_state import (
    BrokerState,
    ExecutionLifecycleState,
    OrderState,
)
from core.recovery.recovery_delta import RecoveryDeltaOrder
from core.recovery.recovery_governance import (
    GovernanceClassification,
    classify_recovery_governance,
)
from core.recovery.recovery_lineage import (
    build_lifecycle_graph,
    build_recovery_lineage,
    validate_lineage,
)
from core.recovery.recovery_risk import score_recovery_risk
from core.recovery.recovery_validator import RecoveryValidationResult
from core.recovery.state_transitions import validate_transition, validate_transition_path


FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "interrupted_runs" / "2026-05-18"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_execution_timeline_reconstructs_delayed_settlement_sequence() -> None:
    broker = _load("current_broker_state.json")
    execution = _load("execution_payload.json")
    recon = _load("posttrade_reconciliation.json")

    timeline = reconstruct_execution_timeline(
        source_run_id="2026-05-18T093507-0400_71f60c1",
        trade_date="2026-05-18",
        execution_payload=execution,
        broker_orders=broker["orders_report_date"],
        fills=broker["fills_report_date"],
        posttrade_reconciliation=recon,
        recovery_summary={"verdict": "SIMULATION_PASS", "recovery_timestamp": "2026-05-18T16:00:00Z"},
    )

    event_types = [event["event_type"] for event in timeline["events"]]
    assert TimelineEventType.SELL_ORDERS_SUBMITTED.value in event_types
    assert TimelineEventType.BUY_PHASE_BLOCKED.value in event_types
    assert TimelineEventType.RECON_CAPTURE_FAILED.value in event_types
    assert TimelineEventType.EVENTUAL_SETTLEMENT_OBSERVED.value in event_types
    assert TimelineEventType.RECOVERY_CANDIDATE_IDENTIFIED.value in event_types
    assert event_types.index("EVENTUAL_SETTLEMENT_OBSERVED") < event_types.index("RECOVERY_CANDIDATE_IDENTIFIED")


def test_state_transition_engine_rejects_illegal_and_post_terminal_mutation() -> None:
    illegal = validate_transition(
        ExecutionLifecycleState.NORMAL_EXECUTION,
        ExecutionLifecycleState.RECOVERY_EXECUTED,
    )
    terminal = validate_transition(
        ExecutionLifecycleState.RECOVERY_RECONCILED,
        ExecutionLifecycleState.RECOVERY_CANDIDATE,
    )
    path = validate_transition_path(
        [
            ExecutionLifecycleState.NORMAL_EXECUTION,
            ExecutionLifecycleState.SELL_PHASE_TIMEOUT,
            ExecutionLifecycleState.SETTLEMENT_PENDING,
            ExecutionLifecycleState.RECOVERY_CANDIDATE,
            ExecutionLifecycleState.RECOVERY_SIMULATED,
        ]
    )

    assert illegal.allowed is False
    assert illegal.reason == "silent_replay_or_unapproved_execution_rejected"
    assert terminal.allowed is False
    assert terminal.reason == "post_terminal_mutation_rejected"
    assert path["ok"] is True
    assert path["operator_required"] is True


def test_eventual_settlement_models_pending_then_reconciled() -> None:
    pending = assess_eventual_settlement(
        observed_orders=[
            OrderState("sell-a", "PFE", "sell", 14, 13, "partially_filled"),
        ],
        reconciliation_passed=False,
        expected_sell_client_ids={"sell-a"},
    )
    reconciled = assess_eventual_settlement(
        observed_orders=[
            OrderState("sell-a", "PFE", "sell", 14, 14, "filled"),
        ],
        reconciliation_passed=True,
        expected_sell_client_ids={"sell-a"},
    )

    assert pending.status == "PENDING_TERMINALITY"
    assert pending.confidence == "LOW"
    assert reconciled.status == "EVENTUALLY_RECONCILED"
    assert reconciled.confidence == "HIGH"


def test_drift_analysis_quantifies_underinvested_partial_rebalance() -> None:
    current = {"CVS": 5, "ELV": 2}
    target = {"CVS": 5, "ELV": 2, "MRK": 7, "UNH": 1}

    drift = analyze_portfolio_drift(
        current_positions=current,
        target_positions=target,
        current_market_values={"CVS": 480, "ELV": 786},
        target_market_values={"CVS": 480, "ELV": 786, "MRK": 783, "UNH": 387},
        current_cash=3937,
        target_cash=1350,
        current_equity=10360,
    )

    assert drift["missing_position_count"] == 2
    assert drift["matched_position_count"] == 2
    assert drift["gross_exposure_drift_pct"] > 0
    assert drift["cash_drift_pct"] < 0
    assert drift["partial_rebalance_completeness_pct"] == 50.0


def test_recovery_risk_score_is_high_for_large_underinvestment_and_lock_warning() -> None:
    settlement = assess_eventual_settlement(
        observed_orders=[OrderState("sell-a", "PFE", "sell", 14, 14, "filled")],
        reconciliation_passed=True,
    )
    validation = RecoveryValidationResult(
        ok=True,
        warnings=["stale_execution_lock_present_do_not_reuse"],
    )
    drift = {
        "missing_position_count": 6,
        "exposure_discontinuity_pct": 0.25,
    }

    risk = score_recovery_risk(
        broker_state=BrokerState(account_status="ACTIVE", cash=3937, equity=10360),
        recovery_delta=[RecoveryDeltaOrder("MRK", "BUY", 7, 799.29)],
        validation=validation,
        drift_analysis=drift,
        settlement=settlement,
        artifact_completeness={"execution_payload": True, "broker_state": True},
    )

    assert risk["overall_risk"] == "HIGH"
    assert risk["dimensions"]["stale_execution_locks"]["level"] == "MODERATE"
    assert risk["dimensions"]["portfolio_drift_severity"]["level"] == "HIGH"


def test_risk_score_critical_for_duplicate_recovery_replay() -> None:
    settlement = assess_eventual_settlement(observed_orders=[], reconciliation_passed=True)

    risk = score_recovery_risk(
        broker_state=BrokerState(account_status="ACTIVE", cash=4000),
        recovery_delta=[RecoveryDeltaOrder("MRK", "BUY", 7, 799.29)],
        validation=RecoveryValidationResult(ok=True),
        drift_analysis={"missing_position_count": 1, "exposure_discontinuity_pct": 0.05},
        settlement=settlement,
        duplicate_order_risk=True,
    )

    assert risk["overall_risk"] == "CRITICAL"
    assert risk["dimensions"]["duplicate_order_risk"]["level"] == "CRITICAL"


def test_fixture_replay_writes_lifecycle_intelligence_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "replay"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/simulate_interrupted_recovery.py",
            "--source-run-id",
            "2026-05-18T093507-0400_71f60c1",
            "--trade-date",
            "2026-05-18",
            "--pretrade-positions",
            str(FIXTURE_DIR / "pretrade_positions.json"),
            "--intended-orders",
            str(FIXTURE_DIR / "intended_orders.json"),
            "--current-broker-state",
            str(FIXTURE_DIR / "current_broker_state.json"),
            "--execution-payload",
            str(FIXTURE_DIR / "execution_payload.json"),
            "--posttrade-reconciliation-status",
            "OK_RECONCILED",
            "--execution-lock-present",
            "--output-dir",
            str(output_dir),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    expected = {
        "recovery_simulation_summary.json",
        "recovery_delta.json",
        "recovery_validation.json",
        "execution_timeline.json",
        "state_transition_trace.json",
        "recovery_risk_report.json",
        "portfolio_drift.json",
        "eventual_settlement.json",
        "lifecycle_summary.md",
        "recovery_decision_trace.md",
        "recovery_governance_report.json",
        "recovery_certification_summary.json",
        "recovery_lineage.json",
        "lifecycle_graph.json",
        "transition_matrix.json",
        "lifecycle_state_diagram.md",
        "recovery_flow_summary.md",
        "operator_notes.md",
    }
    assert expected <= {path.name for path in output_dir.iterdir()}
    summary = json.loads((output_dir / "recovery_simulation_summary.json").read_text())
    risk = json.loads((output_dir / "recovery_risk_report.json").read_text())
    transition = json.loads((output_dir / "state_transition_trace.json").read_text())
    timeline = json.loads((output_dir / "execution_timeline.json").read_text())
    governance = json.loads((output_dir / "recovery_governance_report.json").read_text())
    certification = json.loads((output_dir / "recovery_certification_summary.json").read_text())
    lineage = json.loads((output_dir / "recovery_lineage.json").read_text())
    assert summary["verdict"] == "SIMULATION_PASS"
    assert risk["overall_risk"] in {"HIGH", "MODERATE"}
    assert transition["ok"] is True
    assert timeline["event_count"] >= 5
    assert governance["classification"] == "SAFE_SIMULATION_ONLY"
    assert certification["ok"] is True
    assert lineage["immutable_historical_artifacts"] is True


def test_governance_blocks_terminal_and_duplicate_replay() -> None:
    terminal = classify_recovery_governance(
        lifecycle_state=ExecutionLifecycleState.RECOVERY_RECONCILED,
        validation_ok=True,
        risk_level="LOW",
        dry_run=False,
        recovery_delta_count=1,
    )
    duplicate = classify_recovery_governance(
        lifecycle_state=ExecutionLifecycleState.RECOVERY_CANDIDATE,
        validation_ok=True,
        risk_level="LOW",
        dry_run=False,
        recovery_delta_count=1,
        duplicate_order_risk=True,
    )

    assert terminal.classification == GovernanceClassification.TERMINALIZED
    assert terminal.legal is False
    assert duplicate.classification == GovernanceClassification.RECOVERY_PROHIBITED
    assert duplicate.replay_prohibited is True


def test_lineage_rejects_duplicate_nodes() -> None:
    lineage = build_recovery_lineage(
        source_failed_run_id="run-a",
        trade_date="2026-05-18",
        lifecycle_state="RECOVERY_CANDIDATE",
        timeline={"event_count": 3},
        simulation_artifact={"verdict": "SIMULATION_PASS"},
        governance_report={"classification": "SAFE_SIMULATION_ONLY"},
    )
    lineage["nodes"].append(dict(lineage["nodes"][0]))
    lineage["duplicate_node_ids"] = ["run-a"]

    result = validate_lineage(lineage)
    graph = build_lifecycle_graph(lineage)

    assert result["ok"] is False
    assert result["failures"] == ["duplicate_lineage_node:run-a"]
    assert graph["valid"] is False


def test_replay_outputs_are_stable_after_canonicalization(tmp_path: Path) -> None:
    from core.recovery.recovery_certification import stable_hash

    hashes = []
    for index in range(2):
        output_dir = tmp_path / f"replay_{index}"
        result = subprocess.run(
            [
                sys.executable,
                "scripts/simulate_interrupted_recovery.py",
                "--source-run-id",
                "2026-05-18T093507-0400_71f60c1",
                "--trade-date",
                "2026-05-18",
                "--pretrade-positions",
                str(FIXTURE_DIR / "pretrade_positions.json"),
                "--intended-orders",
                str(FIXTURE_DIR / "intended_orders.json"),
                "--current-broker-state",
                str(FIXTURE_DIR / "current_broker_state.json"),
                "--execution-payload",
                str(FIXTURE_DIR / "execution_payload.json"),
                "--posttrade-reconciliation-status",
                "OK_RECONCILED",
                "--execution-lock-present",
                "--output-dir",
                str(output_dir),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        summary = json.loads((output_dir / "recovery_simulation_summary.json").read_text())
        risk = json.loads((output_dir / "recovery_risk_report.json").read_text())
        transition = json.loads((output_dir / "state_transition_trace.json").read_text())
        hashes.append(stable_hash({"summary": summary, "risk": risk, "transition": transition}))

    assert hashes[0] == hashes[1]


def test_incident_package_manifest_tracks_required_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "incident"
    output_dir.mkdir()
    for name in (
        "execution_timeline.json",
        "recovery_risk_report.json",
        "recovery_governance_report.json",
        "portfolio_drift.json",
        "eventual_settlement.json",
        "lifecycle_summary.md",
        "recovery_decision_trace.md",
        "recovery_lineage.json",
        "lifecycle_graph.json",
        "recovery_certification_summary.json",
    ):
        (output_dir / name).write_text("{}\n", encoding="utf-8")

    manifest = build_incident_package(output_dir=output_dir)

    assert manifest["complete"] is True
    assert (output_dir / "incident_package" / "manifest.json").exists()


def test_certification_reports_missing_artifact_failure(tmp_path: Path) -> None:
    from core.recovery.recovery_certification import certify_recovery_outputs

    result = certify_recovery_outputs(
        output_dir=tmp_path,
        expected_files={"recovery_simulation_summary.json"},
    )

    assert result["ok"] is False
    assert result["failures"] == ["missing_artifact:recovery_simulation_summary.json"]
