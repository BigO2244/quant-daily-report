from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.factory import (
    ChallengeEpoch,
    ExpectedDirection,
    FamilyInference,
    GlobalResearchLedger,
    HoldoutAccess,
    HypothesisFamily,
    IndependentResearchReview,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchExperiment,
    ResearchPhase,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    TrialOutcome,
    TrialResult,
    deterministic_access_id,
    deterministic_attempt_id,
    deterministic_trial_id,
)
from projects.alpha_lab.factory.errors import EventStoreIntegrityError


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)


def _sha(value: str | bytes) -> str:
    raw = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _ledger(tmp_path: Path, name: str = "ledger") -> GlobalResearchLedger:
    root = tmp_path / name
    root.mkdir()
    (root / "events").mkdir()
    return GlobalResearchLedger(root / "events/research.jsonl", research_root=root)


def _epoch(
    ordinal: int,
    *,
    input_sha: str,
    panel_sha: str,
    period: str = "2025-01-01/2025-12-31",
) -> ChallengeEpoch:
    family_id = "FAM-2026-{:03d}".format(ordinal)
    trial_id = deterministic_trial_id(family_id, 900)
    return ChallengeEpoch(
        challenge_epoch_id="CHALLENGE-2026-{:03d}".format(ordinal),
        family_ids=(family_id,),
        trial_ids=(trial_id,),
        expected_input_sha256_by_trial={trial_id: input_sha},
        challenge_period=period,
        panel_manifest_sha256=panel_sha,
        alpha=0.05,
        authorized_by="owner",
        authorization_artifact="authorization.json",
        authorization_sha256=_sha("authorization-{}".format(ordinal)),
        authorized_at=NOW,
    )


def _append_epoch_raw(
    ledger: GlobalResearchLedger, epoch: ChallengeEpoch, *, suffix: str = ""
) -> None:
    ledger.store.append(
        event_id="challenge-epoch:{}{}".format(epoch.challenge_epoch_id, suffix),
        event_type=ledger.CHALLENGE_EPOCH_EVENT,
        occurred_at=epoch.authorized_at,
        recorded_at=epoch.authorized_at,
        payload=epoch.to_dict(),
    )


def _family(*, legacy: bool = False) -> tuple[ResearchWave, HypothesisFamily]:
    wave = ResearchWave(
        wave_id="WAVE-2026-001",
        track=InferenceTrack.EXPLORATORY,
        family_ids=("FAM-2026-001",),
        method=(
            MultipleTestingMethod.HOLM_BONFERRONI
            if legacy
            else MultipleTestingMethod.BENJAMINI_YEKUTIELI
        ),
        alpha_or_q=0.99 if legacy else 0.10,
        registered_at=NOW,
        policy_artifact="policy.json",
        policy_sha256=_sha("policy"),
        owner_ratified=True,
        legacy_policy=legacy,
    )
    family = HypothesisFamily(
        family_id="FAM-2026-001",
        wave_id=wave.wave_id,
        challenge_epoch_id="CHALLENGE-2026-001",
        name="Test family",
        economic_mechanism="A falsifiable mechanism.",
        family_scope_hash=_sha("scope"),
        primary_metric="active_return_after_costs",
        benchmark="benchmark",
        expected_direction=ExpectedDirection.GREATER_THAN,
        null_value=0.0,
        economic_hurdle=0.01,
        primary_variant_id="variant-1",
        maximum_trial_units=1,
        selection_trial_budget=0,
        within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
        family_alpha=0.10,
        registered_at=NOW,
        source_artifact="family.json",
        source_sha256=_sha("family"),
        owner_ratified=True,
    )
    return wave, family


