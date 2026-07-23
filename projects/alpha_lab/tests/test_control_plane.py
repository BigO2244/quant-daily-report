from __future__ import annotations

import ast
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.cli import main as control_plane_main
from projects.alpha_lab.control_plane.evaluator import (
    EvaluatorSpec,
    TechniqueFamily,
    inspect_evaluator_boundary,
    load_spec,
)
from projects.alpha_lab.control_plane.lifecycle import assess_candidate, build_cio_queue
from projects.alpha_lab.control_plane.models import (
    AccessMode,
    CandidateSnapshot,
    DataRequirement,
    DataStatus,
    EvidenceReference,
    OwnerDecision,
    QueueItemType,
    ResearchVerdict,
    ShadowStatus,
)
from projects.alpha_lab.evaluators.price_families import _summarize_rows
from projects.alpha_lab.evaluators.regime_diagnostics import (
    summarize_regime_observations,
)
from projects.alpha_lab.factory import ContractValidationError, ResearchBoundaryError, canonical_hash


NOW = datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)
HASH = "a" * 64
RESEARCH_GATES = {
    "point_in_time_integrity": True,
    "deterministic_replay": True,
    "benchmark_and_factor_model": True,
    "holdout_integrity": True,
    "costs_and_capacity": True,
    "independent_review": True,
}
SHADOW_GATES = {
    "artifact_freshness": True,
    "nav_continuity": True,
    "signal_and_holdings_validity": True,
    "net_performance_passes_frozen_criteria": True,
    "operational_reliability": True,
    "portfolio_utility": True,
    "adversarial_review": True,
}


def _candidate_dict(**overrides):
    payload = {
        "schema_version": "caerus_alpha_lab_candidate_snapshot_v1",
        "hypothesis_id": "HYP-2026-006",
        "experiment_id": "EXP-2026-0006",
        "title": "Activist event response",
        "technique_family": "EVENT_STUDY",
        "economic_mechanism": "Slow institutional response to activist filings.",
        "classification": "ALPHA",
        "captured_at": "2026-07-20T16:00:00Z",
        "research_verdict": "EVIDENCE_READY_FOR_OWNER_REVIEW",
        "research_gates": dict(RESEARCH_GATES),
        "owner_research_decision": "PENDING",
        "shadow_status": "NOT_REQUESTED",
        "shadow_observation_days": 0,
        "shadow_review_checkpoints": [20, 60],
        "last_reviewed_shadow_checkpoint": 0,
        "shadow_gates": dict(SHADOW_GATES),
        "data_requirements": [],
        "evidence": [
            {"artifact": "evidence/EXP-2026-0006.md", "sha256": HASH, "label": "Alpha Card"}
        ],
    }
    payload.update(overrides)
    payload["source_snapshot_hash"] = canonical_hash(payload)
    return payload


def _candidate(**overrides):
    return CandidateSnapshot.from_dict(_candidate_dict(**overrides))


def test_paid_data_requirement_creates_owner_review_without_purchase():
    data = DataRequirement(
        requirement_id="licensed.activist.graph.v1",
        provider_id="licensed.vendor",
        dataset_id="activist_history",
        purpose="Frozen event study",
        access_mode=AccessMode.TRIAL,
        status=DataStatus.MISSING,
        required_fields=("security_id", "available_at"),
        acceptance_criteria=("99% timestamp reconciliation",),
        estimated_one_time_cost_usd=250.0,
        provider_url="https://vendor.example/data",
        free_alternative="Forward SEC observation only",
    )
    candidate = _candidate(data_requirements=[data.to_dict()])
    assessment = assess_candidate(candidate, assessed_at=NOW)
    assert assessment.state == "BLOCKED_DATA"
    assert assessment.recommendation == "REQUEST_DATA_DECISION"
    assert len(assessment.queue_items) == 1
    item = assessment.queue_items[0]
    assert item.item_type is QueueItemType.DATA_ACCESS_REVIEW
    assert item.payload["purchase_performed"] is False
    assert "APPROVE_REQUEST" in item.options


def test_research_evidence_routes_to_owner_but_never_activates_shadow():
    assessment = assess_candidate(_candidate(), assessed_at=NOW)
    assert assessment.state == "EVIDENCE_READY_FOR_OWNER_REVIEW"
    assert assessment.queue_items[0].item_type is QueueItemType.RESEARCH_DECISION_REVIEW
    assert assessment.to_dict()["promotion_performed"] is False


def test_owner_pursue_still_requires_separate_shadow_approval():
    assessment = assess_candidate(
        _candidate(owner_research_decision="PURSUE"), assessed_at=NOW
    )
    assert assessment.state == "AWAITING_SHADOW_APPROVAL"
    assert assessment.queue_items[0].item_type is QueueItemType.SHADOW_ACTIVATION_REVIEW
    assert assessment.queue_items[0].payload["registry_change_performed"] is False


