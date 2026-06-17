# FR-069 Argo Phase C Onboarding Packet

Status: RESEARCH_STAGE_ONBOARDED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet onboards Argo into the FR-069 research-lab lifecycle as a governed
Research-stage meta-model candidate. It does not activate Argo in Shadow,
change allocations, switch sleeves, change strategy selection, alter risk
controls, submit broker orders, install cron, or change paper/live trading
behavior.

## Executive Summary

Argo is the canonical Caerus regime/model-selection overlay under FR-053. It is
not a security-selection sleeve and must not route capital in Phase C.

Current state:

- Strategy registry: `caerus_argo`, family `regime_overlay` / `meta_model`,
  status `research`, shadow disabled, promotion ineligible.
- FR-069 sleeve manifest: `argo`, status `research_placeholder`, lifecycle
  `spec_only`, behavior changes disabled.
- Existing canonical spec:
  `docs/governance/fr_archive/fr_053_argo_research_spec.md`.
- Existing research module emits non-executing regime-selection artifacts.
- Evidence envelope template:
  `docs/governance/fr_active/fr_069_argo_evidence_envelope_template.json`.

Governance conclusion: Argo is the right home for regime allocation and
model-selection research, but it must remain reporting-only until member-sleeve
evidence is decision-grade and a separate owner-approved paper allocation task
exists.

## Argo Thesis

Argo studies when Caerus sleeve families should be trusted. It should classify
market regimes and produce research-only recommendations across sleeve families
using point-in-time diagnostics and frozen member-sleeve evidence.

Argo is not:

- a live allocation engine;
- an execution module;
- a broker or risk-control layer;
- a security-selection strategy;
- permission to move capital among sleeves.

## Research-Stage Status

| Surface | Current state |
|---|---|
| Strategy registry | `caerus_argo`, status `research`, shadow disabled, promotion ineligible. |
| Sleeve manifest | `argo`, `research_placeholder`, `spec_only`, `behavior_change_allowed=false`. |
| Existing spec | `docs/governance/fr_archive/fr_053_argo_research_spec.md`. |
| Existing research module | `research_registry/research/argo.py` and Phase B validation outputs. |
| Phase C evidence template | `docs/governance/fr_active/fr_069_argo_evidence_envelope_template.json`. |

Argo remains Research-stage after this packet.

## Distinctiveness Assessment

| Comparator | Distinctiveness thesis | Current evidence status |
|---|---|---|
| Polaris | Polaris selects securities; Argo evaluates when a core momentum sleeve should be trusted. | Distinct by role. |
| Orion | Orion is a momentum challenger; Argo may evaluate Orion evidence but must not alter Orion. | Distinct by role. |
| Lyra | Lyra is a momentum challenger; Argo may evaluate Lyra evidence but must not alter Lyra. | Distinct by role. |
| Phoenix | Phoenix is crisis reversal; Argo may assign research suitability by regime. | Distinct; Argo consumes, does not replace, Phoenix evidence. |
| Cassiopeia | Cassiopeia selects event-driven securities; Argo evaluates event-sleeve suitability by regime. | Distinct after canonical role cleanup. |
| Cygnus | Cygnus selects earnings-drift securities; Argo evaluates whether earnings-drift evidence is usable. | Distinct by role. |

Argo is sufficiently differentiated by architecture. It is not ready for any
capital-routing role.

## Target Market Conditions

Argo should be evaluated across all regimes, including:

- broad risk-on trends;
- narrow leadership;
- volatile transitions;
- risk-off drawdowns;
- crisis liquidation;
- washed-out recovery.

Regime labels must be point-in-time and generated from inputs available by the
decision timestamp.

## Expected Alpha Source

Argo does not produce direct alpha through security selection. Its expected
research value is better sleeve trust calibration:

- avoid stale or invalid sleeve evidence;
- identify regimes where a sleeve family historically works or fails;
- reduce overreliance on redundant sleeves;
- provide non-executing allocation recommendations for future review.

Any future capital-routing claim requires a separate approval path.

## Expected Strengths

- Gives the research lab a common regime vocabulary.
- Can consume evidence across Polaris, Orion, Lyra, Phoenix, Cassiopeia, and
  Cygnus.
