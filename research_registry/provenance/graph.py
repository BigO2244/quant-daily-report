"""networkx-backed SEM-003 provenance DAG."""

from __future__ import annotations

import networkx as nx

from research_registry.models.base import ResearchObjectEnvelope
from research_registry.models.enums import EdgeType


class ProvenanceGraph:
    def __init__(self) -> None:
        self.graph = nx.DiGraph()

    def add_object(self, envelope: ResearchObjectEnvelope) -> None:
        node_id = envelope.lineage["node_id"]
        if node_id in self.graph and self.graph.nodes[node_id].get("object_id") != envelope.object_id:
            raise ValueError(f"lineage node collision: {node_id}")
        self.graph.add_node(
            node_id,
            object_id=envelope.object_id,
            object_type=envelope.object_type,
            chain_hash=envelope.lineage["transformation_chain_hash"],
        )

    def add_edge(self, parent_node_id: str, child_node_id: str, edge_type: str) -> None:
        if edge_type not in {edge.value for edge in EdgeType}:
            raise ValueError(f"unknown edge type: {edge_type}")
        self.graph.add_edge(parent_node_id, child_node_id, edge_type=edge_type)
        if not nx.is_directed_acyclic_graph(self.graph):
            self.graph.remove_edge(parent_node_id, child_node_id)
            raise ValueError("provenance edge would introduce a cycle")

    def upstream(self, node_id: str) -> list[str]:
        return list(nx.ancestors(self.graph, node_id))

    def downstream(self, node_id: str) -> list[str]:
        return list(nx.descendants(self.graph, node_id))

    def orphans(self, raw_node_ids: set[str]) -> list[str]:
        return [
            node
            for node in self.graph.nodes
            if self.graph.in_degree(node) == 0 and node not in raw_node_ids
        ]

    def assert_dag(self) -> None:
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("provenance graph is cyclic")
