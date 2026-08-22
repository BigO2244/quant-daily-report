from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from projects.alpha_lab.control_plane.evaluator import EvaluatorSpec
from projects.alpha_lab.control_plane.evaluator_recovery import (
    reconcile_finalized_evaluator_bundle,
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
    ResearchBoundaryError,
    canonical_hash,
    canonical_json,
    deterministic_attempt_id,
    deterministic_trial_id,
)


NOW = datetime(2026, 8, 22, 16, 0, tzinfo=timezone.utc)
FAMILY_ID = "FAM-2026-006"
HYPOTHESIS_ID = "HYP-2026-006"
EXPERIMENT_ID = "EXP-2026-0006"
VARIANTS = [
    {
        "variant_id": "primary",
        "variant_definition_hash": hashlib.sha256(b"primary").hexdigest(),
    },
    {
        "variant_id": "placebo",
        "variant_definition_hash": hashlib.sha256(b"placebo").hexdigest(),
    },
]
SEARCH_CENSUS = [
    {
        "search_id": "search-001",
        "search_definition_hash": hashlib.sha256(b"search-001").hexdigest(),
    }
]


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _spec() -> EvaluatorSpec:
    unsigned = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v2",
        "hypothesis_id": HYPOTHESIS_ID,
        "family_id": FAMILY_ID,
        "experiment_id": EXPERIMENT_ID,
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-006",
        "evaluator_id": "recovery_test_v2",
        "technique_family": "EVENT_STUDY",
        "module": "projects.alpha_lab.evaluators.recovery_test_v2",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "frozen_variants": VARIANTS,
        "search_census": SEARCH_CENSUS,
        "search_census_hash": canonical_hash(SEARCH_CENSUS),
        "selection_trial_units": 1,
        "primary_metric": "active_return_after_costs",
        "expected_direction": "GREATER_THAN",
        "null_value": 0.0,
        "economic_hurdle": 0.01,
        "inference_method": "HOLM_BONFERRONI",
        "inference_alpha_or_q": 0.05,
        "resampling_unit": "REBALANCE_DATE_BLOCK",
        "effective_sample_floor": 30,
        "evaluator_code_sha256": _sha("evaluator-code"),
        "data_contract_ids": ["panel_v1"],
        "challenge_period": "2025-01-01/2025-12-31",
    }
    return EvaluatorSpec.from_dict(
        {**unsigned, "spec_hash": canonical_hash(unsigned)}
    )


def _ledger(tmp_path: Path, spec: EvaluatorSpec) -> GlobalResearchLedger:
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
            challenge_epoch_id="CHALLENGE-2026-006",
            name="Recovery family",
            economic_mechanism="A falsifiable mechanism.",
            family_scope_hash=_sha("scope"),
            primary_metric="active_return_after_costs",
            benchmark="frozen benchmark",
            expected_direction=ExpectedDirection.GREATER_THAN,
            null_value=0.0,
            economic_hurdle=0.01,
            primary_variant_id="primary",
            maximum_trial_units=2,
            selection_trial_budget=1,
            within_family_method=MultipleTestingMethod.HOLM_BONFERRONI,
            family_alpha=0.05,
            registered_at=NOW,
            source_artifact="hypotheses/HYP-2026-006.md",
            source_sha256=_sha("hypothesis"),
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
    for ordinal, contract in enumerate(VARIANTS, start=1):
        source_sha = _sha("run-{}".format(ordinal))
        ledger.register_run(
            ResearchRun(
                attempt_id=deterministic_attempt_id(source_sha),
                family_id=FAMILY_ID,
                hypothesis_id=HYPOTHESIS_ID,
                experiment_id=EXPERIMENT_ID,
                run_id="run-{}".format(ordinal),
                run_class=ResearchRunClass.MODEL_TRIAL,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=NOW,
                source_artifact="runs/run-{}.json".format(ordinal),
                source_sha256=source_sha,
                statistical_trial_id=deterministic_trial_id(FAMILY_ID, ordinal),
                primary_metric="active_return_after_costs",
                variant_id=contract["variant_id"],
                variant_definition_hash=contract["variant_definition_hash"],
                consumes_trial_budget=True,
                preregistered=True,
                outcome_data_accessed=False,
                code_sha256=spec.evaluator_code_sha256,
                data_snapshot_sha256=_sha("input"),
                evaluator_spec_sha256=spec.spec_hash,
                effective_sample_floor=30,
                selection_trial_units=1 if ordinal == 1 else 0,
            ),
            recorded_at=NOW,
        )
    return ledger


