# FR-069 Cassiopeia Phase C Onboarding Packet

Status: RESEARCH_STAGE_ONBOARDED
Owner: Caerus Research Program
Last Updated: 2026-06-17
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL
Decision Status: RESEARCH_ONLY / NO_RUNTIME_CHANGE

This packet onboards Cassiopeia into the FR-069 research-lab lifecycle as a
governed Research-stage sleeve candidate. It does not implement Cassiopeia
signals, activate Cassiopeia in Shadow, change allocations, change strategy
selection, alter risk controls, submit broker orders, install cron, or change
paper/live trading behavior.

## Executive Summary

Cassiopeia is the canonical Caerus event-driven sleeve candidate under FR-052.
The current repo intentionally preserves Cassiopeia as event-driven and Argo as
the regime/model-selection overlay. That mapping resolves earlier naming drift
and should not be changed by Phase C onboarding.

Current state:

- Strategy registry: `caerus_cassiopeia`, family `event_driven`, status
  `research`, shadow disabled, promotion ineligible.
- FR-069 sleeve manifest: `cassiopeia`, status `research_placeholder`,
  lifecycle `spec_only`, behavior changes disabled.
- Existing canonical spec:
  `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md`.
- Evidence envelope template:
  `docs/governance/fr_active/fr_069_cassiopeia_evidence_envelope_template.json`.

Governance conclusion: Cassiopeia is distinct enough by thesis to remain a
Research-stage candidate, but it is not ready for implementation or Shadow
until a point-in-time event contract and event tape exist.

## Cassiopeia Thesis

Cassiopeia is a timestamp-aware event-driven sleeve. Its objective is to test
whether discrete corporate or market catalysts produce persistent long-only
forward returns after the event is public and available to the decision process.

Candidate event families include:

- analyst upgrades and target-price increases;
- index additions and reconstitution events;
- activist 13D filings;
- later approved categories such as leadership transitions or spin-offs.

Cassiopeia is not a regime overlay, cash-routing model, execution module, or
replacement for Argo. It is also not authorized to trade event rumors or use
future event outcomes.

## Research-Stage Status

| Surface | Current state |
|---|---|
| Strategy registry | `caerus_cassiopeia`, status `research`, shadow disabled, promotion ineligible. |
| Sleeve manifest | `cassiopeia`, `research_placeholder`, `spec_only`, `behavior_change_allowed=false`. |
| Existing spec | `docs/governance/fr_archive/fr_052_cassiopeia_research_spec.md`. |
| Existing module | None; Cassiopeia is spec-only. |
| Phase C evidence template | `docs/governance/fr_active/fr_069_cassiopeia_evidence_envelope_template.json`. |

Cassiopeia remains Research-stage after this packet.

## Distinctiveness Assessment

| Comparator | Distinctiveness thesis | Current evidence status |
|---|---|---|
| Polaris | Polaris is core momentum; Cassiopeia is catalyst/event availability driven. | Conceptually distinct; empirical event evidence missing. |
| Orion | Orion is momentum-family shadow; Cassiopeia requires event records and availability timestamps. | Conceptually distinct; no decision-grade overlap study yet. |
| Lyra | Lyra is a momentum/holding-period challenger; Cassiopeia is event-window based. | Conceptually distinct; no decision-grade overlap study yet. |
| Phoenix | Phoenix buys dislocation/reversal; Cassiopeia buys approved catalyst events. | Distinct return driver; may overlap after crisis events and must be measured. |
| Cygnus | Cygnus is specifically earnings drift; Cassiopeia is broader event-driven. | Related event family; earnings events should remain Cygnus-owned unless explicitly approved. |
| Argo | Argo is a meta-model/regime overlay; Cassiopeia selects securities from event tapes. | Canonically distinct after 2026-06-08 role cleanup. |

Cassiopeia is sufficiently differentiated by hypothesis to enter governed
Research. It is not sufficiently evidenced to enter Shadow.

## Target Market Conditions

Cassiopeia should be evaluated when structured public events create measurable
post-event drift:

- analyst upgrade cycles;
- index addition demand or deletion avoidance;
- activist ownership campaigns;
- discrete catalyst clusters with reliable source timestamps.

Event date alone is not sufficient. Availability timestamp and source lineage
are required.

## Expected Alpha Source

Cassiopeia's expected alpha source is delayed market incorporation of
event-specific information:

- institutional demand after index additions;
- analyst revision underreaction;
- activist campaign repricing;
- multi-event confirmation after a public catalyst.

