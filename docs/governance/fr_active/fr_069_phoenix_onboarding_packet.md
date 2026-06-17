# FR-069 Phoenix Phase C Onboarding Packet

Status: EXTERNAL_DEPENDENCY_BLOCKED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet onboards Phoenix into the FR-069 research-lab lifecycle as a
governed Research-stage sleeve candidate. It does not implement Phoenix
signals, activate Phoenix in Shadow, change allocations, change strategy
selection, alter risk controls, submit broker orders, install cron, or change
paper/live trading behavior.

## Executive Summary

Phoenix is now represented as a governed Research-stage sleeve candidate under
FR-069 Phase C.

Current state:

- Strategy registry: `caerus_phoenix`, family `crisis_reversal`, status
  `research`, `eligible_for_shadow=true`, `eligible_for_promotion=false`,
  `shadow_tracking.enabled=false`.
- FR-069 sleeve manifest: `phoenix`, status `research_placeholder`,
  lifecycle `spec_only`, behavior changes disabled.
- Research artifacts exist for 2026-06-08, but current evidence is explicitly
  not decision-grade.
- Evidence envelope template:
  `docs/governance/fr_active/fr_069_phoenix_evidence_envelope_template.json`.

Governance conclusion: Phoenix is distinct enough by thesis to deserve
Research-stage onboarding and Phase B risk-shaping evidence is promising, but
Phoenix is now formally on external-dependency hold. It remains blocked from
Shadow until PIT liquidity/capacity evidence can be rebuilt from Sharadar SEP
OHLCV data.

Formal hold state:

- **Classification:** `EXTERNAL_DEPENDENCY_BLOCKED`
- **Blocker:** Nasdaq Data Link `QELx06` temporary API disablement prevents
  verification/rebuild of Sharadar SEP OHLCV access.
- **Owner:** Brett
- **Unblock condition:** Vendor confirms Sharadar SEP OHLCV access restored.
- **Next action after unblock:** rebuild OHLCV cache, build PIT liquidity panel,
  re-run Phoenix Phase C, then run a Shadow-readiness assessment.

## Phoenix Thesis

Phoenix is a contrarian crisis-reversal sleeve. Its objective is to identify
panic-driven market dislocations where forced selling, volatility shocks, or
short-horizon overreaction create unusually attractive forward returns.

Phoenix should buy stress, not strength. That makes it conceptually distinct
from Polaris, Orion, and Lyra, which are momentum-family sleeves.

Phoenix is not intended to be:

- a defensive sleeve;
- a low-volatility sleeve;
- a capital-preservation sleeve;
- a replacement for risk controls;
- an always-on momentum variant.

## Research-Stage Status

| Surface | Current state |
|---|---|
| Strategy registry | `caerus_phoenix`, status `research`, shadow disabled, promotion ineligible. |
| Sleeve manifest | `phoenix`, `research_placeholder`, `spec_only`, `behavior_change_allowed=false`. |
| Existing spec | `docs/governance/fr_archive/fr_050_phoenix_research_spec.md`. |
| Existing research module | `research_registry/research/phoenix.py` and `research/phoenix/`. |
| Existing tests | `Tests/test_research_registry_phoenix.py`. |
| Existing artifacts | `outputs/model_quality/2026-06-08/phoenix_*.json` and `outputs/research/phoenix/2026-06-08/*`. |
| Current evidence artifacts | `outputs/research/phoenix_evidence/phoenix_crisis_recovery_2026-06-17.json`, `outputs/research/phoenix_evidence/phoenix_phase_b_risk_shaping_2026-06-17.json`, `outputs/research/phoenix_evidence/phoenix_phase_c_liquidity_capacity_2026-06-17.json`. |
| Current blocker | `EXTERNAL_DEPENDENCY_BLOCKED`: Nasdaq Data Link `QELx06` temporary disablement; Sharadar SEP OHLCV access must be restored before PIT liquidity/capacity validation. |
| Phase C evidence template | `docs/governance/fr_active/fr_069_phoenix_evidence_envelope_template.json`. |

Phoenix remains Research-stage and external-dependency blocked after this
packet.

## Distinctiveness Assessment

