from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.cli import _seal_candidate
from projects.alpha_lab.control_plane.lifecycle import assess_candidate, build_cio_queue
from projects.alpha_lab.control_plane.models import (
    CandidateSnapshot,
    REQUIRED_RESEARCH_GATES_V2,
)
from projects.alpha_lab.factory import (
    ContractValidationError,
    ExpectedDirection,
    GlobalResearchLedger,
    HypothesisFamily,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchExperiment,
    ResearchPhase,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    canonical_hash,
    deterministic_attempt_id,
    deterministic_trial_id,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
FAMILY_ID = "FAM-2026-006"
HYPOTHESIS_ID = "HYP-2026-006"
EXPERIMENT_ID = "EXP-2026-0006"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ledger(tmp_path: Path) -> GlobalResearchLedger:
    ledger_dir = tmp_path / "ledger"
    ledger_dir.mkdir()
    ledger = GlobalResearchLedger(
        ledger_dir / "research_events.v1.jsonl", research_root=tmp_path
    )
    ledger.register_wave(
        ResearchWave(
            wave_id="WAVE-2026-001",
            track=InferenceTrack.EXPLORATORY,
            family_ids=(FAMILY_ID,),
            method=MultipleTestingMethod.BENJAMINI_YEKUTIELI,
            alpha_or_q=0.10,
            registered_at=NOW,
            policy_artifact="policy/wave.json",
            policy_sha256=_sha("wave"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    ledger.register_family(
        HypothesisFamily(
            family_id=FAMILY_ID,
            wave_id="WAVE-2026-001",
            challenge_epoch_id="CHALLENGE-2026-001",
            name="Lifecycle binding family",
            economic_mechanism="A falsifiable mechanism.",
            family_scope_hash=_sha("scope"),
            primary_metric="active_return_after_costs",
            benchmark="frozen benchmark",
            expected_direction=ExpectedDirection.GREATER_THAN,
            null_value=0.0,
            economic_hurdle=0.01,
            primary_variant_id="primary",
            maximum_trial_units=1,
            selection_trial_budget=0,
            within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
            family_alpha=0.10,
            registered_at=NOW,
            source_artifact="hypotheses/HYP-2026-006.md",
            source_sha256=_sha("family"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    ledger.register_experiment(
        ResearchExperiment(
            experiment_id=EXPERIMENT_ID,
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="INITIAL",
            frozen_primary_metric="active_return_after_costs",
            registered_at=NOW,
            source_artifact="experiments/EXP-2026-0006.json",
            source_sha256=_sha("experiment"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    model_run_sha = _sha("model-run")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(model_run_sha),
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            experiment_id=EXPERIMENT_ID,
            run_id="model-run",
            run_class=ResearchRunClass.MODEL_TRIAL,
            phase=ResearchPhase.DISCOVERY,
            occurred_at=NOW,
            source_artifact="runs/model-run.json",
            source_sha256=model_run_sha,
            statistical_trial_id=deterministic_trial_id(FAMILY_ID, 1),
            primary_metric="active_return_after_costs",
            variant_id="primary",
            variant_definition_hash=_sha("primary"),
            consumes_trial_budget=True,
            preregistered=True,
            code_sha256=_sha("code"),
            data_snapshot_sha256=_sha("discovery-input"),
            evaluator_spec_sha256=_sha("model-spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW,
    )
    run_sha = _sha("challenge-run")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(run_sha),
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            experiment_id=EXPERIMENT_ID,
            run_id="challenge-run",
            run_class=ResearchRunClass.CHALLENGE_READ,
            phase=ResearchPhase.CHALLENGE,
            occurred_at=NOW,
            source_artifact="runs/challenge-run.json",
            source_sha256=run_sha,
            statistical_trial_id=deterministic_trial_id(FAMILY_ID, 900),
            primary_metric="active_return_after_costs",
            variant_id="primary",
            variant_definition_hash=_sha("primary"),
            preregistered=True,
            code_sha256=_sha("code"),
            data_snapshot_sha256=_sha("challenge-input"),
            evaluator_spec_sha256=_sha("challenge-spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW,
    )
    return ledger


def _decision_grade_projection(ledger: GlobalResearchLedger) -> dict:
    gates = {name: True for name in REQUIRED_RESEARCH_GATES_V2}
    return {
        "schema_version": "caerus_alpha_lab_global_research_projection_v1",
        "event_chain_head": ledger.store.read_all()[-1].event_hash,
        "families": [
            {
                "family_id": FAMILY_ID,
                "hypothesis_ids": [HYPOTHESIS_ID],
                "decision_grade_ready": True,
                "research_gates": gates,
            }
        ],
    }


def _candidate_payload(
    projection: dict,
    *,
    family_id: str = FAMILY_ID,
    hypothesis_id: str = HYPOTHESIS_ID,
    experiment_id: str = EXPERIMENT_ID,
) -> dict:
    gates = {name: True for name in REQUIRED_RESEARCH_GATES_V2}
    payload = {
        "schema_version": "caerus_alpha_lab_candidate_snapshot_v2",
        "hypothesis_id": hypothesis_id,
        "experiment_id": experiment_id,
        "family_id": family_id,
        "ledger_event_chain_head": projection["event_chain_head"],
        "ledger_projection_hash": canonical_hash(projection),
        "title": "Lifecycle binding candidate",
        "technique_family": "EVENT_STUDY",
        "economic_mechanism": "A falsifiable mechanism.",
        "classification": "ALPHA",
        "captured_at": "2026-08-22T16:00:00Z",
        "research_verdict": "EVIDENCE_READY_FOR_OWNER_REVIEW",
        "research_gates": gates,
        "owner_research_decision": "PENDING",
        "shadow_status": "NOT_REQUESTED",
        "shadow_observation_days": 0,
        "shadow_review_checkpoints": [20, 60],
        "last_reviewed_shadow_checkpoint": 0,
        "shadow_gates": {"artifact_freshness": True},
        "data_requirements": [],
        "evidence": [
            {"artifact": "evidence/EXP-2026-0006.md", "sha256": _sha("evidence"), "label": "Alpha Card"}
        ],
    }
    payload["source_snapshot_hash"] = canonical_hash(payload)
    return payload


def test_lifecycle_requires_an_actual_ledger_not_a_projection_mapping(tmp_path):
    ledger = _ledger(tmp_path)
    projection = _decision_grade_projection(ledger)
    candidate = CandidateSnapshot.from_dict(_candidate_payload(projection))

    with pytest.raises(ContractValidationError, match="actual GlobalResearchLedger"):
        assess_candidate(
            candidate,
            assessed_at=NOW,
            research_ledger=projection,  # type: ignore[arg-type]
        )
    with pytest.raises(ContractValidationError, match="actual GlobalResearchLedger"):
        build_cio_queue(
            (candidate,),
            generated_at=NOW,
            research_ledger=projection,  # type: ignore[arg-type]
        )
    draft = _candidate_payload(projection)
    draft.pop("source_snapshot_hash")
    with pytest.raises(ContractValidationError, match="actual GlobalResearchLedger"):
        _seal_candidate(
            draft,
            research_ledger=projection,  # type: ignore[arg-type]
        )


def test_assessment_binds_family_hypothesis_and_experiment_exactly(
    tmp_path, monkeypatch
):
    ledger = _ledger(tmp_path)
    projection = _decision_grade_projection(ledger)
    monkeypatch.setattr(ledger, "project", lambda: projection)

    accepted = assess_candidate(
        CandidateSnapshot.from_dict(_candidate_payload(projection)),
        assessed_at=NOW,
        research_ledger=ledger,
    )
    assert accepted.state == "EVIDENCE_READY_FOR_OWNER_REVIEW"

    for mismatch in (
        {"family_id": "FAM-2099-999"},
        {"hypothesis_id": "HYP-2099-999"},
        {"experiment_id": "EXP-2099-9999"},
    ):
        mismatched = assess_candidate(
            CandidateSnapshot.from_dict(_candidate_payload(projection, **mismatch)),
            assessed_at=NOW,
            research_ledger=ledger,
        )
        assert mismatched.state == "RESEARCH_GATES_FAILED"
        assert (
            "candidate_not_bound_to_decision_grade_ledger_state"
            in mismatched.blockers
        )


def test_sealing_rejects_an_unregistered_experiment(tmp_path, monkeypatch):
    ledger = _ledger(tmp_path)
    projection = _decision_grade_projection(ledger)
    monkeypatch.setattr(ledger, "project", lambda: projection)
    draft = _candidate_payload(projection)
    draft.pop("source_snapshot_hash")
    draft.pop("ledger_event_chain_head")
    draft.pop("ledger_projection_hash")
    draft["schema_version"] = "caerus_alpha_lab_candidate_snapshot_v1"

    sealed = _seal_candidate(draft, research_ledger=ledger)
    assert sealed["experiment_id"] == EXPERIMENT_ID
    assert sealed["ledger_projection_hash"] == canonical_hash(projection)

    draft["experiment_id"] = "EXP-2099-9999"
    with pytest.raises(ContractValidationError, match="candidate lineage"):
        _seal_candidate(draft, research_ledger=ledger)


def test_registered_evidence_free_experiment_cannot_piggyback_on_family_evidence(
    tmp_path, monkeypatch
):
    ledger = _ledger(tmp_path)
    evidence_free_experiment_id = "EXP-2026-0007"
    ledger.register_experiment(
        ResearchExperiment(
            experiment_id=evidence_free_experiment_id,
            family_id=FAMILY_ID,
            hypothesis_id=HYPOTHESIS_ID,
            parent_experiment_ids=(EXPERIMENT_ID,),
            generated_after_results=False,
            generation_reason="PRE_RESULT_REFINEMENT",
            frozen_primary_metric="active_return_after_costs",
            registered_at=NOW,
            source_artifact="experiments/EXP-2026-0007.json",
            source_sha256=_sha("evidence-free-experiment"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    projection = _decision_grade_projection(ledger)
    monkeypatch.setattr(ledger, "project", lambda: projection)

    piggybacked = assess_candidate(
        CandidateSnapshot.from_dict(
            _candidate_payload(
                projection, experiment_id=evidence_free_experiment_id
            )
        ),
        assessed_at=NOW,
        research_ledger=ledger,
    )
    assert piggybacked.state == "RESEARCH_GATES_FAILED"
    assert (
        "candidate_not_bound_to_decision_grade_ledger_state"
        in piggybacked.blockers
    )

    draft = _candidate_payload(
        projection, experiment_id=evidence_free_experiment_id
    )
    draft.pop("source_snapshot_hash")
    with pytest.raises(ContractValidationError, match="candidate lineage"):
        _seal_candidate(draft, research_ledger=ledger)


def test_candidate_template_declares_family_identity():
    template_path = Path(__file__).parents[1] / "templates/CANDIDATE_SNAPSHOT.json"
    template = json.loads(template_path.read_text(encoding="utf-8"))
    assert template["family_id"] == "FAM-YYYY-NNN"
