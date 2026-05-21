"""Deterministic artifact-family hydration for grandfathered Caerus artifacts."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from research_registry.models.base import (
    MetadataEnvelopeBuilder,
    ResearchObjectEnvelope,
    compute_source_state_hash,
    sha256_hex,
)
from research_registry.models.enums import ChainStatus, ConfidenceLevel, GovernanceState, ObjectType
from research_registry.models.objects import AuditFinding, GovernanceFR, ResearchArtifact
from research_registry.validation.validators import RegistryValidationError, ValidationFinding, validate_envelope


REGISTRY_PRODUCER = "research_registry.ingestion.families"
EPOCH_AS_OF = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class HydrationFinding:
    code: str
    severity: str
    message: str
    artifact_ref: str | None = None


@dataclass(frozen=True)
class HydrationResult:
    envelopes: list[ResearchObjectEnvelope] = field(default_factory=list)
    findings: list[HydrationFinding] = field(default_factory=list)


def _read_payload(path: Path) -> tuple[Any, str]:
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    if suffix == ".json":
        return json.loads(raw.decode("utf-8")), sha256_hex(raw.decode("utf-8"))
    if suffix == ".csv":
        text = raw.decode("utf-8")
        rows = list(csv.DictReader(text.splitlines()))
        return {"rows": rows, "row_count": len(rows)}, sha256_hex(text)
    if suffix in {".md", ".txt"}:
        text = raw.decode("utf-8")
        return {"text": text}, sha256_hex(text)
    raise ValueError(f"unsupported artifact suffix: {suffix}")


def _first_present(payload: Any, keys: list[str]) -> Any:
    if not isinstance(payload, dict):
        return None
    for key in keys:
        value = payload.get(key)
        if value:
            return value
    return None


def _normalize_as_of(value: Any) -> str:
    if not value:
        return EPOCH_AS_OF
    text = str(value)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        return f"{text}T00:00:00Z"
    if text.endswith("Z"):
        return text
    if text.endswith("+00:00"):
        return text.replace("+00:00", "Z")
    return EPOCH_AS_OF


def _trade_date(payload: Any) -> str | None:
    value = _first_present(payload, ["trade_date", "data_through_date", "latest_shadow_evaluation_trade_date"])
    if value and len(str(value)) >= 10:
        return str(value)[:10]
    return None


def _root_artifact_envelope(path: Path, payload: Any, content_hash: str, family: str) -> ResearchObjectEnvelope:
    trade_date = _trade_date(payload)
    as_of = _normalize_as_of(_first_present(payload, ["generated_at", "as_of", "produced_at", "trade_date"]))
    data = ResearchArtifact(
        artifact_id=f"{family}:{content_hash[:16]}",
        artifact_type=family,
        source_uri=f"artifact://{content_hash[:16]}",
        content_hash=content_hash,
        summary=f"Grandfathered {family} artifact hydrated from immutable content hash.",
        produced_by=REGISTRY_PRODUCER,
    ).to_data()
    envelope = MetadataEnvelopeBuilder().build(
        object_type=ObjectType.RESEARCH_ARTIFACT.value,
        data=data,
        strategy_ref=None,
        trade_date=trade_date,
        surface_ref=f"{family}_{content_hash[:16]}",
        schema_id="caerus.research_artifact",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=as_of,
        produced_by=REGISTRY_PRODUCER,
        produced_at=as_of,
        source_paths=[str(path)],
        input_object_ids=[],
        transformation=f"grandfathered.{family}.root_artifact",
        deterministic=True,
        source_state_hash=content_hash,
        parent_refs=[],
        parent_chain_hashes=[],
        confidence_level=ConfidenceLevel.LOW.value,
        confidence_rationale="grandfathered artifact without SEM-001 production envelope",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=None,
        execution_realism=None,
        chain_status=ChainStatus.NOT_APPLICABLE.value,
        downgrade_reasons=["GRANDFATHERED_ARTIFACT"],
        annotations={
            "grandfathered": {
                "semantic_envelope_inferred": True,
                "lineage_inferred": True,
                "governance_inferred": True,
                "confidence_inferred": True,
                "confidence_ceiling": ConfidenceLevel.LOW.value,
            }
        },
    )
    validate_envelope(envelope)
    return envelope


class ArtifactFamilyAdapter:
    family = "generic"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        path = Path(path)
        try:
            payload, content_hash = _read_payload(path)
            root = _root_artifact_envelope(path, payload, content_hash, self.family)
            return HydrationResult(envelopes=[root])
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RegistryValidationError) as exc:
            return HydrationResult(findings=[HydrationFinding("ARTIFACT_MALFORMED", "HIGH", str(exc), str(path))])


class AuditArtifactAdapter(ArtifactFamilyAdapter):
    family = "audit"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        base = super().hydrate_path(path)
        if not base.envelopes:
            return base
        path = Path(path)
        payload, content_hash = _read_payload(path)
        root = base.envelopes[0]
        if not isinstance(payload, dict):
            return base
        summary = payload.get("overall_classification") or payload.get("finding_summary") or "grandfathered audit artifact"
        audit = AuditFinding(
            finding_id=f"audit_{content_hash[:16]}",
            audit_type=str(payload.get("audit_type", "GRANDFATHERED_AUDIT")),
            severity="HIGH" if payload.get("overall_classification") == "INVALIDATED" else "MEDIUM",
            finding_summary=str(summary),
            affected_objects=[],
            remediation_state="OPEN",
            evidence_refs=[root.object_id],
            discovered_date=_trade_date(payload) or _normalize_as_of(payload.get("generated_at"))[:10],
        )
        child = MetadataEnvelopeBuilder().build(
            object_type=ObjectType.AUDIT_FINDING.value,
            data=audit.to_data(),
            strategy_ref=None,
            trade_date=None,
            surface_ref=f"audit_{content_hash[:16]}",
            schema_id="caerus.audit_finding",
            schema_version="1.0.0",
            ontology_version="1.0.0",
            as_of=root.temporal["as_of"],
            produced_by=REGISTRY_PRODUCER,
            produced_at=root.provenance["produced_at"],
            source_paths=[str(path)],
            input_object_ids=[root.object_id],
            transformation="grandfathered.audit.derived_finding",
            deterministic=True,
            source_state_hash=content_hash,
            parent_refs=[root.object_id],
            parent_chain_hashes=[root.lineage["transformation_chain_hash"]],
            confidence_level=ConfidenceLevel.LOW.value,
            confidence_rationale="audit semantics inferred from grandfathered artifact",
            governance_state=GovernanceState.UNGOVERNED.value,
            governance_coverage_type="UNGOVERNED",
            nav_surface_type=None,
            execution_realism=None,
            chain_status=ChainStatus.NOT_APPLICABLE.value,
            downgrade_reasons=["GRANDFATHERED_ARTIFACT"],
            annotations=root.annotations,
        )
        validate_envelope(child, parent_envelopes={root.object_id: root})
        return HydrationResult(envelopes=[root, child])


class GovernanceArtifactAdapter(ArtifactFamilyAdapter):
    family = "governance"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        base = super().hydrate_path(path)
        if not base.envelopes:
            return base
        path = Path(path)
        payload, content_hash = _read_payload(path)
        root = base.envelopes[0]
        if not isinstance(payload, dict) or not payload.get("fr_id"):
            return base
        fr = GovernanceFR(
            fr_id=str(payload["fr_id"]),
            category=str(payload.get("category", "FR")),
            status=str(payload.get("status", "BACKLOG")),
            blast_radius=str(payload.get("blast_radius", "UNKNOWN")),
            observation_criteria=str(payload.get("observation_criteria", "")),
            rollback_reference=str(payload.get("rollback_reference", "")),
            validation_summary=str(payload.get("validation_summary", "")),
            affected_objects=list(payload.get("affected_objects", [])),
            deployed_date=payload.get("deployed_date"),
            dependencies=list(payload.get("dependencies", [])),
        )
        child = MetadataEnvelopeBuilder().build(
            object_type=ObjectType.GOVERNANCE_FR.value,
            data=fr.to_data(),
            strategy_ref=None,
            trade_date=None,
            surface_ref=f"governance_{content_hash[:16]}",
            schema_id="caerus.governance_fr",
            schema_version="1.0.0",
            ontology_version="1.0.0",
            as_of=root.temporal["as_of"],
            produced_by=REGISTRY_PRODUCER,
            produced_at=root.provenance["produced_at"],
            source_paths=[str(path)],
            input_object_ids=[root.object_id],
            transformation="grandfathered.governance.derived_fr",
            deterministic=True,
            source_state_hash=content_hash,
            parent_refs=[root.object_id],
            parent_chain_hashes=[root.lineage["transformation_chain_hash"]],
            confidence_level=ConfidenceLevel.LOW.value,
            confidence_rationale="governance semantics inferred from grandfathered artifact",
            governance_state=GovernanceState.UNGOVERNED.value,
            governance_coverage_type="UNGOVERNED",
            nav_surface_type=None,
            execution_realism=None,
            chain_status=ChainStatus.NOT_APPLICABLE.value,
            downgrade_reasons=["GRANDFATHERED_ARTIFACT"],
            annotations=root.annotations,
        )
        validate_envelope(child, parent_envelopes={root.object_id: root})
        return HydrationResult(envelopes=[root, child])


class AttributionArtifactAdapter(ArtifactFamilyAdapter):
    family = "attribution"


class ShadowEvaluationArtifactAdapter(ArtifactFamilyAdapter):
    family = "shadow_evaluation"


class RegimeIntelligenceArtifactAdapter(ArtifactFamilyAdapter):
    family = "regime_intelligence"


class PerformanceVeracityArtifactAdapter(AuditArtifactAdapter):
    family = "performance_veracity"


class ExposureIntelligenceArtifactAdapter(ArtifactFamilyAdapter):
    family = "exposure_intelligence"


class ValidationArtifactAdapter(ArtifactFamilyAdapter):
    family = "validation"


FAMILY_ADAPTERS = {
    "generic": ArtifactFamilyAdapter,
    "grandfathered": ArtifactFamilyAdapter,
    "audit": AuditArtifactAdapter,
    "governance": GovernanceArtifactAdapter,
    "attribution": AttributionArtifactAdapter,
    "shadow_evaluation": ShadowEvaluationArtifactAdapter,
    "regime_intelligence": RegimeIntelligenceArtifactAdapter,
    "performance_veracity": PerformanceVeracityArtifactAdapter,
    "exposure_intelligence": ExposureIntelligenceArtifactAdapter,
    "validation": ValidationArtifactAdapter,
}


def ingest_artifact_family(
    *,
    family: str,
    artifact_paths: list[str | Path],
    registry=None,
) -> HydrationResult:
    """Hydrate a bounded artifact family and optionally ingest into a registry.

    The function is the stable institutional ingestion boundary for VM
    shadow hydration. It is deterministic: paths are sorted by their string
    representation, adapters are selected from a closed family map, and no
    global state is mutated. When a registry is supplied, only the caller's
    derived registry index is written; source artifacts are never mutated.
    """

    adapter_cls = FAMILY_ADAPTERS.get(family)
    if adapter_cls is None:
        return HydrationResult(
            findings=[
                HydrationFinding(
                    code="UNKNOWN_ARTIFACT_FAMILY",
                    severity="HIGH",
                    message=f"unknown artifact family: {family}",
                )
            ]
        )

    adapter = adapter_cls()
    envelopes: list[ResearchObjectEnvelope] = []
    findings: list[HydrationFinding] = []
    for path in sorted([Path(path) for path in artifact_paths], key=lambda item: str(item)):
        result = adapter.hydrate_path(path)
        findings.extend(result.findings)
        for envelope in result.envelopes:
            if registry is not None:
                try:
                    registry.ingest(envelope)
                except Exception as exc:
                    findings.append(
                        HydrationFinding(
                            code="REGISTRY_INGEST_FAILED",
                            severity="HIGH",
                            message=str(exc),
                            artifact_ref=str(path),
                        )
                    )
                    continue
            envelopes.append(envelope)
    return HydrationResult(envelopes=envelopes, findings=findings)