def test_shadow_checkpoint_and_paper_nomination_are_fail_closed():
    checkpoint = assess_candidate(
        _candidate(
            owner_research_decision="PURSUE",
            shadow_status="OBSERVING",
            shadow_observation_days=20,
        ),
        assessed_at=NOW,
    )
    assert checkpoint.state == "SHADOW_CHECKPOINT_DUE"
    assert "shadow_evidence_missing" in checkpoint.blockers

    evidence = [
        {"artifact": "evidence/EXP-2026-0006.md", "sha256": HASH, "label": "Alpha Card"},
        {"artifact": "shadow/HYP-2026-006.json", "sha256": "b" * 64, "label": "Shadow evidence envelope"},
    ]
    paper = assess_candidate(
        _candidate(
            owner_research_decision="PURSUE",
            shadow_status="COMPLETE",
            shadow_observation_days=60,
            last_reviewed_shadow_checkpoint=20,
            evidence=evidence,
        ),
        assessed_at=NOW,
    )
    assert paper.state == "PAPER_NOMINATION_READY"
    assert paper.queue_items[0].item_type is QueueItemType.PAPER_PROMOTION_REVIEW
    assert paper.queue_items[0].payload["promotion_performed"] is False

    failed_gates = dict(SHADOW_GATES)
    failed_gates["portfolio_utility"] = False
    failed = assess_candidate(
        _candidate(
            owner_research_decision="PURSUE",
            shadow_status="COMPLETE",
            shadow_observation_days=60,
            last_reviewed_shadow_checkpoint=20,
            shadow_gates=failed_gates,
            evidence=evidence,
        ),
        assessed_at=NOW,
    )
    assert failed.state == "SHADOW_CHECKPOINT_DUE"
    assert "shadow_gate_failed:portfolio_utility" in failed.blockers


def test_cio_queue_prioritizes_paper_over_data_reviews():
    data = DataRequirement(
        requirement_id="paid.data.v1",
        provider_id="vendor",
        dataset_id="history",
        purpose="Test",
        access_mode=AccessMode.PAID,
        status=DataStatus.MISSING,
        required_fields=("available_at",),
        acceptance_criteria=("PIT audit passes",),
        provider_url="https://vendor.example",
    )
    blocked = _candidate(data_requirements=[data.to_dict()])
    paper = _candidate(
        hypothesis_id="HYP-2026-007",
        experiment_id="EXP-2026-0007",
        owner_research_decision="PURSUE",
        shadow_status="COMPLETE",
        shadow_observation_days=60,
        last_reviewed_shadow_checkpoint=20,
        evidence=[
            {"artifact": "alpha.md", "sha256": HASH, "label": "Alpha Card"},
            {"artifact": "shadow.json", "sha256": "b" * 64, "label": "Shadow evidence"},
        ],
    )
    queue = build_cio_queue((blocked, paper), generated_at=NOW)
    assert queue["items"][0]["item_type"] == "PAPER_PROMOTION_REVIEW"
    assert queue["purchase_performed"] is False
    assert queue["promotion_performed"] is False
    repeat = build_cio_queue((blocked, paper), generated_at=datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc))
    assert repeat["decision_fingerprint"] == queue["decision_fingerprint"]
    assert repeat["queue_hash"] != queue["queue_hash"]


def test_candidate_hash_tampering_and_local_write_fail_closed(tmp_path, capsys):
    payload = _candidate_dict()
    payload["classification"] = "TAMPERED"
    with pytest.raises(ContractValidationError, match="hash mismatch"):
        CandidateSnapshot.from_dict(payload)

    candidate_path = tmp_path / "candidate_snapshot.json"
    candidate_path.write_text(json.dumps(_candidate_dict()), encoding="utf-8")
    with pytest.raises((FileNotFoundError, ResearchBoundaryError)):
        control_plane_main(
            [
                "build-queue",
                "--candidate",
                str(candidate_path),
                "--write",
                "--repo-root",
                str(tmp_path),
                "--at",
                "2026-07-20T16:00:00Z",
            ]
        )
    assert not (tmp_path / "outputs").exists()


def test_evaluator_contract_is_bounded_and_hash_checked(tmp_path):
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v1",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "event_v1",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.event_v1",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "primary_metric": "residual_return",
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
    }
    spec = EvaluatorSpec.from_dict({**unsigned, "spec_hash": canonical_hash(unsigned)})
    assert spec.technique_family is TechniqueFamily.EVENT_STUDY
    with pytest.raises(ResearchBoundaryError):
        EvaluatorSpec.from_dict(
            {**unsigned, "module": "brokers.bad", "spec_hash": canonical_hash({**unsigned, "module": "brokers.bad"})}
        )

    safe = tmp_path / "safe.py"
    safe.write_text("def evaluate(packet, phase):\n    return {}\n", encoding="utf-8")
    assert inspect_evaluator_boundary(safe)["status"] == "PASS"
    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text("from brokers.alpaca_broker import AlpacaBroker\n", encoding="utf-8")
    boundary = inspect_evaluator_boundary(unsafe)
    assert boundary["status"] == "FAIL"
    assert boundary["findings"] == ["forbidden_import:brokers.alpaca_broker"]


