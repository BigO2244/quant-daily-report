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
    {
        "name": "morning_cio_brief",
        "description": "Return a compact artifact-backed operator intelligence brief.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "trade_date": {"type": "string"},
            },
        },
    },
    {
        "name": "promotion_readiness",
        "description": "Assess challenger readiness from shadow artifacts only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "lookback_days": {"type": "integer", "default": 5, "minimum": 1},
            },
        },
    },
    {
        "name": "anomaly_report",
        "description": "Report operational and research anomalies from persisted artifacts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "trade_date": {"type": "string"},
                "lookback_days": {"type": "integer", "default": 5, "minimum": 1},
            },
        },
    },
    {
        "name": "execution_timing_by_vix_regime",
        "description": (
            "Stratify the latest execution-timing replay opportunities by VIX regime. "
            "Joins outputs/research/execution_timing/<RUN_DATE>/per_trade_timing.json "
            "to outputs/vix_regime/regime_history.csv on execution_date and returns "
            "per-regime, per-offset mean/median opportunity in USD and bps. Fails "
            "closed (NO_TIMING_DATA / NO_REGIME_DATA) when artifacts are missing; "
            "tags regimes with fewer than `insufficient_sample_threshold` days."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timing_root": {"type": "string", "default": "outputs/research/execution_timing"},
                "regime_history": {"type": "string", "default": "outputs/vix_regime/regime_history.csv"},
                "insufficient_sample_threshold": {"type": "integer", "default": 5, "minimum": 1},
            },
        },
    },
    {
        "name": "execution_timing_summary",
        "description": (
            "Aggregate (non-regime-stratified) summary of the latest execution-"
            "timing replay run. Returns per-offset mean/median opportunity in "
            "USD and bps vs the 9:35 baseline, the best non-baseline offset, "
            "the operator-facing offsets highlighted in the question, and a "
            "conservative recommendation: retain_9_35_baseline, "
            "earlier_timing_appears_better, or insufficient_evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timing_root": {"type": "string", "default": "outputs/research/execution_timing"},
                "question": {"type": "string"},
                "highlighted_offsets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit offset labels (e.g. ['T+0m','T+5m']); supersedes question parsing.",
                },
            },
        },
    },
    {
        "name": "shadow_comparison",
        "description": (
            "Side-by-side comparison of shadow-portfolio strategies from "
            "outputs/shadow_candidates/<DATE>/shadow_evaluation.json + "
            "comparison.json. Strategy names are restricted to the closed "
            "list polaris|orion|lyra|leda. Returns per-strategy NAV / "
            "cumulative return / excess vs SPY / turnover / drawdown panel, "
            "pairwise overlap (when two strategies are named), and a leader "
            "summary. Unknown strategy names → NEEDS_DATA."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "shadow_root": {"type": "string", "default": "outputs/shadow_candidates"},
                "outputs_root": {"type": "string"},
                "question": {"type": "string"},
                "strategies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit strategy names (e.g. ['polaris','orion']); supersedes question parsing.",
                },
            },
        },
    },
    {
        "name": "attribution_analysis",
        "description": (
            "Read-only performance attribution. Reads the latest "
            "outputs/attribution/<DATE>/{attribution_summary,contribution_report,"
            "factor_exposure,regime_performance_breakdown}.json and returns per-"
            "strategy headline 21d return, top contributors / detractors, top "
            "drawdown contributors, factor exposures (market beta, momentum, "
            "volatility, sector concentration), regime-stratified performance, "
            "and a deterministic narrative. When two strategies are named, a "
            "comparison block names the outperformer and the headline delta. "
            "Strategy names restricted to polaris|orion|lyra|leda; unknown → "
            "NEEDS_DATA. Never invents missing metrics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "attribution_root": {"type": "string", "default": "outputs/attribution"},
                "outputs_root": {"type": "string"},
                "question": {"type": "string"},
                "strategies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit strategy names (e.g. ['polaris','orion']); supersedes question parsing.",
                },
            },
        },
    },
    {
        "name": "answer_research_question",
        "description": (
            "Deterministic NL wrapper. Matches the question against a regex whitelist "
            "(no LLM call, no external service) and routes to the appropriate "
            "structured tool. Currently supports the timing + VIX regime intent only; "
            "returns UNSUPPORTED_INTENT with the available phrases for any other input."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "timing_root": {"type": "string"},
                "regime_history": {"type": "string"},
                "insufficient_sample_threshold": {"type": "integer", "minimum": 1},
            },
            "required": ["question"],
        },
    },
]
