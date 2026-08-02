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
    """Approval-gated adapter for the existing local governed CLI only."""
    runner_class = "AIOPSRunnerAdapter"
    def command(self, spec_path: str, mode: str, approved: bool) -> list[str]:
        if not approved: raise PermissionError("Explicit mission approval is required")
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
        record = {"id": mission_id, "objective": normalized, "state": MissionState.DRAFT.value, "approval_state": "PENDING", "metadata": metadata}
        self.store.create_mission(record, tasks, edges, self._timestamp())
        self.store.transition_mission(mission_id, MissionState.PLANNED.value, self._timestamp())
        self.store.transition_mission(mission_id, MissionState.APPROVAL_REQUIRED.value, self._timestamp())
        return self.store.mission(mission_id) or record

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
