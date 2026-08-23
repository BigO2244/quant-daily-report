from __future__ import annotations

import ast
import hashlib
import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.cli import main as control_plane_main
from projects.alpha_lab.control_plane.evaluator import (
    EvaluationPhase,
    EvaluatorSpec,
    TechniqueFamily,
    inspect_evaluator_boundary,
    load_spec,
    run_evaluator,
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
    REQUIRED_RESEARCH_GATES_V2,
    ShadowStatus,
)
from projects.alpha_lab.evaluators.price_families import _summarize_rows
from projects.alpha_lab.evaluators.regime_diagnostics import (
    summarize_regime_observations,
)
from projects.alpha_lab.factory import (
    ContractValidationError,
    ExpectedDirection,
    GlobalResearchLedger,
    HypothesisFamily,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchBoundaryError,
    ResearchExperiment,
    ResearchPhase,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    canonical_hash,
    deterministic_attempt_id,
    deterministic_trial_id,
)


NOW = datetime(2026, 7, 20, 16, 0, tzinfo=timezone.utc)
HASH = "a" * 64
EXACT_LEDGER_PROJECTION_RESEARCH_GATES = frozenset(
    {
        "family_lineage_integrity",
        "frozen_spec_integrity",
        "point_in_time_integrity",
        "deterministic_replay",
        "complete_trial_census",
        "trial_budget_compliant",
        "family_inference_pass",
        "exploratory_wave_fdr_pass",
        "locked_validation_economic_pass",
        "benchmark_and_factor_model",
        "costs_capacity_and_concentration",
        "challenge_epoch_integrity",
        "challenge_confirmation_pass",
        "authenticated_owner_ratification",
        "authenticated_preregistration_authorship",
        "authenticated_data_certification",
        "legacy_definition_complete",
        "independent_review",
        "artifact_and_event_chain_integrity",
    }
)
RESEARCH_GATES = {name: True for name in REQUIRED_RESEARCH_GATES_V2}
RESEARCH_PROJECTION = {
    "event_chain_head": HASH,
    "families": [
        {
            "family_id": "FAM-2026-006",
            "hypothesis_ids": ["HYP-2026-006"],
            "decision_grade_ready": True,
            "research_gates": dict(RESEARCH_GATES),
        },
        {
            "family_id": "FAM-2026-007",
            "hypothesis_ids": ["HYP-2026-007"],
            "decision_grade_ready": True,
            "research_gates": dict(RESEARCH_GATES),
        },
    ],
}
RESEARCH_PROJECTION_HASH = canonical_hash(RESEARCH_PROJECTION)
SHADOW_GATES = {
    "artifact_freshness": True,
    "nav_continuity": True,
    "signal_and_holdings_validity": True,
    "net_performance_passes_frozen_criteria": True,
    "operational_reliability": True,
    "portfolio_utility": True,
    "adversarial_review": True,
}


