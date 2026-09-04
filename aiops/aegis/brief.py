"""Snapshot-reproducible executive brief derived only from persisted state."""

from __future__ import annotations

import hashlib
from typing import Any

from .domain import canonical_json, stable_id
from .priority import PriorityEngine
from .store import AegisStore

SECTIONS = ("Executive Summary", "Mission Portfolio", "Top Priorities", "Decisions Required", "Blockers", "Research Progress", "Engineering Progress", "Validation and Risk", "Stale or Unowned Work", "Newly Imported Work", "Completed Since Prior Brief", "Recommended Executive Actions", "Provenance and Generation Metadata")


class ExecutiveBriefGenerator:
    def __init__(self, store: AegisStore) -> None: self.store = store

    def generate(self, as_of: str, persist: bool = True) -> tuple[dict[str, Any], str]:
        missions = self.store.missions(); entities = self.store.entities(); decisions = self.store.decisions_queue("OPEN")
        reconciliation = self.store.reconciliation(); priorities = PriorityEngine(self.store).ranking(); source_health = self.store.source_health()
        blockers = [item for item in entities if "BLOCK" in item["status"]] + [item for item in missions if item["state"] == "BLOCKED"]
        stale = [item for item in source_health if item["status"] != "CURRENT"]
        unresolved = [item for item in entities if item["status"] == "STATUS_UNRESOLVED"]
        alpha_portfolio = [item for item in entities if item["metadata"].get("alpha_lab_record_type") == "RESEARCH_FAMILY"]
        alpha_blockers = [item for item in entities if item["metadata"].get("alpha_lab_record_type") == "BLOCKER"]
        prior = self.store.briefs(); prior_ids = set(prior[-1]["payload"].get("mission_ids", [])) if prior else set()
        payload: dict[str, Any] = {
            "schema_version": "aegis-executive-brief-v1", "as_of": as_of,
            "executive_summary": {"missions": len(missions), "open_decisions": len(decisions), "blockers": len(blockers), "unresolved": len(unresolved)},
            "mission_portfolio": missions, "top_priorities": priorities, "decisions_required": decisions,
            "blockers": blockers, "research_progress": [e for e in entities if e["entity_type"] in {"INITIATIVE", "MISSION"} and ("RESEARCH" in e["status"] or e["metadata"].get("alpha_lab_record_type") == "RESEARCH_FAMILY")],
            "alpha_lab": {"portfolio": alpha_portfolio, "blockers": alpha_blockers},
            "engineering_progress": [m for m in missions if m.get("owner_capability") == "engineering"],
            "validation_and_risk": {"reconciliation": reconciliation, "source_health": source_health},
            "stale_or_unowned_work": stale + [m for m in missions if not m.get("owner_capability")],
            "newly_imported_work": [e for e in entities if e["origin"] == "IMPORTED"],
            "completed_since_prior_brief": [m for m in missions if m["state"] == "COMPLETED" and m["id"] not in prior_ids],
            "recommended_executive_actions": [{"classification": "RULE_BASED_RECOMMENDATION", "action": r["recommended_action"], "record_id": r["id"]} for r in reconciliation[:5]],
            "unresolved_ambiguity": unresolved, "provenance": {"sources": source_health, "fact_rule": "Persisted Aegis state only", "missing_or_stale_sources": stale},
            "mission_ids": sorted(m["id"] for m in missions),
        }
        digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
        payload["payload_sha256"] = digest
        brief_id = stable_id("brief", {"as_of": as_of, "sha256": digest})
        if persist: self.store.save_brief(brief_id, as_of, digest, payload, as_of)
        return payload, self.render_markdown(payload)

    @staticmethod
    def render_markdown(payload: dict[str, Any]) -> str:
        summary = payload["executive_summary"]
        lines = ["# Aegis Executive Brief", "", f"As of: {payload['as_of']}", "", "## Executive Summary", "", f"Fact: {summary['missions']} missions, {summary['open_decisions']} open decisions, {summary['blockers']} blockers, and {summary['unresolved']} unresolved states."]
        mappings = (
            ("Mission Portfolio", "mission_portfolio", lambda x: f"{x['id']} — {x['state']} — {x['objective']}"),
            ("Top Priorities", "top_priorities", lambda x: f"{x['mission_id']} — {x['total']:.2f}"),
            ("Decisions Required", "decisions_required", lambda x: f"{x['id']} — {x['question']} [{x['confidence']}]"),
            ("Blockers", "blockers", lambda x: f"{x['id']} — {x.get('name', x.get('objective', ''))}"),
            ("Research Progress", "research_progress", lambda x: f"{x['id']} — {x['status']} — {x['name']}"),
            ("Engineering Progress", "engineering_progress", lambda x: f"{x['id']} — {x['state']} — {x['objective']}"),
        )
        for title, key, formatter in mappings:
            lines.extend(["", f"## {title}", ""]); values = payload[key]; lines.extend([f"- {formatter(item)}" for item in values] or ["- None recorded."])
        alpha = payload.get("alpha_lab", {"portfolio": [], "blockers": []})
        lines.extend(["", "## Alpha Lab MVP", "", f"- Fact: {len(alpha['portfolio'])} research families and {len(alpha['blockers'])} explicit blockers imported from pinned source evidence."])
        lines.extend([f"- {item['name']} — {item['status']} — {item['metadata'].get('next_action', '')}" for item in alpha["portfolio"]] or ["- No Alpha Lab snapshot imported."])
        lines.extend(["", "## Validation and Risk", "", f"- Fact: {len(payload['validation_and_risk']['reconciliation'])} reconciliation items; {len(payload['validation_and_risk']['source_health'])} source-health records.", "", "## Stale or Unowned Work", ""])
        lines.extend([f"- {item.get('id', item.get('source_id', 'unknown'))}" for item in payload["stale_or_unowned_work"]] or ["- None recorded."])
        lines.extend(["", "## Newly Imported Work", "", f"- Fact: {len(payload['newly_imported_work'])} imported registry entities.", "", "## Completed Since Prior Brief", ""])
        lines.extend([f"- {item['id']} — {item['objective']}" for item in payload["completed_since_prior_brief"]] or ["- None recorded."])
        lines.extend(["", "## Recommended Executive Actions", ""])
        lines.extend([f"- Rule-based recommendation: {item['action']} ({item['record_id']})" for item in payload["recommended_executive_actions"]] or ["- None recorded."])
        lines.extend(["", "## Provenance and Generation Metadata", "", f"- Snapshot SHA-256: `{payload['payload_sha256']}`", "- Source of facts: persisted Aegis state only.", f"- Missing or stale sources: {len(payload['provenance']['missing_or_stale_sources'])}.", "- Unresolved ambiguity is reported and not silently resolved.", ""])
        return "\n".join(lines)
