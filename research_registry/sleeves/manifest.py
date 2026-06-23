"""Research-only FR-069 sleeve manifest loader and validator.

This module only reads static manifest metadata. It must not import broker,
execution, allocation, strategy-runtime, or cron modules.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = Path(__file__).with_name("manifest.json")
MANIFEST_SCHEMA_VERSION = "caerus_sleeve_manifest_v1"

REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "manifest_version",
    "governance_fr",
    "phase",
    "research_only",
    "behavior_change_allowed",
    "sleeves",
}

REQUIRED_SLEEVE_FIELDS = {
    "sleeve_id",
    "display_name",
    "status",
    "lifecycle_stage",
    "sleeve_type",
    "family",
    "thesis",
    "intended_market_environment",
    "universe_policy",
    "rebalance_cadence",
    "holding_count_policy",
    "risk_profile",
    "benchmark_policy",
    "artifact_requirements",
    "promotion_requirements",
    "retirement_policy",
    "mcp_visibility",
    "implementation_status",
    "behavior_change_allowed",
}

ALLOWED_STATUSES = {
    "current_paper_baseline",
    "current_shadow_challenger",
    "research_placeholder",
}

ALLOWED_LIFECYCLE_STAGES = {
    "paper_observed",
    "shadow_observed",
    "spec_only",
    "shelved_v0",
}

ALLOWED_SLEEVE_TYPES = {
    "security_selection",
    "event_driven",
    "overlay",
    "meta_model",
    "benchmark",
    "reference_portfolio",
}

ALLOWED_IMPLEMENTATION_STATUSES = {
    "existing_strategy_reference",
    "existing_shadow_reference",
    "research_placeholder",
}

CURRENT_SLEEVE_IDS = {"polaris", "polaris_alpha", "orion", "orion_alpha", "lyra"}
FUTURE_PLACEHOLDER_IDS = {"phoenix", "cygnus", "cassiopeia", "argo"}
REQUIRED_SLEEVE_IDS = CURRENT_SLEEVE_IDS | FUTURE_PLACEHOLDER_IDS


@dataclass(frozen=True)
class SleeveManifestError:
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "message": self.message}


def load_sleeve_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def validate_sleeve_manifest(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    errors: list[SleeveManifestError] = []
    warnings: list[str] = []
    try:
        manifest = load_sleeve_manifest(manifest_path)
    except FileNotFoundError:
        return _validation_payload(manifest_path, None, [SleeveManifestError("$", "manifest file not found")], warnings)
    except json.JSONDecodeError as exc:
        return _validation_payload(manifest_path, None, [SleeveManifestError("$", f"invalid JSON: {exc}")], warnings)

    if not isinstance(manifest, dict):
        errors.append(SleeveManifestError("$", "manifest must be a JSON object"))
        return _validation_payload(manifest_path, manifest, errors, warnings)

    missing_top = sorted(REQUIRED_TOP_LEVEL_FIELDS - set(manifest))
    for field in missing_top:
        errors.append(SleeveManifestError("$", f"missing top-level field: {field}"))

    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        errors.append(SleeveManifestError("$.schema_version", f"unsupported schema_version: {manifest.get('schema_version')!r}"))
    if manifest.get("governance_fr") != "FR-069":
        errors.append(SleeveManifestError("$.governance_fr", "manifest must be tied to FR-069"))
    if manifest.get("phase") != "Phase B":
        errors.append(SleeveManifestError("$.phase", "manifest must declare Phase B"))
    if manifest.get("research_only") is not True:
        errors.append(SleeveManifestError("$.research_only", "Phase B manifest must be research_only=true"))
    if manifest.get("behavior_change_allowed") is not False:
        errors.append(SleeveManifestError("$.behavior_change_allowed", "Phase B top-level behavior_change_allowed must be false"))

    sleeves = manifest.get("sleeves")
    if not isinstance(sleeves, list):
        errors.append(SleeveManifestError("$.sleeves", "sleeves must be a list"))
        return _validation_payload(manifest_path, manifest, errors, warnings)

    sleeve_ids: list[str] = []
    for index, sleeve in enumerate(sleeves):
        base = f"$.sleeves[{index}]"
        if not isinstance(sleeve, dict):
            errors.append(SleeveManifestError(base, "sleeve entry must be an object"))
            continue
        sleeve_id = str(sleeve.get("sleeve_id") or "").strip()
        sleeve_ids.append(sleeve_id)

        for field in sorted(REQUIRED_SLEEVE_FIELDS - set(sleeve)):
            errors.append(SleeveManifestError(base, f"missing sleeve field: {field}"))

        if not sleeve_id:
            errors.append(SleeveManifestError(f"{base}.sleeve_id", "sleeve_id is required"))
        if sleeve.get("status") not in ALLOWED_STATUSES:
            errors.append(SleeveManifestError(f"{base}.status", f"invalid status: {sleeve.get('status')!r}"))
        if sleeve.get("lifecycle_stage") not in ALLOWED_LIFECYCLE_STAGES:
            errors.append(SleeveManifestError(f"{base}.lifecycle_stage", f"invalid lifecycle_stage: {sleeve.get('lifecycle_stage')!r}"))
        if sleeve.get("sleeve_type") not in ALLOWED_SLEEVE_TYPES:
            errors.append(SleeveManifestError(f"{base}.sleeve_type", f"invalid sleeve_type: {sleeve.get('sleeve_type')!r}"))
        if sleeve.get("implementation_status") not in ALLOWED_IMPLEMENTATION_STATUSES:
            errors.append(
                SleeveManifestError(
                    f"{base}.implementation_status",
                    f"invalid implementation_status: {sleeve.get('implementation_status')!r}",
                )
            )
        if sleeve.get("behavior_change_allowed") is not False:
            errors.append(SleeveManifestError(f"{base}.behavior_change_allowed", "Phase B sleeves must set behavior_change_allowed=false"))

        artifact_requirements = sleeve.get("artifact_requirements")
        if not isinstance(artifact_requirements, dict):
            errors.append(SleeveManifestError(f"{base}.artifact_requirements", "artifact_requirements must be an object"))
        else:
            required_fields = artifact_requirements.get("required_fields")
            required_artifacts = artifact_requirements.get("required_artifacts")
            if not isinstance(required_fields, list) or not required_fields:
                errors.append(SleeveManifestError(f"{base}.artifact_requirements.required_fields", "required_fields must be a non-empty list"))
            if not isinstance(required_artifacts, list) or not required_artifacts:
                errors.append(SleeveManifestError(f"{base}.artifact_requirements.required_artifacts", "required_artifacts must be a non-empty list"))

        universe_policy = sleeve.get("universe_policy")
        if not isinstance(universe_policy, dict):
            errors.append(SleeveManifestError(f"{base}.universe_policy", "universe_policy must be an object"))
        elif universe_policy.get("point_in_time_required") is not True:
            errors.append(SleeveManifestError(f"{base}.universe_policy.point_in_time_required", "point_in_time_required must be true"))

        if sleeve_id in FUTURE_PLACEHOLDER_IDS:
            if sleeve.get("status") != "research_placeholder":
                errors.append(SleeveManifestError(f"{base}.status", "future sleeves must remain research_placeholder in Phase B"))
            if sleeve.get("lifecycle_stage") in {"paper_observed", "shadow_observed"}:
                errors.append(SleeveManifestError(f"{base}.lifecycle_stage", "future sleeves must not be marked active/paper/shadow in Phase B"))
        if sleeve_id in CURRENT_SLEEVE_IDS and sleeve.get("status") == "research_placeholder":
            warnings.append(f"{sleeve_id}: current sleeve is marked research_placeholder")

    duplicate_ids = sorted(item for item, count in Counter(sleeve_ids).items() if count > 1)
    for sleeve_id in duplicate_ids:
        errors.append(SleeveManifestError("$.sleeves", f"duplicate sleeve_id: {sleeve_id}"))

    missing_sleeves = sorted(REQUIRED_SLEEVE_IDS - set(sleeve_ids))
    for sleeve_id in missing_sleeves:
        errors.append(SleeveManifestError("$.sleeves", f"missing required sleeve: {sleeve_id}"))

    extra_live_markers = [
        sleeve.get("sleeve_id")
        for sleeve in sleeves
        if isinstance(sleeve, dict)
        and str(sleeve.get("status") or "").lower() in {"active", "paper", "promoted", "live", "production"}
    ]
    for sleeve_id in extra_live_markers:
        errors.append(SleeveManifestError("$.sleeves", f"Phase B manifest contains disallowed live/promotion marker: {sleeve_id}"))

    return _validation_payload(manifest_path, manifest, errors, warnings)


def sleeve_inventory_payload(path: str | Path | None = None) -> dict[str, Any]:
    manifest_path = Path(path) if path is not None else DEFAULT_MANIFEST_PATH
    validation = validate_sleeve_manifest(manifest_path)
    manifest = validation.get("manifest") if isinstance(validation.get("manifest"), dict) else {}
    sleeves = manifest.get("sleeves") if isinstance(manifest.get("sleeves"), list) else []
    counts_by_status = Counter(str(item.get("status")) for item in sleeves if isinstance(item, dict))
    counts_by_lifecycle_stage = Counter(str(item.get("lifecycle_stage")) for item in sleeves if isinstance(item, dict))

    compact_sleeves = [
        {
            "sleeve_id": item.get("sleeve_id"),
            "strategy_id": item.get("strategy_id"),
            "display_name": item.get("display_name"),
            "status": item.get("status"),
            "lifecycle_stage": item.get("lifecycle_stage"),
            "sleeve_type": item.get("sleeve_type"),
            "family": item.get("family"),
            "mcp_visibility": item.get("mcp_visibility"),
            "implementation_status": item.get("implementation_status"),
            "behavior_change_allowed": item.get("behavior_change_allowed"),
        }
        for item in sleeves
        if isinstance(item, dict)
    ]

    return {
        "status": "OK" if validation["valid"] else "INVALID_MANIFEST",
        "manifest_path": str(manifest_path),
        "manifest_schema_version": manifest.get("schema_version"),
        "manifest_version": manifest.get("manifest_version"),
        "governance_fr": manifest.get("governance_fr"),
        "phase": manifest.get("phase"),
        "research_only": manifest.get("research_only"),
        "behavior_change_allowed": manifest.get("behavior_change_allowed"),
        "sleeve_count": len(compact_sleeves),
        "counts_by_status": dict(sorted(counts_by_status.items())),
        "counts_by_lifecycle_stage": dict(sorted(counts_by_lifecycle_stage.items())),
        "current_sleeves": [item for item in compact_sleeves if item["sleeve_id"] in CURRENT_SLEEVE_IDS],
        "future_placeholders": [item for item in compact_sleeves if item["sleeve_id"] in FUTURE_PLACEHOLDER_IDS],
        "sleeves": compact_sleeves,
        "validation": {
            "valid": validation["valid"],
            "error_count": validation["error_count"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
        },
    }


def _validation_payload(
    manifest_path: Path,
    manifest: dict[str, Any] | None,
    errors: list[SleeveManifestError],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "valid": not errors,
        "manifest_path": str(manifest_path),
        "manifest": manifest,
        "error_count": len(errors),
        "errors": [error.to_dict() for error in errors],
        "warnings": list(warnings),
    }
