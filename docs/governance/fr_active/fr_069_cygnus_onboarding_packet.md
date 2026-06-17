# FR-069 Cygnus Phase C Onboarding Packet

Status: RESEARCH_STAGE_ONBOARDED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet onboards Cygnus into the FR-069 research-lab lifecycle as a
governed Research-stage sleeve candidate. It does not reactivate shelved Cygnus
v0, implement Cygnus v1, activate Shadow, change allocations, change strategy
selection, alter risk controls, submit broker orders, install cron, or change
paper/live trading behavior.

## Executive Summary

Cygnus is the canonical Caerus earnings/post-earnings drift sleeve under
FR-051. The later generic drift concept was retired under FR-056; this packet
preserves the owner-approved mapping: Cygnus means earnings drift, not generic
factor drift.

Current state:

- Strategy registry: `caerus_cygnus`, family `earnings_drift`, status
  `research`, shadow disabled, promotion ineligible.
- FR-069 sleeve manifest: `cygnus`, status `research_placeholder`,
  lifecycle `shelved_v0`, behavior changes disabled.
- Existing canonical spec:
  `docs/governance/fr_archive/fr_051_cygnus_research_spec.md`.
- Existing v0 research is shelved after Stage 2 failure; 2025-forward holdout
  remains preserved.
- Evidence envelope template:
  `docs/governance/fr_active/fr_069_cygnus_evidence_envelope_template.json`.

Governance conclusion: Cygnus remains a Research-stage sleeve candidate, but it
is blocked from Shadow until a vendor-backed consensus/EPS-surprise data path
and v1 evidence plan are approved.

## Cygnus Thesis

Cygnus tests whether earnings surprise, guidance quality, estimate revisions,
and post-event reaction confirmation create persistent post-earnings drift.

Cygnus is not:

- a generic momentum sleeve;
- a replacement for Polaris, Orion, or Lyra;
- a broad drift detector for all sleeves;
- a production decay monitor;
- a runtime allocation or execution control.

## Research-Stage Status

| Surface | Current state |
|---|---|
| Strategy registry | `caerus_cygnus`, status `research`, shadow disabled, promotion ineligible. |
| Sleeve manifest | `cygnus`, `research_placeholder`, `shelved_v0`, `behavior_change_allowed=false`. |
| Existing spec | `docs/governance/fr_archive/fr_051_cygnus_research_spec.md`. |
| Existing research module | `research/cygnus/` research-only modules. |
| Existing status | v0 Stage 2 failed; v1 vendor/consensus data gated. |
| Phase C evidence template | `docs/governance/fr_active/fr_069_cygnus_evidence_envelope_template.json`. |

Cygnus remains Research-stage after this packet.

## Distinctiveness Assessment

| Comparator | Distinctiveness thesis | Current evidence status |
|---|---|---|
| Polaris | Polaris is price momentum; Cygnus requires earnings-event availability and surprise/revision data. | Conceptually distinct; v0 evidence failed. |
| Orion | Orion is momentum-family shadow; Cygnus is earnings-event driven. | Distinct if consensus/revision data is available. |
| Lyra | Lyra is momentum/holding-period; Cygnus is event-age and revision driven. | Distinct if v1 evidence works. |
| Phoenix | Phoenix is crisis reversal; Cygnus is earnings drift. | Distinct regimes and data dependencies. |
| Cassiopeia | Cassiopeia is broader event-driven; Cygnus owns the earnings-specific event family. | Related but separable by event taxonomy. |
| Argo | Argo is a regime/meta-model overlay; Cygnus selects securities from earnings events. | Canonically distinct. |

Cygnus is sufficiently differentiated by hypothesis to remain governed
Research. It is not ready for Shadow because v0 failed and v1 input data is not
approved.

## Target Market Conditions

Cygnus should be evaluated after earnings events where:

- EPS or revenue surprise is positive;
- guidance or estimate revisions are constructive;
- first eligible reaction is not failed;
- liquidity is sufficient;
- event age remains inside the drift window.

All event and revision inputs must be available by the decision timestamp.

## Expected Alpha Source

Cygnus's expected alpha source is delayed market incorporation of earnings
information:

- underreaction to positive surprise;
- analyst estimate revision persistence;
- guidance continuation;
- price-confirmed post-event drift.

Expected holding period: 10-60 trading days.

## Expected Strengths

