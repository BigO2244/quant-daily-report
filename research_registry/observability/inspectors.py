"""Read-only registry observability and integrity inspection."""

from __future__ import annotations

from dataclasses import dataclass, field

from research_registry.confidence import ConfidenceEngine
from research_registry.registry import SQLiteResearchRegistry
from research_registry.validation.validators import RegistryValidationError, validate_envelope


@dataclass(frozen=True)
class RegistryIntegrityReport:
    status: str
    object_count: int
    edge_count: int
    registry_digest: str
    findings: list[str] = field(default_factory=list)


class RegistryInspector:
    def inspect(self, registry: SQLiteResearchRegistry) -> RegistryIntegrityReport:
        findings: list[str] = []
        objects = registry.store.all_objects()
        for envelope in objects:
            parent_envelopes = {
                parent_id: parent
                for parent_id in envelope.lineage.get("parent_refs", [])
                if (parent := registry.store.get_object(parent_id)) is not None
            }
            try:
                validate_envelope(envelope, parent_envelopes=parent_envelopes)
            except RegistryValidationError as exc:
                findings.extend(f"{envelope.object_id}:{finding.code}" for finding in exc.findings)
        findings.extend(f"ORPHAN:{node_id}" for node_id in registry.orphan_findings())
        try:
            registry.graph.assert_dag()
        except ValueError as exc:
            findings.append(f"DAG_INVALID:{exc}")
        return RegistryIntegrityReport(
            status="PASS" if not findings else "FAIL",
            object_count=len(objects),
            edge_count=len(registry.store.all_edges()),
            registry_digest=registry.registry_digest(),
            findings=sorted(findings),
        )

    def confidence_chain(self, registry: SQLiteResearchRegistry, object_id: str) -> dict:
        envelope = registry.hydrate(object_id)
        parent_envelopes = {
            parent_id: parent
            for parent_id in envelope.lineage.get("parent_refs", [])
            if (parent := registry.store.get_object(parent_id)) is not None
        }
        parent_confidences = {
            parent.object_id: parent.confidence["level"]
            for parent in parent_envelopes.values()
            if parent.object_id in envelope.provenance.get("input_object_ids", [])
        }
        result = ConfidenceEngine().compute(
            object_type=envelope.object_type,
            nav_surface_type=envelope.surface.get("nav_surface_type"),
            chain_status=envelope.surface.get("chain_status"),
            execution_realism=envelope.surface.get("execution_realism"),
            governance_state=envelope.governance.get("state"),
            parent_confidences=parent_confidences,
            deterministic=bool(envelope.provenance.get("deterministic")),
            is_stale=bool(envelope.temporal.get("is_stale")),
            annotations=envelope.annotations,
        )
        return {
            "object_id": object_id,
            "stamped_confidence": envelope.confidence["level"],
            "computed_confidence": result.level.value,
            "limiting_component": result.limiting_component,
            "limiting_dependency": result.limiting_dependency,
            "downgrade_reasons": result.downgrade_reasons,
        }

    def governance_inheritance_view(self, registry: SQLiteResearchRegistry, object_id: str) -> dict:
        envelope = registry.hydrate(object_id)
        parents = [registry.hydrate(parent_id) for parent_id in envelope.lineage.get("parent_refs", [])]
        return {
            "object_id": object_id,
            "governance": envelope.governance,
            "parent_governance": {parent.object_id: parent.governance for parent in parents},
            "parent_surfaces": {parent.object_id: parent.surface.get("nav_surface_type") for parent in parents},
        }
