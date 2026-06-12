# FR-069 Phase A Architecture Package

Status: ACTIVE_PHASE_A
Owner: Caerus Research Program
Last Updated: 2026-06-12
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

This package is the Phase A operating architecture for FR-069. It is
documentation and planning only. It does not change trading, broker submission,
execution, portfolio construction, allocation, model logic, strategy logic,
cron, live-capital behavior, holdout access, or production registry semantics.

FR-070 remains `DEPLOYED_OBSERVING` / `observation_monitoring`. New FR-070
implementation work should reopen only for classified evidence from the next
live run: stale/pre-buy posttrade snapshot, buy timeout/failure, unclassified
cash drift, reconciliation/target-attainment contradiction, or achieved cash
materially outside tolerance without a classified reason.

Orion, Lyra, and FR-063 are not retired by this package. Orion/Lyra
disposition remains data-driven and deferred to the FR-069 sleeve evaluation
framework. FR-063 remains supporting evidence for differentiation and
retirement analysis.

## 1. Canonical Sleeve Protocol

A sleeve is a research module that produces portfolio evidence under a frozen
contract. It is not automatically a production strategy, execution route, or
allocation authority.

### Required Identity

| Field | Meaning | Requirement |
|---|---|---|
| `sleeve_id` | Stable research sleeve identifier, usually aligned with `strategy_id`. | Required and immutable after first evidence run. |
| `display_name` | Operator-facing name. | Required. |
| `family` | Research family such as `core_momentum`, `crisis_reversal`, `earnings_drift`, or `event_driven`. | Required. |
| `sleeve_type` | `security_selection`, `event_driven`, `overlay`, `meta_model`, `benchmark`, or `reference_portfolio`. | Required. |
| `lifecycle_status` | `spec_only`, `research`, `backtest`, `shadow`, `paper`, `live`, `shelved`, or `retired`. | Required. |
| `execution_impact` | `NON_EXECUTIONAL`, `PAPER`, or `LIVE`. | Required; Phase A permits only documented `NON_EXECUTIONAL` work. |

### Required Data Contract

| Field | Meaning | Requirement |
|---|---|---|
| `universe_family` | PIT membership family used for candidates. | Required for security-selection sleeves. |
| `universe_method` | Evidence lineage, with `pit_universe` as the decision-grade requirement. | Required on evidence artifacts. |
| `universe_snapshot_hash` | Reproducibility anchor for the candidate set. | Required for future decision-grade runs. |
| `price_source` | Price source used by the sleeve run. | Required. |
| `benchmark` | Benchmark for excess return, beta, and decision-grade comparisons. | Required unless sleeve is a meta overlay. |
| `availability_policy` | Point-in-time availability rule for inputs. | Required before first decision-grade run. |

### Required Behavior Contract

Each sleeve must eventually declare frozen semantics for:

- `candidates(as_of_date)`: PIT candidate generation from the declared universe.
- `signals(panel, as_of_date)`: deterministic signal construction from available data only.
- `select(signals, as_of_date)`: ranking and selection rules.
- `weights(selection, as_of_date)`: research weights and constraints.
- `exit_rules()`: rebalance, exit, timeout, and stale-input behavior.
- `cost_model`: transaction-cost assumption for research evidence.
- `rebalance_policy`: cadence, calendar, and holiday handling.
- `risk_policy`: research-only caps and diagnostics.

Phase A does not implement this interface in code. It freezes the documentation
target so later implementation FRs can build small, reversible pieces.

### Required Artifact Envelope

Every future sleeve evidence artifact should carry:

`schema_version`, `sleeve_id`, `strategy_id`, `family`, `sleeve_type`,
`lifecycle_status`, `universe_family`, `universe_method`,
`universe_snapshot_hash`, `price_source`, `benchmark`, `trade_date` or
`window`, `holdout_excluded`, `spec_version`, `metrics`, `holdings`,
`attribution`, `reason_codes`, `governance_label`, and `execution_impact`.

Legacy current-universe artifacts remain readable for lineage, but they are
not decision-grade unless explicitly marked with a PIT-compatible method.

## 2. Registry-Onboarding Architecture

The current production-aware strategy registry is
`config/research/strategy_registry.json`, loaded by `core/strategy_registry.py`.
It already tracks `strategy_id`, display fields, `strategy_type`, `family`,
status, shadow eligibility, promotion eligibility, benchmark, execution impact,
capabilities, and shadow tracking.

