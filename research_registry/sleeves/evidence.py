"""Research-only FR-069 sleeve evidence envelope validator.

This module validates static evidence metadata only. It must not import broker,
execution, allocation, strategy-runtime, or cron modules.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from research_registry.sleeves.manifest import DEFAULT_MANIFEST_PATH, load_sleeve_manifest


EVIDENCE_SCHEMA_VERSION = "caerus_sleeve_evidence_v1"

REQUIRED_EVIDENCE_FIELDS = {
    "schema_version",
    "sleeve_id",
    "name",
    "thesis",
    "status",
    "owner",
    "source",
    "hypothesis_class",
    "data_requirements",
    "artifact_paths",
    "benchmark",
    "evaluation_window",
    "metrics_required",
    "known_bias_risks",
    "promotion_blockers",
    "production_impact",
    "decision_state",
    "evidence_last_updated",
}

OPTIONAL_EVIDENCE_FIELDS = {
    "strategy_id",
    "family",
    "sleeve_type",
    "lifecycle_status",
    "universe_family",
    "universe_method",
    "universe_snapshot_hash",
    "price_source",
    "holdout_excluded",
    "spec_version",
    "metrics",
    "holdings",
    "attribution",
    "reason_codes",
    "governance_label",
    "execution_impact",
}

ALLOWED_PRODUCTION_IMPACTS = {"none", "research_only"}
ALLOWED_DECISION_STATES = {"draft", "research_ready", "shadow_candidate", "blocked"}
CRITICAL_LIST_FIELDS = {
    "data_requirements",
    "artifact_paths",
    "metrics_required",
    "known_bias_risks",
    "promotion_blockers",
}


@dataclass(frozen=True)
class SleeveEvidenceIssue:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def load_sleeve_evidence(path: str | Path) -> dict[str, Any]:
    evidence_path = Path(path)
    return json.loads(evidence_path.read_text(encoding="utf-8"))


def validate_sleeve_evidence(
    path: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    evidence_path = Path(path)
    errors: list[SleeveEvidenceIssue] = []
    warnings: list[str] = []
    evidence: dict[str, Any] | None
    manifest: dict[str, Any] = {}

    try:
        evidence = load_sleeve_evidence(evidence_path)
    except FileNotFoundError:
        return _validation_payload(
            evidence_path,
            None,
            [SleeveEvidenceIssue("$", "evidence file not found")],
            warnings,
        )
    except json.JSONDecodeError as exc:
        return _validation_payload(
            evidence_path,
            None,
            [SleeveEvidenceIssue("$", f"invalid JSON: {exc}")],
            warnings,
        )

    if not isinstance(evidence, dict):
        return _validation_payload(
            evidence_path,
            evidence,
            [SleeveEvidenceIssue("$", "evidence must be a JSON object")],
            warnings,
        )

    for field in sorted(REQUIRED_EVIDENCE_FIELDS - set(evidence)):
        errors.append(SleeveEvidenceIssue("$", f"missing required field: {field}"))

    if evidence.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        errors.append(
            SleeveEvidenceIssue(
                "$.schema_version",
                f"unsupported schema_version: {evidence.get('schema_version')!r}",
            )
        )

    sleeve_id = str(evidence.get("sleeve_id") or "").strip()
    if not sleeve_id:
        errors.append(SleeveEvidenceIssue("$.sleeve_id", "sleeve_id is required"))

    manifest_errors: list[SleeveEvidenceIssue] = []
    try:
        manifest = load_sleeve_manifest(manifest_path or DEFAULT_MANIFEST_PATH)
    except Exception as exc:  # pragma: no cover - defensive path
        manifest_errors.append(SleeveEvidenceIssue("$.manifest", f"unable to load sleeve manifest: {exc}"))
    sleeves = {
        str(item.get("sleeve_id") or ""): item
        for item in list((manifest or {}).get("sleeves") or [])
        if isinstance(item, dict)
    }
    if sleeve_id and sleeve_id not in sleeves:
        errors.append(SleeveEvidenceIssue("$.sleeve_id", f"unknown sleeve_id: {sleeve_id}"))
    errors.extend(manifest_errors)

    production_impact = str(evidence.get("production_impact") or "").strip()
    if production_impact not in ALLOWED_PRODUCTION_IMPACTS:
        errors.append(
            SleeveEvidenceIssue(
                "$.production_impact",
                f"production_impact must be one of {sorted(ALLOWED_PRODUCTION_IMPACTS)}",
            )
        )

    decision_state = str(evidence.get("decision_state") or "").strip()
    if decision_state not in ALLOWED_DECISION_STATES:
        errors.append(
            SleeveEvidenceIssue(
                "$.decision_state",
                f"decision_state must be one of {sorted(ALLOWED_DECISION_STATES)}",
            )
        )

    for field in sorted(CRITICAL_LIST_FIELDS):
        value = evidence.get(field)
        if not isinstance(value, list) or not value:
            errors.append(SleeveEvidenceIssue(f"$.{field}", f"{field} must be a non-empty list"))

    artifact_paths = evidence.get("artifact_paths")
    if isinstance(artifact_paths, list):
        for index, item in enumerate(artifact_paths):
            if not isinstance(item, str) or not item.strip():
                errors.append(SleeveEvidenceIssue(f"$.artifact_paths[{index}]", "artifact path must be a non-empty string"))

    evaluation_window = evidence.get("evaluation_window")
    if not isinstance(evaluation_window, dict):
        errors.append(SleeveEvidenceIssue("$.evaluation_window", "evaluation_window must be an object"))
    else:
        if not str(evaluation_window.get("start") or "").strip():
            errors.append(SleeveEvidenceIssue("$.evaluation_window.start", "evaluation_window.start is required"))
        if not str(evaluation_window.get("end") or "").strip():
            errors.append(SleeveEvidenceIssue("$.evaluation_window.end", "evaluation_window.end is required"))

    if evidence.get("universe_method") == "legacy_current_universe":
        warnings.append("legacy_current_universe evidence is readable but non-decision-grade")
    elif evidence.get("universe_method") not in (None, "", "pit_universe"):
        warnings.append(f"unrecognized universe_method: {evidence.get('universe_method')!r}")

    if not evidence.get("universe_method"):
        warnings.append("universe_method missing; evidence is non-decision-grade")
    if not evidence.get("universe_snapshot_hash"):
        warnings.append("universe_snapshot_hash missing; evidence is non-decision-grade")
    if "holdout_excluded" not in evidence or not isinstance(evidence.get("holdout_excluded"), bool):
        warnings.append("holdout_excluded must be an explicit boolean for decision-grade evidence")
    if evidence.get("governance_label") not in (None, "", "RESEARCH_ONLY"):
        warnings.append("governance_label should be RESEARCH_ONLY for Phase B evidence")
    if evidence.get("execution_impact") not in (None, "", "NON_EXECUTIONAL"):
        errors.append(SleeveEvidenceIssue("$.execution_impact", "execution_impact must be NON_EXECUTIONAL when present"))

    sleeve_manifest = sleeves.get(sleeve_id) or {}
    required_by_manifest = list(
        ((sleeve_manifest.get("artifact_requirements") or {}).get("required_fields") or [])
        if isinstance(sleeve_manifest, dict)
        else []
    )
    for field in sorted(str(item) for item in required_by_manifest if str(item).strip()):
        if field in REQUIRED_EVIDENCE_FIELDS or field in OPTIONAL_EVIDENCE_FIELDS:
            if field not in evidence:
                warnings.append(f"manifest-required field not present in evidence envelope: {field}")

    decision_grade = _is_decision_grade(evidence, errors, warnings)
    classification = (
        "research_decision_grade"
        if decision_grade
        else ("blocked" if errors else "non_decision_grade")
    )

    return _validation_payload(
        evidence_path,
        evidence,
        errors,
        warnings,
        decision_grade=decision_grade,
        classification=classification,
    )


def _is_decision_grade(
    evidence: dict[str, Any],
    errors: list[SleeveEvidenceIssue],
    warnings: list[str],
) -> bool:
    del warnings
    if errors:
        return False
    return bool(
        evidence.get("universe_method") == "pit_universe"
        and str(evidence.get("universe_snapshot_hash") or "").strip()
        and evidence.get("holdout_excluded") is True
        and evidence.get("governance_label") == "RESEARCH_ONLY"
        and evidence.get("execution_impact") == "NON_EXECUTIONAL"
        and evidence.get("production_impact") in ALLOWED_PRODUCTION_IMPACTS
    )


def _validation_payload(
    evidence_path: Path,
    evidence: dict[str, Any] | None,
    errors: list[SleeveEvidenceIssue],
    warnings: list[str],
    *,
    decision_grade: bool = False,
    classification: str | None = None,
) -> dict[str, Any]:
    return {
        "valid": not errors,
        "artifact_path": str(evidence_path),
        "artifact": evidence,
        "decision_grade": bool(decision_grade),
        "classification": classification or ("blocked" if errors else "non_decision_grade"),
        "error_count": len(errors),
        "errors": [error.to_dict() for error in errors],
        "warnings": list(warnings),
    }
