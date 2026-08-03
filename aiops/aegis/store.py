"""SQLite persistence with additive forward migrations and append-only events."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .domain import MISSION_TRANSITIONS, TASK_TRANSITIONS, canonical_json, validate_transition

MIGRATIONS = (
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY);
    CREATE TABLE IF NOT EXISTS missions (id TEXT PRIMARY KEY, objective TEXT NOT NULL,
      state TEXT NOT NULL, approval_state TEXT NOT NULL, metadata_json TEXT NOT NULL,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
      title TEXT NOT NULL, capability TEXT NOT NULL, runner_class TEXT NOT NULL,
      output_contract_json TEXT NOT NULL, state TEXT NOT NULL, approval_state TEXT NOT NULL,
      ordinal INTEGER NOT NULL, FOREIGN KEY(mission_id) REFERENCES missions(id));
    CREATE TABLE IF NOT EXISTS task_edges (mission_id TEXT NOT NULL, parent_task_id TEXT NOT NULL,
      child_task_id TEXT NOT NULL, PRIMARY KEY(mission_id, parent_task_id, child_task_id));
    CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
      task_id TEXT, path TEXT NOT NULL, sha256 TEXT NOT NULL, metadata_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, mission_id TEXT NOT NULL,
      task_id TEXT, decision_type TEXT NOT NULL, status TEXT NOT NULL, rationale TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS capabilities (name TEXT PRIMARY KEY, role TEXT NOT NULL,
      description TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS lifecycle_events (sequence INTEGER PRIMARY KEY AUTOINCREMENT,
      entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, event_type TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
    """,
)


class AegisStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            for version, sql in enumerate(MIGRATIONS, start=1):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def event(self, entity_type: str, entity_id: str, event_type: str, payload: dict[str, Any], created_at: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                         (entity_type, entity_id, event_type, canonical_json(payload), created_at))

    def create_mission(self, record: dict[str, Any], tasks: list[dict[str, Any]], edges: list[tuple[str, str]], created_at: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO missions VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (record["id"], record["objective"], record["state"], record["approval_state"], canonical_json(record["metadata"]), created_at, created_at))
            for task in tasks:
                conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (task["id"], record["id"], task["title"], task["capability"], task["runner_class"], canonical_json(task["output_contract"]), task["state"], task["approval_state"], task["ordinal"]))
            conn.executemany("INSERT INTO task_edges VALUES (?, ?, ?)", [(record["id"], parent, child) for parent, child in edges])
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                         ("mission", record["id"], "MISSION_CREATED", canonical_json({"task_count": len(tasks)}), created_at))

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row:
                return None
            result = dict(row); result["metadata"] = json.loads(result.pop("metadata_json"))
            tasks = [dict(item) for item in conn.execute("SELECT * FROM tasks WHERE mission_id = ? ORDER BY ordinal", (mission_id,))]
            for task in tasks: task["output_contract"] = json.loads(task.pop("output_contract_json"))
            result["tasks"] = tasks
            result["edges"] = [dict(item) for item in conn.execute("SELECT parent_task_id, child_task_id FROM task_edges WHERE mission_id = ? ORDER BY parent_task_id, child_task_id", (mission_id,))]
            result["artifacts"] = [dict(item) for item in conn.execute("SELECT * FROM artifacts WHERE mission_id = ? ORDER BY id", (mission_id,))]
            for artifact in result["artifacts"]:
                artifact["metadata"] = json.loads(artifact.pop("metadata_json"))
            result["decisions"] = [dict(item) for item in conn.execute("SELECT * FROM decisions WHERE mission_id = ? ORDER BY created_at, id", (mission_id,))]
            return result

    def missions(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id, objective, state, approval_state, created_at, updated_at FROM missions ORDER BY created_at DESC, id")]

    def transition_mission(self, mission_id: str, target: str, at: str) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row: raise KeyError(mission_id)
            validate_transition(row["state"], target, MISSION_TRANSITIONS)
            conn.execute("UPDATE missions SET state = ?, updated_at = ? WHERE id = ?", (target, at, mission_id))
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", ("mission", mission_id, "STATE_CHANGED", canonical_json({"from": row["state"], "to": target}), at))

    def approve_mission(self, mission_id: str, rationale: str, at: str) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row: raise KeyError(mission_id)
            if row["state"] != "APPROVAL_REQUIRED": raise ValueError("Mission must be APPROVAL_REQUIRED before approval")
            conn.execute("UPDATE missions SET approval_state = 'APPROVED', updated_at = ? WHERE id = ?", (at, mission_id))
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", ("mission", mission_id, "APPROVED", canonical_json({"rationale": rationale}), at))

    def transition_task(self, task_id: str, target: str, at: str) -> None:
        with self._connection() as conn:
            row = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row: raise KeyError(task_id)
            validate_transition(row["state"], target, TASK_TRANSITIONS)
            conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (target, task_id))
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", ("task", task_id, "STATE_CHANGED", canonical_json({"from": row["state"], "to": target}), at))

    def register_capabilities(self, values: list[tuple[str, str]]) -> None:
        with self._connection() as conn:
            conn.executemany("INSERT OR IGNORE INTO capabilities(name, role, description) VALUES (?, ?, ?)", [(capability, role, f"{role} capability: {capability}") for role, capability in values])

    def record_decision(self, decision: dict[str, str], created_at: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)", (decision["id"], decision["mission_id"], decision.get("task_id"), decision["decision_type"], decision["status"], decision["rationale"], created_at))
            conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)", ("decision", decision["id"], "DECISION_RECORDED", canonical_json(decision), created_at))

    def record_artifacts(self, mission_id: str, entries: list[dict[str, str]]) -> None:
        with self._connection() as conn:
            conn.executemany("INSERT OR REPLACE INTO artifacts VALUES (?, ?, ?, ?, ?, ?)", [(entry["id"], mission_id, entry.get("task_id"), entry["path"], entry["sha256"], canonical_json(entry.get("metadata", {}))) for entry in entries])