def _complete_self_attested_review(ledger: GlobalResearchLedger) -> None:
    wave, family = _family()
    ledger.register_wave(wave, recorded_at=NOW)
    ledger.register_family(family, recorded_at=NOW)
    ledger.register_experiment(
        ResearchExperiment(
            experiment_id="EXP-2026-0001",
            family_id=family.family_id,
            hypothesis_id="HYP-2026-001",
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="INITIAL",
            frozen_primary_metric=family.primary_metric,
            registered_at=NOW,
            source_artifact="experiment.json",
            source_sha256=_sha("experiment"),
            owner_ratified=True,
        ),
        recorded_at=NOW,
    )
    trial_id = deterministic_trial_id(family.family_id, 1)
    run_sha = _sha("model-run")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(run_sha),
            family_id=family.family_id,
            hypothesis_id="HYP-2026-001",
            experiment_id="EXP-2026-0001",
            run_id="model-run",
            run_class=ResearchRunClass.MODEL_TRIAL,
            phase=ResearchPhase.DISCOVERY,
            occurred_at=NOW + timedelta(minutes=1),
            source_artifact="model-run.json",
            source_sha256=run_sha,
            statistical_trial_id=trial_id,
            primary_metric=family.primary_metric,
            variant_id=family.primary_variant_id,
            variant_definition_hash=_sha("variant-1"),
            consumes_trial_budget=True,
            preregistered=True,
            code_sha256=_sha("code"),
            data_snapshot_sha256=_sha("discovery-input"),
            evaluator_spec_sha256=_sha("spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW + timedelta(minutes=1),
    )
    ledger.record_result(
        TrialResult(
            statistical_trial_id=trial_id,
            outcome=TrialOutcome.POSITIVE,
            recorded_at=NOW + timedelta(hours=1),
            primary_metric=family.primary_metric,
            primary_metric_value=0.02,
            p_value=0.01,
            inference_eligible=True,
            ineligibility_reasons=(),
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_size=50,
            minimum_effective_sample=30,
            source_artifact="model-result.json",
            source_sha256=_sha("model-result"),
        ),
        recorded_at=NOW + timedelta(hours=1),
    )
    ledger.record_family_inference(
        FamilyInference(
            family_id=family.family_id,
            wave_id=wave.wave_id,
            track=wave.track,
            method=family.within_family_method,
            included_trial_ids=(trial_id,),
            family_omnibus_p_value=0.01,
            adjusted_p_values={trial_id: 0.01},
            primary_variant_pass=True,
            economic_hurdle_pass=True,
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_pass=True,
            recorded_at=NOW + timedelta(hours=2),
            evaluated_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
            source_artifact="inference.json",
            source_sha256=_sha("inference"),
        ),
        recorded_at=NOW + timedelta(hours=2),
    )
    challenge_trial_id = deterministic_trial_id(family.family_id, 900)
    challenge_sha = _sha("challenge-run")
    input_sha = _sha("challenge-input")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(challenge_sha),
            family_id=family.family_id,
            hypothesis_id="HYP-2026-001",
            experiment_id="EXP-2026-0001",
            run_id="challenge-run",
            run_class=ResearchRunClass.CHALLENGE_READ,
            phase=ResearchPhase.CHALLENGE,
            occurred_at=NOW + timedelta(hours=2, minutes=1),
            source_artifact="challenge-run.json",
            source_sha256=challenge_sha,
            statistical_trial_id=challenge_trial_id,
            primary_metric=family.primary_metric,
            variant_id=family.primary_variant_id,
            variant_definition_hash=_sha("variant-1"),
            preregistered=True,
            code_sha256=_sha("challenge-code"),
            data_snapshot_sha256=input_sha,
            evaluator_spec_sha256=_sha("challenge-spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW + timedelta(hours=2, minutes=1),
    )
    epoch = _epoch(1, input_sha=input_sha, panel_sha=_sha("panel"))
    epoch = ChallengeEpoch(**{**epoch.__dict__, "authorized_at": NOW + timedelta(hours=3)})
    ledger.register_challenge_epoch(epoch, recorded_at=NOW + timedelta(hours=3))
    access_payload = {
        "challenge_epoch_id": epoch.challenge_epoch_id,
        "trial_ids": [challenge_trial_id],
        "input_sha256_by_trial": {challenge_trial_id: input_sha},
        "consumer": "evaluator",
        "purpose": "frozen challenge",
    }
    ledger.record_holdout_access(
        HoldoutAccess(
            access_id=deterministic_access_id(access_payload),
            challenge_epoch_id=epoch.challenge_epoch_id,
            trial_ids=(challenge_trial_id,),
            input_sha256_by_trial={challenge_trial_id: input_sha},
            accessed_at=NOW + timedelta(hours=4),
            consumer="evaluator",
            purpose="frozen challenge",
        ),
        recorded_at=NOW + timedelta(hours=4),
    )
    ledger.record_result(
        TrialResult(
            statistical_trial_id=challenge_trial_id,
            outcome=TrialOutcome.POSITIVE,
            recorded_at=NOW + timedelta(hours=4, minutes=30),
            primary_metric=family.primary_metric,
            primary_metric_value=0.02,
            p_value=0.01,
            inference_eligible=True,
            ineligibility_reasons=(),
            stress_scenario_pass=True,
            capacity_and_concentration_pass=True,
            effective_sample_size=50,
            minimum_effective_sample=30,
            source_artifact="challenge-result.json",
            source_sha256=_sha("challenge-result"),
        ),
        recorded_at=NOW + timedelta(hours=4, minutes=30),
    )
    review_bytes = b"independent replay receipt"
    review_path = ledger.store.research_root / "reviews/review.json"
    review_path.parent.mkdir()
    review_path.write_bytes(review_bytes)
    ledger.record_independent_review(
        IndependentResearchReview(
            review_id="REVIEW-{}".format(_sha("review")[:16]),
            family_id=family.family_id,
            reviewer="self-attested reviewer",
            independent_of_research_authors=True,
            reviewed_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
            point_in_time_integrity=True,
            deterministic_replay=True,
            benchmark_and_factor_model_pass=True,
            artifact_integrity_pass=True,
            reviewed_at=NOW + timedelta(hours=5),
            source_artifact="reviews/review.json",
            source_sha256=_sha(review_bytes),
        ),
        recorded_at=NOW + timedelta(hours=5),
    )


