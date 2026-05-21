"""Conformance validators for SEM-001, SEM-002, SEM-003, SEM-006, SEM-007."""

from __future__ import annotations

from dataclasses import dataclass

from research_registry.confidence.engine import ConfidenceEngine
from research_registry.models.base import (
    ResearchObjectEnvelope,
    SEMVER_RE,
    canonical_object_id,
    compute_transformation_chain_hash,
)
from research_registry.models.enums import (
    DATED_OBJECTS,
    NAV_BEARING_OBJECTS,
    RAW_ROOT_OBJECTS,
    STRATEGY_SCOPED_OBJECTS,
    ChainStatus,
    ConfidenceLevel,
    GovernanceState,
    ObjectType,
    SurfaceType,
)
from research_registry.temporal.fencing import parse_utc


@dataclass(frozen=True)
class ValidationFinding:
    code: str
    severity: str
    message: str


class RegistryValidationError(ValueError):
    def __init__(self, findings: list[ValidationFinding]) -> None:
        self.findings = findings
        joined = "; ".join(f"{finding.code}: {finding.message}" for finding in findings)
        super().__init__(joined)


def validate_envelope(
    envelope: ResearchObjectEnvelope,
    *,
    parent_envelopes: dict[str, ResearchObjectEnvelope] | None = None,
) -> list[ValidationFinding]:
    parent_envelopes = parent_envelopes or {}
    findings: list[ValidationFinding] = []

    def add(code: str, message: str, severity: str = "HIGH") -> None:
        findings.append(ValidationFinding(code=code, severity=severity, message=message))

    try:
        ObjectType(envelope.object_type)
    except ValueError:
        add("M001", f"unknown object_type {envelope.object_type}", "CRITICAL")

    for key in ["schema_id", "schema_version", "ontology_version"]:
        if not envelope.schema.get(key):
            add("M002", f"missing schema.{key}", "CRITICAL")
    for key in ["as_of", "trade_date", "valid_from", "valid_to", "staleness_threshold_seconds", "is_stale"]:
        if key not in envelope.temporal:
            add("M003", f"missing temporal.{key}", "CRITICAL")
    for key in ["produced_by", "produced_at", "source_paths", "input_object_ids", "transformation", "deterministic", "source_state_hash"]:
        if key not in envelope.provenance:
            add("M004", f"missing provenance.{key}", "CRITICAL")
    for key in ["level", "rationale", "limiting_dependency", "downgrade_reasons"]:
        if key not in envelope.confidence:
            add("M005", f"missing confidence.{key}", "CRITICAL")
    for key in ["state", "governing_frs", "coverage_type", "observation_status"]:
        if key not in envelope.governance:
            add("M006", f"missing governance.{key}", "CRITICAL")
    for key in ["node_id", "parent_refs", "transformation_chain_hash"]:
        if key not in envelope.lineage:
            add("M007", f"missing lineage.{key}", "CRITICAL")

    if envelope.schema.get("schema_version") and not SEMVER_RE.match(envelope.schema["schema_version"]):
        add("SCHEMA_VERSION_INVALID", "schema_version must be semver")
    if envelope.schema.get("ontology_version") and not SEMVER_RE.match(envelope.schema["ontology_version"]):
        add("ONTOLOGY_VERSION_INVALID", "ontology_version must be semver")

    expected_id = canonical_object_id(
        envelope.object_type,
        envelope.identity.get("strategy_ref"),
        envelope.identity.get("trade_date"),
        envelope.identity.get("surface_ref"),
        envelope.schema.get("schema_version", ""),
    )
    if envelope.object_id != expected_id:
        add("IDENTITY_MISMATCH", f"expected {expected_id}, got {envelope.object_id}", "CRITICAL")
    if envelope.lineage.get("node_id") != f"{envelope.object_id}#node":
        add("LINEAGE_NODE_MISMATCH", "lineage.node_id must be object_id#node", "CRITICAL")

    if envelope.object_type in DATED_OBJECTS:
        if not envelope.identity.get("trade_date") or not envelope.temporal.get("trade_date"):
            add("M009", "dated object missing trade_date", "CRITICAL")
        if envelope.identity.get("trade_date") != envelope.temporal.get("trade_date"):
            add("TRADE_DATE_MISMATCH", "identity and temporal trade_date differ")
    if envelope.object_type in STRATEGY_SCOPED_OBJECTS and not envelope.identity.get("strategy_ref"):
        add("M010", "strategy-scoped object missing strategy_ref", "CRITICAL")

    if envelope.object_type in NAV_BEARING_OBJECTS:
        for field in ["nav_surface_type", "execution_realism", "chain_status"]:
            if not envelope.surface.get(field):
                add("M011", f"NAV-bearing object missing surface.{field}", "CRITICAL")
    try:
        if envelope.surface.get("nav_surface_type") is not None:
            SurfaceType(envelope.surface["nav_surface_type"])
        ChainStatus(envelope.surface["chain_status"])
        ConfidenceLevel(envelope.confidence["level"])
        GovernanceState(envelope.governance["state"])
    except ValueError as exc:
        add("ENUM_INVALID", str(exc), "CRITICAL")

    if not envelope.confidence.get("rationale"):
        add("M012", "confidence.rationale must be non-empty")
    if envelope.provenance.get("deterministic") is False and not envelope.provenance.get("source_state_hash"):
        add("M013", "non-deterministic object missing source_state_hash", "CRITICAL")
    if envelope.governance.get("state", "").startswith("GOVERNED") and not envelope.governance.get("governing_frs"):
        add("GOVERNED_WITHOUT_FR", "governed object must list governing_frs")
    if envelope.governance.get("state") == GovernanceState.GOVERNED_OBSERVING.value and envelope.governance.get("observation_status") == "not_started":
        add("OBSERVING_NOT_STARTED", "GOVERNED_OBSERVING requires observation_status beyond not_started")

    if envelope.object_type not in RAW_ROOT_OBJECTS and not envelope.lineage.get("parent_refs"):
        add("ORPHAN_DERIVATION", "non-raw object has no parents")

    for parent_ref in envelope.lineage.get("parent_refs", []):
        if parent_ref not in parent_envelopes:
            add("LINEAGE_DANGLING_PARENT", f"missing parent {parent_ref}", "CRITICAL")

    try:
        as_of = parse_utc(envelope.temporal["as_of"])
        produced_at = parse_utc(envelope.provenance["produced_at"])
        if produced_at > as_of:
            add("TEMPORAL_PRODUCED_AFTER_AS_OF", "produced_at is after as_of")
    except Exception as exc:
        add("TEMPORAL_INVALID", str(exc), "CRITICAL")

    parent_chain_hashes = [
        parent.lineage["transformation_chain_hash"]
        for parent in parent_envelopes.values()
        if parent.object_id in envelope.lineage.get("parent_refs", [])
    ]
    expected_chain_hash = compute_transformation_chain_hash(
        parent_chain_hashes,
        envelope.schema.get("schema_version", ""),
        envelope.schema.get("ontology_version", ""),
        envelope.provenance.get("produced_by", ""),
        envelope.provenance.get("transformation", ""),
        bool(envelope.provenance.get("deterministic")),
        envelope.provenance.get("source_state_hash"),
    )
    if envelope.lineage.get("transformation_chain_hash") != expected_chain_hash:
        add("CHAIN_HASH_INCONSISTENCY", "transformation_chain_hash does not match SEM-003 formula", "CRITICAL")

    engine = ConfidenceEngine()
    material_parent_confidences = {
        parent.object_id: parent.confidence["level"]
        for parent in parent_envelopes.values()
        if parent.object_id in envelope.provenance.get("input_object_ids", [])
        and envelope.provenance.get("materiality_map", {}).get(parent.object_id, "material") == "material"
    }
    computed = engine.compute(
        object_type=envelope.object_type,
        nav_surface_type=envelope.surface.get("nav_surface_type"),
        chain_status=envelope.surface.get("chain_status"),
        execution_realism=envelope.surface.get("execution_realism"),
        governance_state=envelope.governance.get("state"),
        parent_confidences=material_parent_confidences,
        deterministic=bool(envelope.provenance.get("deterministic")),
        is_stale=bool(envelope.temporal.get("is_stale")),
        annotations=envelope.annotations,
    )
    stamped = ConfidenceLevel(envelope.confidence["level"]) if envelope.confidence.get("level") in {level.value for level in ConfidenceLevel} else None
    if stamped is not None and stamped != computed.level:
        add(
            "CONFIDENCE_STAMP_MISMATCH",
            f"stamped {stamped.value} but recomputed {computed.level.value}",
        )
    if set(computed.downgrade_reasons) - set(envelope.confidence.get("downgrade_reasons", [])):
        add("CONFIDENCE_DOWNGRADE_REASONS_MISSING", "applicable downgrade reasons omitted")

    if findings:
        raise RegistryValidationError(findings)
    return findings