- Can make stale or non-decision-grade sleeve evidence visible.
- Supports portfolio-of-sleeves doctrine without changing production weights.

## Expected Weaknesses

- High overfit risk in regime boundaries.
- Member-sleeve evidence may be stale or non-decision-grade.
- Regime labels can become look-ahead-prone if future returns are used.
- Recommendations can be mistaken for allocation instructions unless explicitly
  labeled research-only.
- Feedback loops are possible if Argo later influences the sleeves it evaluates.

## Failure Modes

- Regime classification uses future volatility or future sleeve returns.
- Member-sleeve evidence is stale or mixed-convention.
- Recommendation quality is driven by one historical crisis.
- Transition rules churn too often.
- Operator-facing reporting blurs research recommendations with production
  allocation.

## Manifest Requirements

Argo must remain compatible with the current FR-069 manifest contract:

- `sleeve_id=argo`;
- `strategy_id=caerus_argo`;
- `status=research_placeholder`;
- `lifecycle_stage=spec_only` until owner-approved Shadow/Paper onboarding;
- `sleeve_type=meta_model`;
- `family=regime_overlay`;
- `universe_method_required=pit_universe`;
- `behavior_change_allowed=false`;
- required artifacts:
  - regime definition;
  - member-sleeve inputs;
  - selection backtest;
  - overfit diagnostics;
  - no-live-switching attestation.

No manifest state change is required for this packet because the current
placeholder is correct.

## Evidence Requirements

Before Shadow, Argo must produce a validated evidence envelope with:

- `schema_version=caerus_sleeve_evidence_v1`;
- `sleeve_id=argo`;
- `production_impact=research_only`;
- `execution_impact=NON_EXECUTIONAL`;
- `governance_label=RESEARCH_ONLY`;
- frozen member-sleeve input list;
- `holdout_excluded=true`;
- benchmark `SPY`;
- PIT regime definitions and indicator snapshots;
- metrics for classification stability, transition false positives,
  recommendation turnover, member-sleeve freshness, overfit diagnostics, and
  recommendation quality versus static baselines;
- no-live-switching attestation.

Template:
`docs/governance/fr_active/fr_069_argo_evidence_envelope_template.json`.

## Promotion Gates

### Research -> Shadow

Required before Shadow:

1. Owner-approved regime taxonomy.
2. PIT regime indicators and transition trace.
3. Frozen member-sleeve input set.
4. Member-sleeve evidence freshness and decision-grade status.
5. No-live-switching attestation.
6. Evidence envelope validates through
   `scripts/research/validate_sleeve_evidence.py`.
7. Overfit diagnostics and transition-churn review.
8. Explicit comparison versus static sleeve baselines and SPY.
9. No unresolved look-ahead or stale-member-input blocker.

### Shadow -> Paper

Requires a separate owner-approved FR after Shadow evidence, including
allocation-risk review and target-attainment expectations. This packet does not
authorize Paper or any production allocation switching.

### Paper -> Pilot Capital / Production

Out of scope. Requires separate owner approval, broker/reconciliation review,
capital cap, rollback plan, and production monitoring.

## Retirement Criteria

Argo should be shelved or retired if decision-grade research shows:

- regime labels are unstable or look-ahead-prone;
- recommendations do not beat static baselines after costs and turnover;
- member-sleeve evidence remains too stale for decisions;
- the overlay increases drawdown or churn;
- operator reporting cannot keep recommendations clearly non-executing;
- required evidence cannot be produced without feedback loops.

Retirement requires explicit owner approval and a lineage note.

## Research Roadmap

1. Freeze regime taxonomy and version the boundary rules.
2. Emit member-sleeve input freshness and decision-grade diagnostics.
3. Validate the evidence envelope template against real artifacts.
4. Compare recommendations versus static equal-weight, Polaris-only, and SPY
   baselines.
5. Add overfit and transition-churn diagnostics.
6. Produce a Shadow-readiness packet only if Research evidence is
   decision-grade.

## Acceptance Criteria For This Onboarding

- Argo has a Phase C governance packet.
- Argo has a research-only evidence-envelope template.
- Argo remains `research_placeholder` / `spec_only` in the sleeve manifest.
- No production runtime files are changed.

RESEARCH_ONLY
NO_RUNTIME_CHANGE
