# FR-054 Dynamic Strategy Registry Audit And Design

Status: Implemented Foundation
Owner: Caerus Research Program
Last Updated: 2026-06-03
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Executive Summary

FR-054 introduces a configuration-backed strategy registry for Caerus research
and shadow infrastructure. The registry distinguishes security-selection
strategies, overlays, benchmarks, and reference portfolios, and provides
helpers for active shadow security-selection strategies, promotion candidates,
baseline strategy selection, labels, and short names.

The safe implementation scope migrates core shadow generation, shadow
evaluation, promotion windows, feedback-loop artifacts, attribution/risk helper
allowlists, research packet ordering, governance calibration, strategy
differentiation, scorecard reporting, scorecard health checks, and backfill
helpers to registry-backed strategy discovery.

Execution, broker, cron, paper trading, production strategy selection,
portfolio construction, promotion thresholds, and capital allocation are not
changed.

## Repository Audit

Search terms:

- `Polaris`
- `Orion`
- `Lyra`
- `caerus_polaris`
- `caerus_orion`
- `caerus_lyra`

Primary executable hard-coded assumptions found:

| Area | Files | Assumption | Risk | Migration Status |
|---|---|---|---|---|
| Shadow generation | `research/shadow_tracking/strategies.py`, `research/shadow_tracking/run.py` | Fixed Polaris/Orion/Lyra definitions, model loops, labels, baseline comparison | High | Migrated to registry-backed active security-selection strategies |
| Shadow performance/evaluation | `research/shadow_tracking/run.py` | Fixed model slugs and fixed strategy labels | High | Migrated |
| Promotion readiness windows | `research/promotion_readiness_windows.py` | Fixed strategy tuple and fixed Polaris control | High | Migrated |
| Phase C readiness | `research/shadow_tracking/run.py` | Fixed active baseline and challenger role inference | High | Migrated |
| Feedback loop artifacts | `core/feedback_loop_artifacts.py` | Fixed strategy tuple and compact names | High | Migrated |
| Position attribution | `research/attribution/position_pnl.py` | Fixed strategy JSON file allowlist | Medium | Migrated |
| Risk summaries | `research/risk_summary.py`, `research/risk_coverage.py` | Fixed strategy file/name allowlists | Medium | Migrated |
| Regime attribution | `research/regime_attribution.py` | Fixed strategy tuple | Medium | Migrated |
| Strategy differentiation | `research/strategy_differentiation.py`, `research/differentiation_diagnostic.py` | Fixed pair list or strategy set | Medium | Migrated |
| Promotion governance | `research/promotion_governance.py`, `research/governance_calibration.py` | Fixed strategy set and control strategy | Medium | Migrated |
| Research packet builders | `scripts/research/build_research_clarity_wave.py`, `scripts/research/build_daily_research_packet.py` | Fixed strategy order and promotion-candidate set | Medium | Partially migrated; narrative text remains legacy |
| Scorecard reporting | `core/shadow_scoreboard.py`, `core/portfolio_learning_report.py`, `scripts/send_shadow_cio_report.py`, `scripts/check_shadow_scorecard_health.py` | Fixed model list, labels, baseline | Medium | Migrated |
| Shadow refresh/backfill | `scripts/refresh_shadow_scorecard_artifacts.py`, `scripts/backfill_shadow_artifacts.py` | Fixed NAV columns, names, expected files | Medium | Migrated |
| Daily health | `scripts/caerus_daily_health_check.py` | Fixed shadow strategy tuple | Medium | Migrated |
| Research registry query parser | `research_registry/research/shadow_comparison.py` | Fixed known strategy short-name tuple | Medium | Migrated to registry short names with legacy `leda` preserved |
| Dashboard frontend | `web/dashboard/quant_daily_executive.js` | Fixed shadow chart series for Polaris/Orion/Lyra | Medium | Deferred; requires dashboard data/schema and UI validation |
| Dashboard builder | `scripts/research/build_dashboard_v1.py` | Fixed strategy metadata and control strategy | Medium | Deferred |
| Research review packet renderer | `research/review_packet.py` | Fixed Polaris/Orion/Lyra decision fields and headings | Medium | Deferred; large narrative/reporting surface |
| Dynamic allocation research | `research/dynamic_strategy_allocation.py` | Fixed three-strategy allocation policies | Medium | Deferred; this module intentionally models legacy three-way research policies |
| Execution identity | `core/strategy_identity.py`, `scripts/format_precompute_email.py`, `scripts/live_vs_shadow_reconciliation.py` | Fixed Polaris paper/control identity | High if changed | Deferred by design; execution identity must remain fixed |
| Operational recovery scripts | `scripts/send_post_close_research_digest.py` and selected older analysis modules | Fixed existing shadow artifacts | Low/Medium | Deferred; not required for safe registry foundation |

