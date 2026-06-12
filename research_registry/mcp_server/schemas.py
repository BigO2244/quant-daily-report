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
        "name": "execution_target_attainment",
        "description": (
            "Read-only execution target-attainment diagnostic. Distinguishes "
            "operational/reconciliation success from economic target deployment "
            "by comparing target cash, achieved posttrade cash, post-sell "
            "rebudget expectations, submitted/filled side counts, and skipped "
            "or deferred buy notional. Returns OK_TARGET_ATTAINED, "
            "WARN_CASH_DRIFT, WARN_RECONCILED_BUT_UNDERDEPLOYED, "
            "WARN_POSTTRADE_SNAPSHOT_STALE_OR_PRE_BUY, FAIL_EXECUTION_INCOMPLETE, "
            "or UNKNOWN_INSUFFICIENT_ARTIFACTS."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "trade_date": {"type": "string"},
                "run_id": {"type": "string"},
                "cash_weight_drift_tolerance": {
                    "type": "number",
                    "default": 0.02,
                    "description": "Cash-weight tolerance in decimal weight units; default is 2 percentage points.",
                },
                "notional_drift_tolerance": {
                    "type": "number",
                    "description": "Optional absolute dollar tolerance; default is max($25, 0.25% of equity).",
                },
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
        "name": "fr069_sleeve_inventory",
        "description": (
            "Read-only FR-069 sleeve manifest inventory. Loads the research-only "
            "sleeve manifest, validates it, and returns compact sleeve metadata, "
            "counts by status/lifecycle, current sleeves, future placeholders, "
            "and validation warnings/errors. It does not mutate files, call "
            "brokers, generate artifacts, or change production strategy behavior."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "manifest_path": {
                    "type": "string",
                    "description": "Optional override path for fixture/testing manifests.",
                },
            },
        },
    },
    {
        "name": "promotion_readiness",
        "description": (
            "Per-strategy promotion-readiness assessment from shadow artifacts. "
            "Reads outputs/shadow_candidates/<DATE>/shadow_evaluation.json + the "
            "FR-028 Phase C sidecar (promotion_readiness.json) when present + "
            "the per-strategy stability_analysis.json. Returns per-strategy "
            "panels with recommendation tier (promote / hold / research_only / "
            "insufficient_evidence), confidence, blockers, reason codes, "
            "gating metrics, and an explanation grounded in artifacts. The "
            "generic top-level fields (current_leader, recommendation, "
            "confidence_level, evidence) are preserved for backward "
            "compatibility. Strategy names parsed from the question are "
            "restricted to polaris|orion|lyra|leda; unknown → NEEDS_DATA."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "lookback_days": {"type": "integer", "default": 5, "minimum": 1},
                "question": {"type": "string"},
                "strategies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit strategy names; supersedes question parsing.",
                },
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
        "name": "stable_window_evaluation",
        "description": (
            "Stable-window / random-window evaluation summariser. Reads "
            "outputs/research/random_windows_*.csv and "
            "outputs/research/stable_window_evaluation/*.json and returns "
            "per-policy dispersion (p10/median/p90 of CAGR, max drawdown, "
            "Sharpe, ulcer), consistency (% positive-return windows), "
            "best/worst windows, start-date sensitivity, and promotion-grade "
            "window-validity counts. Fails closed with NO_WINDOW_DATA when "
            "no artifacts are present; flags insufficient_sample per policy "
            "when n_windows < 30."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "research_root": {"type": "string", "default": "outputs/research"},
                "stable_window_root": {"type": "string", "default": "outputs/research/stable_window_evaluation"},
                "outputs_root": {"type": "string"},
                "question": {"type": "string"},
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
        "name": "strategy_behavior_differentiation",
        "description": (
            "Return-stream behavioral differentiation across challenger "
            "strategies. Reads the per-strategy daily NAV series at "
            "outputs/shadow_candidates/performance/shadow_nav_series.csv "
            "and returns per-pair daily-return correlation, rolling "
            "20D/60D correlation stability (mean, p10, p90, IQR), "
            "shared-negative-day counts, downside correlation, worst "
            "shared drawdown day, behavioral similarity tier "
            "(highly_similar_behavior / partially_similar_behavior / "
            "behaviorally_differentiated / insufficient_evidence), and "
            "an all-strategies rollup (most-similar pair, most-"
            "differentiated pair, average pairwise correlation, common "
            "negative days, diversification verdict). Strategy names "
            "restricted to polaris|orion|lyra|leda. Fails closed with "
            "NO_RETURN_STREAM (and a candidate_artifact_inventory + "
            "proposed_artifact_contract) when the NAV series is absent."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "nav_series_path": {
                    "type": "string",
                    "default": "outputs/shadow_candidates/performance/shadow_nav_series.csv",
                },
                "question": {"type": "string"},
                "strategies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit strategy names; supersedes question parsing.",
                },
            },
        },
    },
    {
        "name": "strategy_differentiation",
        "description": (
            "Read-only synthesis of shadow + attribution artifacts that "
            "answers whether the challenger strategies are genuinely "
            "different bets or mostly the same factor / sector / holding "
            "exposure. Returns per-pair (holdings overlap %, sector overlap, "
            "factor proximity, shared top contributor/detractor, shared "
            "drawdown contributors, similarity score, verdict) plus an all-"
            "strategies rollup (most-similar pair, most-differentiated pair, "
            "common factor flags, diversification verdict). Strategy names "
            "are restricted to polaris|orion|lyra|leda; unknown → NEEDS_DATA. "
            "Fails closed with NO_SHADOW_DATA when shadow_candidates is "
            "absent. Never invents metrics."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "shadow_root": {"type": "string", "default": "outputs/shadow_candidates"},
                "attribution_root": {"type": "string", "default": "outputs/attribution"},
                "question": {"type": "string"},
                "strategies": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional explicit strategy names; supersedes question parsing.",
                },
            },
        },
    },
    {
        "name": "operational_drag_analysis",
        "description": (
            "Read-only intended-vs-actual operational drag summary. Reads "
            "outputs/operational_drag/<DATE>/{operational_drag,"
            "stable_window_analysis,operational_drag_attribution}.json and "
            "returns latest intended vs actual vs SPY performance, stable-window "
            "table, main drag contributors, data coverage status, and missing "
            "artifact warnings. It does not generate artifacts or call the broker."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "outputs_root": {"type": "string", "default": "outputs"},
                "trade_date": {"type": "string"},
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
