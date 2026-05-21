"""Deterministic local research object registry backed by SQLite and networkx."""

from __future__ import annotations

from pathlib import Path

from research_registry.models.base import ResearchObjectEnvelope
from research_registry.models.enums import EdgeType, RAW_ROOT_OBJECTS
from research_registry.provenance.graph import ProvenanceGraph
from research_registry.storage.sqlite import SQLiteStore
from research_registry.validation.validators import validate_envelope


class SQLiteResearchRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self.store = SQLiteStore(db_path)
        self.graph = ProvenanceGraph()
        self._load_graph_from_store()

    def close(self) -> None:
        self.store.close()

    def ingest(self, envelope: ResearchObjectEnvelope) -> None:
        parent_envelopes = {
            parent_id: parent
            for parent_id in envelope.lineage.get("parent_refs", [])
            if (parent := self.store.get_object(parent_id)) is not None
        }
        validate_envelope(envelope, parent_envelopes=parent_envelopes)

        existing = self.store.get_object(envelope.object_id)
        if existing and existing.identity != envelope.identity:
            raise ValueError(f"IDENTITY_COLLISION: {envelope.object_id}")
        if existing and existing.to_dict() != envelope.to_dict():
            raise ValueError(f"IDENTITY_COLLISION: differing envelope for {envelope.object_id}")
        if existing:
            return

        self.store.insert_object(envelope)
        self.graph.add_object(envelope)
        for parent_id in envelope.lineage.get("parent_refs", []):
            parent = self.store.get_object(parent_id)
            if parent is None:
                raise ValueError(f"LINEAGE_DANGLING_PARENT: {parent_id}")
            self.graph.add_object(parent)
            self.graph.add_edge(
                parent.lineage["node_id"],
                envelope.lineage["node_id"],
                EdgeType.DERIVED_FROM.value,
            )
            self.store.insert_edge(
                parent.lineage["node_id"],
                envelope.lineage["node_id"],
                EdgeType.DERIVED_FROM.value,
            )

    def hydrate(self, object_id: str) -> ResearchObjectEnvelope:
        envelope = self.store.get_object(object_id)
        if envelope is None:
            raise KeyError(object_id)
        parent_envelopes = {
            parent_id: parent
            for parent_id in envelope.lineage.get("parent_refs", [])
            if (parent := self.store.get_object(parent_id)) is not None
        }
        validate_envelope(envelope, parent_envelopes=parent_envelopes)
        return envelope

    def upstream(self, object_id: str) -> list[str]:
        node_id = self.store.node_for_object(object_id)
        if node_id is None:
            raise KeyError(object_id)
        node_to_object = {
            envelope.lineage["node_id"]: envelope.object_id for envelope in self.store.all_objects()
        }
        return sorted(node_to_object[node] for node in self.graph.upstream(node_id))

    def downstream(self, object_id: str) -> list[str]:
        node_id = self.store.node_for_object(object_id)
        if node_id is None:
            raise KeyError(object_id)
        node_to_object = {
            envelope.lineage["node_id"]: envelope.object_id for envelope in self.store.all_objects()
        }
        return sorted(node_to_object[node] for node in self.graph.downstream(node_id))

    def orphan_findings(self) -> list[str]:
        raw_nodes = {
            envelope.lineage["node_id"]
            for envelope in self.store.all_objects()
            if envelope.object_type in RAW_ROOT_OBJECTS
        }
        return self.graph.orphans(raw_nodes)

    def registry_digest(self) -> str:
        return self.store.registry_digest()

    def _load_graph_from_store(self) -> None:
        for envelope in self.store.all_objects():
            self.graph.add_object(envelope)
        for edge in self.store.all_edges():
            self.graph.add_edge(edge["parent_node_id"], edge["child_node_id"], edge["edge_type"])
