from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from projects.alpha_lab.factory import (
    AppendOnlyJSONLEventStore,
    ContractValidationError,
    EventStoreIntegrityError,
    ResearchBoundaryError,
)
from projects.alpha_lab.factory.canonical import canonical_hash
from projects.alpha_lab.factory.import_research_ledger import (
    EXACT_FAMILY_MAPPING,
    FROZEN_TRIAL_BUDGETS,
    KNOWN_PRIMARY_METRICS,
    OWNER_NORMALIZED_FAMILY_DEFINITIONS,
    _validate_migration_definition,
    audit_existing,
    bootstrap_inventory,
)


NOW = datetime(2026, 7, 24, 5, 18, tzinfo=timezone.utc)


def _owner_normalization_definition():
    return {
        "decision": "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION",
        "owner": "Brett Olson",
        "recorded_at": "2026-08-22T16:00:00Z",
        "family_mappings": deepcopy(EXACT_FAMILY_MAPPING),
        "family_definitions": deepcopy(OWNER_NORMALIZED_FAMILY_DEFINITIONS),
        "dependence_contract": {
            "assumption": "NO_POSITIVE_DEPENDENCE_CLAIM",
            "artifact_sha256": None,
        },
    }


_NORMALIZED_FAMILY_FIELDS = tuple(
    next(iter(OWNER_NORMALIZED_FAMILY_DEFINITIONS.values())).keys()
)


def _mutated_normalized_value(field, value):
    if field == "name":
        return "mutated-name"
    if field == "economic_mechanism":
        return "mutated-mechanism"
    if field == "primary_metric":
        return "mutated-primary-metric"
    if field == "benchmark":
        return "mutated-benchmark"
    if field == "expected_direction":
        return "LESS_THAN"
    if field in {"null_value", "economic_hurdle"}:
        return 0.001
    if field == "primary_variant_id":
        return "mutated-primary-variant"
    if field in {"maximum_trial_units", "selection_trial_budget"}:
        return int(value) + 1
    if field == "within_family_method":
        return "HOLM_BONFERRONI" if value != "HOLM_BONFERRONI" else "ROMANO_WOLF"
    if field == "family_alpha":
        return 0.09 if float(value) != 0.09 else 0.08
    if field == "legacy_ambiguity_blockers":
        return list(value) + ["MUTATED_LEGACY_BLOCKER"]
    raise AssertionError("unexpected normalized field: {}".format(field))


@pytest.mark.parametrize("hypothesis_id", sorted(EXACT_FAMILY_MAPPING))
@pytest.mark.parametrize("field", _NORMALIZED_FAMILY_FIELDS)
def test_owner_normalization_rejects_every_field_mutation_for_all_hypotheses(
    hypothesis_id, field
):
    """Each normalized migration field is immutable for every frozen HYP."""

    definition = _owner_normalization_definition()
    family_id = EXACT_FAMILY_MAPPING[hypothesis_id]
    raw = definition["family_definitions"][family_id]
    raw[field] = _mutated_normalized_value(field, raw[field])
    with pytest.raises(ContractValidationError):
        _validate_migration_definition(definition, {"hypothesis_sources": {}})


