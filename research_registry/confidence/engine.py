"""SEM-007 confidence lattice and propagation engine."""

from __future__ import annotations

from dataclasses import dataclass

from research_registry.models.enums import (
    CONFIDENCE_RANK,
    NAV_BEARING_OBJECTS,
    ChainStatus,
    ConfidenceLevel,
    GovernanceState,
    SurfaceType,
)


SURFACE_CEILINGS = {
    SurfaceType.LIVE_BROKER_PAPER_NAV.value: ConfidenceLevel.BROKER_AUTHORITATIVE,
    SurfaceType.OPERATIONAL_SHADOW_NAV.value: ConfidenceLevel.PARTIAL_CONFIDENCE,
    SurfaceType.RESEARCH_BACKTEST_NAV.value: ConfidenceLevel.PARTIAL_CONFIDENCE,
    None: ConfidenceLevel.BROKER_AUTHORITATIVE,
}


@dataclass(frozen=True)
class ConfidenceResult:
    level: ConfidenceLevel
    limiting_component: str
    limiting_dependency: str | None
    downgrade_reasons: list[str]


def confidence_min(*levels: ConfidenceLevel) -> ConfidenceLevel:
    return min(levels, key=lambda level: CONFIDENCE_RANK[level])


def downgrade_one(level: ConfidenceLevel) -> ConfidenceLevel:
    rank_to_level = {rank: enum for enum, rank in CONFIDENCE_RANK.items()}
    return rank_to_level[max(CONFIDENCE_RANK[level] - 1, 0)]


class ConfidenceEngine:
    def compute(
        self,
        *,
        object_type: str,
        nav_surface_type: str | None,
        chain_status: str,
        execution_realism: str | None,
        governance_state: str,
        parent_confidences: dict[str, str],
        deterministic: bool,
        is_stale: bool,
        annotations: dict | None = None,
    ) -> ConfidenceResult:
        annotations = annotations or {}
        components: list[tuple[str, ConfidenceLevel, str | None]] = []
        components.append(
            (
                "surface",
                SURFACE_CEILINGS.get(nav_surface_type, ConfidenceLevel.UNAVAILABLE),
                None,
            )
        )
        components.append(("governance", self._governance_ceiling(object_type, governance_state), None))

        if parent_confidences:
            limiting_parent, parent_floor = min(
                ((obj_id, ConfidenceLevel(level)) for obj_id, level in parent_confidences.items()),
                key=lambda item: CONFIDENCE_RANK[item[1]],
            )
            components.append(("propagation", parent_floor, limiting_parent))

        trigger_floor, downgrade_reasons = self._trigger_floor(
            chain_status=chain_status,
            execution_realism=execution_realism,
            governance_state=governance_state,
            deterministic=deterministic,
            is_stale=is_stale,
            annotations=annotations,
        )
        components.append(("trigger", trigger_floor, None))

        limiting_component, level, limiting_dependency = min(
            components, key=lambda item: CONFIDENCE_RANK[item[1]]
        )
        return ConfidenceResult(
            level=level,
            limiting_component=limiting_component,
            limiting_dependency=limiting_dependency,
            downgrade_reasons=downgrade_reasons,
        )

    def _governance_ceiling(self, object_type: str, governance_state: str) -> ConfidenceLevel:
        if governance_state in {GovernanceState.GOVERNED_OBSERVING.value, GovernanceState.GOVERNED_DRAFT.value}:
            return ConfidenceLevel.PARTIAL_CONFIDENCE
        if governance_state == GovernanceState.UNGOVERNED.value and object_type in NAV_BEARING_OBJECTS:
            return ConfidenceLevel.LOW
        return ConfidenceLevel.BROKER_AUTHORITATIVE

    def _trigger_floor(
        self,
        *,
        chain_status: str,
        execution_realism: str | None,
        governance_state: str,
        deterministic: bool,
        is_stale: bool,
        annotations: dict,
    ) -> tuple[ConfidenceLevel, list[str]]:
        floor = ConfidenceLevel.BROKER_AUTHORITATIVE
        reasons: list[str] = []

        def apply(code: str, level: ConfidenceLevel) -> None:
            nonlocal floor
            reasons.append(code)
            floor = confidence_min(floor, level)

        if chain_status == ChainStatus.NO_PRIOR.value:
            apply("CHAIN_NO_PRIOR", ConfidenceLevel.LOW)
        if chain_status == ChainStatus.BROKEN_CHAIN.value:
            apply("CHAIN_BROKEN", ConfidenceLevel.LOW)
        if chain_status == ChainStatus.REPAIRED.value:
            apply("CHAIN_REPAIRED", ConfidenceLevel.PARTIAL_CONFIDENCE)
        if execution_realism == "MODEL_CLOSE_WITH_SYNTHETIC_COSTS":
            apply("BACKTEST_SYNTHETIC", ConfidenceLevel.PARTIAL_CONFIDENCE)
        if governance_state == GovernanceState.GOVERNED_OBSERVING.value:
            apply("GOV_OBSERVING", ConfidenceLevel.PARTIAL_CONFIDENCE)
        if not deterministic:
            apply("NON_DETERMINISTIC", ConfidenceLevel.PARTIAL_CONFIDENCE)
        if annotations.get("surface_override"):
            apply("SURFACE_OVERRIDE", ConfidenceLevel.LOW)
        if annotations.get("grandfathered"):
            apply("GRANDFATHERED_ARTIFACT", ConfidenceLevel.LOW)
        if annotations.get("provenance_invalidated"):
            apply("INPUT_INVALIDATED", ConfidenceLevel.LOW)
        if annotations.get("reconstruction", {}).get("kind") == "HYBRID":
            apply("RECONSTRUCTION_HYBRID", ConfidenceLevel.PARTIAL_CONFIDENCE)
        if is_stale:
            reasons.append("STALE")
            floor = downgrade_one(floor)

        return floor, sorted(set(reasons))