def test_epoch_registration_rejects_input_hash_reuse_across_renamed_epochs(tmp_path):
    ledger = _ledger(tmp_path)
    shared_input = _sha("shared holdout")
    _append_epoch_raw(
        ledger,
        _epoch(1, input_sha=shared_input, panel_sha=_sha("panel-a")),
    )
    with pytest.raises(EventStoreIntegrityError, match="input hash"):
        ledger.register_challenge_epoch(
            _epoch(
                2,
                input_sha=shared_input,
                panel_sha=_sha("panel-b"),
                period="2027-01-01/2027-12-31",
            ),
            recorded_at=NOW,
        )


def test_epoch_registration_rejects_same_panel_with_overlapping_period(tmp_path):
    ledger = _ledger(tmp_path)
    shared_panel = _sha("shared panel")
    _append_epoch_raw(
        ledger,
        _epoch(1, input_sha=_sha("input-a"), panel_sha=shared_panel),
    )
    with pytest.raises(EventStoreIntegrityError, match="overlapping period"):
        ledger.register_challenge_epoch(
            _epoch(
                2,
                input_sha=_sha("input-b"),
                panel_sha=shared_panel,
                period="2025-06-01/2026-05-31",
            ),
            recorded_at=NOW,
        )


def test_semantic_replay_rejects_pairwise_holdout_reuse(tmp_path):
    ledger = _ledger(tmp_path)
    shared_input = _sha("shared input")
    _append_epoch_raw(
        ledger,
        _epoch(1, input_sha=shared_input, panel_sha=_sha("panel-a")),
    )
    _append_epoch_raw(
        ledger,
        _epoch(2, input_sha=shared_input, panel_sha=_sha("panel-b")),
    )
    with pytest.raises(EventStoreIntegrityError, match="reused holdout"):
        ledger.project()


def test_semantic_replay_rejects_unbound_challenge_entrant(tmp_path):
    ledger = _ledger(tmp_path)
    _append_epoch_raw(
        ledger,
        _epoch(1, input_sha=_sha("input"), panel_sha=_sha("panel")),
    )
    with pytest.raises(EventStoreIntegrityError, match="entrant binding mismatch"):
        ledger.project()


def test_access_cannot_claim_a_time_after_the_event_record(tmp_path):
    ledger = _ledger(tmp_path)
    epoch = _epoch(1, input_sha=_sha("input"), panel_sha=_sha("panel"))
    _append_epoch_raw(ledger, epoch)
    payload = {
        "challenge_epoch_id": epoch.challenge_epoch_id,
        "trial_ids": list(epoch.trial_ids),
        "input_sha256_by_trial": dict(epoch.expected_input_sha256_by_trial),
        "consumer": "evaluator",
        "purpose": "challenge",
    }
    with pytest.raises(EventStoreIntegrityError, match="follow event recording"):
        ledger.record_holdout_access(
            HoldoutAccess(
                access_id=deterministic_access_id(payload),
                challenge_epoch_id=epoch.challenge_epoch_id,
                trial_ids=epoch.trial_ids,
                input_sha256_by_trial=epoch.expected_input_sha256_by_trial,
                accessed_at=NOW + timedelta(minutes=1),
                consumer="evaluator",
                purpose="challenge",
            ),
            recorded_at=NOW,
        )


def test_legacy_policy_wave_is_permanently_non_decision_grade(tmp_path):
    ledger = _ledger(tmp_path)
    wave, family = _family(legacy=True)
    ledger.register_wave(wave, recorded_at=NOW)
    ledger.register_family(family, recorded_at=NOW)
    row = ledger.project()["families"][0]
    assert row["decision_grade_ready"] is False
    assert "LEGACY_POLICY_WAVE_NOT_DECISION_GRADE" in row["decision_grade_blockers"]


