# Canonical Allocator Baseline Specification

Date: 2026-06-22
Program: Decision-Grade PIT Research Infrastructure Completion
Phase: 4 - Canonical Allocator Baseline
Governance label: RESEARCH_ONLY / NON_EXECUTIONAL
Runtime impact: none
Gate result: PASS

## Objective

Eliminate competing allocator proxies for future research. All allocator, conviction, concentration, and portfolio-construction studies must compare against one canonical current-framework baseline.

## Canonical Baseline

The canonical current allocator benchmark is:

`PortfolioAllocator` semantics as implemented in `core/portfolio_alloc.py`, reconstructed in research-only replay form from canonical PIT decision tapes.

It is not:

- `current_artifact_target_proxy`
- equal budget across Polaris/Orion/Lyra unless that is the documented sleeve-strength state for the replay date
- broker execution replay
- target-attainment output
- shadow candidate target aggregation

## Required Semantics

Future research baseline replays must model:

1. Active sleeve detection from non-empty target weights.
2. Sleeve budget by sleeve strength.
3. Redistribution of uncappable sleeve budget to sleeves with headroom.
4. Combination of sleeve weights by security.
5. Max-position capping without silent alpha-preserving renormalization.
6. Turnover constraint, when prior weights are supplied and the study declares it in scope.
7. Minimum gross exposure boost where cap headroom exists.
8. Cash routing as residual and with explicit cash reason.
9. Regime/exposure overlay only if the study explicitly declares that it is replaying the production daily construction surface.

## Required Inputs

| Input | Requirement |
| --- | --- |
| Candidate/weight source | Canonical decision tape |
| Identity | `security_id` |
| Ticker | display only |
| Sleeve specs | frozen and documented |
| Sleeve strengths | point-in-time or explicitly fixed research assumption |
| Max position | point-in-time or explicitly fixed research assumption |
| Min gross exposure | point-in-time or explicitly fixed research assumption |
| Prior weights | required when turnover constraints are included |
| Cash | explicit residual row or explicit cash series |

## Reporting Requirements

Every baseline replay must emit:

- gross exposure
- cash weight
- sleeve allocations requested
- sleeve allocations realized after cap redistribution
- max position
- holdings count
- turnover
- cap violations/capped excess
- min-gross boost amount
- cash reason

## Disallowed Baselines

The following may be retained only as lineage or sensitivity variants:

- `current_artifact_target_proxy`
- any replay using `data/universe.csv`
- any replay using `outputs/research/flow_detection_v1/price_panel.parquet`
- ticker-keyed replay panels
- conviction variants used as the baseline

## Gate

PASS. Future studies can reference this document as the canonical baseline definition.

Implementation of the baseline replay adapter is still required before Phase 7 conviction-allocation rebaseline can be run.