Phase A does not edit that registry or its validation code. The recommended
architecture is a two-layer registry model:

1. **Production-aware strategy registry** — existing file and loader remain the
   authority for current strategy identity, shadow tracking, promotion
   eligibility, and execution impact.
2. **Research-only sleeve manifest** — future FR-owned metadata layer that can
   describe sleeve data contracts, PIT evidence requirements, lifecycle gates,
   artifact families, and holdout policy without changing production behavior.

### Future Research-Only Manifest Fields

| Field | Purpose |
|---|---|
| `sleeve_id` | Primary key for sleeve evidence. |
| `strategy_id` | Optional link to existing strategy registry entry. |
| `display_name` | Human-readable label. |
| `sleeve_type` | Security selection, event driven, overlay, meta model, benchmark, or reference. |
| `lifecycle_status` | Spec, research, backtest, shadow, paper, live, shelved, or retired. |
| `universe_family` | PIT candidate family. |
| `benchmark` | Benchmark family and ticker. |
| `input_contracts` | Required data sources and availability rules. |
| `artifact_families` | Output artifact names and locations. |
| `promotion_gates` | Required metrics and observation windows. |
| `holdout_policy` | Frozen holdout windows and authorization rules. |
| `owner_decisions` | Explicit approvals needed before promotion, retirement, or rename. |
| `execution_boundary` | Confirmation that the sleeve is research-only unless separately approved. |

The existing `delta_summary_name` field in `config/research/strategy_registry.json`
is a current manifest detail preserved through raw payload handling, not a
Phase A sleeve-contract field. Any future schema decision should either model
that field explicitly or document why it remains a presentation-only extension.

### Onboarding Gate Sequence

1. Create or update a research-only sleeve spec.
2. Add manifest metadata in a future approved FR.
3. Generate non-decision-grade smoke artifacts against fixtures.
4. Run PIT data availability and universe snapshot checks.
5. Run backtest/parity evidence outside holdout.
6. Feed read-only evidence into MCP and research review packet surfaces.
7. Request explicit owner decision before shadow, paper, promotion, retirement,
   or registry behavior changes.

MCP remains read-only. It must not mutate `config/research/strategy_registry.json`,
research artifacts, production registry state, or sleeve lifecycle state.

## 3. Research Lab Operating Model

The Research Lab operates as a repeatable evidence factory. It should make the
PIT-safe path easier than bespoke one-off research scripts.

### Operating Loop

1. **Register intent** — identify the sleeve, research question, non-goals, data
   sources, and holdout boundary before running experiments.
2. **Check data readiness** — verify PIT universe, prices, events, benchmarks,
   and required metadata.
3. **Run bounded research** — produce artifacts under a dated output path with
   the standard envelope.
4. **Classify evidence** — mark artifacts as `decision_grade`,
   `non_decision_grade`, `partial`, `blocked`, or `shelved`.
5. **Publish read-only surfaces** — expose summaries through research review
   packets and MCP without changing production routing.
6. **Review governance gates** — check PIT method, holdout exclusion,
   pre-registration, observation windows, differentiation, and risk.
7. **Record owner decisions** — promotion, retirement, rename, holdout access,
   production registry edits, and allocation changes require explicit approval.

The read-only Research Review Packet remains the main cross-artifact aggregator.
Future sleeve evidence should be consumable by
`outputs/research_review/YYYY-MM-DD/` artifacts, including
`research_review.json`, `research_review.md`, `research_review.html`,
`research_review_sources.json`, and `research_review_summary.json`.

### Evidence States

| State | Meaning |
|---|---|
| `spec_only` | Canonical idea exists; no implementation evidence. |
| `research_partial` | Some artifacts exist but blockers prevent decision use. |
| `research_decision_grade` | PIT-safe evidence satisfies artifact and governance requirements. |
| `shadow_candidate` | Eligible for shadow-only observation after owner approval. |
| `paper_candidate` | Eligible for paper behavior only after separate approval. |
| `shelved` | Evidence failed or dependency blocked; preserve lineage. |
| `retired` | Explicit owner-approved removal from active consideration. |

### Governance Gates

Decision-grade evidence must pass these gates before it can support promotion
or retirement decisions:

- PIT universe method and universe snapshot hash present.
- Holdout excluded unless a single owner-approved holdout run is recorded.
- Pre-registration complete before first decision-grade run.
- Observation-window gates satisfied for 20/40/60-day readiness where relevant.
- Promotion readiness blocks on performance, differentiation, risk, universe,
  and execution-timing gates unless every gate passes.
