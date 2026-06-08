# FR-065 Dashboard Decision-Grade Consolidation

Status: ACTIVE_RESEARCH
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

FR-065 adds a compact model-quality evidence section to the existing terminal
dashboard. The dashboard remains broker-authoritative for NAV, positions, fills,
and execution-state panels.

## Data Contract

The dashboard data model should include:

- `decision_grade.status`: `READY`, `PARTIAL`, or `BLOCKED`
- `latest_model_quality_date`
- `promotion_ready_count`
- `decision_grade_strategy_change`
- `top_blockers`
- `confidence_summary`
- `source_paths`
- `reason_codes`

The section must render visibly as `PARTIAL` when model-quality artifacts are
missing. It must not hide warnings or replace broker state with planned trades.

## Sources

Preferred sources are dated artifacts under `outputs/model_quality/<date>/`,
including model-quality packet, model tournament, Argo validation, strategy
differentiation, Phoenix Phase B, and multi-asset framework artifacts when
present.

## Non-Goals

- No dashboard redesign.
- No substitution of planned trades for broker state.
- No hiding broker, freshness, validation, or execution warnings.
- No broker submission change.
- No cron timing change.
- No production order generation or strategy promotion.