def test_owner_normalization_has_precise_metrics_for_every_hypothesis():
    assert set(KNOWN_PRIMARY_METRICS) == set(EXACT_FAMILY_MAPPING)
    assert all(
        not metric.startswith("LEGACY_FROZEN_METRIC")
        for metric in KNOWN_PRIMARY_METRICS.values()
    )


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(tmp_path):
    repo_root = tmp_path / "repo"
    data_root = repo_root / "outputs/research/alpha_lab"
    hypothesis_hashes = {}
    for hypothesis_id in FROZEN_TRIAL_BUDGETS:
        body = "# {} — Synthetic frozen hypothesis\n\n".format(hypothesis_id)
        frozen_hash = __import__("hashlib").sha256(body.encode("utf-8")).hexdigest()
        hypothesis_hashes[hypothesis_id] = frozen_hash
        hypothesis_path = (
            repo_root
            / "projects/alpha_lab/hypotheses/{}_synthetic.md".format(hypothesis_id)
        )
        hypothesis_path.parent.mkdir(parents=True, exist_ok=True)
        hypothesis_path.write_text(
            body
            + "## Freeze record\n\n- Spec hash: `sha256:{}`\n".format(
                frozen_hash
            ),
            encoding="utf-8",
        )
    bundle = data_root / "HYP-2026-006/run-001"
    bundle.mkdir(parents=True)
    provider_gate = {"assets": []}
    manifest = {
        "schema_version": "caerus_alpha_lab_run_manifest_v1",
        "hypothesis_id": "HYP-2026-006",
        "experiment_id": "EXP-2026-0006",
        "run_id": "run-001",
        "created_at": "2026-07-24T05:18:00Z",
        "hypothesis_hash": hypothesis_hashes["HYP-2026-006"],
        "provider_gate_hash": canonical_hash(provider_gate),
        "data_snapshot_hash": canonical_hash([]),
    }
    result = {
        "schema_version": "caerus_alpha_lab_data_gate_result_v1",
        "run_manifest_hash": canonical_hash(manifest),
        "outcome": "READY_FOR_FROZEN_EVALUATOR",
        "returns_accessed": False,
        "holdout_accessed": False,
    }
    _write_json(bundle / "run_manifest.json", manifest)
    _write_json(bundle / "result.json", result)
    _write_json(bundle / "provider_gate.json", provider_gate)
    evaluator_input = {"hypothesis_id": "HYP-2026-006", "assets": {}}
    _write_json(bundle / "evaluator_input.json", evaluator_input)
    store = AppendOnlyJSONLEventStore(bundle / "events.jsonl", research_root=data_root)
    store.append(
        event_id="gate-start",
        event_type="data_gate_started",
        occurred_at=NOW,
        recorded_at=NOW,
        payload={"run_id": "run-001"},
    )
    store.append(
        event_id="gate-review",
        event_type="data_gate_review",
        occurred_at=NOW,
        recorded_at=NOW,
        payload=result,
    )

    evaluator = data_root / (
        "control_plane/evaluator_runs/HYP-2026-006/2026-07-24/bundle/result.json"
    )
    unsigned_spec = {
        "schema_version": "caerus_alpha_lab_evaluator_spec_v1",
        "hypothesis_id": "HYP-2026-006",
        "evaluator_id": "synthetic",
        "technique_family": "OTHER",
        "module": "projects.alpha_lab.evaluators.synthetic",
        "callable_name": "evaluate",
        "maximum_variants": 2,
        "primary_metric": "worst_case_annualized_excess_return_after_costs",
        "data_contract_ids": ["synthetic.v1"],
        "challenge_period": "2025-01-01/2026-06-30",
    }
    spec = {**unsigned_spec, "spec_hash": canonical_hash(unsigned_spec)}
    _write_json(
        repo_root
        / "projects/alpha_lab/experiments/evaluator_specs/HYP-2026-006.json",
        spec,
    )
    evaluator_module = repo_root / "projects/alpha_lab/evaluators/synthetic.py"
    evaluator_module.parent.mkdir(parents=True, exist_ok=True)
    evaluator_module.write_text(
        "def evaluate(packet, phase):\n    return {}\n", encoding="utf-8"
    )
    evaluator_code_sha = __import__("hashlib").sha256(
        evaluator_module.read_bytes()
    ).hexdigest()
    grid = {
        phase: {
            "cost_scenarios": {
                cost: {"pessimistic": {}, "zero_incremental": {}}
                for cost in ("base", "stress")
            }
        }
        for phase in ("DISCOVERY", "VALIDATION")
    }
    envelope = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v1",
        "hypothesis_id": "HYP-2026-006",
        "input_packet_hash": canonical_hash(evaluator_input),
        "spec_hash": spec["spec_hash"],
        "phase": "DISCOVERY",
        "result": {
            "primary_metric_name": "worst_case_annualized_excess_return_after_costs",
            "primary_metric_value": -0.01,
            "variant_count": 2,
            "challenge_period_accessed": False,
            "variants": [
                {
                    "variant_id": "primary",
                    "phases": grid,
                    "worst_case_validation_annualized_excess_return_after_costs": -0.01,
                },
                {
                    "variant_id": "placebo",
                    "phases": grid,
                    "worst_case_validation_annualized_excess_return_after_costs": -0.02,
                },
            ],
        },
        "boundary_attestation": {"source_sha256": evaluator_code_sha},
    }
    envelope["result_hash"] = canonical_hash(envelope)
    _write_json(evaluator, envelope)
    result_bytes = evaluator.read_bytes()
    _write_json(
        evaluator.parent / "manifest.json",
        {
            "retrieved_at": "2026-07-24T05:30:00Z",
            "files": [
                {
                    "name": "result.json",
                    "bytes": len(result_bytes),
                    "sha256": __import__("hashlib").sha256(result_bytes).hexdigest(),
                }
            ],
        },
    )
    return repo_root, data_root


def test_import_audit_separates_gate_attempts_trials_and_robustness(tmp_path):
    repo_root, data_root = _fixture(tmp_path)
    report = audit_existing(repo_root=repo_root, data_root=data_root)
    assert report["data_gate_attempt_count"] == 1
    assert report["statistical_trial_count"] == 2
    assert report["model_trial_count"] == 2
    assert report["robustness_record_count"] == 2
    assert report["challenge_read_count"] == 0
    assert report["family_mapping_owner_ratified"] is False
    assert report["data_gate_status_counts"] == {"READY_FOR_FROZEN_EVALUATOR": 1}
    assert [
        item["worst_case_validation_annualized_excess_return_after_costs"]
        for item in report["evaluator_batches"][0]["variants"]
    ] == [-0.01, -0.02]


def test_import_audit_rejects_provider_hash_tampering(tmp_path):
    repo_root, data_root = _fixture(tmp_path)
    provider = next(data_root.glob("HYP-2026-006/*/provider_gate.json"))
    _write_json(provider, {"assets": [{"asset_id": "tampered", "files": []}]})
    with pytest.raises(ContractValidationError, match="provider gate hash mismatch"):
        audit_existing(repo_root=repo_root, data_root=data_root)


def test_import_write_fails_outside_canonical_gcp_even_with_ratification(tmp_path):
    repo_root, data_root = _fixture(tmp_path)
    inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    with pytest.raises(ResearchBoundaryError, match="canonical GCP"):
        bootstrap_inventory(
            repo_root=repo_root,
            data_root=data_root,
            inventory=inventory,
            signed_plan={},
            publication_authorization={},
            identity_history=None,
        )


def test_removed_unsigned_bootstrap_signature_is_rejected(tmp_path):
    """The old mutable/idempotent bootstrap API cannot bypass QS-003."""

    with pytest.raises(TypeError):
        bootstrap_inventory(
            repo_root=tmp_path,
            data_root=tmp_path,
            inventory={},
            ratification={},
            ratification_path=tmp_path / "removed.json",
            recorded_at=NOW,
        )