- Catalyst-specific return driver.
- Natural attribution to surprise, revisions, reaction, and guidance.
- Potentially low correlation to pure momentum if event data is strong.
- Explicit holdout-preservation governance already exists.

## Expected Weaknesses

- v0 Stage 2 failed and must not be re-tuned against the holdout.
- Consensus/EPS-surprise vendor dependency is unresolved.
- Earnings samples may be sparse inside the Caerus universe.
- Transaction costs can erase short-horizon drift.
- Announcement-time and revision-availability rules are easy to contaminate.

## Failure Modes

- Vendor data lacks point-in-time revision lineage.
- Announcement timestamps are missing or restated.
- v1 repeats v0 failure after costs.
- Apparent drift is just momentum exposure.
- Holdout leakage or post-failure re-tuning invalidates evidence.

## Manifest Requirements

Cygnus must remain compatible with the current FR-069 manifest contract:

- `sleeve_id=cygnus`;
- `strategy_id=caerus_cygnus`;
- `status=research_placeholder`;
- `lifecycle_stage=shelved_v0` until owner-approved v1 research reopening;
- `sleeve_type=event_driven`;
- `family=earnings_drift`;
- `universe_method_required=pit_universe`;
- `behavior_change_allowed=false`;
- required artifacts:
  - event tape;
  - consensus surprise inputs;
  - coverage report;
  - backtest summary;
  - cost sensitivity.

No manifest state change is required for this packet because the current
placeholder is correct.

## Evidence Requirements

Before Shadow, Cygnus must produce a validated evidence envelope with:

- `schema_version=caerus_sleeve_evidence_v1`;
- `sleeve_id=cygnus`;
- `production_impact=research_only`;
- `execution_impact=NON_EXECUTIONAL`;
- `governance_label=RESEARCH_ONLY`;
- PIT universe membership and `universe_snapshot_hash`;
- `holdout_excluded=true`;
- benchmark `SPY`;
- event tape with announcement-time availability;
- consensus EPS/revenue surprise and estimate-revision lineage;
- metrics for rank IC, net IR versus SPY, event coverage, cost sensitivity,
  turnover, drawdown, and excess correlation versus the momentum family.

Template:
`docs/governance/fr_active/fr_069_cygnus_evidence_envelope_template.json`.

## Promotion Gates

### Research -> Shadow

Required before Shadow:

1. Owner-approved v1 research reopening plan.
2. Vendor-backed PIT consensus/EPS-surprise data.
3. Estimate-revision lineage with availability dates.
4. Holdout-preservation attestation.
5. Evidence envelope validates through
   `scripts/research/validate_sleeve_evidence.py`.
6. Stage 2-like criteria pass without re-tuning against the holdout.
7. Positive or defensible net IR after costs.
8. Explicit comparison versus Polaris, Orion, Lyra, Phoenix, Cassiopeia, Argo,
   and SPY.
9. No unresolved look-ahead, vendor restatement, or holdout leakage blocker.

### Shadow -> Paper

Requires a separate owner-approved FR after Shadow evidence, including
execution-risk review and target-attainment expectations. This packet does not
authorize Paper.

### Paper -> Pilot Capital / Production

Out of scope. Requires separate owner approval, broker/reconciliation review,
capital cap, rollback plan, and production monitoring.

## Retirement Criteria

Cygnus should remain shelved or be retired if decision-grade research shows:

- v1 data cannot be made PIT-safe;
- v1 fails the preregistered evidence gates;
- returns are not robust after costs;
- event coverage is too sparse;
- excess return is explained by momentum exposure;
- holdout integrity cannot be preserved.

Retirement requires explicit owner approval and a lineage note.

## Research Roadmap

1. Select or reject a consensus/EPS-surprise data vendor.
2. Freeze v1 evidence gates before touching holdout data.
3. Build PIT announcement and revision availability lineage.
4. Validate the evidence envelope template against real artifacts.
5. Compare v1 evidence versus v0 failure and existing sleeves.
6. Produce a Shadow-readiness packet only if Research evidence is
   decision-grade.

## Acceptance Criteria For This Onboarding

- Cygnus has a Phase C governance packet.
- Cygnus has a research-only evidence-envelope template.
- Cygnus remains `research_placeholder` / `shelved_v0` in the sleeve manifest.
- No production runtime files are changed.

RESEARCH_ONLY
NO_RUNTIME_CHANGE
