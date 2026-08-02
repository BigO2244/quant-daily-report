"""Deterministic mission decomposition, packet generation, and read models."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import MissionState, TaskState, canonical_json, stable_id
from .store import AegisStore

ROLE_CAPABILITIES = (
    ("Chief of Staff", "mission_planning"), ("Chief Quant", "portfolio_risk_analysis"),
    ("Research Lead", "research"), ("Modeling Lead", "modeling"), ("Data Lead", "pit_validation"),
    ("Risk Officer", "risk_review"), ("Independent Reviewer", "verification_review"),
    ("Engineering Lead", "engineering"), ("Operations Lead", "operations"),
)
TASK_TEMPLATES = (
    ("Research brief", "research", "research_adapter"), ("Modeling plan", "modeling", "modeling_adapter"),
    ("PIT and data validation", "pit_validation", "data_pit_adapter"), ("Backtest protocol", "backtesting", "backtesting_adapter"),
    ("Portfolio and risk analysis", "portfolio_risk_analysis", "portfolio_risk_adapter"),
    ("Engineering implementation", "engineering", "engineering_adapter"), ("Independent verification", "verification_review", "verification_adapter"),
    ("Operations handoff", "operations", "operations_adapter"),
)


class PacketOnlyRunnerAdapter:
    """Safe default: returns a deterministic packet and executes nothing."""
    runner_class = "PacketOnlyRunnerAdapter"
    def dispatch(self, packet: dict[str, Any]) -> dict[str, Any]: return packet


class AIOPSRunnerAdapter:
    """Persisted-approval-gated adapter for the existing governed CLI only."""
    runner_class = "AIOPSRunnerAdapter"
    def __init__(self, store: AegisStore) -> None: self.store = store
    def command(self, mission_id: str, task_id: str, spec_path: str, mode: str) -> list[str]:
        mission = self.store.mission(mission_id)
        if not mission: raise KeyError(f"Invalid mission reference: {mission_id}")
        task = next((item for item in mission["tasks"] if item["id"] == task_id), None)
        if not task or not task.get("output_contract"): raise ValueError("Valid task output contract is required")
        if mission["approval_state"] != "APPROVED": raise PermissionError("Explicit persisted mission approval is required")
        return ["aiops", "run-all", "--spec", spec_path, "--mode", mode]


class AegisService:
    def __init__(self, store: AegisStore) -> None:
        self.store = store
        self.store.register_capabilities(list(ROLE_CAPABILITIES))

    @staticmethod
    def _timestamp() -> str: return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    def create_mission(self, objective: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized = " ".join(objective.split())
        if not normalized: raise ValueError("Mission objective is required")
        metadata = metadata or {}
        mission_id = stable_id("mission", {"objective": normalized, "metadata": metadata})
        existing = self.store.mission(mission_id)
        if existing: return existing
        tasks = []
        for ordinal, (title, capability, adapter) in enumerate(TASK_TEMPLATES, start=1):
            task_id = stable_id("task", {"mission": mission_id, "ordinal": ordinal, "capability": capability})
            tasks.append({"id": task_id, "title": title, "capability": capability, "runner_class": "PacketOnlyRunnerAdapter", "adapter": adapter,
                          "output_contract": {"schema_version": "aegis-output-v1", "required": ["summary", "provenance", "validation"]},
                          "state": TaskState.DRAFT.value, "approval_state": "NOT_REQUIRED", "ordinal": ordinal})
        edges = [(tasks[index - 1]["id"], tasks[index]["id"]) for index in range(1, len(tasks))]
        record = {"id": mission_id, "objective": normalized, "state": MissionState.DRAFT.value, "approval_state": "PENDING", "metadata": metadata,
                  "origin": metadata.get("origin", "NATIVE"), "source_record_id": metadata.get("source_record_id"),
                  "owner_capability": metadata.get("owner_capability"), "next_action": metadata.get("next_action")}
        self.store.create_mission(record, tasks, edges, self._timestamp())
        self._register_mission_graph(record, tasks, edges)
        self.store.transition_mission(mission_id, MissionState.PLANNED.value, self._timestamp())
        self.store.transition_mission(mission_id, MissionState.APPROVAL_REQUIRED.value, self._timestamp())
        self.store.upsert_entity({"id": mission_id, "entity_type": "MISSION", "name": normalized, "status": MissionState.APPROVAL_REQUIRED.value,
                                  "origin": record.get("origin", "NATIVE"), "source_record_id": record.get("source_record_id"), "metadata": metadata}, self._timestamp())
        return self.store.mission(mission_id) or record

    def _register_mission_graph(self, record: dict[str, Any], tasks: list[dict[str, Any]], edges: list[tuple[str, str]]) -> None:
        at = self._timestamp()
        self.store.upsert_entity({"id": record["id"], "entity_type": "MISSION", "name": record["objective"], "status": record["state"], "origin": record.get("origin", "NATIVE"), "source_record_id": record.get("source_record_id"), "metadata": record.get("metadata", {})}, at)
        for task in tasks:
            self.store.upsert_entity({"id": task["id"], "entity_type": "TASK", "name": task["title"], "status": task["state"], "origin": "NATIVE", "source_record_id": None, "metadata": {"mission_id": record["id"], "capability": task["capability"]}}, at)
            self.store.add_relationship(record["id"], task["id"], "PARENT_OF", {"evidence": "native mission decomposition"}, "HIGH", at)
        for parent, child in edges:
            self.store.add_relationship(child, parent, "DEPENDS_ON", {"evidence": "native task DAG"}, "HIGH", at)

    def approve(self, mission_id: str, rationale: str) -> dict[str, Any]:
        self.store.approve_mission(mission_id, rationale, self._timestamp())
        self.store.record_decision({"id": stable_id("decision", {"mission": mission_id, "rationale": rationale}), "mission_id": mission_id,
                                    "decision_type": "MISSION_APPROVAL", "status": "APPROVED", "rationale": rationale}, self._timestamp())
        return self.store.mission(mission_id) or {}

    def execution_packet(self, mission_id: str, task_id: str, spec_path: str | None = None, mode: str = "BUILD") -> dict[str, Any]:
        mission = self.store.mission(mission_id)
        if not mission: raise KeyError(mission_id)
        task = next((item for item in mission["tasks"] if item["id"] == task_id), None)
        if not task: raise KeyError(task_id)
        packet = {"schema_version": "aegis-execution-packet-v1", "mission_id": mission_id, "task_id": task_id,
                  "capability": task["capability"], "runner_class": task["runner_class"], "output_contract": task["output_contract"],
                  "dependencies": [edge["parent_task_id"] for edge in mission["edges"] if edge["child_task_id"] == task_id],
                  "approval_state": mission["approval_state"], "aiops": {"spec_path": spec_path, "mode": mode} if spec_path else None}
        packet["packet_sha256"] = hashlib.sha256(canonical_json(packet).encode("utf-8")).hexdigest()
        return packet

    def link_github(self, mission_id: str, external_entity_id: str, record_type: str, as_of: str) -> str:
        if not self.store.mission(mission_id): raise KeyError(f"Invalid mission reference: {mission_id}")
        if not self.store.entity(external_entity_id): raise KeyError(f"Unknown GitHub record: {external_entity_id}")
        relationship_type = "TRACKED_BY_PR" if record_type.upper() == "PR" else "TRACKED_BY_ISSUE"
        return self.store.add_relationship(mission_id, external_entity_id, relationship_type, {"evidence": "explicit operator link"}, "HIGH", as_of)

    def related_work(self, initiative_id: str) -> list[dict[str, Any]]: return self.store.traverse(initiative_id)
    def blockers(self, mission_id: str) -> list[dict[str, Any]]: return self.store.traverse(mission_id, "out", "BLOCKED_BY")
    def produced_artifacts(self, mission_id: str) -> list[dict[str, Any]]: return self.store.traverse(mission_id, "out", "PRODUCES")
    def linked_prs(self, mission_id: str) -> list[dict[str, Any]]: return self.store.traverse(mission_id, "out", "TRACKED_BY_PR")
    def decisions_supported_by_artifact(self, artifact_id: str) -> list[dict[str, Any]]: return self.store.traverse(artifact_id, "out", "VALIDATES")
    def downstream_dependents(self, blocked_task_id: str) -> list[dict[str, Any]]: return self.store.traverse(blocked_task_id, "in", "DEPENDS_ON")
    def research_to_implementation_lineage(self, entity_id: str) -> list[dict[str, Any]]: return self.store.traverse(entity_id)

    def artifact_manifest(self, mission_id: str, paths: list[Path]) -> dict[str, Any]:
        entries = []
        for path in sorted(paths, key=lambda item: str(item)):
            entries.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest = {"schema_version": "aegis-artifact-manifest-v1", "mission_id": mission_id, "artifacts": entries}
        manifest["manifest_sha256"] = hashlib.sha256(canonical_json(manifest).encode("utf-8")).hexdigest()
        self.store.record_artifacts(mission_id, [{"id": stable_id("artifact", {"mission": mission_id, **entry}), **entry, "metadata": {"source": "local_manifest"}} for entry in entries])
        return manifest

    def import_metadata(self, mission_id: str, paths: list[Path]) -> list[dict[str, Any]]:
        """Register names and filesystem metadata only; source contents are never copied or executed."""
        entries = [{"id": stable_id("artifact", {"mission": mission_id, "path": str(path)}), "path": str(path), "sha256": "METADATA_ONLY",
                    "metadata": {"source": "metadata_import", "exists": path.exists(), "size": path.stat().st_size if path.exists() else None}} for path in sorted(paths, key=str)]
        self.store.record_artifacts(mission_id, entries)
        return entries

    def executive_brief(self) -> str:
        missions = self.store.missions()
        blockers = [m for m in missions if m["state"] in {"BLOCKED", "DECISION_REQUIRED", "APPROVAL_REQUIRED"}]
        lines = ["# Aegis Executive Brief", "", f"- Missions: {len(missions)}", f"- Decision / approval queue: {len(blockers)}", "", "## Portfolio"]
        lines.extend(f"- {item['id']}: {item['state']} — {item['objective']}" for item in missions)
        return "\n".join(lines) + "\n"

    def mission_control_model(self) -> list[dict[str, Any]]:
        """Deterministic portfolio, DAG, blocker, artifact, and decision read model."""
        return [self.store.mission(item["id"]) or item for item in self.store.missions()]
