from __future__ import annotations

from typing import Any


def build_recovery_lineage(
    *,
    source_failed_run_id: str,
    trade_date: str,
    lifecycle_state: str,
    timeline: dict[str, Any],
    simulation_artifact: dict[str, Any],
    governance_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    nodes = [
        {
            "id": source_failed_run_id,
            "type": "ORIGINAL_EXECUTION",
            "label": "Original interrupted execution",
            "immutable": True,
        },
        {
            "id": f"{source_failed_run_id}:interruption",
            "type": "INTERRUPTION",
            "label": lifecycle_state,
            "immutable": True,
        },
        {
            "id": f"{source_failed_run_id}:settlement",
            "type": "SETTLEMENT_OBSERVATION",
            "label": "Eventual broker settlement observation",
            "immutable": True,
        },
        {
            "id": f"{source_failed_run_id}:simulation",
            "type": "RECOVERY_SIMULATION",
            "label": simulation_artifact.get("verdict"),
            "immutable": True,
        },
    ]
    if governance_report:
        nodes.append(
            {
                "id": f"{source_failed_run_id}:governance",
                "type": "GOVERNANCE_DECISION",
                "label": governance_report.get("classification"),
                "immutable": True,
            }
        )

    edges = [
        {
            "from": source_failed_run_id,
            "to": f"{source_failed_run_id}:interruption",
            "relationship": "entered_failure_state",
        },
        {
            "from": f"{source_failed_run_id}:interruption",
            "to": f"{source_failed_run_id}:settlement",
            "relationship": "eventually_settled",
        },
        {
            "from": f"{source_failed_run_id}:settlement",
            "to": f"{source_failed_run_id}:simulation",
            "relationship": "enabled_dry_run_recovery_analysis",
        },
    ]
    if governance_report:
        edges.append(
            {
                "from": f"{source_failed_run_id}:simulation",
                "to": f"{source_failed_run_id}:governance",
                "relationship": "produced_governance_decision",
            }
        )

    return {
        "source_failed_run_id": source_failed_run_id,
        "trade_date": trade_date,
        "lineage_version": 1,
        "event_count": timeline.get("event_count", 0),
        "nodes": nodes,
        "edges": edges,
        "duplicate_node_ids": _duplicate_ids([node["id"] for node in nodes]),
        "immutable_historical_artifacts": True,
    }


def build_lifecycle_graph(lineage: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_type": "RECOVERY_LIFECYCLE",
        "source_failed_run_id": lineage.get("source_failed_run_id"),
        "nodes": lineage.get("nodes", []),
        "edges": lineage.get("edges", []),
        "valid": not lineage.get("duplicate_node_ids"),
    }


def validate_lineage(lineage: dict[str, Any]) -> dict[str, Any]:
    duplicate_node_ids = list(lineage.get("duplicate_node_ids") or [])
    return {
        "ok": not duplicate_node_ids,
        "failures": [f"duplicate_lineage_node:{node_id}" for node_id in duplicate_node_ids],
        "node_count": len(lineage.get("nodes") or []),
        "edge_count": len(lineage.get("edges") or []),
    }


def _duplicate_ids(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in ids:
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
    return duplicates

