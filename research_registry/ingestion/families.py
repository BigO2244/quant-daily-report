"""Deterministic artifact-family hydration for grandfathered Caerus artifacts."""

from __future__ import annotations

import csv
import json
import re
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


def _safe_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _existing_files(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists() and path.is_file()]


def _source_payloads(paths: list[Path]) -> dict[str, Any]:
    payloads: dict[str, Any] = {}
    for path in sorted(_existing_files(paths), key=lambda item: str(item)):
        if path.suffix.lower() == ".json":
            payloads[str(path)] = _safe_json(path)
        else:
            payloads[str(path)] = _safe_text(path)
    return payloads


def _artifact_envelope(
    *,
    path: Path,
    source_paths: list[Path],
    family: str,
    summary: str,
    trade_date: str | None,
    as_of: str,
    data: dict[str, Any],
    annotations: dict[str, Any] | None = None,
    confidence_rationale: str | None = None,
) -> ResearchObjectEnvelope:
    source_state_hash = compute_source_state_hash(_source_payloads(source_paths))
    artifact = ResearchArtifact(
        artifact_id=f"{family}:{source_state_hash[:16]}",
        artifact_type=family,
        source_uri=f"artifact://{family}/{source_state_hash[:16]}",
        content_hash=source_state_hash,
        summary=summary,
        produced_by=REGISTRY_PRODUCER,
    ).to_data()
    artifact.update(data)
    envelope = MetadataEnvelopeBuilder().build(
        object_type=ObjectType.RESEARCH_ARTIFACT.value,
        data=artifact,
        strategy_ref=None,
        trade_date=trade_date,
        surface_ref=f"{family}_{source_state_hash[:16]}",
        schema_id=f"caerus.{family}",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=as_of,
        produced_by=REGISTRY_PRODUCER,
        produced_at=as_of,
        source_paths=[str(item) for item in sorted(source_paths, key=lambda item: str(item))],
        input_object_ids=[],
        transformation=f"real_artifact.{family}.root_artifact",
        deterministic=True,
        source_state_hash=source_state_hash,
        parent_refs=[],
        parent_chain_hashes=[],
        confidence_level=ConfidenceLevel.LOW.value,
        confidence_rationale=confidence_rationale or "read-only real artifact hydration with inferred registry envelope",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=None,
        execution_realism=None,
        chain_status=ChainStatus.NOT_APPLICABLE.value,
        downgrade_reasons=["GRANDFATHERED_ARTIFACT"],
        annotations=annotations
        or {
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


class ExecutionRunArtifactAdapter(ArtifactFamilyAdapter):
    family = "execution_run"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        run_root = Path(path)
        if run_root.is_file():
            run_root = run_root.parent
        source_paths = _existing_files(
            [
                run_root / "execution_payload.json",
                run_root / "execution_results.json",
                run_root / "operator_summary.json",
            ]
        )
        if not source_paths:
            return HydrationResult(
                findings=[
                    HydrationFinding(
                        "EXECUTION_RUN_ARTIFACTS_MISSING",
                        "HIGH",
                        f"no execution run artifacts found under {run_root}",
                        str(path),
                    )
                ]
            )
        payload = _safe_json(run_root / "execution_payload.json") or {}
        results = _safe_json(run_root / "execution_results.json") or {}
        summary = _safe_json(run_root / "operator_summary.json") or {}
        trade_date = _trade_date(payload) or _trade_date(results) or _trade_date(summary)
        as_of = _normalize_as_of(
            _first_present(summary, ["updated_at", "generated_at", "trade_date"])
            or _first_present(payload, ["created_at", "completed_at", "trade_date"])
            or _first_present(results, ["generated_at", "trade_date"])
        )
        data = {
            "run_id": str(payload.get("run_id") or results.get("run_id") or summary.get("run_id") or run_root.name),
            "trade_date": trade_date,
            "status": summary.get("terminal_status") or results.get("status") or payload.get("status") or payload.get("execution_status"),
            "operator_execution_status": summary.get("operator_execution_status") or payload.get("operator_execution_status"),
            "submitted_count": summary.get("submitted_count") or results.get("submitted_count") or payload.get("submitted_count"),
            "accepted_count": summary.get("accepted_count") or results.get("accepted_count") or payload.get("accepted_count"),
            "rejected_count": summary.get("rejected_count") or results.get("rejected_count") or payload.get("rejected_count"),
            "execution_integrity_status": summary.get("execution_integrity_status"),
            "artifact_role": "execution_run",
        }
        envelope = _artifact_envelope(
            path=run_root,
            source_paths=source_paths,
            family=self.family,
            summary=f"Execution run registry object for {run_root.name}.",
            trade_date=trade_date,
            as_of=as_of,
            data=data,
        )
        return HydrationResult(envelopes=[envelope])


class ExecutionIntegrityArtifactAdapter(ArtifactFamilyAdapter):
    family = "execution_integrity"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        artifact_path = Path(path)
        if artifact_path.is_dir():
            artifact_path = artifact_path / "audit" / "execution_integrity.json"
        payload = _safe_json(artifact_path)
        if not isinstance(payload, dict):
            return HydrationResult(
                findings=[
                    HydrationFinding(
                        "EXECUTION_INTEGRITY_ARTIFACT_MISSING",
                        "HIGH",
                        f"execution integrity artifact missing or malformed: {artifact_path}",
                        str(path),
                    )
                ]
            )
        trade_date = _trade_date(payload)
        as_of = _normalize_as_of(_first_present(payload, ["generated_at", "trade_date"]))
        root = _artifact_envelope(
            path=artifact_path,
            source_paths=[artifact_path],
            family=self.family,
            summary=f"Execution integrity audit status {payload.get('status') or 'UNKNOWN'}.",
            trade_date=trade_date,
            as_of=as_of,
            data={
                "run_id": payload.get("run_id"),
                "status": payload.get("status"),
                "finding_count": len(payload.get("findings") or []),
                "pending_buy_count": payload.get("pending_buy_count"),
                "missing_buy_count": len(payload.get("missing_buy_orders") or []),
                "artifact_role": "execution_integrity_audit",
            },
        )
        severity = "HIGH" if payload.get("status") == "FAIL" else ("MEDIUM" if payload.get("status") == "WARN" else "LOW")
        finding = AuditFinding(
            finding_id=f"execution_integrity_{root.provenance['source_state_hash'][:16]}",
            audit_type="EXECUTION_INTEGRITY",
            severity=severity,
            finding_summary=f"Execution integrity status {payload.get('status') or 'UNKNOWN'}",
            affected_objects=[],
            remediation_state="OPEN" if payload.get("status") in {"WARN", "FAIL"} else "CLOSED",
            evidence_refs=[root.object_id],
            discovered_date=trade_date or as_of[:10],
        )
        child = MetadataEnvelopeBuilder().build(
            object_type=ObjectType.AUDIT_FINDING.value,
            data=finding.to_data(),
            strategy_ref=None,
            trade_date=None,
            surface_ref=f"execution_integrity_{root.provenance['source_state_hash'][:16]}",
            schema_id="caerus.audit_finding",
            schema_version="1.0.0",
            ontology_version="1.0.0",
            as_of=root.temporal["as_of"],
            produced_by=REGISTRY_PRODUCER,
            produced_at=root.provenance["produced_at"],
            source_paths=[str(artifact_path)],
            input_object_ids=[root.object_id],
            transformation="real_artifact.execution_integrity.derived_finding",
            deterministic=True,
            source_state_hash=root.provenance["source_state_hash"],
            parent_refs=[root.object_id],
            parent_chain_hashes=[root.lineage["transformation_chain_hash"]],
            confidence_level=ConfidenceLevel.LOW.value,
            confidence_rationale="execution integrity semantics inferred from existing audit artifact",
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


class ResearchPacketArtifactAdapter(ArtifactFamilyAdapter):
    family = "research_packet"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        packet_root = Path(path)
        source_paths = [packet_root] if packet_root.is_file() else _existing_files(
            [
                packet_root / "packet.json",
                packet_root / "summary.json",
                packet_root / "packet.md",
                packet_root / "packet.html",
            ]
        )
        if not source_paths:
            return HydrationResult(
                findings=[
                    HydrationFinding(
                        "RESEARCH_PACKET_ARTIFACTS_MISSING",
                        "HIGH",
                        f"no research packet artifacts found under {packet_root}",
                        str(path),
                    )
                ]
            )
        packet = _safe_json(packet_root / "packet.json") if packet_root.is_dir() else _safe_json(packet_root)
        summary_payload_raw = _safe_json(packet_root / "summary.json") if packet_root.is_dir() else {}
        summary_payload = summary_payload_raw if isinstance(summary_payload_raw, dict) else {}
        packet_payload = packet if isinstance(packet, dict) else {}
        trade_date = _trade_date(packet_payload) or _trade_date(summary_payload) or (packet_root.name if re.match(r"^\d{4}-\d{2}-\d{2}$", packet_root.name) else None)
        as_of = _normalize_as_of(
            _first_present(packet_payload, ["generated_at", "as_of", "trade_date"])
            or _first_present(summary_payload, ["generated_at", "as_of", "trade_date"])
            or trade_date
        )
        root = _artifact_envelope(
            path=packet_root,
            source_paths=source_paths,
            family=self.family,
            summary=f"Daily research packet for {trade_date or packet_root.name}.",
            trade_date=trade_date,
            as_of=as_of,
            data={
                "packet_date": trade_date,
                "status": packet_payload.get("status") or summary_payload.get("status"),
                "confidence": packet_payload.get("confidence") or summary_payload.get("confidence"),
                "source_readiness": packet_payload.get("source_readiness") or summary_payload.get("source_readiness"),
                "artifact_role": "research_packet",
            },
        )
        return HydrationResult(envelopes=[root])


class GovernanceDocArtifactAdapter(ArtifactFamilyAdapter):
    family = "governance_doc"

    def hydrate_path(self, path: str | Path) -> HydrationResult:
        doc_path = Path(path)
        if not doc_path.is_file():
            return HydrationResult(
                findings=[
                    HydrationFinding(
                        "GOVERNANCE_DOC_MISSING",
                        "HIGH",
                        f"governance doc missing: {doc_path}",
                        str(path),
                    )
                ]
            )
        text = _safe_text(doc_path)
        if text is None:
            return HydrationResult(findings=[HydrationFinding("GOVERNANCE_DOC_UNREADABLE", "HIGH", str(doc_path), str(path))])
        root = _artifact_envelope(
            path=doc_path,
            source_paths=[doc_path],
            family=self.family,
            summary=f"Governance document {doc_path.name}.",
            trade_date=None,
            as_of=EPOCH_AS_OF,
            data={
                "document_name": doc_path.name,
                "fr_refs": sorted(set(re.findall(r"\bFR-\d{3}\b", text))),
                "hotfix_refs": sorted(set(re.findall(r"\bHOTFIX-\d{4}-\d{2}-\d{2}\b", text))),
                "artifact_role": "governance_doc",
            },
        )
        envelopes = [root]
        refs = sorted(set(re.findall(r"\b(?:FR-\d{3}|HOTFIX-\d{4}-\d{2}-\d{2})\b", text)))
        for ref in refs:
            ref_line = next((line for line in text.splitlines() if ref in line), "")
            status_match = re.search(
                r"`?(DEPLOYED_OBSERVING|REVIEWED_DEFERRED|PROMOTION_READY|READY_VALIDATED|IN_PROGRESS|DEPLOYED|BACKLOG|READY)`?",
                ref_line,
            )
            status = status_match.group(1) if status_match else "BACKLOG"
            if status == "DEPLOYED":
                governance_state = GovernanceState.GOVERNED_DEPLOYED.value
            elif status == "DEPLOYED_OBSERVING":
                governance_state = GovernanceState.GOVERNED_OBSERVING.value
            elif status == "REVIEWED_DEFERRED":
                governance_state = GovernanceState.GOVERNED_DEFERRED.value
            else:
                governance_state = GovernanceState.GOVERNED_DRAFT.value
            fr = GovernanceFR(
                fr_id=ref,
                category="HOTFIX" if ref.startswith("HOTFIX") else "FR",
                status=status,
                blast_radius="UNKNOWN",
                observation_criteria="See source governance document.",
                rollback_reference="See source governance document.",
                validation_summary="Hydrated from governance markdown document.",
                affected_objects=[root.object_id],
                deployed_date=None,
                dependencies=[],
            )
            downgrade_reasons = ["GRANDFATHERED_ARTIFACT"]
            if governance_state == GovernanceState.GOVERNED_OBSERVING.value:
                downgrade_reasons.append("GOV_OBSERVING")
            child = MetadataEnvelopeBuilder().build(
                object_type=ObjectType.GOVERNANCE_FR.value,
                data=fr.to_data(),
                strategy_ref=None,
                trade_date=None,
                surface_ref=f"{ref.lower()}_{root.provenance['source_state_hash'][:8]}",
                schema_id="caerus.governance_fr",
                schema_version="1.0.0",
                ontology_version="1.0.0",
                as_of=root.temporal["as_of"],
                produced_by=REGISTRY_PRODUCER,
                produced_at=root.provenance["produced_at"],
                source_paths=[str(doc_path)],
                input_object_ids=[root.object_id],
                transformation="real_artifact.governance_doc.derived_fr",
                deterministic=True,
                source_state_hash=root.provenance["source_state_hash"],
                parent_refs=[root.object_id],
                parent_chain_hashes=[root.lineage["transformation_chain_hash"]],
                confidence_level=ConfidenceLevel.LOW.value,
                confidence_rationale="governance state inferred from markdown text",
                governance_state=governance_state,
                governance_coverage_type="DIRECT",
                governing_frs=[ref],
                observation_status="observing" if governance_state == GovernanceState.GOVERNED_OBSERVING.value else "not_required",
                nav_surface_type=None,
                execution_realism=None,
                chain_status=ChainStatus.NOT_APPLICABLE.value,
                downgrade_reasons=downgrade_reasons,
                annotations=root.annotations,
            )
            validate_envelope(child, parent_envelopes={root.object_id: root})
            envelopes.append(child)
        return HydrationResult(envelopes=envelopes)


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
    "execution_run": ExecutionRunArtifactAdapter,
    "execution_integrity": ExecutionIntegrityArtifactAdapter,
    "research_packet": ResearchPacketArtifactAdapter,
    "governance_doc": GovernanceDocArtifactAdapter,
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
