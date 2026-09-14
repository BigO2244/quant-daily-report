"""Rule-based reconciliation that never auto-merges ambiguous work."""

from __future__ import annotations

from itertools import combinations
from typing import Any

from .domain import normalize_text, stable_id
from .store import AegisStore


class ReconciliationEngine:
    def __init__(self, store: AegisStore) -> None: self.store = store

    def run(self, as_of: str) -> list[dict[str, Any]]:
        entities = self.store.entities()
        references = self.store.external_references()
        relationships = self.store.relationships()
        records: list[dict[str, Any]] = []
        by_name: dict[str, list[dict[str, Any]]] = {}
        for entity in entities: by_name.setdefault(normalize_text(entity["name"]), []).append(entity)
        for name, matches in sorted(by_name.items()):
            if len(matches) > 1:
                for left, right in combinations(matches, 2):
                    records.append(self._record("EXACT_DUPLICATE", left, right, "Canonical names match exactly", {"normalized_name": name}, "Review and explicitly approve merge or retain as distinct"))
                    if left["status"] != right["status"]:
                        records.append(self._record("STATE_CONFLICT", left, right, "Matching work has conflicting source states", {"states": [left["status"], right["status"]]}, "Review source authority and select a state; do not auto-resolve"))
        distinct = [items[0] for items in by_name.values()]
        for left, right in combinations(distinct, 2):
            score = self._similarity(left["name"], right["name"])
            if score >= 0.75:
                records.append(self._record("PROBABLE_DUPLICATE", left, right, "Token similarity exceeded deterministic threshold", {"jaccard": score, "threshold": 0.75}, "Review only; no automatic merge"))
        linked_ids = {edge["source_id"] for edge in relationships} | {edge["target_id"] for edge in relationships}
        for reference in references:
            if reference["entity_id"] not in linked_ids:
                category = "ORPHANED_PR" if reference["external_type"] == "PR" else "ORPHANED_ISSUE"
                entity = self.store.entity(reference["entity_id"]) or {"id": reference["entity_id"], "name": reference["external_id"]}
                records.append(self._record(category, entity, None, "Open GitHub record has no explicit Aegis relationship", {"url": reference.get("url")}, "Link to an existing mission or explicitly classify as standalone"))
        mission_entity_ids = {entity["id"] for entity in entities if entity["entity_type"] == "MISSION"}
        native_mission_ids = {mission["id"] for mission in self.store.missions()}
        for entity_id in sorted(mission_entity_ids - linked_ids):
            entity = self.store.entity(entity_id)
            if entity and entity["origin"] == "IMPORTED":
                records.append(self._record("ACTIVE_WORK_WITHOUT_AEGIS_MISSION", entity, None, "Imported mission-shaped work is not linked to a native Aegis mission", {}, "Create or link a native approval-gated mission"))
        for mission in self.store.missions():
            if not mission.get("owner_capability"):
                records.append(self._record("MISSING_OWNER_CAPABILITY", {"id": mission["id"], "name": mission["objective"]}, None, "Mission has no owner capability", {}, "Assign an explicit capability owner"))
            if not mission.get("next_action"):
                records.append(self._record("MISSING_NEXT_ACTION", {"id": mission["id"], "name": mission["objective"]}, None, "Mission has no recorded next action", {}, "Record the next reviewable action"))
            if mission["id"] not in linked_ids and mission["id"] in native_mission_ids:
                records.append(self._record("MISSION_WITHOUT_SOURCE_EVIDENCE", {"id": mission["id"], "name": mission["objective"]}, None, "Native mission has no relationship to imported evidence", {}, "Attach source evidence or explicitly attest that the mission is native"))
        self.store.replace_reconciliation(records, as_of)
        return records

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        a, b = set(normalize_text(left).replace(":", " ").split()), set(normalize_text(right).replace(":", " ").split())
        return len(a & b) / len(a | b) if a | b else 0.0

    @staticmethod
    def _record(category: str, left: dict[str, Any], right: dict[str, Any] | None, explanation: str, evidence: dict[str, Any], action: str) -> dict[str, Any]:
        values = {"category": category, "entity": left["id"], "related": right["id"] if right else None}
        return {"id": stable_id("recon", values), "category": category, "entity_id": left["id"], "related_entity_id": right["id"] if right else None,
                "status": "PENDING_APPROVAL", "explanation": explanation, "evidence": evidence, "recommended_action": action}