| Comparator | Distinctiveness thesis | Current evidence status |
|---|---|---|
| Polaris | Polaris buys broad large-cap momentum strength; Phoenix buys dislocation/reversal candidates after stress. | Conceptually distinct. Existing 2026-06-08 Phase B review shows low one-day holdings overlap versus Polaris, but evidence is sparse and not decision-grade. |
| Orion | Orion is a concentrated core-momentum challenger; Phoenix is crisis-reversal. | Conceptually distinct. Existing Phase B review shows zero one-day overlap versus Orion, but decision-grade PIT evidence is missing. |
| Lyra | Lyra is a concentrated momentum/holding-period challenger; Phoenix is crisis-reversal. | Conceptually distinct. Existing Phase B review shows zero one-day overlap versus Lyra, but decision-grade PIT evidence is missing. |

Phoenix is sufficiently differentiated by hypothesis to enter governed Research.
It is not sufficiently differentiated by completed evidence to enter Shadow.

## Target Market Conditions

Phoenix should be evaluated during:

- sharp market drawdowns;
- volatility spikes;
- forced liquidation / washout conditions;
- broad de-risking or breadth collapse;
- post-stress recovery windows;
- local single-name dislocations when liquidity remains adequate.

The crisis/dislocation label must be point-in-time and preregistered before
performance is evaluated.

## Expected Alpha Source

Phoenix's expected alpha source is short-horizon behavioral overreaction:

- panic selling creates temporary mispricing;
- capitulation volume identifies forced or non-fundamental selling pressure;
- oversold/liquid names recover as liquidity normalizes;
- crisis baskets may rebound differently from momentum winners.

Expected holding period: 5-20 trading days.

Expected portfolio role: episodic opportunity sleeve, not a constant allocation.

## Expected Strengths

- Potential diversification versus momentum sleeves during stress/recovery.
- Explicit crisis-window observability.
- Long-only and compatible with the Caerus doctrine.
- Existing research module already emits deterministic research artifacts with
  `RESEARCH_ONLY` / `NON_EXECUTIONAL` labels.
- Candidate overlap can be measured versus Polaris, Orion, and Lyra.

## Expected Weaknesses

- Sparse true crisis observations.
- Risk of buying falling knives.
- High turnover during dislocations.
- Regime-label overfitting risk.
- Sensitivity to transaction costs and liquidity.
- Potential negative expectancy if crisis filters are too broad.
- Existing Phase B backtest summary is poor and non-decision-grade.

## Known Evidence From Existing Artifacts

Existing 2026-06-08 artifacts are useful for onboarding context, not promotion:

- `phoenix_phase_b_review.json` confidence is `LOW` and
  `decision_grade=false`.
- Decision-grade blockers include:
  - `NO_DECISION_GRADE_UNDER_PHASE_B_REVIEW`;
  - `PHOENIX_PHASE_B_RESEARCH_ONLY`;
  - `SPARSE_PHOENIX_ACTIVE_DAYS:1/20`;
  - `SPARSE_PHOENIX_HISTORY:1/120`.
- Phase B review reports one active Phoenix day and one source artifact.
- Drawdown/recovery summary reports total return `-0.7291`, max drawdown
  `-0.7409`, and no recovery to new high.
- Existing overlap review shows one-day average overlap of about 5.3% versus
  Polaris and 0% versus Orion/Lyra.
- `phoenix_evidence_tracker.json` reports `FORWARD_RETURN_NOT_YET_OBSERVABLE`
  for the 2026-06-08 signal date.
- `phoenix_backtest_summary.json` reports CAGR `-0.0999`, Sharpe `-0.9`, hit
  rate `0.212`, and average turnover `0.2293`.

This evidence supports onboarding, not promotion.

## Manifest Requirements

Phoenix must remain compatible with the existing FR-069 manifest contract:

- `sleeve_id=phoenix`;
- `strategy_id=caerus_phoenix`;
- `status=research_placeholder`;
- `lifecycle_stage=spec_only` until owner-approved Shadow onboarding;
- `sleeve_type=security_selection`;
- `family=crisis_reversal`;
- `universe_method_required=pit_universe`;
- `behavior_change_allowed=false`;
- required artifacts:
  - crisis-window definition;
  - candidate universe;
  - signal panel;
  - backtest summary;
  - drawdown/recovery analysis.

No manifest state change is required for this packet because the current
placeholder is correct.

## Evidence Requirements

Before Shadow, Phoenix must produce a validated evidence envelope with:

