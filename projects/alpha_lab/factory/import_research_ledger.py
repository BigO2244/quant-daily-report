"""Audit and migrate existing Alpha Lab evidence into the global ledger.

Dry-run is the default. Canonical writes require both the exact GCP repository
root and an owner-ratified migration manifest. The importer never treats a
data-gate attempt, cost scenario, validation window, or regime cell as a
statistical trial.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .canonical import canonical_hash, canonical_json, parse_datetime
from .errors import ContractValidationError, ResearchBoundaryError
from .research_ledger import (
    ExpectedDirection,
    GlobalResearchLedger,
    HypothesisFamily,
    InferenceTrack,
    MultipleTestingMethod,
    ResearchPhase,
    ResearchExperiment,
    ResearchRun,
    ResearchRunClass,
    ResearchWave,
    TrialOutcome,
    TrialResult,
    deterministic_attempt_id,
    deterministic_trial_id,
)
from .store import AppendOnlyJSONLEventStore


AUTHORITATIVE_REPO_ROOT = Path("/mnt/disks/alpha-lab/alpha-lab-project")
AUTHORITATIVE_DATA_ROOT = AUTHORITATIVE_REPO_ROOT / "outputs/research/alpha_lab"
LEDGER_RELATIVE_PATH = Path("outputs/research/alpha_lab/ledger/research_events.v1.jsonl")
LEGACY_WAVE_ID = "WAVE-2026-001"
LEGACY_CHALLENGE_EPOCH_ID = "CHALLENGE-2026-001"
_DECLARED_SPEC_HASH = re.compile(r"Spec hash: `sha256:([0-9a-f]{64})`")
EXPECTED_CANONICAL_GATE_COUNT = 66
EXPECTED_CANONICAL_GATE_STATUSES = {
    "BLOCKED_DATA": 60,
    "READY_FOR_FROZEN_EVALUATOR": 6,
}
EXPECTED_CANONICAL_VARIANTS_BY_HYPOTHESIS = {
    "HYP-2026-006": 3,
    "HYP-2026-007": 2,
    "HYP-2026-008": 3,
}
EXPECTED_CANONICAL_ROBUSTNESS_COUNT = 8
EXPECTED_CANONICAL_CHALLENGE_READ_COUNT = 0
EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES = frozenset(
    "HYP-2026-{:03d}".format(index) for index in range(1, 14)
)

FROZEN_TRIAL_BUDGETS = {
    "HYP-2026-001": 6,
    "HYP-2026-002": 6,
    "HYP-2026-003": 5,
    "HYP-2026-004": 6,
    "HYP-2026-005": 4,
    "HYP-2026-006": 3,
    "HYP-2026-007": 2,
    "HYP-2026-008": 3,
    "HYP-2026-009": 4,
    "HYP-2026-010": 3,
    "HYP-2026-011": 2,
    "HYP-2026-012": 2,
    "HYP-2026-013": 3,
}

KNOWN_PRIMARY_METRICS = {
    "HYP-2026-001": "worst_case_cost_adjusted_incremental_information_ratio",
    "HYP-2026-006": "worst_case_annualized_excess_return_after_costs",
    "HYP-2026-007": "worst_case_annualized_excess_return_after_costs",
    "HYP-2026-008": "worst_case_annualized_excess_return_after_costs",
    "HYP-2026-009": "validation_delta_portfolio_information_ratio",
    "HYP-2026-010": "validation_60d_factor_residual_car_after_costs",
    "HYP-2026-011": "worst_case_annualized_excess_return_after_costs",
    "HYP-2026-012": "worst_case_annualized_excess_return_after_costs",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ContractValidationError("{} must contain a JSON object".format(path))
    return value


def _receipt(paths: Iterable[Path], root: Path) -> str:
    mapping = {
        str(path.relative_to(root)): _sha256_file(path)
        for path in sorted(set(paths))
    }
    return canonical_hash(mapping)


def _family_id(hypothesis_id: str) -> str:
    return "FAM-{}".format(hypothesis_id.removeprefix("HYP-"))


def _experiment_id(manifest: Mapping[str, Any]) -> str:
    value = str(manifest.get("experiment_id", ""))
    if not value:
        raise ContractValidationError("run manifest is missing experiment_id")
    return value


def _hypothesis_source(repo_root: Path, hypothesis_id: str) -> Optional[Path]:
    matches = sorted((repo_root / "projects/alpha_lab/hypotheses").glob(hypothesis_id + "*"))
    if len(matches) > 1:
        raise ContractValidationError(
            "multiple frozen hypothesis sources found for {}".format(hypothesis_id)
        )
    return matches[0] if matches else None


def _frozen_hypothesis_source(
    repo_root: Path, hypothesis_id: str
) -> Tuple[Path, str]:
    path = _hypothesis_source(repo_root, hypothesis_id)
    if path is None:
        raise ContractValidationError(
            "frozen hypothesis source is missing for {}".format(hypothesis_id)
        )
    text = path.read_text(encoding="utf-8")
    marker = "## Freeze record\n"
    match = _DECLARED_SPEC_HASH.search(text)
    if marker not in text or match is None:
        raise ContractValidationError(
            "frozen hypothesis contract is incomplete for {}".format(hypothesis_id)
        )
    frozen_hash = hashlib.sha256(text.split(marker, 1)[0].encode("utf-8")).hexdigest()
    if frozen_hash != match.group(1):
        raise ContractValidationError(
            "frozen hypothesis body hash mismatch for {}".format(hypothesis_id)
        )
    return path, frozen_hash


def audit_existing(*, repo_root: Path, data_root: Path) -> Dict[str, Any]:
    """Return a read-only, hash-verified inventory of legacy evidence."""

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    manifests = sorted(data_root.glob("HYP-2026-[0-9][0-9][0-9]/*/run_manifest.json"))
    gate_rows: List[Dict[str, Any]] = []
    event_paths: List[Path] = []
    result_paths: List[Path] = []
    provider_paths: List[Path] = []
    evaluator_input_paths: List[Path] = []
    manifest_paths: List[Path] = []
    evaluator_manifest_paths: List[Path] = []
    hypothesis_sources = {
        hypothesis_id: _frozen_hypothesis_source(repo_root, hypothesis_id)
        for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS)
    }
    for manifest_path in manifests:
        bundle = manifest_path.parent
        result_path = bundle / "result.json"
        event_path = bundle / "events.jsonl"
        provider_path = bundle / "provider_gate.json"
        evaluator_input_path = bundle / "evaluator_input.json"
        if not all(path.is_file() for path in (result_path, event_path, provider_path)):
            raise ContractValidationError("incomplete data-gate bundle: {}".format(bundle))
        manifest = _load_json(manifest_path)
        result = _load_json(result_path)
        provider = _load_json(provider_path)
        if result.get("run_manifest_hash") != canonical_hash(manifest):
            raise ContractValidationError("run manifest hash mismatch: {}".format(bundle))
        hypothesis_id = str(manifest.get("hypothesis_id", ""))
        source = hypothesis_sources.get(hypothesis_id)
        if source is None or manifest.get("hypothesis_hash") != source[1]:
            raise ContractValidationError(
                "run manifest hypothesis hash mismatch: {}".format(bundle)
            )
        if manifest.get("provider_gate_hash") != canonical_hash(provider):
            raise ContractValidationError("provider gate hash mismatch: {}".format(bundle))
        assets = provider.get("assets")
        if not isinstance(assets, list):
            raise ContractValidationError("provider gate assets are missing: {}".format(bundle))
        data_snapshot = [
            {"asset_id": item["asset_id"], "files": item["files"]}
            for item in assets
        ]
        if manifest.get("data_snapshot_hash") != canonical_hash(data_snapshot):
            raise ContractValidationError("data snapshot hash mismatch: {}".format(bundle))
        store = AppendOnlyJSONLEventStore(event_path, research_root=data_root)
        events = store.read_all()
        if len(events) != 2 or [event.event_type for event in events] != [
            "data_gate_started",
            "data_gate_review",
        ]:
            raise ContractValidationError("unexpected data-gate event chain: {}".format(bundle))
        if canonical_hash(events[-1].payload) != canonical_hash(result):
            raise ContractValidationError("terminal event differs from result: {}".format(bundle))
        gate_rows.append(
            {
                "hypothesis_id": manifest["hypothesis_id"],
                "hypothesis_hash": manifest["hypothesis_hash"],
                "experiment_id": _experiment_id(manifest),
                "run_id": manifest["run_id"],
                "occurred_at": manifest["created_at"],
                "source_artifact": str(manifest_path),
                "source_sha256": _sha256_file(manifest_path),
                "terminal_event_hash": events[-1].event_hash,
                "data_gate_status": result.get("outcome", result.get("data_gate_status")),
                "returns_accessed": bool(result.get("returns_accessed", False)),
                "holdout_accessed": bool(result.get("holdout_accessed", False)),
                "evaluator_input_hash": (
                    canonical_hash(_load_json(evaluator_input_path))
                    if evaluator_input_path.is_file()
                    else None
                ),
            }
        )
        if gate_rows[-1]["returns_accessed"] or gate_rows[-1]["holdout_accessed"]:
            raise ContractValidationError("data-gate attempt accessed outcome data")
        manifest_paths.append(manifest_path)
        result_paths.append(result_path)
        event_paths.append(event_path)
        provider_paths.append(provider_path)
        if evaluator_input_path.is_file():
            evaluator_input_paths.append(evaluator_input_path)

    evaluator_paths = sorted(
        data_root.glob("control_plane/evaluator_runs/HYP-2026-[0-9][0-9][0-9]/*/*/result.json")
    )
    evaluator_rows: List[Dict[str, Any]] = []
    for path in evaluator_paths:
        envelope = _load_json(path)
        bundle_manifest_path = path.parent / "manifest.json"
        bundle_manifest = _load_json(bundle_manifest_path)
        evaluator_manifest_paths.append(bundle_manifest_path)
        result_record = next(
            (
                item
                for item in bundle_manifest.get("files", [])
                if item.get("name") == "result.json"
            ),
            None,
        )
        if (
            result_record is None
            or int(result_record.get("bytes", -1)) != path.stat().st_size
            or result_record.get("sha256") != _sha256_file(path)
        ):
            raise ContractValidationError("evaluator bundle manifest mismatch: {}".format(path))
        if canonical_hash({key: value for key, value in envelope.items() if key != "result_hash"}) != envelope.get(
            "result_hash"
        ):
            raise ContractValidationError("evaluator result hash mismatch: {}".format(path))
        result = envelope.get("result")
        if not isinstance(result, Mapping):
            raise ContractValidationError("evaluator result payload is missing")
        variants = result.get("variants")
        if not isinstance(variants, list) or len(variants) != result.get("variant_count"):
            raise ContractValidationError("evaluator variant census mismatch: {}".format(path))
        if envelope.get("phase") == ResearchPhase.CHALLENGE.value or result.get(
            "challenge_period_accessed"
        ):
            raise ContractValidationError("legacy inventory unexpectedly accessed challenge data")
        for variant in variants:
            phases = variant.get("phases")
            if not isinstance(phases, Mapping) or set(phases) != {
                "DISCOVERY",
                "VALIDATION",
            }:
                raise ContractValidationError("legacy robustness windows are incomplete")
            grid_cells = 0
            for phase_payload in phases.values():
                costs = phase_payload.get("cost_scenarios")
                if not isinstance(costs, Mapping) or set(costs) != {"base", "stress"}:
                    raise ContractValidationError("legacy robustness cost grid is incomplete")
                for cost_payload in costs.values():
                    if not isinstance(cost_payload, Mapping) or not {
                        "pessimistic",
                        "zero_incremental",
                    }.issubset(cost_payload):
                        raise ContractValidationError(
                            "legacy robustness terminal grid is incomplete"
                        )
                    grid_cells += 2
            if grid_cells != 8:
                raise ContractValidationError("legacy robustness grid must contain eight cells")
        hypothesis_id = str(envelope["hypothesis_id"])
        matching_gates = [
            row
            for row in gate_rows
            if row["hypothesis_id"] == hypothesis_id
            and row["evaluator_input_hash"] == envelope["input_packet_hash"]
        ]
        if len(matching_gates) != 1:
            raise ContractValidationError(
                "evaluator input must join exactly one authoritative data gate"
            )
        spec_path = (
            repo_root
            / "projects/alpha_lab/experiments/evaluator_specs"
            / "{}.json".format(hypothesis_id)
        )
        spec = _load_json(spec_path)
        unsigned_spec = {key: value for key, value in spec.items() if key != "spec_hash"}
        if canonical_hash(unsigned_spec) != spec.get("spec_hash") or envelope.get(
            "spec_hash"
        ) != spec.get("spec_hash"):
            raise ContractValidationError("evaluator spec hash mismatch: {}".format(path))
        if int(result["variant_count"]) != int(spec["maximum_variants"]):
            raise ContractValidationError(
                "legacy evaluator variant count differs from the frozen ceiling"
            )
        evaluator_module_path = repo_root / Path(
            str(spec["module"]).replace(".", "/") + ".py"
        )
        boundary = envelope.get("boundary_attestation")
        if (
            not isinstance(boundary, Mapping)
            or not evaluator_module_path.is_file()
            or boundary.get("source_sha256") != _sha256_file(evaluator_module_path)
        ):
            raise ContractValidationError(
                "evaluator code hash does not match the frozen source module"
            )
        evaluator_rows.append(
            {
                "hypothesis_id": hypothesis_id,
                "experiment_id": matching_gates[0]["experiment_id"],
                "source_artifact": str(path),
                "source_sha256": _sha256_file(path),
                "result_hash": envelope["result_hash"],
                "input_packet_hash": envelope["input_packet_hash"],
                "primary_metric": result["primary_metric_name"],
                "primary_metric_value": result.get("primary_metric_value"),
                "retrieved_at": bundle_manifest["retrieved_at"],
                "evaluator_spec_sha256": spec["spec_hash"],
                "evaluator_code_sha256": boundary["source_sha256"],
                "effective_sample_floor": int(spec.get("effective_sample_floor", 1)),
                "variant_ids": [str(item["variant_id"]) for item in variants],
                "variants": variants,
                "phase": envelope["phase"],
            }
        )

    variant_count = sum(len(item["variant_ids"]) for item in evaluator_rows)
    hypothesis_source_paths = [item[0] for item in hypothesis_sources.values()]
    for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS):
        experiment_ids = {
            row["experiment_id"]
            for row in gate_rows
            if row["hypothesis_id"] == hypothesis_id
        }
        if len(experiment_ids) > 1:
            raise ContractValidationError(
                "hypothesis maps to multiple legacy experiments: {}".format(
                    hypothesis_id
                )
            )
    status_counts: Dict[str, int] = {}
    for row in gate_rows:
        key = str(row["data_gate_status"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "schema_version": "caerus_alpha_lab_legacy_inventory_v1",
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "source_receipts": {
            "run_manifests": _receipt(manifest_paths, data_root),
            "gate_results": _receipt(result_paths, data_root),
            "event_chains": _receipt(event_paths, data_root),
            "provider_gates": _receipt(provider_paths, data_root),
            "evaluator_inputs": _receipt(evaluator_input_paths, data_root),
            "evaluator_results": _receipt(evaluator_paths, data_root),
            "evaluator_manifests": _receipt(evaluator_manifest_paths, data_root),
            "hypothesis_sources": _receipt(hypothesis_source_paths, repo_root),
        },
        "data_gate_attempt_count": len(gate_rows),
        "data_gate_status_counts": status_counts,
        "model_trial_count": variant_count,
        "robustness_record_count": variant_count,
        "challenge_read_count": 0,
        "statistical_trial_count": variant_count,
        "gate_attempts": gate_rows,
        "evaluator_batches": evaluator_rows,
        "proposed_family_mapping": {
            hypothesis_id: _family_id(hypothesis_id)
            for hypothesis_id in sorted(FROZEN_TRIAL_BUDGETS)
        },
        "family_mapping_owner_ratified": False,
        "migration_blockers": [
            "OWNER_RATIFICATION_REQUIRED_FOR_FAMILY_MAPPING",
            "LEGACY_CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",
            "NO_CHALLENGE_ACCESS_OR_CONFIRMATION",
        ],
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }


def _validate_ratification(
    ratification: Mapping[str, Any],
    inventory: Mapping[str, Any],
    *,
    ratification_path: Path,
) -> Mapping[str, str]:
    if ratification.get("decision") != "RATIFY_GLOBAL_RESEARCH_LEDGER_MIGRATION":
        raise ContractValidationError("owner ratification decision is missing")
    if ratification.get("owner") != "Brett Olson":
        raise ContractValidationError("migration ratification must name Brett Olson")
    if ratification.get("source_receipts") != inventory["source_receipts"]:
        raise ContractValidationError("ratification does not bind the audited source snapshot")
    declared_artifact = Path(str(ratification.get("artifact", ""))).expanduser().resolve()
    if declared_artifact != ratification_path.expanduser().resolve():
        raise ContractValidationError("ratification artifact path does not match the reviewed file")
    if not declared_artifact.is_file():
        raise ContractValidationError("ratification artifact is unavailable")
    if canonical_hash(_load_json(declared_artifact)) != canonical_hash(ratification):
        raise ContractValidationError(
            "ratification payload differs from the reviewed artifact"
        )
    unsigned_ratification = {
        key: value for key, value in ratification.items() if key != "artifact_sha256"
    }
    if canonical_hash(unsigned_ratification) != ratification.get("artifact_sha256"):
        raise ContractValidationError("ratification artifact hash mismatch")
    mappings = ratification.get("family_mappings")
    if not isinstance(mappings, Mapping):
        raise ContractValidationError("ratification requires explicit family_mappings")
    expected = set(FROZEN_TRIAL_BUDGETS)
    if set(mappings) != expected:
        raise ContractValidationError("ratification must resolve all 13 registered hypotheses")
    grouped: Dict[str, List[str]] = {}
    for hypothesis_id, family_id in mappings.items():
        grouped.setdefault(str(family_id), []).append(str(hypothesis_id))
    definitions = ratification.get("family_definitions", {})
    if not isinstance(definitions, Mapping):
        raise ContractValidationError("family_definitions must be a mapping")
    for family_id, hypothesis_ids in grouped.items():
        if family_id not in definitions:
            raise ContractValidationError(
                "an explicit family definition is required for every owner-ratified family"
            )
        required = {
            "name",
            "economic_mechanism",
            "primary_metric",
            "benchmark",
            "expected_direction",
            "null_value",
            "economic_hurdle",
            "primary_variant_id",
            "maximum_trial_units",
            "selection_trial_budget",
            "within_family_method",
            "family_alpha",
        }
        if not isinstance(definitions[family_id], Mapping) or not required.issubset(
            definitions[family_id]
        ):
            raise ContractValidationError(
                "family definition must freeze all substantive fields"
            )
    return {str(key): str(value) for key, value in mappings.items()}


def _source_for_family(repo_root: Path, hypothesis_id: str) -> Tuple[str, str]:
    path, frozen_hash = _frozen_hypothesis_source(repo_root, hypothesis_id)
    return str(path), frozen_hash


def _validate_canonical_census(inventory: Mapping[str, Any]) -> None:
    variant_counts: Dict[str, int] = {}
    for batch in inventory["evaluator_batches"]:
        hypothesis_id = str(batch["hypothesis_id"])
        variant_counts[hypothesis_id] = variant_counts.get(hypothesis_id, 0) + len(
            batch["variant_ids"]
        )
    experiment_hypotheses = set()
    for hypothesis_id in EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES:
        experiment_ids = {
            row["experiment_id"]
            for row in inventory["gate_attempts"]
            if row["hypothesis_id"] == hypothesis_id
        }
        if len(experiment_ids) == 1:
            experiment_hypotheses.add(hypothesis_id)
    actual = {
        "data_gate_attempt_count": inventory["data_gate_attempt_count"],
        "data_gate_status_counts": dict(inventory["data_gate_status_counts"]),
        "data_gate_hypotheses": sorted(
            {str(row["hypothesis_id"]) for row in inventory["gate_attempts"]}
        ),
        "variants_by_hypothesis": variant_counts,
        "robustness_record_count": inventory["robustness_record_count"],
        "challenge_read_count": inventory["challenge_read_count"],
        "experiment_hypotheses": sorted(experiment_hypotheses),
    }
    expected = {
        "data_gate_attempt_count": EXPECTED_CANONICAL_GATE_COUNT,
        "data_gate_status_counts": EXPECTED_CANONICAL_GATE_STATUSES,
        "data_gate_hypotheses": sorted(
            EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES
        ),
        "variants_by_hypothesis": EXPECTED_CANONICAL_VARIANTS_BY_HYPOTHESIS,
        "robustness_record_count": EXPECTED_CANONICAL_ROBUSTNESS_COUNT,
        "challenge_read_count": EXPECTED_CANONICAL_CHALLENGE_READ_COUNT,
        "experiment_hypotheses": sorted(EXPECTED_CANONICAL_EXPERIMENT_HYPOTHESES),
    }
    if canonical_hash(actual) != canonical_hash(expected):
        raise ContractValidationError(
            "canonical migration census differs from the one-time reviewed baseline"
        )


def bootstrap_inventory(
    *,
    repo_root: Path,
    data_root: Path,
    inventory: Mapping[str, Any],
    ratification: Mapping[str, Any],
    ratification_path: Path,
    recorded_at: datetime,
    _ledger_path_override: Optional[Path] = None,
    _preflight_complete: bool = False,
) -> Dict[str, Any]:
    """Append deterministic migration events after explicit owner ratification."""

    repo_root = repo_root.resolve()
    data_root = data_root.resolve()
    if repo_root != AUTHORITATIVE_REPO_ROOT or data_root != AUTHORITATIVE_DATA_ROOT:
        raise ResearchBoundaryError("global ledger writes are permitted only on canonical GCP")
    fresh_inventory = audit_existing(repo_root=repo_root, data_root=data_root)
    if canonical_hash(fresh_inventory) != canonical_hash(inventory):
        raise ContractValidationError("supplied inventory differs from a fresh canonical audit")
    inventory = fresh_inventory
    _validate_canonical_census(inventory)
    mappings = _validate_ratification(
        ratification,
        inventory,
        ratification_path=ratification_path,
    )
    canonical_ledger_path = repo_root / LEDGER_RELATIVE_PATH
    if not _preflight_complete:
        with tempfile.TemporaryDirectory(
            prefix=".ledger-preflight-", dir=str(data_root)
        ) as temporary_directory:
            scratch_ledger_path = Path(temporary_directory) / "research_events.v1.jsonl"
            canonical_existed = canonical_ledger_path.exists()
            if canonical_existed:
                if canonical_ledger_path.is_symlink() or not canonical_ledger_path.is_file():
                    raise ResearchBoundaryError(
                        "canonical ledger path must be a regular non-symlink file"
                    )
                shutil.copyfile(canonical_ledger_path, scratch_ledger_path)
            preflight_report = bootstrap_inventory(
                repo_root=repo_root,
                data_root=data_root,
                inventory=inventory,
                ratification=ratification,
                ratification_path=ratification_path,
                recorded_at=recorded_at,
                _ledger_path_override=scratch_ledger_path,
                _preflight_complete=True,
            )
            canonical_ledger_path.parent.mkdir(parents=False, exist_ok=True)
            if canonical_existed:
                if canonical_ledger_path.read_bytes() != scratch_ledger_path.read_bytes():
                    raise ContractValidationError(
                        "existing canonical ledger is incomplete or changed; automatic repair is forbidden"
                    )
                verified_report = dict(preflight_report)
                verified_report["appended_event_count"] = 0
                verified_report["appended_event_ids"] = []
                return verified_report
            try:
                os.link(scratch_ledger_path, canonical_ledger_path)
            except FileExistsError as exc:
                raise ContractValidationError(
                    "canonical ledger appeared during atomic publication"
                ) from exc
            directory_descriptor = os.open(
                str(canonical_ledger_path.parent), os.O_RDONLY
            )
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            return preflight_report
    ledger_path = _ledger_path_override or canonical_ledger_path
    ledger_path.parent.mkdir(parents=False, exist_ok=True)
    ledger = GlobalResearchLedger(ledger_path, research_root=data_root)
    existing_by_id = {item.event_id: item for item in ledger.store.read_all()}
    appended: List[str] = []

    def append_or_verify(
        event_id: str, expected_payload: Mapping[str, Any], append_event: Any
    ) -> None:
        existing = existing_by_id.get(event_id)
        if existing is not None:
            if canonical_hash(existing.payload) != canonical_hash(expected_payload):
                raise ContractValidationError(
                    "idempotent import found a conflicting existing event: {}".format(
                        event_id
                    )
                )
            return
        event = append_event()
        appended.append(event.event_id)
        existing_by_id[event.event_id] = event

    family_ids = tuple(dict.fromkeys(mappings[item] for item in sorted(mappings)))
    wave = ResearchWave(
        wave_id=str(ratification.get("wave_id", LEGACY_WAVE_ID)),
        track=InferenceTrack.EXPLORATORY,
        family_ids=family_ids,
        method=MultipleTestingMethod(
            ratification.get("wave_method", MultipleTestingMethod.HOLM_BONFERRONI.value)
        ),
        alpha_or_q=float(ratification.get("wave_alpha_or_q", 0.05)),
        registered_at=parse_datetime(str(ratification["ratified_at"])),
        policy_artifact=str(ratification["artifact"]),
        policy_sha256=str(ratification["artifact_sha256"]),
        owner_ratified=True,
        dependence_contract_sha256=ratification.get("dependence_contract_sha256"),
        legacy_policy=True,
    )
    append_or_verify(
        "wave:{}".format(wave.wave_id),
        wave.to_dict(),
        lambda: ledger.register_wave(wave, recorded_at=recorded_at),
    )

    metrics_by_hypothesis = dict(KNOWN_PRIMARY_METRICS)
    for batch in inventory["evaluator_batches"]:
        metrics_by_hypothesis[batch["hypothesis_id"]] = batch["primary_metric"]
    family_groups: Dict[str, List[str]] = {}
    for hypothesis_id, family_id in mappings.items():
        family_groups.setdefault(family_id, []).append(hypothesis_id)
    definitions = ratification.get("family_definitions", {})
    for family_id, hypothesis_ids in sorted(family_groups.items()):
        hypothesis_ids = sorted(hypothesis_ids)
        representative = hypothesis_ids[0]
        source_artifact = str(ratification["artifact"])
        source_sha = str(ratification["artifact_sha256"])
        definition = definitions.get(family_id, {})
        if not isinstance(definition, Mapping):
            raise ContractValidationError("family definition must be an object")
        metric_candidates = {
            metrics_by_hypothesis.get(item, "LEGACY_FROZEN_METRIC_NO_OUTCOME_ACCESS")
            for item in hypothesis_ids
        }
        if len(metric_candidates) > 1 and "primary_metric" not in definition:
            raise ContractValidationError(
                "merged family requires an explicit frozen primary_metric"
            )
        family = HypothesisFamily(
            family_id=family_id,
            wave_id=wave.wave_id,
            challenge_epoch_id=str(
                ratification.get("challenge_epoch_id", LEGACY_CHALLENGE_EPOCH_ID)
            ),
            name=str(definition["name"]),
            economic_mechanism=str(definition["economic_mechanism"]),
            family_scope_hash=canonical_hash(
                {
                    "hypothesis_ids": hypothesis_ids,
                    "family_id": family_id,
                    "ratification_sha256": ratification["artifact_sha256"],
                }
            ),
            primary_metric=str(definition["primary_metric"]),
            benchmark=str(definition["benchmark"]),
            expected_direction=ExpectedDirection(definition["expected_direction"]),
            null_value=float(definition["null_value"]),
            economic_hurdle=float(definition["economic_hurdle"]),
            primary_variant_id=str(definition["primary_variant_id"]),
            maximum_trial_units=int(definition["maximum_trial_units"]),
            selection_trial_budget=int(definition["selection_trial_budget"]),
            within_family_method=MultipleTestingMethod(
                definition["within_family_method"]
            ),
            family_alpha=float(definition["family_alpha"]),
            registered_at=parse_datetime(str(ratification["ratified_at"])),
            source_artifact=source_artifact,
            source_sha256=source_sha,
            owner_ratified=True,
        )
        append_or_verify(
            "family:{}".format(family_id),
            family.to_dict(),
            lambda family=family: ledger.register_family(
                family, recorded_at=recorded_at
            ),
        )

    experiment_by_hypothesis = {
        row["hypothesis_id"]: row["experiment_id"]
        for row in inventory["gate_attempts"]
    }
    for hypothesis_id, experiment_id in sorted(experiment_by_hypothesis.items()):
        family_id = mappings[hypothesis_id]
        family_metric = str(definitions[family_id]["primary_metric"])
        source_artifact, source_sha = _source_for_family(repo_root, hypothesis_id)
        experiment = ResearchExperiment(
            experiment_id=experiment_id,
            family_id=family_id,
            hypothesis_id=hypothesis_id,
            parent_experiment_ids=(),
            generated_after_results=False,
            generation_reason="LEGACY_IMPORT",
            frozen_primary_metric=family_metric,
            registered_at=parse_datetime(str(ratification["ratified_at"])),
            source_artifact=source_artifact,
            source_sha256=source_sha,
            owner_ratified=True,
        )
        append_or_verify(
            "experiment:{}".format(experiment_id),
            experiment.to_dict(),
            lambda experiment=experiment: ledger.register_experiment(
                experiment, recorded_at=recorded_at
            ),
        )
    for row in inventory["gate_attempts"]:
        semantic_sha = canonical_hash(
            {"source_sha256": row["source_sha256"], "semantic": "DATA_GATE"}
        )
        run = ResearchRun(
            attempt_id=deterministic_attempt_id(semantic_sha),
            family_id=mappings[row["hypothesis_id"]],
            hypothesis_id=row["hypothesis_id"],
            experiment_id=row["experiment_id"],
            run_id=row["run_id"],
            run_class=ResearchRunClass.DATA_GATE,
            phase=ResearchPhase.DATA,
            occurred_at=parse_datetime(row["occurred_at"]),
            source_artifact=row["source_artifact"],
            source_sha256=row["source_sha256"],
            outcome_data_accessed=False,
            challenge_accessed=False,
            legacy_accounting_quality="SOURCE_NATIVE",
            source_chain_head_hash=row["terminal_event_hash"],
            attempt_outcome=row["data_gate_status"],
        )
        append_or_verify(
            "attempt:{}".format(run.attempt_id),
            run.to_dict(),
            lambda run=run: ledger.register_run(run, recorded_at=recorded_at),
        )

    next_ordinal_by_family: Dict[str, int] = {}
    for batch in inventory["evaluator_batches"]:
        hypothesis_id = batch["hypothesis_id"]
        family_id = mappings[hypothesis_id]
        for variant_id, variant in zip(batch["variant_ids"], batch["variants"]):
            ordinal = next_ordinal_by_family.get(family_id, 0) + 1
            next_ordinal_by_family[family_id] = ordinal
            trial_id = deterministic_trial_id(family_id, ordinal)
            trial_semantic_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "MODEL_TRIAL",
                }
            )
            run = ResearchRun(
                attempt_id=deterministic_attempt_id(trial_semantic_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.MODEL_TRIAL,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                statistical_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                variant_id=variant_id,
                variant_definition_hash=canonical_hash(variant),
                consumes_trial_budget=True,
                preregistered=False,
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="AGGREGATE_ONLY",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                effective_sample_floor=batch["effective_sample_floor"],
            )
            append_or_verify(
                "attempt:{}".format(run.attempt_id),
                run.to_dict(),
                lambda run=run: ledger.register_run(run, recorded_at=recorded_at),
            )
            result = TrialResult(
                statistical_trial_id=trial_id,
                outcome=TrialOutcome.NEGATIVE,
                recorded_at=parse_datetime(batch["retrieved_at"]),
                primary_metric=batch["primary_metric"],
                primary_metric_value=variant[
                    "worst_case_validation_annualized_excess_return_after_costs"
                ],
                p_value=None,
                inference_eligible=False,
                ineligibility_reasons=("CORRECTED_SIGNIFICANCE_NOT_IMPLEMENTED",),
                stress_scenario_pass=False,
                capacity_and_concentration_pass=False,
                effective_sample_size=0,
                minimum_effective_sample=batch["effective_sample_floor"],
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
            )
            append_or_verify(
                "result:{}".format(trial_id),
                result.to_dict(),
                lambda result=result: ledger.record_result(
                    result, recorded_at=recorded_at
                ),
            )
            robustness_sha = canonical_hash(
                {
                    "result_hash": batch["result_hash"],
                    "variant_id": variant_id,
                    "semantic": "ROBUSTNESS_GRID",
                }
            )
            robustness = ResearchRun(
                attempt_id=deterministic_attempt_id(robustness_sha),
                family_id=family_id,
                hypothesis_id=hypothesis_id,
                experiment_id=experiment_by_hypothesis[hypothesis_id],
                run_id="{}:{}:robustness".format(batch["result_hash"][:16], variant_id),
                run_class=ResearchRunClass.ROBUSTNESS,
                phase=ResearchPhase.DISCOVERY,
                occurred_at=parse_datetime(batch["retrieved_at"]),
                source_artifact=batch["source_artifact"],
                source_sha256=batch["source_sha256"],
                parent_trial_id=trial_id,
                primary_metric=batch["primary_metric"],
                outcome_data_accessed=True,
                challenge_accessed=False,
                legacy_accounting_quality="SOURCE_NATIVE_8_CELL_GRID",
                source_chain_head_hash=batch["result_hash"],
                code_sha256=batch["evaluator_code_sha256"],
                data_snapshot_sha256=batch["input_packet_hash"],
                evaluator_spec_sha256=batch["evaluator_spec_sha256"],
                prespecified_non_selective=True,
            )
            append_or_verify(
                "attempt:{}".format(robustness.attempt_id),
                robustness.to_dict(),
                lambda robustness=robustness: ledger.register_run(
                    robustness, recorded_at=recorded_at
                ),
            )

    projection = ledger.project()
    return {
        "schema_version": "caerus_alpha_lab_legacy_bootstrap_report_v1",
        "appended_event_count": len(appended),
        "appended_event_ids": appended,
        "projection": projection,
        "challenge_events_imported": 0,
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--ratification-manifest", type=Path)
    parser.add_argument("--at")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    inventory = audit_existing(repo_root=args.repo_root, data_root=args.data_root)
    if not args.write:
        print(canonical_json(inventory))
        return 0
    if args.ratification_manifest is None:
        raise ContractValidationError("--write requires --ratification-manifest")
    ratification = _load_json(args.ratification_manifest)
    recorded_at = (
        parse_datetime(args.at) if args.at else datetime.now(timezone.utc)
    )
    report = bootstrap_inventory(
        repo_root=args.repo_root,
        data_root=args.data_root,
        inventory=inventory,
        ratification=ratification,
        ratification_path=args.ratification_manifest,
        recorded_at=recorded_at,
    )
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