Documentation and tests contain many expected references to the current
strategy names. These are not all defects. Tests should continue to encode
current behavior where they assert backward compatibility.

## Artifact And Schema Assumptions

Existing artifacts assume:

- security-selection strategies produce holdings and target weights;
- `shadow_performance.json` contains NAV-like rows for model strategies and SPY;
- `comparison.json` contains pairwise overlap for holdings-producing models;
- `promotion_readiness.json` only evaluates challenger security-selection
  strategies;
- `feedback_loop_summary.json` uses compact keys: `polaris`, `orion`, `lyra`;
- legacy fields such as `differences_vs_polaris` and
  `excess_return_vs_polaris` remain part of the schema.

FR-054 preserves those artifact fields. The registry changes discovery and
iteration, not artifact shape.

Overlay strategies such as Argo are explicitly not expected to produce:

- holdings;
- executable target weights;
- NAV chains;
- position attribution;
- promotion-readiness metrics.

## Registry Architecture

Registry file:

- `config/research/strategy_registry.json`

Loader:

- `core/strategy_registry.py`

Schema version:

- `caerus_strategy_registry_v1`

Required fields:

```json
{
  "strategy_id": "caerus_phoenix",
  "display_name": "Caerus Phoenix",
  "strategy_type": "security_selection",
  "family": "crisis_reversal",
  "status": "research",
  "eligible_for_shadow": true,
  "eligible_for_promotion": false,
  "benchmark": "SPY",
  "execution_impact": "NON_EXECUTIONAL"
}
```

Supported statuses:

- `paper`
- `shadow`
- `research`
- `retired`

Supported strategy types:

- `security_selection`
- `overlay`
- `benchmark`
- `reference_portfolio`

Supported families:

- `core_momentum`
- `crisis_reversal`
- `earnings_drift`
- `event_driven`
- `regime_overlay`
- `benchmark`
- `reference`

Current active entries:

- `caerus_polaris`: paper baseline, security-selection, active in shadow
  tracking.
- `caerus_orion`: shadow challenger, security-selection, promotion candidate.
- `caerus_lyra`: shadow challenger, security-selection, promotion candidate.
- `spy_benchmark`: benchmark/reference row.

Future inactive entries:

- `caerus_phoenix`: research security-selection, not active in current shadow
  tracking.
- `caerus_cygnus`: research security-selection, not active in current shadow
  tracking.
- `caerus_cassiopeia`: research security-selection, not active in current shadow
  tracking.
- `caerus_argo`: research overlay, not a holdings-producing strategy.

Activation rule for current shadow security-selection workflows:

```text
strategy_type == security_selection
and status in {paper, shadow}
and eligible_for_shadow == true
and shadow_tracking.enabled == true
```

This rule keeps research-only future entries inert.

## Overlay Handling

Overlay entries are validated separately. An overlay may declare regime or
recommendation capabilities, but it may not declare holdings or NAV capability.

Argo registry posture:

```json
{
  "strategy_id": "caerus_argo",
  "display_name": "Caerus Argo",
  "strategy_type": "overlay",
  "family": "regime_overlay",
  "status": "research",
  "eligible_for_shadow": false,
  "eligible_for_promotion": false,
  "benchmark": null,
  "execution_impact": "NON_EXECUTIONAL"
}
```

Argo remains reporting/research only and is excluded from holdings, NAV,
attribution, shadow comparison, and promotion-readiness loops.

