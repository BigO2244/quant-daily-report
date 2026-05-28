"""Tool schema declarations for the local Caerus MCP-compatible server."""

from __future__ import annotations

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "build_caerus_registry",
        "description": "Build a disposable read-only registry index from Caerus artifact roots.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "db_path": {"type": "string"},
                "runs_root": {"type": "string", "default": "outputs/runs"},
                "packets_root": {"type": "string", "default": "outputs/research_packets"},
                "docs_root": {"type": "string", "default": "docs/governance"},
                "limit": {"type": "integer", "default": 10, "minimum": 0},
            },
        },
    },
    {
        "name": "latest_runs",
        "description": "Show latest registered execution runs.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}, "limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "run_health",
        "description": "Summarize one registered execution run and its integrity artifact.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}, "run_id": {"type": "string"}}, "required": ["run_id"]},
    },
    {
        "name": "integrity_findings",
        "description": "List WARN/FAIL execution integrity records.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}, "limit": {"type": "integer"}}},
    },
    {
        "name": "governance_open",
        "description": "List deduplicated current-state governance items.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "db_path": {"type": "string"},
                "show_duplicates": {"type": "boolean", "default": False},
                "include_deferred": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "research_packet_status",
        "description": "Show latest registered research packet readiness.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}, "limit": {"type": "integer", "default": 10}}},
    },
    {
        "name": "registry_summary",
        "description": "Return registry summary and statistics.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}}},
    },
    {
        "name": "query_registry",
        "description": "Query registered objects by type, artifact family, surface, confidence, or governance state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "db_path": {"type": "string"},
                "limit": {"type": "integer"},
                "artifact_type": {"type": "string"},
                "data_artifact_type": {"type": "string"},
                "surface": {"type": "string"},
                "confidence": {"type": "string"},
                "governance": {"type": "string"},
            },
        },
    },
    {
        "name": "lineage",
        "description": "Return lineage for one registered object.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}, "object_id": {"type": "string"}}, "required": ["object_id"]},
    },
    {
        "name": "daily_operator_brief",
        "description": "Return a compact read-only morning/evening operator brief.",
        "inputSchema": {"type": "object", "properties": {"db_path": {"type": "string"}}},
    },
    {
        "name": "artifact_status",
        "description": "Inspect latest Caerus artifact families directly from the read-only outputs tree.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "limit": {"type": "integer", "default": 10, "minimum": 0},
            },
        },
    },
    {
        "name": "operator_daily_summary",
        "description": "Summarize today's read-only operator state from latest Caerus artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "trade_date": {"type": "string"},
            },
        },
    },
    {
        "name": "artifact_drilldown",
        "description": "Return compact latest artifact paths and required-file probes without raw payload dumps.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "family": {"type": "string", "default": "all"},
            },
        },
    },
]
