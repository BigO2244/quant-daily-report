# FR-055 Registry Surface Cleanup Audit

Status: Implemented Stage 2 Cleanup
Owner: Caerus Research Program
Last Updated: 2026-06-03
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Executive Summary

FR-055 completes the safe portion of the remaining strategy-registry surface
cleanup after FR-054. The implementation keeps Polaris as the paper/control
identity, keeps Orion and Lyra as the only currently active challengers, and
keeps Phoenix, Cygnus, Cassiopeia, and Argo inactive in current outputs.

The cleanup migrates remaining high-value reporting surfaces where the change
is low risk:

- research review packet final strategy statuses;
- dashboard shadow command-center payload metadata;
- dashboard frontend shadow chart series;
- research-registry query parsing for NAV/holdings comparison targets.

No execution, broker, cron, paper trading, capital allocation, strategy
selection, or governance threshold code was changed.

## Remaining Fixed-Name Surface Audit

Search terms:

- `Polaris`, `Orion`, `Lyra`
- `caerus_polaris`, `caerus_orion`, `caerus_lyra`
- `differences_vs_polaris`
- `three-way`, `3 strategy`

| Surface | Decision | Reason |
|---|---|---|
| `research/review_packet.py` | Migrated where safe | Added registry-driven `strategy_statuses` while preserving legacy `polaris_status`, `orion_status`, and `lyra_status` fields. |
| `scripts/research/build_dashboard_v1.py` | Migrated | Shadow command-center strategy metadata now comes from active registry security-selection entries. |
| `web/dashboard/quant_daily_executive.js` | Migrated | Shadow excess chart now renders the strategies present in the dashboard payload, with legacy colors preserved for Polaris/Orion/Lyra. |
| `research_registry/research/shadow_comparison.py` | Migrated | Recognized query names now come from registry security-selection entries plus legacy `leda`; overlays are excluded. |
| `research_registry/research/promotion_readiness.py` | Documentation cleanup | Parser wording now reflects registry-backed security-selection names. |
| `research_registry/research/strategy_behavior_differentiation.py` | Documentation cleanup | Proposed NAV artifact contract no longer hard-codes only the current three strategy columns. |
| `research/dynamic_strategy_allocation.py` | Left fixed, legacy historical research | This is explicitly a three-strategy research policy study. Generalizing it would change research semantics. |
| `research/analysis/stable_window_evaluation.py` | Deferred legacy operational analysis | Read-only older analysis still reports Polaris/Orion/Lyra/SPY windows. Migration is not needed for active daily reporting. |
| `scripts/research/check_research_source_readiness.py` and `scripts/research/check_price_hydration_health.py` | Left fixed, legacy operator language | These are FR-030/Orion operator-readiness helpers, not generic strategy reporting surfaces. |
| `scripts/run_shadow_candidates_daily.sh` Desktop `Orion.md` alias | Left fixed, operator compatibility | The alias is a user-facing launcher convention. Changing it would be an operator workflow change. |
| `scripts/format_precompute_email.py`, `core/strategy_identity.py`, `scripts/live_vs_shadow_reconciliation.py` | Must remain fixed | These preserve Polaris paper/control identity and execution-adjacent semantics. |
| Documentation examples and runbooks | Left fixed | They document current operating state or historical examples, not active discovery logic. |
| Tests with Polaris/Orion/Lyra fixtures | Left fixed | These assert backward compatibility for the current active strategy set. |

## Implementation Details

Registry-aware additions:

- `load_strategy_registry_for_repo(repo_root)` lets tools load a local fixture
  registry during tests while production uses the canonical registry.
- Review packet final control summary now includes additive
  `strategy_statuses` keyed by registry-discovered active security-selection
  strategy IDs.
- Dashboard shadow command center uses registry-derived active
  security-selection entries for rows, rolling excess series keys, candidate
  counts, and control slug.
- Dashboard chart rendering reads the strategy rows already present in the
  payload instead of fixed Polaris/Orion/Lyra series.
- Research-registry question parsing excludes overlays such as Argo from
  holdings/NAV comparison flows.

Compatibility preserved:

- `differences_vs_polaris` and `excess_return_vs_polaris` are retained.
- `polaris_status`, `orion_status`, and `lyra_status` are retained.
- Polaris remains the control strategy.
- Current active output set remains Polaris, Orion, and Lyra.
- Phoenix, Cygnus, Cassiopeia, and Argo do not appear in current outputs.

## Overlay Handling

Argo remains a registry overlay and is excluded from:

- shadow holdings/NAV loops;
- dashboard shadow command-center strategy rows;
- review packet strategy status rows;
- research-registry NAV/holdings comparison query parsing.

## Deferred Work

The remaining fixed references are intentionally deferred or intentionally
fixed:

- `research/dynamic_strategy_allocation.py`: legacy three-way allocation
  research. A generic successor should be designed separately to avoid changing
  historical policy semantics.
- `research/analysis/stable_window_evaluation.py`: older read-only operational
  analysis. It can be migrated later if it becomes a primary daily surface.
- Operator command names and runbook examples mentioning Orion: preserve for
  workflow compatibility.
- Execution/control identity surfaces: must remain Polaris-specific unless a
  separate promotion or execution-governance task changes that explicitly.

## Recommendation

IMPLEMENTED SAFELY.

The remaining unfixed surfaces are classified as intentional control-specific
identity, legacy historical research, or operator-documentation examples.