- Differentiation evidence includes both static overlap/factor analysis and
  behavioral return-stream analysis.
- Legacy current-universe artifacts are explicitly demoted to
  `non_decision_grade`.

### Read-Only MCP Requirements

Future FR-069 MCP surfaces should be read-only and answer:

- What sleeves exist and what lifecycle state are they in?
- Which sleeves have PIT decision-grade evidence?
- Which artifacts are stale, partial, blocked, or legacy current-universe only?
- Which sleeves are promotion-ready, blocked, or under observation?
- How do Orion and Lyra differ by holdings, active share, returns, attribution,
  and regime behavior?
- Which owner decisions are required before implementation, promotion, or
  retirement?

## 4. Future Sleeve Inventory

| Sleeve / Overlay | Current role | Phase A classification | Required onboarding work |
|---|---|---|---|
| Polaris | Current paper baseline | Reference security-selection sleeve | Preserve behavior; define parity gate against FR-068 PIT rebaseline before any generalized harness migration. |
| Orion | Shadow challenger | Core-momentum security-selection sleeve | Preserve shadow behavior; PIT rebaseline and differentiation evidence required before any disposition decision. |
| Lyra | Shadow challenger | Core-momentum security-selection sleeve | Preserve shadow behavior; PIT rebaseline and differentiation evidence required before any disposition decision. |
| Phoenix | Research crisis-reversal candidate | Crisis-reversal security-selection sleeve | Define activation data, regime inputs, artifact envelope, passive evidence, and drawdown/recovery criteria. |
| Cygnus | Shelved v0 earnings/post-earnings-drift research | Event-driven sleeve, vendor-gated for v1 | Preserve v0 shelved verdict; define consensus/EPS-surprise dependency before any v1 evidence run. |
| Cassiopeia | Canonical event-driven spec-only strategy | Event-driven spec placeholder | Preserve canonical FR-052 role; no implementation until event data contract and pre-registration are approved. |
| Argo | Research regime/model-selection layer | L5 meta overlay, not a sleeve | Consume PIT-method sleeve evidence only; no production switching without later approval. |
| Vela | Proposed small-cap momentum sleeve | Future small-cap security-selection sleeve | Requires `small_cap_band` family, benchmark policy, capacity/liquidity evidence, and PIT price coverage. |

## 5. Recommended Phase B Implementation Roadmap

Phase B should remain research-only unless a later task explicitly authorizes a
runtime behavior change.

1. **Phase B1 — Research-only sleeve manifest**
   - Add a non-production manifest and validator for sleeve metadata.
   - Tests: manifest schema, invalid lifecycle states, missing PIT fields,
     strategy-registry link checks.
   - No production registry behavior changes.

2. **Phase B2 — Artifact envelope validator**
   - Add validation for required sleeve evidence fields and PIT lineage.
   - Tests: valid envelope, legacy current-universe demotion, missing holdout
     flag, missing universe hash.
   - No research math changes.

3. **Phase B3 — Read-only MCP sleeve inventory**
   - Add MCP tools for sleeve inventory, evidence status, and owner-decision
     blockers.
   - Tests: tool registration, read-only behavior, deterministic blocked states,
     and no mutation of strategy manifest or source artifacts.
   - No execution or allocation coupling.

4. **Phase B4 — Polaris parity harness design**
   - Build a research-only generalized harness around Polaris as reference.
   - Acceptance: reproduce FR-068 Polaris PIT rebaseline within tolerance before
     touching Orion/Lyra.
   - No paper/live behavior change.

5. **Phase B5 — Orion/Lyra PIT evidence packet**
   - Run PIT rebaselines and differentiation analysis under the shared envelope.
   - Output informs future disposition but does not approve retirement.
   - No shadow/paper behavior change.

6. **Phase B6 — Phoenix/Cygnus/Cassiopeia/Argo onboarding specs**
   - Convert each future sleeve/overlay into a manifest-ready spec with data
     dependencies, gates, and owner decisions.
   - No implementation until each spec has its own approved FR.

## 6. Phase A Completion Checklist

- Canonical Sleeve Protocol documented.
- Registry-Onboarding Architecture documented.
- Research Lab Operating Model documented.
- Future Sleeve Inventory documented.
- Recommended Phase B implementation roadmap documented.
- FR-070 remains observation/monitoring.
- Orion, Lyra, and FR-063 remain active/deferred inputs, not retired.
- No production code or runtime behavior changed.
