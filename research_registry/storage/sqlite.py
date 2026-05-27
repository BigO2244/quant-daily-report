"""Inspectable local SQLite storage for the derived registry index."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from research_registry.models.base import ResearchObjectEnvelope, canonical_json, sha256_hex


class SQLiteStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.connection = sqlite3.connect(str(self.db_path))
        self.connection.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.connection.close()

    def _init_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS objects (
                object_id TEXT PRIMARY KEY,
                object_type TEXT NOT NULL,
                strategy_ref TEXT,
                trade_date TEXT,
                surface_ref TEXT,
                schema_id TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                ontology_version TEXT NOT NULL,
                nav_surface_type TEXT,
                confidence_level TEXT NOT NULL,
                governance_state TEXT NOT NULL,
                as_of TEXT NOT NULL,
                produced_at TEXT NOT NULL,
                node_id TEXT NOT NULL UNIQUE,
                transformation_chain_hash TEXT NOT NULL,
                source_state_hash TEXT,
                envelope_json TEXT NOT NULL,
                envelope_hash TEXT NOT NULL,
                is_superseded INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS lineage (
                parent_node_id TEXT NOT NULL,
                child_node_id TEXT NOT NULL,
                edge_type TEXT NOT NULL,
                PRIMARY KEY (parent_node_id, child_node_id, edge_type)
            );
            CREATE INDEX IF NOT EXISTS idx_objects_identity
                ON objects (object_type, strategy_ref, trade_date, surface_ref, schema_version);
            CREATE INDEX IF NOT EXISTS idx_objects_as_of ON objects (as_of);
            CREATE INDEX IF NOT EXISTS idx_objects_chain_hash ON objects (transformation_chain_hash);
            CREATE INDEX IF NOT EXISTS idx_lineage_child ON lineage (child_node_id);
            CREATE INDEX IF NOT EXISTS idx_lineage_parent ON lineage (parent_node_id);
            """
        )
        self.connection.commit()

    def insert_object(self, envelope: ResearchObjectEnvelope) -> None:
        payload = envelope.to_dict()
        envelope_json = canonical_json(payload)
        self.connection.execute(
            """
            INSERT INTO objects (
                object_id, object_type, strategy_ref, trade_date, surface_ref,
                schema_id, schema_version, ontology_version, nav_surface_type,
                confidence_level, governance_state, as_of, produced_at, node_id,
                transformation_chain_hash, source_state_hash, envelope_json, envelope_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                envelope.object_id,
                envelope.object_type,
                envelope.identity.get("strategy_ref"),
                envelope.identity.get("trade_date"),
                envelope.identity.get("surface_ref"),
                envelope.schema["schema_id"],
                envelope.schema["schema_version"],
                envelope.schema["ontology_version"],
                envelope.surface.get("nav_surface_type"),
                envelope.confidence["level"],
                envelope.governance["state"],
                envelope.temporal["as_of"],
                envelope.provenance["produced_at"],
                envelope.lineage["node_id"],
                envelope.lineage["transformation_chain_hash"],
                envelope.provenance.get("source_state_hash"),
                envelope_json,
                sha256_hex(payload),
            ),
        )
        self.connection.commit()

    def insert_edge(self, parent_node_id: str, child_node_id: str, edge_type: str) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO lineage (parent_node_id, child_node_id, edge_type) VALUES (?, ?, ?)",
            (parent_node_id, child_node_id, edge_type),
        )
        self.connection.commit()

    def get_object(self, object_id: str) -> ResearchObjectEnvelope | None:
        row = self.connection.execute(
            "SELECT envelope_json FROM objects WHERE object_id = ?", (object_id,)
        ).fetchone()
        if row is None:
            return None
        return ResearchObjectEnvelope.from_dict(json.loads(row["envelope_json"]))

    def all_objects(self) -> list[ResearchObjectEnvelope]:
        rows = self.connection.execute(
            "SELECT envelope_json FROM objects ORDER BY object_id"
        ).fetchall()
        return [ResearchObjectEnvelope.from_dict(json.loads(row["envelope_json"])) for row in rows]

    def all_edges(self) -> list[dict[str, str]]:
        rows = self.connection.execute(
            "SELECT parent_node_id, child_node_id, edge_type FROM lineage ORDER BY parent_node_id, child_node_id, edge_type"
        ).fetchall()
        return [dict(row) for row in rows]

    def node_for_object(self, object_id: str) -> str | None:
        row = self.connection.execute(
            "SELECT node_id FROM objects WHERE object_id = ?", (object_id,)
        ).fetchone()
        return None if row is None else row["node_id"]

    def object_ids(self) -> list[str]:
        rows = self.connection.execute("SELECT object_id FROM objects ORDER BY object_id").fetchall()
        return [row["object_id"] for row in rows]

    def registry_digest(self) -> str:
        objects = [
            dict(row)
            for row in self.connection.execute(
                "SELECT object_id, envelope_hash FROM objects ORDER BY object_id"
            ).fetchall()
        ]
        edges = self.all_edges()
        return sha256_hex({"objects": objects, "edges": edges})

    def bulk_insert(self, envelopes: Iterable[ResearchObjectEnvelope]) -> None:
        for envelope in envelopes:
            self.insert_object(envelope)