def test_review_artifact_must_exist_inside_root_and_match_hash(tmp_path):
    ledger = _ledger(tmp_path)
    _, family = _family()
    ledger.store.append(
        event_id="raw-family",
        event_type=ledger.FAMILY_EVENT,
        occurred_at=NOW,
        recorded_at=NOW,
        payload=family.to_dict(),
    )
    trial_id = deterministic_trial_id(family.family_id, 900)
    run_sha = _sha("challenge-run")
    run = ResearchRun(
        attempt_id=deterministic_attempt_id(run_sha),
        family_id=family.family_id,
        hypothesis_id="HYP-2026-001",
        experiment_id="EXP-2026-0001",
        run_id="challenge-run",
        run_class=ResearchRunClass.CHALLENGE_READ,
        phase=ResearchPhase.CHALLENGE,
        occurred_at=NOW,
        source_artifact="challenge-run.json",
        source_sha256=run_sha,
        statistical_trial_id=trial_id,
        primary_metric=family.primary_metric,
        variant_id=family.primary_variant_id,
        variant_definition_hash=_sha("variant"),
        preregistered=True,
        code_sha256=_sha("code"),
        data_snapshot_sha256=_sha("input"),
        evaluator_spec_sha256=_sha("spec"),
        effective_sample_floor=30,
    )
    ledger.store.append(
        event_id="raw-run",
        event_type=ledger.RUN_EVENT,
        occurred_at=NOW,
        recorded_at=NOW,
        payload=run.to_dict(),
    )
    result = TrialResult(
        statistical_trial_id=trial_id,
        outcome=TrialOutcome.POSITIVE,
        recorded_at=NOW + timedelta(minutes=1),
        primary_metric=family.primary_metric,
        primary_metric_value=0.02,
        p_value=0.01,
        inference_eligible=True,
        ineligibility_reasons=(),
        stress_scenario_pass=True,
        capacity_and_concentration_pass=True,
        effective_sample_size=50,
        minimum_effective_sample=30,
        source_artifact="challenge-result.json",
        source_sha256=_sha("result"),
    )
    ledger.store.append(
        event_id="raw-result",
        event_type=ledger.RESULT_EVENT,
        occurred_at=result.recorded_at,
        recorded_at=result.recorded_at,
        payload=result.to_dict(),
    )

    def review(source_sha: str) -> IndependentResearchReview:
        return IndependentResearchReview(
            review_id="REVIEW-{}".format(_sha("artifact-review")[:16]),
            family_id=family.family_id,
            reviewer="reviewer",
            independent_of_research_authors=True,
            reviewed_ledger_head_hash=ledger.store.read_all()[-1].event_hash,
            point_in_time_integrity=True,
            deterministic_replay=True,
            benchmark_and_factor_model_pass=True,
            artifact_integrity_pass=True,
            reviewed_at=NOW + timedelta(minutes=2),
            source_artifact="reviews/review.json",
            source_sha256=source_sha,
        )

    with pytest.raises(EventStoreIntegrityError, match="must exist inside"):
        ledger.record_independent_review(
            review(_sha("missing")), recorded_at=NOW + timedelta(minutes=2)
        )
    artifact = ledger.store.research_root / "reviews/review.json"
    artifact.parent.mkdir()
    artifact.write_bytes(b"review receipt")
    with pytest.raises(EventStoreIntegrityError, match="hash does not match"):
        ledger.record_independent_review(
            review(_sha("wrong")), recorded_at=NOW + timedelta(minutes=2)
        )
    ledger.record_independent_review(
        review(_sha(b"review receipt")), recorded_at=NOW + timedelta(minutes=2)
    )


def test_self_attested_review_remains_explicitly_non_decision_grade(tmp_path):
    ledger = _ledger(tmp_path)
    _complete_self_attested_review(ledger)
    row = ledger.project()["families"][0]
    assert all(row["research_gates"].values())
    assert row["decision_grade_ready"] is False
    assert (
        "AUTHENTICATED_INDEPENDENT_REVIEW_NOT_IMPLEMENTED"
        in row["decision_grade_blockers"]
    )


