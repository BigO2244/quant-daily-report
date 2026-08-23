from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from projects.alpha_lab.factory import (
    ChallengeEpoch,
    ContractValidationError,
    ExpectedDirection,
    EventStoreIntegrityError,
    FamilyInference,
    GlobalResearchLedger,
    HoldoutAccess,
    HypothesisFamily,
    IndependentResearchReview,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchPhase,
    ResearchExperiment,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    TrialOutcome,
    TrialResult,
    benjamini_hochberg,
    benjamini_yekutieli,
    deterministic_access_id,
    deterministic_attempt_id,
    deterministic_trial_id,
    holm_bonferroni,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _ledger(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir()
    return GlobalResearchLedger(directory / "research_events.v1.jsonl", research_root=tmp_path)


def _wave(*family_ids: str, owner_ratified: bool = True) -> ResearchWave:
    return ResearchWave(
        wave_id="WAVE-2026-001",
        track=InferenceTrack.EXPLORATORY,
        family_ids=tuple(family_ids),
        method=MultipleTestingMethod.BENJAMINI_YEKUTIELI,
        alpha_or_q=0.10,
        registered_at=NOW,
        policy_artifact="policy/wave.json",
        policy_sha256=_sha("wave"),
        owner_ratified=owner_ratified,
    )


def _family(
    *,
    maximum_trial_units: int = 2,
    owner_ratified: bool = True,
    legacy_definition_blockers=(),
):
    return HypothesisFamily(
        family_id="FAM-2026-001",
        wave_id="WAVE-2026-001",
        challenge_epoch_id="CHALLENGE-2026-001",
        name="Test family",
        economic_mechanism="A falsifiable mechanism.",
        family_scope_hash=_sha("scope"),
        primary_metric="active_return_after_costs",
        benchmark="frozen benchmark",
        expected_direction=ExpectedDirection.GREATER_THAN,
        null_value=0.0,
        economic_hurdle=0.01,
        primary_variant_id="variant-1",
        maximum_trial_units=maximum_trial_units,
        selection_trial_budget=0,
        within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
        family_alpha=0.10,
        registered_at=NOW,
        source_artifact="hypotheses/HYP-2026-001.md",
        source_sha256=_sha("hypothesis"),
        owner_ratified=owner_ratified,
        legacy_definition_blockers=tuple(legacy_definition_blockers),
    )


def _data_gate(label: str = "gate") -> ResearchRun:
    source_sha = _sha(label)
    return ResearchRun(
        attempt_id=deterministic_attempt_id(source_sha),
        family_id="FAM-2026-001",
        hypothesis_id="HYP-2026-001",
        experiment_id="EXP-2026-0001",
        run_id=label,
        run_class=ResearchRunClass.DATA_GATE,
        phase=ResearchPhase.DATA,
        occurred_at=NOW,
        source_artifact="runs/{}/run_manifest.json".format(label),
        source_sha256=source_sha,
    )


def _model_trial(ordinal: int) -> ResearchRun:
    trial_id = deterministic_trial_id("FAM-2026-001", ordinal)
    source_sha = _sha("trial-{}".format(ordinal))
    return ResearchRun(
        attempt_id=deterministic_attempt_id(source_sha),
        family_id="FAM-2026-001",
        hypothesis_id="HYP-2026-001",
        experiment_id="EXP-2026-0001",
        run_id="run-{}".format(ordinal),
        run_class=ResearchRunClass.MODEL_TRIAL,
        phase=ResearchPhase.DISCOVERY,
        occurred_at=NOW + timedelta(minutes=ordinal),
        source_artifact="results/trial-{}.json".format(ordinal),
        source_sha256=source_sha,
        statistical_trial_id=trial_id,
        primary_metric="active_return_after_costs",
        variant_id="variant-{}".format(ordinal),
        variant_definition_hash=_sha("variant-{}".format(ordinal)),
        consumes_trial_budget=True,
        preregistered=True,
        outcome_data_accessed=False,
        code_sha256=_sha("code"),
        data_snapshot_sha256=_sha("data"),
        evaluator_spec_sha256=_sha("spec"),
        effective_sample_floor=30,
    )


def _eligible_result(ordinal: int, p_value: float) -> TrialResult:
    return TrialResult(
        statistical_trial_id=deterministic_trial_id("FAM-2026-001", ordinal),
        outcome=TrialOutcome.POSITIVE,
        recorded_at=NOW + timedelta(hours=ordinal),
        primary_metric="active_return_after_costs",
        primary_metric_value=0.02,
        p_value=p_value,
        inference_eligible=True,
        ineligibility_reasons=(),
        stress_scenario_pass=True,
        capacity_and_concentration_pass=True,
        effective_sample_size=50,
        minimum_effective_sample=30,
        source_artifact="results/trial-{}.json".format(ordinal),
        source_sha256=_sha("result-{}".format(ordinal)),
    )


def _bootstrap(ledger: GlobalResearchLedger, *, maximum_trial_units: int = 2) -> None:
    ledger.register_wave(_wave("FAM-2026-001"), recorded_at=NOW)
    ledger.register_family(
        _family(maximum_trial_units=maximum_trial_units), recorded_at=NOW
    )
    ledger.register_experiment(
        ResearchExperiment(
            experiment_id="EXP-2026-0001",
            family_id="FAM-2026-001",
            hypothesis_id="HYP-2026-001",
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="INITIAL",
            frozen_primary_metric="active_return_after_costs",
            registered_at=NOW,
            source_artifact="experiments/EXP-2026-0001.json",
            source_sha256=_sha("experiment"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )


def test_legacy_definition_blockers_are_projected_and_fail_decision_grade(tmp_path):
    ledger = _ledger(tmp_path)
    ledger.register_wave(_wave("FAM-2026-001"), recorded_at=NOW)
    ledger.register_family(
        _family(
            legacy_definition_blockers=(
                "LEGACY_PRIMARY_COMPARATOR_UNRESOLVED",
            )
        ),
        recorded_at=NOW,
    )

    row = ledger.project()["families"][0]
    assert row["legacy_definition_blockers"] == [
        "LEGACY_PRIMARY_COMPARATOR_UNRESOLVED"
    ]
    assert "LEGACY_PRIMARY_COMPARATOR_UNRESOLVED" in row["decision_grade_blockers"]
    assert row["research_gates"]["legacy_definition_complete"] is False
    assert row["decision_grade_ready"] is False


def test_data_gates_and_robustness_do_not_consume_statistical_trial_budget(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger, maximum_trial_units=2)
    ledger.register_run(_data_gate(), recorded_at=NOW)
    first = _model_trial(1)
    ledger.register_run(first, recorded_at=NOW)
    robustness_sha = _sha("robustness")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(robustness_sha),
            family_id=first.family_id,
            hypothesis_id=first.hypothesis_id,
            experiment_id=first.experiment_id,
            run_id="robustness-grid",
            run_class=ResearchRunClass.ROBUSTNESS,
            phase=ResearchPhase.DISCOVERY,
            occurred_at=NOW,
            source_artifact="results/robustness.json",
            source_sha256=robustness_sha,
            parent_trial_id=first.statistical_trial_id,
            primary_metric=first.primary_metric,
            outcome_data_accessed=True,
            prespecified_non_selective=True,
        ),
        recorded_at=NOW,
    )
    ledger.register_run(_model_trial(2), recorded_at=NOW)
    with pytest.raises(EventStoreIntegrityError, match="budget exceeded"):
        ledger.register_run(_model_trial(3), recorded_at=NOW)
    projection = ledger.project()
    assert projection["data_provenance_attempt_count"] == 1
    assert projection["statistical_trial_count"] == 2
    assert projection["robustness_record_count"] == 1


def test_negative_legacy_result_is_evidence_not_a_data_blocker(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger)
    trial = _model_trial(1)
    legacy = ResearchRun(
        **{
            **trial.__dict__,
            "legacy_accounting_quality": "AGGREGATE_ONLY",
            "outcome_data_accessed": True,
        }
    )
    ledger.register_run(legacy, recorded_at=NOW)
    ledger.record_result(
        TrialResult(
            statistical_trial_id=legacy.statistical_trial_id,
            outcome=TrialOutcome.NEGATIVE,
            recorded_at=NOW + timedelta(minutes=1),
            primary_metric=legacy.primary_metric,
            primary_metric_value=-0.04,
            p_value=None,
            inference_eligible=False,
            ineligibility_reasons=("CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",),
            stress_scenario_pass=False,
            capacity_and_concentration_pass=False,
            effective_sample_size=0,
            minimum_effective_sample=30,
            source_artifact=legacy.source_artifact,
            source_sha256=legacy.source_sha256,
        ),
        recorded_at=NOW + timedelta(minutes=1),
    )
    row = ledger.project()["families"][0]
    assert row["statistical_trial_count"] == 1
    assert "LEGACY_TRIAL_IDENTITY_INCOMPLETE" in row["decision_grade_blockers"]
    assert not any("DATA" in blocker for blocker in row["decision_grade_blockers"])


def test_family_inference_is_complete_and_bound_to_current_chain_head(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger)
    for ordinal, p_value in ((1, 0.01), (2, 0.04)):
        ledger.register_run(_model_trial(ordinal), recorded_at=NOW)
        ledger.record_result(_eligible_result(ordinal, p_value), recorded_at=NOW)
    head = ledger.store.read_all()[-1].event_hash
    inference = FamilyInference(
        family_id="FAM-2026-001",
        wave_id="WAVE-2026-001",
        track=InferenceTrack.EXPLORATORY,
        method=MultipleTestingMethod.HOLM_BONFERRONI,
        included_trial_ids=(
            deterministic_trial_id("FAM-2026-001", 1),
            deterministic_trial_id("FAM-2026-001", 2),
        ),
        family_omnibus_p_value=0.02,
        adjusted_p_values={
            deterministic_trial_id("FAM-2026-001", 1): 0.02,
            deterministic_trial_id("FAM-2026-001", 2): 0.04,
        },
        primary_variant_pass=True,
        economic_hurdle_pass=True,
        stress_scenario_pass=True,
        capacity_and_concentration_pass=True,
        effective_sample_pass=True,
        recorded_at=NOW + timedelta(hours=3),
        evaluated_ledger_head_hash=head,
        source_artifact="inference/family.json",
        source_sha256=_sha("inference"),
    )
    ledger.record_family_inference(inference, recorded_at=NOW + timedelta(hours=3))
    projection = ledger.project()
    assert projection["waves"]["WAVE-2026-001"]["complete_family_inference"] is True
    assert projection["families"][0]["wave_multiple_testing_pass"] is True
    assert projection["families"][0]["decision_grade_ready"] is False
    assert "CHALLENGE_EPOCH_NOT_REGISTERED" in projection["families"][0][
        "decision_grade_blockers"
    ]


def test_positive_result_cannot_override_frozen_effect_direction(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger)
    ledger.register_run(_model_trial(1), recorded_at=NOW)
    with pytest.raises(EventStoreIntegrityError, match="economic hurdle"):
        ledger.record_result(
            TrialResult(
                statistical_trial_id="FAM-2026-001-T001",
                outcome=TrialOutcome.POSITIVE,
                recorded_at=NOW + timedelta(hours=1),
                primary_metric="active_return_after_costs",
                primary_metric_value=-999.0,
                p_value=0.001,
                inference_eligible=True,
                ineligibility_reasons=(),
                stress_scenario_pass=True,
                capacity_and_concentration_pass=True,
                effective_sample_size=50,
                minimum_effective_sample=30,
                source_artifact="results/fabricated.json",
                source_sha256=_sha("fabricated"),
            ),
            recorded_at=NOW + timedelta(hours=1),
        )


def test_family_inference_is_recomputed_not_self_attested(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger)
    for ordinal, p_value in ((1, 0.01), (2, 0.04)):
        ledger.register_run(_model_trial(ordinal), recorded_at=NOW)
        ledger.record_result(_eligible_result(ordinal, p_value), recorded_at=NOW)
    with pytest.raises(EventStoreIntegrityError, match="internally verified"):
        ledger.record_family_inference(
            FamilyInference(
                family_id="FAM-2026-001",
                wave_id="WAVE-2026-001",
                track=InferenceTrack.EXPLORATORY,
                method=MultipleTestingMethod.HOLM_BONFERRONI,
                included_trial_ids=(
                    "FAM-2026-001-T001",
                    "FAM-2026-001-T002",
                ),
                family_omnibus_p_value=0.000001,
                adjusted_p_values={
                    "FAM-2026-001-T001": 0.000001,
                    "FAM-2026-001-T002": 0.000001,
                },
                primary_variant_pass=True,
                economic_hurdle_pass=True,
                stress_scenario_pass=True,
                capacity_and_concentration_pass=True,
                effective_sample_pass=True,
                recorded_at=NOW + timedelta(hours=3),
                evaluated_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
                source_artifact="inference/fabricated.json",
                source_sha256=_sha("fabricated-inference"),
            ),
            recorded_at=NOW + timedelta(hours=3),
        )


def test_shared_challenge_epoch_is_consumed_exactly_once(tmp_path):
    ledger = _ledger(tmp_path)
    _bootstrap(ledger)
    ledger.register_run(_model_trial(1), recorded_at=NOW)
    ledger.record_result(_eligible_result(1, 0.01), recorded_at=NOW)
    ledger.record_family_inference(
        FamilyInference(
            family_id="FAM-2026-001",
            wave_id="WAVE-2026-001",
            track=InferenceTrack.EXPLORATORY,
            method=MultipleTestingMethod.HOLM_BONFERRONI,
            included_trial_ids=(deterministic_trial_id("FAM-2026-001", 1),),
            family_omnibus_p_value=0.01,
            adjusted_p_values={
                deterministic_trial_id("FAM-2026-001", 1): 0.01
            },
            primary_variant_pass=True,
            economic_hurdle_pass=True,
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_pass=True,
            recorded_at=NOW + timedelta(hours=2),
            evaluated_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
            source_artifact="inference/family.json",
            source_sha256=_sha("challenge-inference"),
        ),
        recorded_at=NOW + timedelta(hours=2),
    )
    trial_id = deterministic_trial_id("FAM-2026-001", 900)
    source_sha = _sha("challenge-run")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(source_sha),
            family_id="FAM-2026-001",
            hypothesis_id="HYP-2026-001",
            experiment_id="EXP-2026-0001",
            run_id="challenge-run",
            run_class=ResearchRunClass.CHALLENGE_READ,
            phase=ResearchPhase.CHALLENGE,
            occurred_at=NOW,
            source_artifact="challenge/run.json",
            source_sha256=source_sha,
            statistical_trial_id=trial_id,
            primary_metric="active_return_after_costs",
            variant_id="variant-1",
            variant_definition_hash=_sha("variant-1"),
            consumes_trial_budget=False,
            preregistered=True,
            outcome_data_accessed=False,
            challenge_accessed=False,
            code_sha256=_sha("challenge-code"),
            data_snapshot_sha256=_sha("challenge-input"),
            evaluator_spec_sha256=_sha("challenge-spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW,
    )
    input_sha = _sha("challenge-input")
    ledger.register_challenge_epoch(
        ChallengeEpoch(
            challenge_epoch_id="CHALLENGE-2026-001",
            family_ids=("FAM-2026-001",),
            trial_ids=(trial_id,),
            expected_input_sha256_by_trial={trial_id: input_sha},
            challenge_period="2025-01-01/2026-06-30",
            panel_manifest_sha256=_sha("panel"),
            alpha=0.05,
            authorized_by="Brett Olson",
            authorization_artifact="decisions/challenge.json",
            authorization_sha256=_sha("authorization"),
            authorized_at=NOW + timedelta(hours=3),
        ),
        recorded_at=NOW,
    )
    payload = {
        "challenge_epoch_id": "CHALLENGE-2026-001",
        "trial_ids": [trial_id],
        "input_sha256_by_trial": {trial_id: input_sha},
        "consumer": "evaluator",
        "purpose": "Frozen challenge",
    }
    access = HoldoutAccess(
        access_id=deterministic_access_id(payload),
        challenge_epoch_id="CHALLENGE-2026-001",
        trial_ids=(trial_id,),
        input_sha256_by_trial={trial_id: input_sha},
        accessed_at=NOW + timedelta(hours=4),
        consumer="evaluator",
        purpose="Frozen challenge",
    )
    ledger.record_holdout_access(access, recorded_at=NOW + timedelta(hours=4))
    ledger.record_result(
        TrialResult(
            statistical_trial_id=trial_id,
            outcome=TrialOutcome.POSITIVE,
            recorded_at=NOW + timedelta(hours=4, minutes=30),
            primary_metric="active_return_after_costs",
            primary_metric_value=0.02,
            p_value=0.01,
            inference_eligible=True,
            ineligibility_reasons=(),
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_size=50,
            minimum_effective_sample=30,
            source_artifact="challenge/result.json",
            source_sha256=_sha("challenge-result"),
        ),
        recorded_at=NOW + timedelta(hours=4, minutes=30),
    )
    review_path = tmp_path / "reviews/FAM-2026-001.json"
    review_path.parent.mkdir()
    review_path.write_text("review-artifact", encoding="utf-8")
    ledger.record_independent_review(
        IndependentResearchReview(
            review_id="REVIEW-{}".format(_sha("review")[:16]),
            family_id="FAM-2026-001",
            reviewer="Independent reviewer",
            independent_of_research_authors=True,
            reviewed_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
            point_in_time_integrity=True,
            deterministic_replay=True,
            benchmark_and_factor_model_pass=True,
            artifact_integrity_pass=True,
            reviewed_at=NOW + timedelta(hours=5),
            source_artifact="reviews/FAM-2026-001.json",
            source_sha256=_sha("review-artifact"),
        ),
        recorded_at=NOW + timedelta(hours=5),
    )
    family_projection = ledger.project()["families"][0]
    assert family_projection["decision_grade_ready"] is False
    assert (
            "AUTHENTICATED_INDEPENDENT_REVIEW_MISSING_OR_INVALID"
        in family_projection["decision_grade_blockers"]
    )
    second = HoldoutAccess(
        access_id=deterministic_access_id({**payload, "consumer": "other"}),
        challenge_epoch_id=access.challenge_epoch_id,
        trial_ids=access.trial_ids,
        input_sha256_by_trial=access.input_sha256_by_trial,
        accessed_at=NOW + timedelta(hours=5),
        consumer="other",
        purpose=access.purpose,
    )
    with pytest.raises(EventStoreIntegrityError, match="already accessed"):
        ledger.record_holdout_access(
            second, recorded_at=NOW + timedelta(hours=5)
        )


def test_known_multiple_testing_vectors():
    values = (("a", 0.01), ("b", 0.04), ("c", 0.03))
    bh = benjamini_hochberg(values, q=0.05)
    assert [round(item["adjusted_p_value"], 6) for item in bh] == [0.03, 0.04, 0.04]
    by = benjamini_yekutieli(values, q=0.10)
    assert [round(item["adjusted_p_value"], 6) for item in by] == [
        0.055,
        0.073333,
        0.073333,
    ]
    holm = holm_bonferroni(values, alpha=0.05)
    assert [round(item["adjusted_p_value"], 6) for item in holm] == [
        0.03,
        0.06,
        0.06,
    ]


def test_positive_result_without_p_value_is_rejected():
    with pytest.raises(ContractValidationError, match="requires a p-value"):
        TrialResult(
            statistical_trial_id="FAM-2026-001-T001",
            outcome=TrialOutcome.POSITIVE,
            recorded_at=NOW,
            primary_metric="metric",
            primary_metric_value=1.0,
            p_value=None,
            inference_eligible=True,
            ineligibility_reasons=(),
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_size=50,
            minimum_effective_sample=30,
            source_artifact="result.json",
            source_sha256=_sha("result"),
        )
