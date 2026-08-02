"""End-to-end, non-executing AEG-002 operationalization workflow."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .brief import ExecutiveBriefGenerator
from .dashboard import render_mission_control
from .domain import canonical_json, stable_id
from .importers import GitHubAdapter, GitHubImporter, RepositoryStateImporter
from .priority import PriorityEngine
from .reconciliation import ReconciliationEngine
from .service import AegisService
from .store import AegisStore

FIRST_MISSION_OBJECTIVE = "Consolidate all active Alpha Lab, Atlas, sleeve research, data-collection, risk-validation, engineering, governance, and dashboard work into Aegis; identify duplicates, dependencies, blockers, unresolved states, and the next five decisions requiring Brett’s attention."


class Operationalizer:
    def __init__(self, store: AegisStore, repo_root: Path) -> None:
        self.store = store; self.repo_root = Path(repo_root); self.service = AegisService(store)

    def run(self, as_of: str, github_adapter: GitHubAdapter | None, dry_run: bool = False, output_root: Path | None = None) -> dict[str, Any]:
        repo_result = RepositoryStateImporter(self.store, self.repo_root).import_state(as_of, dry_run=dry_run)
        github_result = GitHubImporter(self.store, "BigO2244/quant-daily-report").import_records(github_adapter, as_of, dry_run=dry_run) if github_adapter else None
        results = [repo_result] + ([github_result] if github_result else [])
        if dry_run:
            return {"dry_run": True, "imports": [result.as_dict() for result in results]}
        mission = self.service.create_mission(FIRST_MISSION_OBJECTIVE, {"owner_capability": "mission_planning", "next_action": "Review the five open executive decisions", "source_record_id": "AEG-002", "origin": "NATIVE", "as_of": as_of})
        caerus_id = stable_id("entity", {"taxonomy": "aegis_caerus_v1", "type": "PROGRAM", "name": "Caerus"})
        try: self.store.add_hierarchy(caerus_id, mission["id"], {"evidence": "AEG-002 explicit mission scope", "rule": "explicit_mission_scope_v1"}, as_of)
        except ValueError: pass
        for result in results:
            for entity_id in result.entities:
                if entity_id == mission["id"]: continue
                self.store.add_relationship(mission["id"], entity_id, "RELATED_TO", {"evidence": "AEG-002 explicit consolidation scope", "source_id": result.source_id}, "HIGH", as_of)
        reconciliation = ReconciliationEngine(self.store).run(as_of)
        PriorityEngine(self.store).score(mission["id"], {"criticality": 4, "blocker_impact": 4, "dependency_count": 5, "decision_urgency": 4, "production_risk": 1, "research_value": 5, "data_readiness": 4, "effort_remaining": 3, "age": 1, "executive_priority": 5, "required_by_active": 5, "incident_resolution": 0, "evidence_readiness": 4}, as_of)
        decisions = self._queue_decisions(mission["id"], reconciliation, [item for result in results for item in result.unresolved], as_of)
        brief_json, brief_md = ExecutiveBriefGenerator(self.store).generate(as_of)
        payload = {"mission": self.store.mission(mission["id"]), "imports": [result.as_dict() for result in results], "reconciliation": reconciliation, "decisions": decisions, "brief": brief_json}
        if output_root:
            self._write_outputs(Path(output_root), as_of, payload)
        return payload

    def _queue_decisions(self, mission_id: str, reconciliation: list[dict[str, Any]], unresolved: list[dict[str, Any]], as_of: str) -> list[dict[str, Any]]:
        candidates = [{"decision_type": "APPROVE_MISSION", "question": "Approve the non-executing AEG-002 consolidation mission for continued registry work?", "recommended_action": "Approve only the registry and review scope", "alternatives": ["Reject mission", "Request further evidence"], "evidence_links": ["reports/aegis/aeg_002_pr167_readiness_review.md"], "confidence": "HIGH", "risk_if_delayed": "Current work remains fragmented", "risk_if_approved": "Registry maintenance effort; no production execution authority"}]
        for item in unresolved:
            candidates.append({"decision_type": "REQUEST_FURTHER_EVIDENCE", "question": f"How should unresolved initiative {item['name']} be classified?", "recommended_action": "Request current source evidence before assigning an active or completed state", "alternatives": ["Park as unresolved", "Link additional evidence"], "evidence_links": ["reports/aegis/import/unresolved_state_items.json"], "confidence": "INSUFFICIENT_EVIDENCE", "risk_if_delayed": "Initiative remains unresolved", "risk_if_approved": "Premature classification could misstate current work"})
        for item in reconciliation:
            candidates.append({"decision_type": "APPROVE_RECONCILIATION", "question": f"Approve the recommended handling for {item['category']} record {item['id']}?", "recommended_action": item["recommended_action"], "alternatives": ["Defer", "Request further evidence"], "evidence_links": ["reports/aegis/reconciliation/recommended_actions.json"], "confidence": "MEDIUM" if item["evidence"] else "LOW", "risk_if_delayed": "Registry ambiguity persists", "risk_if_approved": "Incorrect linkage could obscure distinct work"})
        while len(candidates) < 5:
            candidates.append({"decision_type": "REQUEST_FURTHER_EVIDENCE", "question": f"Which evidence source should be reviewed next for consolidation checkpoint {len(candidates) + 1}?", "recommended_action": "Review the persisted source provenance before choosing a lifecycle state", "alternatives": ["Defer review"], "evidence_links": ["reports/aegis/import/source_provenance.json"], "confidence": "INSUFFICIENT_EVIDENCE", "risk_if_delayed": "Registry coverage remains incomplete", "risk_if_approved": "No state change; review effort only"})
        queued = []
        for candidate in candidates[:5]:
            candidate.update({"id": stable_id("decision", {"mission": mission_id, "question": candidate["question"]}), "mission_id": mission_id, "decision_owner": "Brett", "due_date": None, "status": "OPEN", "rationale": "", "final_decision_event": None})
            self.store.queue_decision(candidate, as_of); queued.append(candidate)
        return queued

    def _write_outputs(self, root: Path, as_of: str, payload: dict[str, Any]) -> None:
        imports = payload["imports"]; reconciliation = payload["reconciliation"]
        unresolved = [item for result in imports for item in result["unresolved"]]
        provenance = [item for result in imports for item in result["provenance"]]
        self._json(root / "import/current_state_import_manifest.json", {"schema_version": "aegis-import-manifest-v1", "as_of": as_of, "imports": imports})
        self._text(root / "import/current_state_import_summary.md", f"# Current State Import Summary\n\nAs of: {as_of}\n\n- Sources: {len(imports)}\n- Records inspected: {sum(i['records_seen'] for i in imports)}\n- Records changed: {sum(i['records_changed'] for i in imports)}\n- Unresolved states: {len(unresolved)}\n- GitHub metadata is planning provenance only, not trading or broker evidence.\n")
        self._json(root / "import/unresolved_state_items.json", unresolved); self._json(root / "import/source_provenance.json", provenance)
        self._text(root / "import/legacy_work_migration_report.md", f"# Legacy Work Migration Report\n\nAs of: {as_of}\n\n- Imported legacy/source records: {sum(i['records_seen'] for i in imports)}\n- Legacy records are warnings and review inputs; existing Caerus workflows are not blocked.\n- Records without explicit evidence remain unresolved or enter reconciliation.\n")
        categories = {name: [r for r in reconciliation if r["category"] == name] for name in {r["category"] for r in reconciliation}}
        self._text(root / "reconciliation/reconciliation_summary.md", f"# Reconciliation Summary\n\nAs of: {as_of}\n\n- Pending approval: {len(reconciliation)}\n- Categories: {', '.join(sorted(categories)) or 'none'}\n- Probable duplicates are never merged automatically.\n")
        self._json(root / "reconciliation/exact_duplicates.json", categories.get("EXACT_DUPLICATE", [])); self._json(root / "reconciliation/probable_duplicates.json", categories.get("PROBABLE_DUPLICATE", []))
        self._json(root / "reconciliation/orphaned_records.json", [r for r in reconciliation if r["category"].startswith("ORPHANED")]); self._json(root / "reconciliation/state_conflicts.json", categories.get("STATE_CONFLICT", [])); self._json(root / "reconciliation/recommended_actions.json", reconciliation)
        self._json(root / "briefs/latest.json", payload["brief"]); self._text(root / "briefs/latest.md", ExecutiveBriefGenerator.render_markdown(payload["brief"]))
        stamp = as_of[:10]; self._json(root / f"briefs/{stamp}.json", payload["brief"]); self._text(root / f"briefs/{stamp}.md", ExecutiveBriefGenerator.render_markdown(payload["brief"]))
        self._text(root / "mission_control.html", render_mission_control(self.service, as_of))
        mission_root = root / "first_mission"; self._json(mission_root / "mission_record.json", payload["mission"]); self._json(mission_root / "relationship_graph.json", self.store.relationships(payload["mission"]["id"])); self._json(mission_root / "blocker_report.json", self.service.blockers(payload["mission"]["id"])); self._json(mission_root / "decision_queue.json", payload["decisions"])
        self._json(mission_root / "boundary_attestation.json", {"as_of": as_of, "status": "NON_EXECUTING", "prohibited_actions_invoked": [], "statement": "Registry import and report generation only; no production, trading, broker, scheduler, VM, deployment, or capital mutation."})
        artifacts = sorted(path for path in root.rglob("*") if path.is_file())
        manifest = self.service.artifact_manifest(payload["mission"]["id"], artifacts)
        self._json(mission_root / "artifact_manifest.json", manifest)

    @staticmethod
    def _json(path: Path, value: Any) -> None: Operationalizer._text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")
    @staticmethod
    def _text(path: Path, value: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True); temp = path.with_suffix(path.suffix + ".tmp"); temp.write_text(value, encoding="utf-8"); os.replace(temp, path); os.chmod(path, 0o600)