def test_semantic_replay_rejects_review_appended_before_challenge_result(tmp_path):
    original = _ledger(tmp_path, "original")
    _complete_self_attested_review(original)
    original_records = original.store.read_all()
    assert original_records[-2].event_type == original.RESULT_EVENT
    assert original_records[-1].event_type == original.REVIEW_EVENT

    reordered = _ledger(tmp_path, "reordered")
    source_review = original.store.research_root / "reviews/review.json"
    target_review = reordered.store.research_root / "reviews/review.json"
    target_review.parent.mkdir()
    target_review.write_bytes(source_review.read_bytes())

    for record in original_records[:-2]:
        reordered.store.append(
            event_id=record.event_id,
            event_type=record.event_type,
            occurred_at=record.occurred_at,
            recorded_at=record.recorded_at,
            payload=record.payload,
        )

    review_record = original_records[-1]
    review_payload = dict(review_record.payload)
    review_payload["reviewed_ledger_head_hash"] = reordered.store.read_all()[
        -1
    ].event_hash
    reordered.store.append(
        event_id=review_record.event_id,
        event_type=review_record.event_type,
        occurred_at=review_record.occurred_at,
        recorded_at=review_record.recorded_at,
        payload=review_payload,
    )
    result_record = original_records[-2]
    reordered.store.append(
        event_id=result_record.event_id,
        event_type=result_record.event_type,
        occurred_at=result_record.occurred_at,
        recorded_at=result_record.recorded_at,
        payload=result_record.payload,
    )

    with pytest.raises(
        EventStoreIntegrityError, match="review before closed challenge evidence"
    ):
        reordered.project()


def test_semantic_replay_rejects_post_result_child_appended_before_parent_result(
    tmp_path,
):
    ledger = _ledger(tmp_path)
    wave, family = _family()
    ledger.register_wave(wave, recorded_at=NOW)
    ledger.register_family(family, recorded_at=NOW)
    parent = ResearchExperiment(
        experiment_id="EXP-2026-0001",
        family_id=family.family_id,
        hypothesis_id="HYP-2026-001",
        parent_experiment_ids=(),
        generated_after_results=False,
        generation_reason="INITIAL",
        frozen_primary_metric=family.primary_metric,
        registered_at=NOW,
        source_artifact="parent-experiment.json",
        source_sha256=_sha("parent-experiment"),
        owner_ratified=True,
    )
    ledger.register_experiment(parent, recorded_at=NOW)
    trial_id = deterministic_trial_id(family.family_id, 1)
    run_sha = _sha("parent-run")
    ledger.register_run(
        ResearchRun(
            attempt_id=deterministic_attempt_id(run_sha),
            family_id=family.family_id,
            hypothesis_id=parent.hypothesis_id,
            experiment_id=parent.experiment_id,
            run_id="parent-run",
            run_class=ResearchRunClass.MODEL_TRIAL,
            phase=ResearchPhase.DISCOVERY,
            occurred_at=NOW + timedelta(minutes=1),
            source_artifact="parent-run.json",
            source_sha256=run_sha,
            statistical_trial_id=trial_id,
            primary_metric=family.primary_metric,
            variant_id=family.primary_variant_id,
            variant_definition_hash=_sha("variant-1"),
            consumes_trial_budget=True,
            preregistered=True,
            code_sha256=_sha("code"),
            data_snapshot_sha256=_sha("input"),
            evaluator_spec_sha256=_sha("spec"),
            effective_sample_floor=30,
        ),
        recorded_at=NOW + timedelta(minutes=1),
    )
    child = ResearchExperiment(
        experiment_id="EXP-2026-0002",
        family_id=family.family_id,
        hypothesis_id=parent.hypothesis_id,
        parent_experiment_ids=(parent.experiment_id,),
        generated_after_results=True,
        generation_reason="POST_RESULT_ITERATION",
        frozen_primary_metric=family.primary_metric,
        registered_at=NOW + timedelta(minutes=3),
        source_artifact="child-experiment.json",
        source_sha256=_sha("child-experiment"),
        owner_ratified=True,
    )
    ledger.store.append(
        event_id="experiment:{}".format(child.experiment_id),
        event_type=ledger.EXPERIMENT_EVENT,
        occurred_at=child.registered_at,
        recorded_at=child.registered_at,
        payload=child.to_dict(),
    )
    result = TrialResult(
        statistical_trial_id=trial_id,
        outcome=TrialOutcome.POSITIVE,
        recorded_at=NOW + timedelta(minutes=2),
        primary_metric=family.primary_metric,
        primary_metric_value=0.02,
        p_value=0.01,
        inference_eligible=True,
        ineligibility_reasons=(),
        stress_scenario_pass=True,
        capacity_and_concentration_pass=True,
        effective_sample_size=50,
        minimum_effective_sample=30,
        source_artifact="parent-result.json",
        source_sha256=_sha("parent-result"),
    )
    ledger.store.append(
        event_id="result:{}".format(trial_id),
        event_type=ledger.RESULT_EVENT,
        occurred_at=result.recorded_at,
        recorded_at=result.recorded_at,
        payload=result.to_dict(),
    )

    with pytest.raises(EventStoreIntegrityError, match="post-result lineage"):
        ledger.project()
