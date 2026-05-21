"""Replay-safe read-only query and introspection infrastructure."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from research_registry.models.base import ResearchObjectEnvelope
from research_registry.registry import SQLiteResearchRegistry
from research_registry.temporal import TemporalFence
from research_registry.validation.surfaces import surface_compatibility


@dataclass(frozen=True)
class LineageView:
    object_id: str
    parents: list[str]
    children: list[str]
    ancestors: list[str]
    descendants: list[str]


@dataclass(frozen=True)
class ReconstructionView:
    object_id: str
    anchor: str
    reconstruction_status: str
    truth_mode: str
    object: ResearchObjectEnvelope | None = None
    excluded_reason: str | None = None


@dataclass(frozen=True)
class RegistryStatistics:
    object_count: int
    edge_count: int
    by_type: dict[str, int]
    by_surface: dict[str, int]
    by_confidence: dict[str, int]
    by_governance_state: dict[str, int]


@dataclass(frozen=True)
class SurfaceConflict:
    strategy_ref: str | None
    trade_date: str | None
    surfaces: list[str]
    compatibility: dict[str, str]
    object_ids: list[str]


class RegistryQuery:
    """Side-effect-free query facade over a hydrated SQLiteResearchRegistry."""

    def __init__(self, registry: SQLiteResearchRegistry) -> None:
        self.registry = registry
        self.temporal_fence = TemporalFence()

    def get_object(self, object_id: str) -> ResearchObjectEnvelope:
        return self.registry.hydrate(object_id)

    def list_objects(self) -> list[ResearchObjectEnvelope]:
        return self._sorted(self.registry.store.all_objects())

    def query_by_type(self, object_type: str) -> list[ResearchObjectEnvelope]:
        return self._sorted(obj for obj in self.list_objects() if obj.object_type == object_type)

    def query_by_surface(self, nav_surface_type: str | None) -> list[ResearchObjectEnvelope]:
        return self._sorted(
            obj for obj in self.list_objects() if obj.surface.get("nav_surface_type") == nav_surface_type
        )

    def query_by_confidence(self, confidence_level: str) -> list[ResearchObjectEnvelope]:
        return self._sorted(obj for obj in self.list_objects() if obj.confidence.get("level") == confidence_level)

    def query_by_governance_state(self, governance_state: str) -> list[ResearchObjectEnvelope]:
        return self._sorted(obj for obj in self.list_objects() if obj.governance.get("state") == governance_state)

    def query_as_of(self, anchor: str) -> list[ResearchObjectEnvelope]:
        return self._sorted(self.temporal_fence.fence(self.list_objects(), anchor))

    def query_trade_date(self, trade_date: str) -> list[ResearchObjectEnvelope]:
        return self._sorted(
            obj
            for obj in self.list_objects()
            if obj.identity.get("trade_date") == trade_date or obj.temporal.get("trade_date") == trade_date
        )

    def get_lineage(self, object_id: str) -> LineageView:
        return LineageView(
            object_id=object_id,
            parents=self.get_parents(object_id),
            children=self.get_children(object_id),
            ancestors=self.registry.upstream(object_id),
            descendants=self.registry.downstream(object_id),
        )

    def get_parents(self, object_id: str) -> list[str]:
        envelope = self.get_object(object_id)
        return sorted(envelope.lineage.get("parent_refs", []))

    def get_children(self, object_id: str) -> list[str]:
        return sorted(
            envelope.object_id
            for envelope in self.list_objects()
            if object_id in envelope.lineage.get("parent_refs", [])
        )

    def reconstruct_object_state(self, object_id: str, anchor: str) -> ReconstructionView:
        envelope = self.get_object(object_id)
        if self.temporal_fence.admissible(envelope, anchor):
            return ReconstructionView(
                object_id=object_id,
                anchor=anchor,
                reconstruction_status="PRESENT_AT_ANCHOR",
                truth_mode="CANONICAL",
                object=envelope,
            )
        return ReconstructionView(
            object_id=object_id,
            anchor=anchor,
            reconstruction_status="OBJECT_NOT_PRESENT_AT_ANCHOR",
            truth_mode="CANONICAL",
            excluded_reason="TEMPORAL_FENCE",
        )

    def registry_summary(self) -> dict:
        statistics = self.registry_statistics()
        return {
            "object_count": statistics.object_count,
            "edge_count": statistics.edge_count,
            "registry_digest": self.registry.registry_digest(),
            "orphan_count": len(self.detect_orphans()),
            "surface_conflict_count": len(self.detect_surface_conflicts()),
        }

    def registry_statistics(self) -> RegistryStatistics:
        objects = self.list_objects()
        return RegistryStatistics(
            object_count=len(objects),
            edge_count=len(self.registry.store.all_edges()),
            by_type=dict(sorted(Counter(obj.object_type for obj in objects).items())),
            by_surface=dict(
                sorted(Counter(str(obj.surface.get("nav_surface_type")) for obj in objects).items())
            ),
            by_confidence=dict(sorted(Counter(obj.confidence.get("level") for obj in objects).items())),
            by_governance_state=dict(sorted(Counter(obj.governance.get("state") for obj in objects).items())),
        )

    def detect_orphans(self) -> list[str]:
        return sorted(self.registry.orphan_findings())

    def detect_surface_conflicts(self) -> list[SurfaceConflict]:
        grouped: dict[tuple[str | None, str | None], list[ResearchObjectEnvelope]] = defaultdict(list)
        for envelope in self.list_objects():
            surface = envelope.surface.get("nav_surface_type")
            if surface is None:
                continue
            grouped[(envelope.identity.get("strategy_ref"), envelope.identity.get("trade_date"))].append(envelope)

        conflicts: list[SurfaceConflict] = []
        for (strategy_ref, trade_date), envelopes in sorted(grouped.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
            surfaces = sorted({envelope.surface.get("nav_surface_type") for envelope in envelopes if envelope.surface.get("nav_surface_type")})
            if len(surfaces) < 2:
                continue
            compatibility: dict[str, str] = {}
            has_conflict = False
            for left_index, left in enumerate(surfaces):
                for right in surfaces[left_index + 1 :]:
                    relation = surface_compatibility(left, right).compatibility
                    compatibility[f"{left}|{right}"] = relation
                    if relation == "INCOMPATIBLE":
                        has_conflict = True
            if has_conflict:
                conflicts.append(
                    SurfaceConflict(
                        strategy_ref=strategy_ref,
                        trade_date=trade_date,
                        surfaces=surfaces,
                        compatibility=dict(sorted(compatibility.items())),
                        object_ids=sorted(envelope.object_id for envelope in envelopes),
                    )
                )
        return conflicts

    def _sorted(self, envelopes: Iterable[ResearchObjectEnvelope]) -> list[ResearchObjectEnvelope]:
        return sorted(envelopes, key=lambda envelope: envelope.object_id)