def test_eight_newly_frozen_evaluator_specs_are_hash_valid():
    spec_root = (
        Path(__file__).parents[1] / "experiments" / "evaluator_specs"
    )
    expected = {
        "HYP-2026-001",
        "HYP-2026-006",
        "HYP-2026-007",
        "HYP-2026-008",
        "HYP-2026-009",
        "HYP-2026-010",
        "HYP-2026-011",
        "HYP-2026-012",
    }
    loaded = {load_spec(path).hypothesis_id for path in sorted(spec_root.glob("*.json"))}
    assert loaded == expected
    for module_name in (
        "blocked_families.py",
        "price_families.py",
    ):
        boundary = inspect_evaluator_boundary(
            Path(__file__).parents[1] / "evaluators" / module_name
        )
        assert boundary["status"] == "PASS"


def test_price_family_primary_metric_includes_stress_cost_and_capacity():
    rows = [
        {
            "variant_id": "primary",
            "decision_date": "2020-01-31",
            "return_end_date": "2020-02-28",
            "sample_phase": "VALIDATION",
            "candidate_pessimistic_return": 0.02,
            "benchmark_pessimistic_return": 0.01,
            "candidate_zero_incremental_return": 0.021,
            "benchmark_zero_incremental_return": 0.01,
            "selected_min_dollar_adv": 2_000_000.0,
            "selected_count": 10,
            "market_20": 0.01,
            "market_63": 0.02,
            "market_vol_20": 0.15,
        },
        {
            "variant_id": "primary",
            "decision_date": "2020-02-28",
            "return_end_date": "2020-03-31",
            "sample_phase": "VALIDATION",
            "candidate_pessimistic_return": 0.018,
            "benchmark_pessimistic_return": 0.01,
            "candidate_zero_incremental_return": 0.019,
            "benchmark_zero_incremental_return": 0.01,
            "selected_min_dollar_adv": 2_000_000.0,
            "selected_count": 10,
            "market_20": -0.01,
            "market_63": 0.01,
            "market_vol_20": 0.18,
        },
    ]
    summary = _summarize_rows(
        rows,
        annualization=12,
        base_one_way_cost_bps=15.0,
        stress_one_way_cost_bps=30.0,
        capacity_fraction_of_adv=0.05,
    )
    validation = summary["phases"]["VALIDATION"]["cost_scenarios"]
    assert validation["stress"]["pessimistic"][
        "annualized_excess_return_after_costs"
    ] < validation["base"]["pessimistic"][
        "annualized_excess_return_after_costs"
    ]
    assert summary[
        "worst_case_validation_annualized_excess_return_after_costs"
    ] == validation["stress"]["pessimistic"][
        "annualized_excess_return_after_costs"
    ]
    assert summary["conservative_validation_capacity_dollars"] == 1_000_000
    assert summary["capacity_supports_one_million_dollars"] is True


def test_control_plane_has_no_production_imports_or_order_calls():
    root = Path(__file__).parents[1] / "control_plane"
    forbidden_modules = {
        "alpha_stack",
        "brokers",
        "core",
        "daily_quant_report",
        "deploy",
        "reconciliation",
        "scripts",
    }
    forbidden_calls = {
        "cancel_order",
        "submit_market_order",
        "submit_option_limit_order",
        "submit_option_market_order",
    }
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(
                    alias.name.split(".")[0] in forbidden_modules for alias in node.names
                ), path
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden_modules, path
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "attr", getattr(node.func, "id", ""))
                assert name not in forbidden_calls, path


def _regime_row(index: int, regime: str = "bull_trend"):
    return {
        "observation_id": "obs-{}".format(index),
        "decision_at": "2020-01-01T16:00:00Z",
        "regime_available_at": "2020-01-01T16:00:00Z",
        "return_start_at": "2020-01-02T14:30:00Z",
        "return_end_at": "2020-01-03T21:00:00Z",
        "regime": regime,
        "candidate_return": 0.02,
        "benchmark_return": 0.01,
    }


def test_regime_diagnostics_enforce_point_in_time_and_sample_thresholds():
    rows = [_regime_row(index) for index in range(31)]
    rows.extend(_regime_row(100 + index, "bear_trend") for index in range(10))
    payload = summarize_regime_observations(rows)
    assert payload["observation_count"] == 41
    assert payload["total_coverage_sufficient"] is False
    assert payload["decision_grade_regimes"] == ["bull_trend"]
    assert payload["regimes"]["bull_trend"]["confidence"] == "HIGH"
    assert payload["regimes"]["bear_trend"]["confidence"] == "MEDIUM"
    assert payload["regime_selection_coverage_ready"] is False
    assert payload["regime_selection_claim_permitted"] is False
    assert payload["primary_alpha_claim_permitted_from_regime_slice"] is False
    assert payload["allocation_change_performed"] is False

    lookahead = _regime_row(999)
    lookahead["regime_available_at"] = "2020-01-02T16:00:00Z"
    with pytest.raises(ContractValidationError, match="not available"):
        summarize_regime_observations([lookahead])

    duplicate = [_regime_row(1), _regime_row(1)]
    with pytest.raises(ContractValidationError, match="duplicate observation"):
        summarize_regime_observations(duplicate)
