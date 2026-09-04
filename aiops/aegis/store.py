"""Transactional SQLite registry with forward-only migrations and audit events."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .domain import (
    ACYCLIC_RELATIONSHIPS,
    CERTAINTY_CLASSES,
    ENTITY_TYPES,
    MISSION_TRANSITIONS,
    RELATIONSHIP_TYPES,
    TASK_TRANSITIONS,
    canonical_json,
    stable_id,
    validate_transition,
)

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
    """
    ALTER TABLE missions ADD COLUMN origin TEXT NOT NULL DEFAULT 'NATIVE';
    ALTER TABLE missions ADD COLUMN source_record_id TEXT;
    ALTER TABLE missions ADD COLUMN owner_capability TEXT;
    ALTER TABLE missions ADD COLUMN next_action TEXT;

    CREATE TABLE registry_entities (
      id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, name TEXT NOT NULL,
      status TEXT NOT NULL, origin TEXT NOT NULL, source_record_id TEXT,
      metadata_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(origin, source_record_id, entity_type));
    CREATE TABLE hierarchy_links (
      parent_id TEXT NOT NULL REFERENCES registry_entities(id) ON DELETE RESTRICT,
      child_id TEXT PRIMARY KEY REFERENCES registry_entities(id) ON DELETE RESTRICT,
      provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
      CHECK(parent_id <> child_id));
    CREATE TABLE relationships (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES registry_entities(id) ON DELETE RESTRICT,
      target_id TEXT NOT NULL REFERENCES registry_entities(id) ON DELETE RESTRICT,
      relationship_type TEXT NOT NULL, certainty TEXT NOT NULL,
      inference_rule TEXT, provenance_json TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(source_id, target_id, relationship_type), CHECK(source_id <> target_id));
    CREATE TABLE import_sources (
      id TEXT PRIMARY KEY, source_type TEXT NOT NULL, source_uri TEXT NOT NULL,
      authoritative_scope TEXT NOT NULL, fetched_at TEXT NOT NULL, stale_after TEXT,
      metadata_json TEXT NOT NULL, UNIQUE(source_type, source_uri));
    CREATE TABLE source_snapshots (
      id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES import_sources(id) ON DELETE RESTRICT,
      content_sha256 TEXT NOT NULL, record_count INTEGER NOT NULL,
      captured_at TEXT NOT NULL, payload_json TEXT NOT NULL,
      UNIQUE(source_id, content_sha256));
    CREATE TABLE external_references (
      id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES registry_entities(id) ON DELETE RESTRICT,
      source_id TEXT NOT NULL REFERENCES import_sources(id) ON DELETE RESTRICT,
      external_type TEXT NOT NULL, external_id TEXT NOT NULL, url TEXT,
      state TEXT NOT NULL, metadata_json TEXT NOT NULL, created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL, UNIQUE(source_id, external_type, external_id));
    CREATE TABLE reconciliation_records (
      id TEXT PRIMARY KEY, category TEXT NOT NULL, entity_id TEXT,
      related_entity_id TEXT, status TEXT NOT NULL, explanation TEXT NOT NULL,
      evidence_json TEXT NOT NULL, recommended_action TEXT NOT NULL,
      created_at TEXT NOT NULL, UNIQUE(category, entity_id, related_entity_id));
    CREATE TABLE priority_scores (
      mission_id TEXT PRIMARY KEY REFERENCES missions(id) ON DELETE RESTRICT,
      urgency REAL NOT NULL, importance REAL NOT NULL, risk REAL NOT NULL,
      readiness REAL NOT NULL, total REAL NOT NULL, explanation_json TEXT NOT NULL,
      calculated_at TEXT NOT NULL);
    CREATE TABLE executive_overrides (
      id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
      override_rank INTEGER NOT NULL, rationale TEXT NOT NULL, owner TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE decision_queue (
      id TEXT PRIMARY KEY, mission_id TEXT NOT NULL REFERENCES missions(id) ON DELETE RESTRICT,
      decision_type TEXT NOT NULL, question TEXT NOT NULL, recommended_action TEXT NOT NULL,
      alternatives_json TEXT NOT NULL, evidence_links_json TEXT NOT NULL,
      confidence TEXT NOT NULL, risk_if_delayed TEXT NOT NULL,
      risk_if_approved TEXT NOT NULL, decision_owner TEXT NOT NULL, due_date TEXT,
      status TEXT NOT NULL, rationale TEXT NOT NULL, final_event_json TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE brief_snapshots (
      id TEXT PRIMARY KEY, as_of TEXT NOT NULL, payload_sha256 TEXT NOT NULL,
      payload_json TEXT NOT NULL, created_at TEXT NOT NULL,
      UNIQUE(as_of, payload_sha256));
    CREATE TABLE stale_data_status (
      source_id TEXT PRIMARY KEY REFERENCES import_sources(id) ON DELETE RESTRICT,
      status TEXT NOT NULL, reason TEXT NOT NULL, evaluated_at TEXT NOT NULL);
    CREATE INDEX idx_relationship_source ON relationships(source_id, relationship_type);
    CREATE INDEX idx_relationship_target ON relationships(target_id, relationship_type);
    CREATE INDEX idx_external_entity ON external_references(entity_id);
    CREATE INDEX idx_reconciliation_category ON reconciliation_records(category, status);
    """,
)


class AegisStore:
    """Single-process writer/multi-reader store; concurrent writers wait five seconds."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.migrate()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._open()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def migrate(self) -> None:
        with self._connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)")
            for version, sql in enumerate(MIGRATIONS, start=1):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = ?", (version,)).fetchone():
                    continue
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))

    def schema_version(self) -> int:
        with self._connection() as conn:
            return int(conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0])

    @staticmethod
    def _event(conn: sqlite3.Connection, entity_type: str, entity_id: str, event_type: str, payload: dict[str, Any], at: str) -> None:
        conn.execute("INSERT INTO lifecycle_events(entity_type, entity_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)",
                     (entity_type, entity_id, event_type, canonical_json(payload), at))

    def events(self, entity_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as conn:
            sql = "SELECT * FROM lifecycle_events" + (" WHERE entity_id = ?" if entity_id else "") + " ORDER BY sequence"
            rows = conn.execute(sql, (entity_id,) if entity_id else ()).fetchall()
            return [dict(row) for row in rows]

    def create_mission(self, record: dict[str, Any], tasks: list[dict[str, Any]], edges: list[tuple[str, str]], created_at: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO missions(id, objective, state, approval_state, metadata_json, created_at, updated_at, origin, source_record_id, owner_capability, next_action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (record["id"], record["objective"], record["state"], record["approval_state"], canonical_json(record.get("metadata", {})), created_at, created_at, record.get("origin", "NATIVE"), record.get("source_record_id"), record.get("owner_capability"), record.get("next_action")))
            for task in tasks:
                conn.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (task["id"], record["id"], task["title"], task["capability"], task["runner_class"], canonical_json(task["output_contract"]), task["state"], task["approval_state"], task["ordinal"]))
            conn.executemany("INSERT INTO task_edges VALUES (?, ?, ?)", [(record["id"], parent, child) for parent, child in edges])
            self._event(conn, "mission", record["id"], "MISSION_CREATED", {"task_count": len(tasks)}, created_at)

    def mission(self, mission_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["metadata"] = json.loads(result.pop("metadata_json"))
            tasks = [dict(item) for item in conn.execute("SELECT * FROM tasks WHERE mission_id = ? ORDER BY ordinal", (mission_id,))]
            for task in tasks:
                task["output_contract"] = json.loads(task.pop("output_contract_json"))
            result["tasks"] = tasks
            result["edges"] = [dict(item) for item in conn.execute("SELECT parent_task_id, child_task_id FROM task_edges WHERE mission_id = ? ORDER BY parent_task_id, child_task_id", (mission_id,))]
            result["artifacts"] = [dict(item) for item in conn.execute("SELECT * FROM artifacts WHERE mission_id = ? ORDER BY id", (mission_id,))]
            for artifact in result["artifacts"]:
                artifact["metadata"] = json.loads(artifact.pop("metadata_json"))
            result["decisions"] = [dict(item) for item in conn.execute("SELECT * FROM decisions WHERE mission_id = ? ORDER BY created_at, id", (mission_id,))]
            return result

    def missions(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("SELECT id, objective, state, approval_state, origin, source_record_id, owner_capability, next_action, created_at, updated_at FROM missions ORDER BY created_at DESC, id")]

    def transition_mission(self, mission_id: str, target: str, at: str) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row:
                raise KeyError(mission_id)
            validate_transition(row["state"], target, MISSION_TRANSITIONS)
            conn.execute("UPDATE missions SET state = ?, updated_at = ? WHERE id = ?", (target, at, mission_id))
            self._event(conn, "mission", mission_id, "STATE_CHANGED", {"from": row["state"], "to": target}, at)

    def approve_mission(self, mission_id: str, rationale: str, at: str) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM missions WHERE id = ?", (mission_id,)).fetchone()
            if not row:
                raise KeyError(mission_id)
            if row["state"] != "APPROVAL_REQUIRED":
                raise ValueError("Mission must be APPROVAL_REQUIRED before approval")
            conn.execute("UPDATE missions SET approval_state = 'APPROVED', updated_at = ? WHERE id = ?", (at, mission_id))
            self._event(conn, "mission", mission_id, "APPROVED", {"rationale": rationale}, at)

    def transition_task(self, task_id: str, target: str, at: str) -> None:
        with self.transaction() as conn:
            row = conn.execute("SELECT state FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise KeyError(task_id)
            validate_transition(row["state"], target, TASK_TRANSITIONS)
            conn.execute("UPDATE tasks SET state = ? WHERE id = ?", (target, task_id))
            self._event(conn, "task", task_id, "STATE_CHANGED", {"from": row["state"], "to": target}, at)

    def add_task_edge(self, mission_id: str, parent: str, child: str) -> None:
        with self.transaction() as conn:
            rows = conn.execute("SELECT parent_task_id, child_task_id FROM task_edges WHERE mission_id = ?", (mission_id,)).fetchall()
            graph: dict[str, set[str]] = {}
            for row in rows:
                graph.setdefault(row["parent_task_id"], set()).add(row["child_task_id"])
            if self._reachable(graph, child, parent):
                raise ValueError("Task dependency cycle rejected")
            conn.execute("INSERT OR IGNORE INTO task_edges VALUES (?, ?, ?)", (mission_id, parent, child))

    def register_capabilities(self, values: list[tuple[str, str]]) -> None:
        with self._connection() as conn:
            conn.executemany("INSERT OR IGNORE INTO capabilities(name, role, description) VALUES (?, ?, ?)", [(capability, role, f"{role} capability: {capability}") for role, capability in values])

    def record_decision(self, decision: dict[str, str], created_at: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT INTO decisions VALUES (?, ?, ?, ?, ?, ?, ?)", (decision["id"], decision["mission_id"], decision.get("task_id"), decision["decision_type"], decision["status"], decision["rationale"], created_at))
            self._event(conn, "decision", decision["id"], "DECISION_RECORDED", decision, created_at)

    def record_artifacts(self, mission_id: str, entries: list[dict[str, Any]]) -> None:
        with self.transaction() as conn:
            for entry in entries:
                existing = conn.execute("SELECT path, sha256, metadata_json FROM artifacts WHERE id = ?", (entry["id"],)).fetchone()
                values = (entry["path"], entry["sha256"], canonical_json(entry.get("metadata", {})))
                if existing and tuple(existing) != values:
                    raise ValueError(f"Artifact identity collision: {entry['id']}")
                if not existing:
                    conn.execute("INSERT INTO artifacts VALUES (?, ?, ?, ?, ?, ?)", (entry["id"], mission_id, entry.get("task_id"), *values))
                    self._event(conn, "artifact", entry["id"], "ARTIFACT_RECORDED", {"mission_id": mission_id, "path": entry["path"]}, entry.get("created_at", "1970-01-01T00:00:00Z"))

    def upsert_entity(self, entity: dict[str, Any], at: str, conn: sqlite3.Connection | None = None) -> bool:
        if entity["entity_type"] not in ENTITY_TYPES:
            raise ValueError(f"Invalid entity type: {entity['entity_type']}")
        owns = conn is None
        context = self.transaction() if owns else _borrow(conn)
        with context as active:
            existing = active.execute("SELECT name, status, metadata_json FROM registry_entities WHERE id = ?", (entity["id"],)).fetchone()
            metadata = canonical_json(entity.get("metadata", {}))
            changed = not existing or tuple(existing) != (entity["name"], entity["status"], metadata)
            active.execute("INSERT INTO registry_entities VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name, status=excluded.status, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                           (entity["id"], entity["entity_type"], entity["name"], entity["status"], entity.get("origin", "NATIVE"), entity.get("source_record_id"), metadata, at, at))
            if changed:
                self._event(active, "registry_entity", entity["id"], "ENTITY_IMPORTED" if entity.get("origin") == "IMPORTED" else "ENTITY_RECORDED", {"status": entity["status"]}, at)
            return changed

    def entity(self, entity_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM registry_entities WHERE id = ?", (entity_id,)).fetchone()
            if not row:
                return None
            result = dict(row); result["metadata"] = json.loads(result.pop("metadata_json")); return result

    def entities(self, entity_type: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM registry_entities" + (" WHERE entity_type = ?" if entity_type else "") + " ORDER BY entity_type, name, id", (entity_type,) if entity_type else ()).fetchall()
            result = [dict(row) for row in rows]
            for item in result:
                item["metadata"] = json.loads(item.pop("metadata_json"))
            return result

    def add_hierarchy(self, parent_id: str, child_id: str, provenance: dict[str, Any], at: str) -> None:
        with self.transaction() as conn:
            self._require_entities(conn, parent_id, child_id)
            existing = conn.execute("SELECT parent_id FROM hierarchy_links WHERE child_id = ?", (child_id,)).fetchone()
            if existing and existing["parent_id"] == parent_id:
                return
            if existing:
                raise ValueError(f"Hierarchy child already has a parent: {child_id}")
            graph = self._adjacency(conn, "SELECT parent_id, child_id FROM hierarchy_links")
            if self._reachable(graph, child_id, parent_id):
                raise ValueError("Hierarchy cycle rejected")
            conn.execute("INSERT INTO hierarchy_links VALUES (?, ?, ?, ?)", (parent_id, child_id, canonical_json(provenance), at))
            self._event(conn, "relationship", stable_id("hierarchy", {"parent": parent_id, "child": child_id}), "HIERARCHY_LINKED", {"parent": parent_id, "child": child_id, "provenance": provenance}, at)

    def hierarchy(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM hierarchy_links ORDER BY parent_id, child_id")]

    def add_relationship(self, source_id: str, target_id: str, relationship_type: str, provenance: dict[str, Any], certainty: str, at: str, inference_rule: str | None = None) -> str:
        if relationship_type not in RELATIONSHIP_TYPES:
            raise ValueError(f"Invalid relationship type: {relationship_type}")
        if certainty not in CERTAINTY_CLASSES:
            raise ValueError(f"Invalid certainty: {certainty}")
        if inference_rule and not provenance.get("evidence"):
            raise ValueError("Inferred relationships require recorded evidence")
        edge_id = stable_id("edge", {"source": source_id, "target": target_id, "type": relationship_type})
        with self.transaction() as conn:
            self._require_entities(conn, source_id, target_id)
            if relationship_type in ACYCLIC_RELATIONSHIPS:
                rows = conn.execute("SELECT source_id, target_id FROM relationships WHERE relationship_type = ?", (relationship_type,)).fetchall()
                graph: dict[str, set[str]] = {}
                for row in rows:
                    graph.setdefault(row["source_id"], set()).add(row["target_id"])
                if self._reachable(graph, target_id, source_id):
                    raise ValueError(f"{relationship_type} cycle rejected")
            existing = conn.execute("SELECT id FROM relationships WHERE source_id=? AND target_id=? AND relationship_type=?", (source_id, target_id, relationship_type)).fetchone()
            if existing:
                return str(existing["id"])
            conn.execute("INSERT INTO relationships VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (edge_id, source_id, target_id, relationship_type, certainty, inference_rule, canonical_json(provenance), at))
            self._event(conn, "relationship", edge_id, "RELATIONSHIP_CREATED", {"source": source_id, "target": target_id, "type": relationship_type, "certainty": certainty, "provenance": provenance}, at)
        return edge_id

    def relationships(self, entity_id: str | None = None, relationship_type: str | None = None) -> list[dict[str, Any]]:
        clauses, values = [], []
        if entity_id:
            clauses.append("(source_id = ? OR target_id = ?)"); values.extend([entity_id, entity_id])
        if relationship_type:
            clauses.append("relationship_type = ?"); values.append(relationship_type)
        sql = "SELECT * FROM relationships" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY relationship_type, source_id, target_id"
        with self._connection() as conn:
            result = [dict(row) for row in conn.execute(sql, values)]
            for item in result:
                item["provenance"] = json.loads(item.pop("provenance_json"))
            return result

    def traverse(self, entity_id: str, direction: str = "both", relationship_type: str | None = None) -> list[dict[str, Any]]:
        seen, frontier = {entity_id}, [entity_id]
        while frontier:
            current = frontier.pop(0)
            for edge in self.relationships(current, relationship_type):
                candidates = []
                if direction in {"out", "both"} and edge["source_id"] == current: candidates.append(edge["target_id"])
                if direction in {"in", "both"} and edge["target_id"] == current: candidates.append(edge["source_id"])
                for candidate in sorted(candidates):
                    if candidate not in seen: seen.add(candidate); frontier.append(candidate)
        return [self.entity(item) for item in sorted(seen - {entity_id}) if self.entity(item)]

    def record_import_source(self, source: dict[str, Any], at: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO import_sources VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET fetched_at=excluded.fetched_at, stale_after=excluded.stale_after, metadata_json=excluded.metadata_json",
                         (source["id"], source["source_type"], source["source_uri"], source["authoritative_scope"], at, source.get("stale_after"), canonical_json(source.get("metadata", {}))))

    def record_snapshot(self, source_id: str, payload: Any, captured_at: str) -> str:
        serialized = canonical_json(payload)
        snapshot_id = stable_id("snapshot", {"source": source_id, "sha256": __import__("hashlib").sha256(serialized.encode()).hexdigest()})
        with self._connection() as conn:
            conn.execute("INSERT OR IGNORE INTO source_snapshots VALUES (?, ?, ?, ?, ?, ?)", (snapshot_id, source_id, __import__("hashlib").sha256(serialized.encode()).hexdigest(), len(payload) if isinstance(payload, list) else 1, captured_at, serialized))
        return snapshot_id

    def upsert_external_reference(self, reference: dict[str, Any], at: str, conn: sqlite3.Connection | None = None) -> bool:
        owns = conn is None
        context = self.transaction() if owns else _borrow(conn)
        with context as active:
            existing = active.execute("SELECT state, metadata_json, url FROM external_references WHERE id = ?", (reference["id"],)).fetchone()
            values = (reference["state"], canonical_json(reference.get("metadata", {})), reference.get("url"))
            changed = not existing or tuple(existing) != values
            active.execute("INSERT INTO external_references VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id, url=excluded.url, state=excluded.state, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at",
                           (reference["id"], reference["entity_id"], reference["source_id"], reference["external_type"], str(reference["external_id"]), reference.get("url"), reference["state"], values[1], at, at))
            if changed: self._event(active, "external_reference", reference["id"], "EXTERNAL_REFERENCE_IMPORTED", {"state": reference["state"]}, at)
            return changed

    def external_references(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            result = [dict(row) for row in conn.execute("SELECT * FROM external_references ORDER BY source_id, external_type, external_id")]
            for item in result: item["metadata"] = json.loads(item.pop("metadata_json"))
            return result

    def replace_reconciliation(self, records: list[dict[str, Any]], at: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM reconciliation_records")
            for item in records:
                conn.execute("INSERT INTO reconciliation_records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (item["id"], item["category"], item.get("entity_id"), item.get("related_entity_id"), item.get("status", "PENDING_APPROVAL"), item["explanation"], canonical_json(item.get("evidence", {})), item["recommended_action"], at))

    def reconciliation(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            result = [dict(row) for row in conn.execute("SELECT * FROM reconciliation_records ORDER BY category, id")]
            for item in result: item["evidence"] = json.loads(item.pop("evidence_json"))
            return result

    def save_priority(self, score: dict[str, Any], at: str) -> None:
        with self._connection() as conn:
            conn.execute("INSERT INTO priority_scores VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mission_id) DO UPDATE SET urgency=excluded.urgency, importance=excluded.importance, risk=excluded.risk, readiness=excluded.readiness, total=excluded.total, explanation_json=excluded.explanation_json, calculated_at=excluded.calculated_at", (score["mission_id"], score["urgency"], score["importance"], score["risk"], score["readiness"], score["total"], canonical_json(score["explanation"]), at))

    def priorities(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            result = [dict(row) for row in conn.execute("SELECT * FROM priority_scores ORDER BY total DESC, mission_id")]
            for item in result: item["explanation"] = json.loads(item.pop("explanation_json"))
            return result

    def save_override(self, mission_id: str, rank: int, rationale: str, owner: str, at: str) -> str:
        override_id = stable_id("override", {"mission": mission_id, "rank": rank, "rationale": rationale, "owner": owner})
        with self._connection() as conn:
            conn.execute("INSERT INTO executive_overrides VALUES (?, ?, ?, ?, ?, ?)", (override_id, mission_id, rank, rationale, owner, at))
        return override_id

    def overrides(self) -> list[dict[str, Any]]:
        with self._connection() as conn: return [dict(row) for row in conn.execute("SELECT * FROM executive_overrides ORDER BY created_at, id")]

    def queue_decision(self, item: dict[str, Any], at: str) -> None:
        if item["confidence"] not in CERTAINTY_CLASSES: raise ValueError("Invalid confidence classification")
        with self.transaction() as conn:
            conn.execute("INSERT INTO decision_queue VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET question=excluded.question, recommended_action=excluded.recommended_action, alternatives_json=excluded.alternatives_json, evidence_links_json=excluded.evidence_links_json, confidence=excluded.confidence, risk_if_delayed=excluded.risk_if_delayed, risk_if_approved=excluded.risk_if_approved, due_date=excluded.due_date, status=excluded.status, rationale=excluded.rationale, updated_at=excluded.updated_at",
                         (item["id"], item["mission_id"], item["decision_type"], item["question"], item["recommended_action"], canonical_json(item.get("alternatives", [])), canonical_json(item.get("evidence_links", [])), item["confidence"], item["risk_if_delayed"], item["risk_if_approved"], item["decision_owner"], item.get("due_date"), item.get("status", "OPEN"), item.get("rationale", ""), canonical_json(item["final_decision_event"]) if item.get("final_decision_event") else None, at, at))
            self._event(conn, "decision_queue", item["id"], "DECISION_QUEUED", {"mission_id": item["mission_id"], "type": item["decision_type"]}, at)

    def decisions_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("SELECT * FROM decision_queue" + (" WHERE status = ?" if status else "") + " ORDER BY due_date IS NULL, due_date, created_at, id", (status,) if status else ()).fetchall()
            result = [dict(row) for row in rows]
            for item in result:
                item["alternatives"] = json.loads(item.pop("alternatives_json")); item["evidence_links"] = json.loads(item.pop("evidence_links_json")); item["final_decision_event"] = json.loads(item.pop("final_event_json")) if item.get("final_event_json") else None
            return result

    def resolve_queue_decision(self, decision_id: str, status: str, rationale: str, owner: str, at: str) -> None:
        event = {"status": status, "rationale": rationale, "owner": owner, "decided_at": at}
        with self.transaction() as conn:
            if not conn.execute("SELECT 1 FROM decision_queue WHERE id = ?", (decision_id,)).fetchone(): raise KeyError(decision_id)
            conn.execute("UPDATE decision_queue SET status=?, rationale=?, final_event_json=?, updated_at=? WHERE id=?", (status, rationale, canonical_json(event), at, decision_id))
            self._event(conn, "decision_queue", decision_id, "DECISION_FINALIZED", event, at)

    def save_stale_status(self, source_id: str, status: str, reason: str, at: str) -> None:
        with self._connection() as conn: conn.execute("INSERT INTO stale_data_status VALUES (?, ?, ?, ?) ON CONFLICT(source_id) DO UPDATE SET status=excluded.status, reason=excluded.reason, evaluated_at=excluded.evaluated_at", (source_id, status, reason, at))

    def source_health(self) -> list[dict[str, Any]]:
        with self._connection() as conn: return [dict(row) for row in conn.execute("SELECT s.id, s.source_type, s.source_uri, s.fetched_at, s.stale_after, COALESCE(h.status, 'UNKNOWN') status, COALESCE(h.reason, 'not evaluated') reason FROM import_sources s LEFT JOIN stale_data_status h ON h.source_id=s.id ORDER BY s.id")]

    def save_brief(self, brief_id: str, as_of: str, payload_sha256: str, payload: dict[str, Any], at: str) -> None:
        with self._connection() as conn: conn.execute("INSERT OR IGNORE INTO brief_snapshots VALUES (?, ?, ?, ?, ?)", (brief_id, as_of, payload_sha256, canonical_json(payload), at))

    def briefs(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            result = [dict(row) for row in conn.execute("SELECT * FROM brief_snapshots ORDER BY as_of, id")]
            for item in result: item["payload"] = json.loads(item.pop("payload_json"))
            return result

    @staticmethod
    def _require_entities(conn: sqlite3.Connection, *ids: str) -> None:
        for entity_id in ids:
            if not conn.execute("SELECT 1 FROM registry_entities WHERE id = ?", (entity_id,)).fetchone(): raise KeyError(entity_id)

    @staticmethod
    def _adjacency(conn: sqlite3.Connection, sql: str) -> dict[str, set[str]]:
        graph: dict[str, set[str]] = {}
        for source, target in conn.execute(sql): graph.setdefault(source, set()).add(target)
        return graph

    @staticmethod
    def _reachable(graph: dict[str, set[str]], start: str, target: str) -> bool:
        frontier, seen = [start], set()
        while frontier:
            current = frontier.pop()
            if current == target: return True
            if current in seen: continue
            seen.add(current); frontier.extend(sorted(graph.get(current, set())))
        return False


@contextmanager
def _borrow(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    yield connection