- `schema_version=caerus_sleeve_evidence_v1`;
- `sleeve_id=phoenix`;
- `production_impact=research_only`;
- `execution_impact=NON_EXECUTIONAL`;
- `governance_label=RESEARCH_ONLY`;
- PIT universe membership and `universe_snapshot_hash`;
- `holdout_excluded=true`;
- benchmark `SPY`;
- explicit crisis-window start/end definitions;
- artifact paths for signal panel, decision trace, backtest summary,
  drawdown/recovery analysis, and candidate overlap;
- metrics for crisis-window return, recovery-window return, drawdown capture,
  max drawdown, turnover, hit rate, cost sensitivity, and overlap versus
  Polaris/Orion/Lyra;
- known bias risks and promotion blockers.

Template:
`docs/governance/fr_active/fr_069_phoenix_evidence_envelope_template.json`.

## Promotion Gates

### Research -> Shadow

Required before Shadow:

1. Owner-approved crisis-window definition.
2. PIT universe and price-source lineage with real snapshot hash.
3. Evidence envelope validates through
   `scripts/research/validate_sleeve_evidence.py`.
4. Passive forward-return observation for Phoenix candidate baskets.
5. Positive or defensible crisis/recovery expectancy after costs.
6. Explicit comparison versus Polaris, Orion, Lyra, and SPY.
7. Drawdown/recovery and falling-knife diagnostics.
8. Shadow integration plan that is non-blocking and non-executing.
9. No unresolved look-ahead or survivorship blocker.

### Shadow -> Paper

Requires a separate owner-approved FR after shadow evidence, including execution
risk review and target-attainment expectations. This packet does not authorize
Paper.

### Paper -> Pilot Capital / Production

Out of scope. Requires separate owner approval, broker/reconciliation review,
capital cap, rollback plan, and production monitoring.

## Retirement Criteria

Phoenix should be shelved or retired if decision-grade research shows:

- no positive crisis/recovery expectancy after realistic costs;
- activation is too sparse for reliable evaluation;
- crisis labels cannot be made point-in-time;
- performance is dominated by falling-knife losses;
- overlap with momentum sleeves remains high during the only profitable windows;
- liquidity/cost sensitivity eliminates alpha;
- required evidence cannot be produced without look-ahead bias.

Retirement requires explicit owner approval and a lineage note. Name reuse is
not permitted without a separate governance decision.

## Research Roadmap

1. Freeze crisis-window definitions before evaluating forward returns.
2. Build or select PIT universe snapshots and price-source hashes.
3. Validate the Phoenix evidence envelope template against real artifacts.
4. Review 2026-06-08 sparse evidence and expand passive observation only after
   enough post-signal returns become observable.
5. Compare Phoenix against Polaris, Orion, Lyra, and SPY in stress and recovery
   windows.
6. Run cost/liquidity/falling-knife sensitivity.
7. Produce a Shadow-readiness packet only if Research evidence is
   decision-grade.

## External Dependency Hold

Phoenix is formally on hold as of 2026-06-17.

| Field | Value |
|---|---|
| Classification | `EXTERNAL_DEPENDENCY_BLOCKED` |
| Blocker | Nasdaq Data Link `QELx06` temporary disablement |
| Owner | Brett |
| Unblock condition | Vendor confirms Sharadar SEP OHLCV access restored |
| Next action after unblock | Rebuild OHLCV cache; build PIT liquidity panel; re-run Phoenix Phase C; run Shadow-readiness assessment |

No Phoenix logic, thresholds, research outputs, live signals, allocation,
execution, broker behavior, risk controls, promotion thresholds, or cron
behavior are changed by this hold state.

## Acceptance Criteria For This Onboarding

- Phoenix has a Phase C governance packet.
- Phoenix has a research-only evidence-envelope template.
- Phoenix remains `research_placeholder` / `spec_only` in the sleeve manifest.
- Phoenix remains `status=research` and `shadow_tracking.enabled=false` in the
  strategy registry.
- Manifest validation passes.
- Evidence template validation passes.
- No production runtime files are changed.

## Decision Status

`RESEARCH_ONLY / NO_RUNTIME_CHANGE`

Allowed next state: continue Research-stage evidence collection.

Disallowed actions from this packet:

- activate Phoenix;
- add Phoenix to Shadow;
- promote Phoenix;
- allocate to Phoenix;
- change production signals, rankings, sizing, target generation, execution,
  broker behavior, cron, or risk controls.
