"""SEM-004 governance inheritance engine."""

from __future__ import annotations

from dataclasses import dataclass

from research_registry.models.enums import GOVERNANCE_PRECEDENCE, GovernanceState


FR_STATUS_TO_STATE = {
    "BACKLOG": GovernanceState.GOVERNED_DRAFT,
    "READY": GovernanceState.GOVERNED_DRAFT,
    "READY_VALIDATED": GovernanceState.GOVERNED_DRAFT,
    "IN_PROGRESS": GovernanceState.GOVERNED_DRAFT,
    "PROMOTION_READY": GovernanceState.GOVERNED_DRAFT,
    "DEPLOYED_OBSERVING": GovernanceState.GOVERNED_OBSERVING,
    "DEPLOYED": GovernanceState.GOVERNED_DEPLOYED,
    "REVIEWED_DEFERRED": GovernanceState.GOVERNED_DEFERRED,
}


@dataclass(frozen=True)
class GovernanceResult:
    state: GovernanceState
    governing_frs: list[str]
    coverage_type: str
    inheritance_blocked: bool = False
    block_reasons: list[str] | None = None


class GovernanceEngine:
    def state_from_fr_statuses(self, statuses: list[str]) -> GovernanceState:
        if not statuses:
            return GovernanceState.UNGOVERNED
        states = [FR_STATUS_TO_STATE.get(status, GovernanceState.UNGOVERNED) for status in statuses]
        return max(states, key=lambda state: GOVERNANCE_PRECEDENCE[state])

    def inherit(
        self,
        *,
        parent_governance: dict[str, dict],
        deterministic: bool,
        child_surface: str | None,
        parent_surfaces: dict[str, str | None],
        child_ontology_version: str,
        parent_ontology_versions: dict[str, str],
        materiality_map: dict[str, str] | None = None,
    ) -> GovernanceResult:
        materiality_map = materiality_map or {}
        block_reasons: list[str] = []
        if not deterministic:
            block_reasons.append("NON_DETERMINISTIC_DERIVATION")

        inherited_frs: set[str] = set()
        inherited_states: list[GovernanceState] = []
        for parent_id, governance in parent_governance.items():
            materiality = materiality_map.get(parent_id, "material")
            parent_state = GovernanceState(governance["state"])
            if materiality == "material" and parent_state == GovernanceState.UNGOVERNED:
                block_reasons.append(f"UNGOVERNED_MATERIAL_PARENT:{parent_id}")
            if parent_surfaces.get(parent_id) != child_surface:
                block_reasons.append(f"SURFACE_BOUNDARY:{parent_id}")
            if parent_ontology_versions.get(parent_id) != child_ontology_version:
                block_reasons.append(f"ONTOLOGY_VERSION_BOUNDARY:{parent_id}")
            if str(parent_state).startswith("GOVERNED"):
                inherited_frs.update(governance.get("governing_frs", []))
                inherited_states.append(parent_state)

        if block_reasons or not inherited_frs:
            return GovernanceResult(
                state=GovernanceState.UNGOVERNED,
                governing_frs=[],
                coverage_type="UNGOVERNED",
                inheritance_blocked=bool(block_reasons),
                block_reasons=block_reasons,
            )
        return GovernanceResult(
            state=max(inherited_states, key=lambda state: GOVERNANCE_PRECEDENCE[state]),
            governing_frs=sorted(inherited_frs),
            coverage_type="INHERITED",
        )
