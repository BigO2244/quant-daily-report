"""Idempotently close ledger trials from an already finalized evaluator bundle."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from projects.alpha_lab.factory.canonical import canonical_hash, parse_datetime
from projects.alpha_lab.factory.errors import (
    ContractValidationError,
    EventStoreIntegrityError,
    ResearchBoundaryError,
)
from projects.alpha_lab.factory.research_ledger import (
    GlobalResearchLedger,
    ResearchRunClass,
    TrialOutcome,
    TrialResult,
)

from .evaluator import (
    EvaluationPhase,
    EvaluatorSpec,
    validate_evaluator_result_envelope,
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_object(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(
            "finalized evaluator bundle contains invalid JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ContractValidationError("finalized evaluator artifact must be an object")
    return value


def _verify_bundle(
    bundle_dir: Path, *, research_root: Path
) -> tuple[Mapping[str, Any], Mapping[str, Any], Path, str, datetime]:
    root = research_root.expanduser().resolve()
    bundle_dir = bundle_dir.expanduser().resolve()
    try:
        relative = bundle_dir.relative_to(root)
    except ValueError as exc:
        raise ResearchBoundaryError(
            "evaluator recovery bundle must be inside the research root"
        ) from exc
    if ".staging" in relative.parts:
        raise ResearchBoundaryError("staged evaluator bundles cannot close trials")
    manifest_path = bundle_dir / "manifest.json"
    result_path = bundle_dir / "result.json"
    if not manifest_path.is_file() or not result_path.is_file():
        raise ContractValidationError(
            "recovery requires a finalized manifest and result artifact"
        )
    manifest = _load_object(manifest_path)
    envelope = _load_object(result_path)
    if (
        manifest.get("schema_version")
        != "caerus_alpha_lab_control_plane_bundle_v1"
        or manifest.get("source_id") != "alpha_lab.control_plane"
        or manifest.get("classification") != "RESEARCH_ONLY_NON_EXECUTIONAL"
        or manifest.get("promotion_performed") is not False
        or manifest.get("trading_behavior_changed") is not False
        or manifest.get("credentials_persisted") is not False
        or manifest.get("purchase_performed") is not False
    ):
        raise ContractValidationError("evaluator bundle manifest is not research-only")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ContractValidationError("evaluator bundle manifest files are invalid")
    result_rows = [item for item in files if item.get("name") == "result.json"]
    result_bytes = result_path.read_bytes()
    result_sha = _sha256_bytes(result_bytes)
    if len(files) != 1 or len(result_rows) != 1 or result_rows[0] != {
        "name": "result.json",
        "bytes": len(result_bytes),
        "sha256": result_sha,
    }:
        raise ContractValidationError("evaluator result differs from its bundle manifest")
    if manifest.get("bundle_id") != bundle_dir.name:
        raise ContractValidationError("evaluator bundle identity is inconsistent")
    retrieved_at = parse_datetime(str(manifest.get("retrieved_at")))
    content_hash = canonical_hash({"result.json": result_sha})
    timestamp = retrieved_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    expected_bundle_id = "{}-{}".format(timestamp, content_hash[:12])
    if manifest.get("bundle_id") != expected_bundle_id:
        raise ContractValidationError(
            "evaluator bundle identity is not bound to finalized content"
        )
    unsigned = dict(envelope)
    supplied_hash = unsigned.pop("result_hash", None)
    if supplied_hash != canonical_hash(unsigned):
        raise ContractValidationError("evaluator result_hash is invalid")
    return manifest, envelope, result_path, result_sha, retrieved_at


def reconcile_finalized_evaluator_bundle(
    *,
    bundle_dir: Path,
    ledger: GlobalResearchLedger,
    spec: EvaluatorSpec,
    recorded_at: datetime,
) -> Dict[str, Any]:
    """Close missing results and verify matching closures after an interrupted run.

    The finalized bundle is immutable evidence. Repeated calls add only missing
    result events; an existing closure with a different payload fails closed.
    """

    if not isinstance(ledger, GlobalResearchLedger):
        raise ContractValidationError("recovery requires a verified research ledger")
    ledger.project()
    manifest, envelope, result_path, result_sha, retrieved_at = _verify_bundle(
        bundle_dir, research_root=ledger.store.research_root
    )
    if spec.schema_version != "caerus_alpha_lab_evaluator_spec_v2":
        raise ContractValidationError("only v2 evaluator bundles can close new trials")
    relative_bundle = result_path.parent.relative_to(ledger.store.research_root)
    if relative_bundle.parts != (
        "control_plane",
        "evaluator_runs",
        str(spec.hypothesis_id),
        retrieved_at.date().isoformat(),
        str(manifest["bundle_id"]),
    ):
        raise ResearchBoundaryError(
            "evaluator recovery bundle is outside its canonical hypothesis namespace"
        )
    phase = validate_evaluator_result_envelope(spec=spec, envelope=envelope)
    expected_envelope = {
        "hypothesis_id": spec.hypothesis_id,
        "family_id": spec.family_id,
        "experiment_id": spec.experiment_id,
        "exploratory_wave_id": spec.exploratory_wave_id,
        "challenge_epoch_id": spec.challenge_epoch_id,
        "evaluator_id": spec.evaluator_id,
        "spec_hash": spec.spec_hash,
    }
    if any(envelope.get(key) != value for key, value in expected_envelope.items()):
        raise ContractValidationError("finalized result differs from the frozen spec")
    try:
        trial_ids = tuple(str(item) for item in envelope["registered_trial_ids"])
        result = envelope["result"]
        variants = result["variants"]
    except (KeyError, TypeError) as exc:
        raise ContractValidationError("evaluator result envelope is incomplete") from exc
    if not isinstance(result, Mapping) or not isinstance(variants, list):
        raise ContractValidationError("evaluator result rows are invalid")
    if len(trial_ids) != len(variants) or len(trial_ids) != result.get("variant_count"):
        raise ContractValidationError("trial closure census differs from evaluator output")
    run_class = (
        ResearchRunClass.CHALLENGE_READ
        if phase is EvaluationPhase.CHALLENGE
        else ResearchRunClass.MODEL_TRIAL
    )
    runs = ledger.require_registered_trials(trial_ids, run_class=run_class)
    expected_run_bindings = {
        "family_id": spec.family_id,
        "hypothesis_id": spec.hypothesis_id,
        "experiment_id": spec.experiment_id,
        "primary_metric": spec.primary_metric,
        "evaluator_spec_sha256": spec.spec_hash,
        "code_sha256": spec.evaluator_code_sha256,
        "effective_sample_floor": spec.effective_sample_floor,
    }
    if any(
        any(run.get(key) != value for key, value in expected_run_bindings.items())
        for run in runs
    ):
        raise ContractValidationError("registered trials differ from the frozen spec")
    if any(
        run.get("data_snapshot_sha256") != envelope["input_source_sha256"]
        for run in runs
    ):
        raise ContractValidationError(
            "finalized input hash differs from registered trial inputs"
        )
    registered_contracts = [
        {
            "statistical_trial_id": trial_id,
            "variant_id": run.get("variant_id"),
            "variant_definition_hash": run.get("variant_definition_hash"),
        }
        for trial_id, run in zip(trial_ids, runs)
    ]
    expected_contracts = [
        {"statistical_trial_id": trial_id, **variant.to_dict()}
        for trial_id, variant in zip(trial_ids, spec.frozen_variants)
    ]
    if (
        registered_contracts != expected_contracts
        or envelope.get("registered_trial_contracts") != expected_contracts
        or envelope.get("frozen_variant_contract_hash")
        != canonical_hash(spec.frozen_variant_dicts)
        or [
            {
                "variant_id": item.get("variant_id"),
                "variant_definition_hash": item.get("variant_definition_hash"),
            }
            for item in variants
        ]
        != spec.frozen_variant_dicts
    ):
        raise ContractValidationError(
            "finalized variants differ from the registered frozen contracts"
        )
    selection_units = sum(int(run.get("selection_trial_units", 0)) for run in runs)
    if (
        selection_units != spec.selection_trial_units
        or envelope.get("selection_trial_units") != spec.selection_trial_units
        or envelope.get("search_census_hash") != spec.search_census_hash
    ):
        raise ContractValidationError(
            "finalized search accounting differs from registered trials"
        )
    if phase is EvaluationPhase.CHALLENGE:
        receipt_hash = envelope.get("challenge_access_receipt_hash")
        receipts = [
            record
            for record in ledger.store.read_all()
            if record.event_type == ledger.HOLDOUT_EVENT
            and record.event_hash == receipt_hash
        ]
        if len(receipts) != 1:
            raise ContractValidationError(
                "challenge result is not bound to a canonical access event"
            )
        receipt = receipts[0]
        if (
            len(trial_ids) != 1
            or receipt.payload.get("challenge_epoch_id")
            != spec.challenge_epoch_id
            or receipt.payload.get("trial_ids") != list(trial_ids)
            or receipt.payload.get("input_sha256_by_trial")
            != {
                trial_id: envelope["input_source_sha256"]
                for trial_id in trial_ids
            }
        ):
            raise ContractValidationError(
                "challenge access event differs from finalized entrant bindings"
            )
        if retrieved_at < receipt.occurred_at:
            raise ContractValidationError("challenge result predates holdout access")
    existing = {
        record.payload["statistical_trial_id"]: record.payload
        for record in ledger.store.read_all()
        if record.event_type == ledger.RESULT_EVENT
    }
    added = []
    verified = []
    for trial_id, variant in zip(trial_ids, variants):
        try:
            trial_result = TrialResult(
                statistical_trial_id=trial_id,
                outcome=TrialOutcome(str(variant["evidence_verdict"])),
                recorded_at=retrieved_at,
                primary_metric=str(result["primary_metric_name"]),
                primary_metric_value=variant.get("primary_metric_value"),
                p_value=variant.get("p_value"),
                inference_eligible=variant["inference_eligible"],
                ineligibility_reasons=tuple(variant["ineligibility_reasons"]),
                stress_scenario_pass=variant["stress_scenario_pass"],
                capacity_and_concentration_pass=variant[
                    "capacity_and_concentration_pass"
                ],
                effective_sample_size=variant["effective_sample_size"],
                minimum_effective_sample=int(spec.effective_sample_floor or 0),
                source_artifact=str(result_path),
                source_sha256=result_sha,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ContractValidationError("evaluator result row is incomplete") from exc
        expected_payload = trial_result.to_dict()
        prior = existing.get(trial_id)
        if prior is not None:
            if canonical_hash(prior) != canonical_hash(expected_payload):
                raise EventStoreIntegrityError(
                    "existing trial closure conflicts with the finalized bundle"
                )
            verified.append(trial_id)
            continue
        ledger.record_result(trial_result, recorded_at=recorded_at)
        added.append(trial_id)
    return {
        "schema_version": "caerus_alpha_lab_evaluator_reconciliation_v1",
        "bundle_id": manifest.get("bundle_id"),
        "result_sha256": result_sha,
        "added_trial_ids": added,
        "verified_trial_ids": verified,
        "complete": len(added) + len(verified) == len(trial_ids),
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