Expected holding period: event-specific, initially 5-60 trading days.

## Expected Strengths

- Return driver is distinct from pure momentum.
- Event availability can be audited with deterministic records.
- Long-only design is compatible with Caerus doctrine.
- Event-family attribution can explain why a candidate entered the basket.

## Expected Weaknesses

- Sparse sample sizes.
- Event timestamp and source quality risk.
- Ticker mapping and corporate-action drift.
- Crowded catalyst reactions.
- High transaction-cost sensitivity around event dates.
- Risk of accidental look-ahead through revised event metadata.

## Failure Modes

- Event tape lacks reliable availability timestamps.
- Later event outcomes leak into earlier selection dates.
- Positive returns are concentrated in one event family or one period.
- Event reaction is exhausted before entry.
- M&A rumor or deal-risk events create unmodeled downside.
- Candidate overlap with momentum sleeves explains apparent alpha.

## Manifest Requirements

Cassiopeia must remain compatible with the current FR-069 manifest contract:

- `sleeve_id=cassiopeia`;
- `strategy_id=caerus_cassiopeia`;
- `status=research_placeholder`;
- `lifecycle_stage=spec_only` until owner-approved Shadow onboarding;
- `sleeve_type=event_driven`;
- `family=event_driven`;
- `universe_method_required=pit_universe`;
- `behavior_change_allowed=false`;
- required artifacts:
  - event contract;
  - event tape;
  - candidate universe;
  - backtest summary;
  - sparse-sample diagnostics.

No manifest state change is required for this packet because the current
placeholder is correct.

## Evidence Requirements

Before Shadow, Cassiopeia must produce a validated evidence envelope with:

- `schema_version=caerus_sleeve_evidence_v1`;
- `sleeve_id=cassiopeia`;
- `production_impact=research_only`;
- `execution_impact=NON_EXECUTIONAL`;
- `governance_label=RESEARCH_ONLY`;
- PIT universe membership and `universe_snapshot_hash`;
- `holdout_excluded=true`;
- benchmark `SPY`;
- approved event taxonomy and event-family definitions;
- event tape with availability timestamps and source lineage;
- metrics for event-window return, post-event drift, hit rate, drawdown,
  turnover, cost sensitivity, event coverage, candidate overlap, and
  correlation versus existing sleeves.

Template:
`docs/governance/fr_active/fr_069_cassiopeia_evidence_envelope_template.json`.

## Promotion Gates

### Research -> Shadow

Required before Shadow:

1. Owner-approved event taxonomy.
2. PIT event tape with availability timestamps.
3. Ticker mapping and raw payload lineage.
4. Evidence envelope validates through
   `scripts/research/validate_sleeve_evidence.py`.
5. Passive forward-return observation by event family.
6. Positive or defensible event-window expectancy after costs.
7. Sparse-sample diagnostics and event-family concentration review.
8. Explicit comparison versus Polaris, Orion, Lyra, Phoenix, Cygnus, Argo, and
   SPY.
9. No unresolved look-ahead, restatement, or survivorship blocker.

### Shadow -> Paper

Requires a separate owner-approved FR after Shadow evidence, including
execution-risk review and target-attainment expectations. This packet does not
authorize Paper.

### Paper -> Pilot Capital / Production

Out of scope. Requires separate owner approval, broker/reconciliation review,
capital cap, rollback plan, and production monitoring.

## Retirement Criteria

Cassiopeia should be shelved or retired if decision-grade research shows:

- event data cannot be made PIT-safe;
- event sample size remains too sparse for decisions;
- returns disappear after transaction costs;
- event alpha is explained by existing momentum sleeves;
- source restatements or ticker mapping cannot be controlled;
- required evidence cannot be produced without look-ahead bias.

Retirement requires explicit owner approval and a lineage note.

## Research Roadmap

1. Freeze event taxonomy and exclude rumor/deal-risk categories from MVP.
2. Build a PIT event contract and deterministic event tape.
3. Validate the evidence envelope template against real artifacts.
4. Run passive event-family observation before any Shadow request.
5. Compare event-family performance versus existing sleeves and SPY.
6. Produce a Shadow-readiness packet only if Research evidence is
   decision-grade.

## Acceptance Criteria For This Onboarding

- Cassiopeia has a Phase C governance packet.
- Cassiopeia has a research-only evidence-envelope template.
- Cassiopeia remains `research_placeholder` / `spec_only` in the sleeve
  manifest.
- No production runtime files are changed.

RESEARCH_ONLY
NO_RUNTIME_CHANGE