def _bundle(tmp_path: Path, spec: EvaluatorSpec) -> Path:
    retrieved_at = NOW + timedelta(hours=1)
    trial_ids = [deterministic_trial_id(FAMILY_ID, item) for item in (1, 2)]
    raw = {
        "variant_count": 2,
        "primary_metric_name": "active_return_after_costs",
        "orders_submitted": False,
        "search_census": SEARCH_CENSUS,
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": 1,
        "variants": [
            {
                **contract,
                "evidence_verdict": "NEGATIVE",
                "primary_metric_value": -0.01,
                "p_value": None,
                "inference_eligible": False,
                "ineligibility_reasons": ["NO_VALID_P_VALUE"],
                "stress_scenario_pass": False,
                "capacity_and_concentration_pass": False,
                "effective_sample_size": 20,
            }
            for contract in VARIANTS
        ],
    }
    envelope = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v2",
        "hypothesis_id": HYPOTHESIS_ID,
        "family_id": FAMILY_ID,
        "experiment_id": EXPERIMENT_ID,
        "exploratory_wave_id": "WAVE-2026-001",
        "challenge_epoch_id": "CHALLENGE-2026-006",
        "evaluator_id": spec.evaluator_id,
        "technique_family": "EVENT_STUDY",
        "phase": "DISCOVERY",
        "spec_hash": spec.spec_hash,
        "input_packet_hash": _sha("input"),
        "input_source_sha256": _sha("input"),
        "registered_trial_ids": trial_ids,
        "registered_trial_contracts": [
            {"statistical_trial_id": trial_id, **contract}
            for trial_id, contract in zip(trial_ids, VARIANTS)
        ],
        "frozen_variant_contract_hash": canonical_hash(VARIANTS),
        "search_census_hash": spec.search_census_hash,
        "selection_trial_units": 1,
        "challenge_access_receipt_hash": None,
        "boundary_attestation": {
            "schema_version": "caerus_alpha_lab_evaluator_boundary_v1",
            "source_path": "/frozen/evaluators/recovery_test_v2.py",
            "status": "PASS",
            "findings": [],
            "source_sha256": spec.evaluator_code_sha256,
        },
        "result": raw,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
    envelope["result_hash"] = canonical_hash(envelope)
    result_bytes = (canonical_json(envelope) + "\n").encode("utf-8")
    result_sha = hashlib.sha256(result_bytes).hexdigest()
    content_hash = canonical_hash({"result.json": result_sha})
    bundle_id = "{}-{}".format(
        retrieved_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        content_hash[:12],
    )
    bundle_dir = (
        tmp_path
        / "control_plane/evaluator_runs"
        / HYPOTHESIS_ID
        / retrieved_at.date().isoformat()
        / bundle_id
    )
    bundle_dir.mkdir(parents=True)
    (bundle_dir / "result.json").write_bytes(result_bytes)
    manifest = {
        "schema_version": "caerus_alpha_lab_control_plane_bundle_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "bundle_id": bundle_id,
        "retrieved_at": retrieved_at,
        "source_id": "alpha_lab.control_plane",
        "files": [
            {
                "name": "result.json",
                "bytes": len(result_bytes),
                "sha256": result_sha,
            }
        ],
        "credentials_persisted": False,
        "trading_behavior_changed": False,
        "promotion_performed": False,
        "purchase_performed": False,
    }
    (bundle_dir / "manifest.json").write_text(
        canonical_json(manifest) + "\n", encoding="utf-8"
    )
    return bundle_dir