## Migration Plan

Stage 1, implemented:

- Add registry config and loader.
- Validate registry entries and duplicate IDs.
- Migrate core active strategy discovery in shadow tracking and related
  research/reporting helpers.
- Preserve all current artifact schemas and rendered current-strategy behavior.
- Add tests for registry behavior and backward compatibility.

Stage 2, recommended:

- Refactor dashboard data builder and frontend shadow series to consume
  registry-provided display metadata.
- Refactor `research/review_packet.py` narrative fields to use registry roles
  while preserving current report text.
- Refactor older operational scripts and one-off diagnostics that still encode
  the three-strategy set.

Stage 3, future onboarding:

- Add adapter-level support for non-Alpha-Lab strategy implementations in
  shadow tracking.
- Promote Phoenix/Cygnus/Cassiopeia only after their implementation artifacts
  satisfy the active shadow adapter contract.
- Add Argo only to overlay-aware review/reporting surfaces, not holdings loops.

## Backward Compatibility

Unchanged:

- order of active shadow strategies: Polaris, Orion, Lyra;
- Polaris remains baseline/control;
- Orion and Lyra remain promotion candidates;
- SPY remains benchmark;
- output field names such as `differences_vs_polaris` and
  `excess_return_vs_polaris`;
- shadow portfolio construction and Alpha Lab variant specs;
- execution, broker, cron, and paper trading behavior.

Rollback:

1. Revert the registry commit.
2. Restore fixed tuples in touched research/reporting modules.
3. Re-run targeted shadow, promotion, feedback, reporting, and py_compile
   validation.
4. No runtime artifact cleanup is required because schemas are unchanged.

## Future Strategy Onboarding Walkthrough: Phoenix

After FR-054, onboarding `caerus_phoenix` to active shadow should require:

Files created:

- `research/phoenix/` implementation modules.
- `Tests/test_phoenix_*.py` strategy tests.

Files modified:

- `config/research/strategy_registry.json`
  - set `status` from `research` to `shadow` when approved;
  - keep `strategy_type: security_selection`;
  - set `eligible_for_shadow: true`;
  - set `shadow_tracking.enabled: true`;
  - provide a supported strategy implementation adapter/source variant.

Tests required:

- deterministic selection;
- no look-ahead behavior;
- empty-but-valid artifacts;
- active registry inclusion only after status/adapter activation;
- shadow comparison/evaluation/promotion-readiness compatibility;
- artifact shape and attribution compatibility.

Expected artifacts:

- `outputs/shadow_candidates/<date>/caerus_phoenix.json`
- Phoenix row in `comparison.json`
- Phoenix row in `shadow_performance.json`
- Phoenix row in `shadow_evaluation.json`
- Phoenix panel in `promotion_readiness.json`, only after it is promotion
  eligible.

Repository-wide edits not expected:

- no edits to shadow comparison loops;
- no edits to promotion-readiness strategy tuples;
- no edits to feedback-loop strategy tuples;
- no edits to attribution allowlists;
- no edits to scorecard model lists.

## Risks And Assumptions

Risks:

- Some dashboard and review-packet surfaces still have fixed display
  assumptions.
- The current `research.shadow_tracking` implementation still maps active
  Alpha Lab variants through source variants. Future non-Alpha-Lab strategies
  need an adapter contract before activation.
- Registry metadata can incorrectly activate a research strategy if status and
  `shadow_tracking.enabled` are changed prematurely.
- Legacy field names containing `polaris` remain for backward compatibility.

Assumptions:

- Polaris remains the only paper/control strategy.
- Orion and Lyra remain the only active challenger strategies today.
- Phoenix, Cygnus, Cassiopeia, and Argo are not active participants yet.
- A future promotion task will explicitly validate any new strategy adapter
  before setting `status: shadow`.

## Recommendation

IMPLEMENTED SAFELY for the registry foundation and core research/shadow
enumeration migration.

Do not activate Phoenix, Cygnus, Cassiopeia, or Argo until Stage 2 reporting
cleanup and Stage 3 adapter tests are complete.