@pytest.fixture
def research_ledger(tmp_path, monkeypatch):
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    ledger = GlobalResearchLedger(
        ledger_dir / "research_events.v1.jsonl", research_root=tmp_path
    )
    family_ids = ("FAM-2026-006", "FAM-2026-007")
    ledger.register_wave(
        ResearchWave(
            wave_id="WAVE-2026-001",
            track=InferenceTrack.EXPLORATORY,
            family_ids=family_ids,
            method=MultipleTestingMethod.BENJAMINI_YEKUTIELI,
            alpha_or_q=0.10,
            registered_at=NOW,
            policy_artifact="policy/wave.json",
            policy_sha256=hashlib.sha256(b"wave").hexdigest(),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    for ordinal in (6, 7):
        family_id = "FAM-2026-{:03d}".format(ordinal)
        hypothesis_id = "HYP-2026-{:03d}".format(ordinal)
        experiment_id = "EXP-2026-{:04d}".format(ordinal)
        ledger.register_family(
            HypothesisFamily(
                family_id=family_id,
                wave_id="WAVE-2026-001",
                challenge_epoch_id="CHALLENGE-2026-{:03d}".format(ordinal),
                name="Synthetic lifecycle family",
                economic_mechanism="A falsifiable mechanism.",
                family_scope_hash=hashlib.sha256(family_id.encode()).hexdigest(),
                primary_metric="residual_return",
                benchmark="frozen benchmark",
                expected_direction=ExpectedDirection.GREATER_THAN,
                null_value=0.0,
                economic_hurdle=0.01,
                primary_variant_id="primary",
                maximum_trial_units=1,
                selection_trial_budget=0,
                within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
                family_alpha=0.05,
                registered_at=NOW,
                source_artifact="hypotheses/{}.md".format(hypothesis_id),
                source_sha256=hashlib.sha256(hypothesis_id.encode()).hexdigest(),
                owner_ratified=True,
            ),
            recorded_at=NOW,
        )
        ledger.register_experiment(
            ResearchExperiment(
                experiment_id=experiment_id,
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                parent_experiment_ids=(),
                generated_after_results=False,
                generation_reason="INITIAL",
                frozen_primary_metric="residual_return",
                registered_at=NOW,
                source_artifact="experiments/{}.json".format(experiment_id),
                source_sha256=hashlib.sha256(experiment_id.encode()).hexdigest(),
                owner_ratified=True,
            ),
            recorded_at=NOW,
        )
        model_sha = hashlib.sha256(
            "{}-model".format(experiment_id).encode()
        ).hexdigest()
        ledger.register_run(
            ResearchRun(
                attempt_id=deterministic_attempt_id(model_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                run_id="{}-model".format(experiment_id),
                run_class=ResearchRunClass.MODEL_TRIAL,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=NOW,
                source_artifact="runs/{}-model.json".format(experiment_id),
                source_sha256=model_sha,
                statistical_trial_id=deterministic_trial_id(family_id, 1),
                primary_metric="residual_return",
                variant_id="primary",
                variant_definition_hash=hashlib.sha256(b"primary").hexdigest(),
                consumes_trial_budget=True,
                preregistered=True,
                code_sha256=hashlib.sha256(b"code").hexdigest(),
                data_snapshot_sha256=hashlib.sha256(b"discovery-input").hexdigest(),
                evaluator_spec_sha256=hashlib.sha256(b"model-spec").hexdigest(),
                effective_sample_floor=30,
            ),
            recorded_at=NOW,
        )
        challenge_sha = hashlib.sha256(
            "{}-challenge".format(experiment_id).encode()
        ).hexdigest()
        ledger.register_run(
            ResearchRun(
                attempt_id=deterministic_attempt_id(challenge_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_id,
                run_id="{}-challenge".format(experiment_id),
                run_class=ResearchRunClass.CHALLENGE_READ,
                phase=ResearchPhase.CHALLENGE,
                occurred_at=NOW,
                source_artifact="runs/{}-challenge.json".format(experiment_id),
                source_sha256=challenge_sha,
                statistical_trial_id=deterministic_trial_id(family_id, 900),
                primary_metric="residual_return",
                variant_id="primary",
                variant_definition_hash=hashlib.sha256(b"primary").hexdigest(),
                preregistered=True,
                code_sha256=hashlib.sha256(b"challenge-code").hexdigest(),
                data_snapshot_sha256=hashlib.sha256(b"challenge-input").hexdigest(),
                evaluator_spec_sha256=hashlib.sha256(b"challenge-spec").hexdigest(),
                effective_sample_floor=30,
            ),
            recorded_at=NOW,
        )
    projection = {
        **RESEARCH_PROJECTION,
        "event_chain_head": ledger.store.read_all()[-1].event_hash,
    }
    monkeypatch.setattr(ledger, "project", lambda: projection)
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "RESEARCH_PROJECTION", projection)
    monkeypatch.setattr(module, "RESEARCH_PROJECTION_HASH", canonical_hash(projection))
    return ledger


def _evaluator_contract_fields(*variant_ids):
    frozen = [
        {
            "variant_id": variant_id,
            "variant_definition_hash": hashlib.sha256(
                variant_id.encode("utf-8")
            ).hexdigest(),
        }
        for variant_id in variant_ids
    ]
    return {
        "frozen_variants": frozen,
        "search_census": [],
        "search_census_hash": canonical_hash([]),
        "selection_trial_units": 0,
    }


def _candidate_dict(**overrides):
    payload = {
        "schema_version": "caerus_alpha_lab_candidate_snapshot_v2",
        "hypothesis_id": "HYP-2026-006",
        "experiment_id": "EXP-2026-0006",
        "family_id": "FAM-2026-006",
        "ledger_event_chain_head": RESEARCH_PROJECTION["event_chain_head"],
        "ledger_projection_hash": RESEARCH_PROJECTION_HASH,
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


def test_research_evidence_routes_to_owner_but_never_activates_shadow(research_ledger):
    assessment = assess_candidate(
        _candidate(), assessed_at=NOW, research_ledger=research_ledger
    )
    assert assessment.state == "EVIDENCE_READY_FOR_OWNER_REVIEW"
    assert assessment.queue_items[0].item_type is QueueItemType.RESEARCH_DECISION_REVIEW
    assert assessment.to_dict()["promotion_performed"] is False


def test_owner_review_candidate_cannot_self_attest_without_ledger_projection():
    assessment = assess_candidate(_candidate(), assessed_at=NOW)
    assert assessment.state == "RESEARCH_GATES_FAILED"
    assert "canonical_research_ledger_projection_missing" in assessment.blockers


def test_owner_review_verdict_requires_exact_projection_research_gate_set():
    assert REQUIRED_RESEARCH_GATES_V2 == EXACT_LEDGER_PROJECTION_RESEARCH_GATES

    valid_projection_gates = {
        name: True for name in EXACT_LEDGER_PROJECTION_RESEARCH_GATES
    }
    candidate = _candidate(research_gates=valid_projection_gates)
    assert set(candidate.research_gates) == EXACT_LEDGER_PROJECTION_RESEARCH_GATES

    gates = dict(valid_projection_gates)
    del gates["complete_trial_census"]
    with pytest.raises(ContractValidationError, match="mandatory v2 gates"):
        _candidate(research_gates=gates)

    gates = dict(valid_projection_gates)
    gates["not_a_ledger_projection_gate"] = True
    with pytest.raises(ContractValidationError, match="unexpected v2 gates"):
        _candidate(research_gates=gates)


def test_owner_pursue_still_requires_separate_shadow_approval(research_ledger):
    assessment = assess_candidate(
        _candidate(owner_research_decision="PURSUE"),
        assessed_at=NOW,
        research_ledger=research_ledger,
    )
    assert assessment.state == "AWAITING_SHADOW_APPROVAL"
    assert assessment.queue_items[0].item_type is QueueItemType.SHADOW_ACTIVATION_REVIEW
    assert assessment.queue_items[0].payload["registry_change_performed"] is False


def test_shadow_checkpoint_and_paper_nomination_are_fail_closed(research_ledger):
    checkpoint = assess_candidate(
        _candidate(
            owner_research_decision="PURSUE",
            shadow_status="OBSERVING",
            shadow_observation_days=20,
        ),
        assessed_at=NOW,
        research_ledger=research_ledger,
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
        research_ledger=research_ledger,
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
        research_ledger=research_ledger,
    )
    assert failed.state == "SHADOW_CHECKPOINT_DUE"
    assert "shadow_gate_failed:portfolio_utility" in failed.blockers


def test_cio_queue_prioritizes_paper_over_data_reviews(research_ledger):
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
        family_id="FAM-2026-007",
        owner_research_decision="PURSUE",
        shadow_status="COMPLETE",
        shadow_observation_days=60,
        last_reviewed_shadow_checkpoint=20,
        evidence=[
            {"artifact": "alpha.md", "sha256": HASH, "label": "Alpha Card"},
            {"artifact": "shadow.json", "sha256": "b" * 64, "label": "Shadow evidence"},
        ],
    )
    queue = build_cio_queue(
        (blocked, paper), generated_at=NOW, research_ledger=research_ledger
    )
    assert queue["items"][0]["item_type"] == "PAPER_PROMOTION_REVIEW"
    assert queue["purchase_performed"] is False
    assert queue["promotion_performed"] is False
    repeat = build_cio_queue(
        (blocked, paper),
        generated_at=datetime(2026, 7, 21, 16, 0, tzinfo=timezone.utc),
        research_ledger=research_ledger,
    )
    assert repeat["decision_fingerprint"] == queue["decision_fingerprint"]
    assert repeat["queue_hash"] != queue["queue_hash"]


def test_candidate_hash_tampering_and_local_write_fail_closed(tmp_path, capsys):
    payload = _candidate_dict()
    payload["classification"] = "TAMPERED"
    with pytest.raises(ContractValidationError, match="hash mismatch"):
        CandidateSnapshot.from_dict(payload)

    candidate_path = tmp_path / "candidate_snapshot.json"
    candidate_path.write_text(json.dumps(_candidate_dict()), encoding="utf-8")
    with pytest.raises((ContractValidationError, FileNotFoundError, ResearchBoundaryError)):
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
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "event_v1",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.event_v1",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "primary_metric": "residual_return",
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
        "family_id": "FAM-2026-006",
        "experiment_id": "EXP-2026-0006",
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.0,
        "inference_method": "ROMANO_WOLF",
        "inference_alpha_or_q": 0.10,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": HASH,
        **_evaluator_contract_fields("primary", "placebo"),
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


def test_evaluator_requires_one_registered_trial_id_per_variant(tmp_path, monkeypatch):
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "event_v1",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.synthetic_test",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "primary_metric": "residual_return",
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
        "family_id": "FAM-2026-006",
        "experiment_id": "EXP-2026-0006",
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.0,
        "inference_method": "ROMANO_WOLF",
        "inference_alpha_or_q": 0.10,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": hashlib.sha256(
            b"def evaluate(packet, phase):\n    return {}\n"
        ).hexdigest(),
        **_evaluator_contract_fields("primary", "placebo"),
    }
    spec = EvaluatorSpec.from_dict({**unsigned, "spec_hash": canonical_hash(unsigned)})
    source = tmp_path / "synthetic_test.py"
    source.write_text("def evaluate(packet, phase):\n    return {}\n", encoding="utf-8")
    module = types.ModuleType(spec.module)
    module.__file__ = str(source)
    module.evaluate = lambda packet, phase: {
        "variant_count": 2,
        "primary_metric_name": "residual_return",
        "orders_submitted": False,
        "search_census": [],
        "search_census_hash": canonical_hash([]),
        "selection_trial_units": 0,
        "variants": [
            {
                "variant_id": "primary",
                "variant_definition_hash": hashlib.sha256(b"primary").hexdigest(),
                "evidence_verdict": "NEGATIVE",
                "primary_metric_value": -0.01,
                "p_value": None,
                "inference_eligible": False,
                "ineligibility_reasons": ["NO_VALID_P_VALUE"],
                "stress_scenario_pass": False,
                "capacity_and_concentration_pass": False,
                "effective_sample_size": 20,
            },
            {
                "variant_id": "placebo",
                "variant_definition_hash": hashlib.sha256(b"placebo").hexdigest(),
                "evidence_verdict": "NEGATIVE",
                "primary_metric_value": -0.02,
                "p_value": None,
                "inference_eligible": False,
                "ineligibility_reasons": ["NO_VALID_P_VALUE"],
                "stress_scenario_pass": False,
                "capacity_and_concentration_pass": False,
                "effective_sample_size": 20,
            },
        ],
    }
    monkeypatch.setattr(
        "projects.alpha_lab.control_plane.evaluator.importlib.import_module",
        lambda name: module,
    )
    packet = {
        "data_gate_status": "READY_FOR_FROZEN_EVALUATOR",
        "hypothesis_id": spec.hypothesis_id,
        "assets": {"event_tape_v1": {}},
    }
    with pytest.raises(ContractValidationError, match="every evaluator variant"):
        run_evaluator(
            spec=spec,
            input_packet=packet,
            phase=EvaluationPhase.DISCOVERY,
            registered_trial_ids=("FAM-2026-006-T001",),
        )
    result = run_evaluator(
        spec=spec,
        input_packet=packet,
        phase=EvaluationPhase.DISCOVERY,
        registered_trial_ids=("FAM-2026-006-T001", "FAM-2026-006-T002"),
    )
    assert result["registered_trial_ids"] == [
        "FAM-2026-006-T001",
        "FAM-2026-006-T002",
    ]


def test_challenge_evaluator_rejects_boolean_only_authority(tmp_path, monkeypatch):
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "event_v1",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.synthetic_test",
        "callable_name": "evaluate",
        "maximum_variants": 1,
        "primary_metric": "residual_return",
        "data_contract_ids": ["event_tape_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
        "family_id": "FAM-2026-006",
        "experiment_id": "EXP-2026-0006",
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.0,
        "inference_method": "ROMANO_WOLF",
        "inference_alpha_or_q": 0.10,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": HASH,
        **_evaluator_contract_fields("primary"),
    }
    spec = EvaluatorSpec.from_dict({**unsigned, "spec_hash": canonical_hash(unsigned)})
    packet = {
        "data_gate_status": "READY_FOR_FROZEN_EVALUATOR",
        "hypothesis_id": spec.hypothesis_id,
        "assets": {"event_tape_v1": {}},
    }
    with pytest.raises(ContractValidationError, match="canonical ledger access event"):
        run_evaluator(
            spec=spec,
            input_packet=packet,
            phase=EvaluationPhase.CHALLENGE,
            registered_trial_ids=("FAM-2026-006-T900",),
        )


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