def test_finalized_bundle_reconciliation_is_idempotent_after_crash(tmp_path):
    spec = _spec()
    ledger = _ledger(tmp_path, spec)
    bundle = _bundle(tmp_path, spec)

    first = reconcile_finalized_evaluator_bundle(
        bundle_dir=bundle,
        ledger=ledger,
        spec=spec,
        recorded_at=NOW + timedelta(hours=2),
    )
    assert first["added_trial_ids"] == [
        deterministic_trial_id(FAMILY_ID, 1),
        deterministic_trial_id(FAMILY_ID, 2),
    ]
    assert first["complete"] is True

    repeated = reconcile_finalized_evaluator_bundle(
        bundle_dir=bundle,
        ledger=ledger,
        spec=spec,
        recorded_at=NOW + timedelta(hours=3),
    )
    assert repeated["added_trial_ids"] == []
    assert repeated["verified_trial_ids"] == first["added_trial_ids"]
    assert repeated["complete"] is True


@pytest.mark.parametrize(
    "tamper",
    (
        "orders",
        "search_census",
        "boundary",
        "input_hash",
        "input_packet_hash",
        "schema",
    ),
)
def test_recovery_revalidates_semantics_not_only_hashes(tmp_path, tamper):
    spec = _spec()
    ledger = _ledger(tmp_path, spec)
    bundle = _bundle(tmp_path, spec)
    result_path = bundle / "result.json"
    envelope = json.loads(result_path.read_text(encoding="utf-8"))
    if tamper == "orders":
        envelope["result"]["orders_submitted"] = True
    elif tamper == "search_census":
        envelope["result"]["search_census"] = []
        envelope["result"]["search_census_hash"] = canonical_hash([])
        envelope["result"]["selection_trial_units"] = 0
    elif tamper == "boundary":
        envelope["boundary_attestation"] = {}
    elif tamper == "input_hash":
        envelope["input_source_sha256"] = _sha("different-input")
    elif tamper == "input_packet_hash":
        envelope["input_packet_hash"] = _sha("different-input-packet")
    else:
        envelope["schema_version"] = "self_consistent_but_invalid"
    unsigned = dict(envelope)
    unsigned.pop("result_hash")
    envelope["result_hash"] = canonical_hash(unsigned)
    result_bytes = (canonical_json(envelope) + "\n").encode("utf-8")
    result_path.write_bytes(result_bytes)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        {
            "name": "result.json",
            "bytes": len(result_bytes),
            "sha256": hashlib.sha256(result_bytes).hexdigest(),
        }
    ]
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    with pytest.raises((ContractValidationError, ResearchBoundaryError)):
        reconcile_finalized_evaluator_bundle(
            bundle_dir=bundle,
            ledger=ledger,
            spec=spec,
            recorded_at=NOW + timedelta(hours=2),
        )
    assert not any(
        item.event_type == ledger.RESULT_EVENT for item in ledger.store.read_all()
    )


def test_recovery_rejects_bundle_id_retained_after_coordinated_content_change(
    tmp_path,
):
    spec = _spec()
    ledger = _ledger(tmp_path, spec)
    bundle = _bundle(tmp_path, spec)
    result_path = bundle / "result.json"
    envelope = json.loads(result_path.read_text(encoding="utf-8"))
    envelope["result"]["variants"][0]["primary_metric_value"] = -0.02
    unsigned = dict(envelope)
    unsigned.pop("result_hash")
    envelope["result_hash"] = canonical_hash(unsigned)
    result_bytes = (canonical_json(envelope) + "\n").encode("utf-8")
    result_path.write_bytes(result_bytes)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = [
        {
            "name": "result.json",
            "bytes": len(result_bytes),
            "sha256": hashlib.sha256(result_bytes).hexdigest(),
        }
    ]
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")

    with pytest.raises(ContractValidationError, match="finalized content"):
        reconcile_finalized_evaluator_bundle(
            bundle_dir=bundle,
            ledger=ledger,
            spec=spec,
            recorded_at=NOW + timedelta(hours=2),
        )
    assert not any(
        item.event_type == ledger.RESULT_EVENT for item in ledger.store.read_all()
    )
