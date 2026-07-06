# Portfolio Construction Doctrine Draft

Audit date: 2026-06-26

Scope: Draft only. This file does not create or modify `docs/governance/portfolio_construction_doctrine.md`. It proposes text for Brett review before a governance-doc patch.

## Summary

Caerus' existing high-level investment doctrine says the system is not designed to be a balanced portfolio and should allocate toward highest-conviction opportunities. Current implementation evidence shows the production-shaped constructor still behaves as a sleeve-merge architecture, not a global Alpha Chase constructor.

This draft doctrine separates governance intent from implementation status. It defines what Caerus should optimize for, when concentration is desirable, which guardrails are non-negotiable, and what evidence is required before Alpha Chase can move from research to shadow, paper, live-pilot, or production influence.

## Evidence Reviewed

- `docs/governance/caerus_investment_doctrine.md`
- `docs/governance/caerus_phase2_alpha_strategy.md`
- `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md`
- Prior CIO Alpha Concentration audit.
- Existing FR-105 research modules and sparse local FR-105 outputs.

## Files/Modules Inspected

| File/module | Relevance |
| --- | --- |
| `docs/governance/caerus_investment_doctrine.md` | Defines maximum long-term capital appreciation and permits concentration. |
| `docs/governance/caerus_phase2_alpha_strategy.md` | Defines alpha evidence and promotion standard. |
| `docs/governance/fr_active/fr_105_global_portfolio_optimizer_and_decision_provenance.md` | Defines FR-105 research-only Alpha Chase/provenance path. |
| `research/fr105_*` | Evidence that current global optimizer work is research-only. |

## Proposed Governance Document

Target path, not created in this pass:

`docs/governance/portfolio_construction_doctrine.md`

### 1. Purpose

This doctrine defines how Caerus should allocate capital among candidate investments once research, data, and governance evidence are sufficient. It governs portfolio-construction research, shadow comparisons, paper promotion, live-pilot influence, and future production portfolio-construction changes.

### 2. Optimization Objective

Caerus optimizes for long-term absolute capital appreciation.

The portfolio-construction objective is:

- maximize expected alpha and conviction-weighted opportunity;
- preserve the ability to compound capital through non-negotiable risk controls;
- avoid diversification that only exists to create institutional-looking breadth;
- avoid concentration that is not supported by source-labeled evidence.

Sharpe, volatility, drawdown, diversification, and benchmark-relative metrics are controls and diagnostics. They are not the primary objective unless Brett explicitly changes the doctrine.

### 3. Concentration Principle

Concentration is desirable when:

- the top candidates have source-backed score or expected-alpha separation;
- the expected alpha gain is large enough to justify turnover, liquidity, and drawdown risk;
- guardrails pass;
- the candidate universe is PIT-safe and not survivorship-biased;
- current-policy breadth is shown to dilute expected return;
- shadow evidence supports the construction method over a sufficient observation window.

Concentration is not desirable when:

- scores are tightly clustered or mostly unavailable;
- rank or score is inferred from target weights or allocation weights;
- sector/liquidity/turnover risk would dominate the expected alpha benefit;
- artifact lineage is stale or missing;
- concentration is being used to compensate for execution or reporting uncertainty.

### 4. Minimum Diversification Guardrails

Non-negotiable guardrails for shadow research:

- max single-name weight;
- effective-N floor;
- sector exposure cap or explicit sector exposure warning;
- liquidity and capacity checks;
- turnover cap and cost sensitivity;
- cash target/cash drag visibility;
- min-notional viability;
- data completeness threshold;
- no duplicate ticker exposure across sleeves without explicit consolidation;
- no look-ahead data in selection.

Paper/live guardrails must be stricter than or equal to shadow guardrails unless Brett approves an exception.

### 5. Sleeve Contribution To Global Allocation

Sleeves should contribute candidates to a global opportunity set, not permanently reserve capital by default.

Acceptable sleeve roles:

- source of candidate ideas;
- source of score/rank/expected-alpha evidence;
- source of differentiated risk/return drivers;
- source of regime-specific context;
- source of guardrail or exclusion evidence.

Sleeves should not receive capital simply because they exist. A sleeve may receive zero allocation if its candidates do not rank highly enough or if its data quality is insufficient.

### 6. Conviction Versus Breadth

Conviction may override breadth when:

- source-labeled score/rank evidence is strong;
- marginal contribution of lower-ranked holdings is weak or negative;
- top-N frontier improves expected value after turnover and risk controls;
- suppressed high-ranked candidates are explained and tracked;
- the resulting portfolio still passes guardrails.

Breadth may override conviction when:

- candidate scores are sparse or unreliable;
- concentration breaches risk controls;
- turnover/liquidity/capacity would make the portfolio impractical;
- shadow evidence has not proven improved outcomes;
- current sleeves provide proven differentiated risk control.

### 7. Score Provenance Rule

Alpha scores must be artifact-backed.

Allowed score fields:

- `conviction_score`;
- `score` with a source label;
- `expected_alpha` with a source label;
- explicit global rank from a PIT-safe ranking artifact.

Prohibited score sources:

- `target_weight`;
- `allocation_weight`;
- `final_target_weight`;
- `final_allocation_weight`;
- fallback target/allocation weights;
- post-decision returns.

Missing scores must render as `unavailable`.

### 8. Evidence Required Before Promotion

Research to shadow:

- Phase 0/1 artifact completeness passes.
- Candidate universe and score provenance are PIT-safe.
- Current-policy baseline is reproducible.
- Shadow artifact is deterministic and default-off.

Shadow to paper:

- Sufficient observation window.
- Current versus Alpha Chase comparison is stable.
- Turnover, liquidity, drawdown, and concentration guardrails pass.
- No unresolved artifact lineage gaps.
- Brett approves paper-only promotion.

Paper to live-pilot:

- Paper evidence passes.
- FR-104 live-pilot controls are satisfied.
- Exact order plan is reviewed.
- Broker/reconciliation rollback path is documented.
- Brett explicitly approves live-pilot influence.

Live-pilot to production:

- Multi-day/month evidence is retained.
- Execution residuals are understood.
- No silent promotion or config change.
- Rollback and kill switch are tested.
- Brett explicitly approves production influence.

### 9. Reporting Requirements

Every Alpha Chase report must show:

- current sleeve-merge baseline;
- Alpha Chase shadow variant;
- optional core-satellite variant only if approved;
- candidate score source;
- current and target weights;
- concentration metrics;
- guardrail status;
- constraints and suppression reasons;
- source artifact paths;
- unavailable fields.

### 10. Non-Goals

This doctrine does not approve:

- changing current production allocator behavior;
- changing paper/live target weights;
- changing sizing;
- changing broker submission;
- changing live-pilot order type or selected order;
- automatic promotion from shadow to paper/live;
- using target weights or allocation weights as alpha scores.

## Findings

### Finding 1: Doctrine supports Alpha Chase intent but implementation does not yet

Severity: High

The investment doctrine permits concentration, but the current implemented path is still sleeve-merge. Governance should say this plainly.

Proposed fix: Create the doctrine doc after Brett approves the draft.

Risk classification: Governance documentation only.

### Finding 2: Core-satellite requires explicit approval

Severity: Medium

Core-satellite is a plausible transition architecture, but it can also dilute a true Alpha Chase mandate.

Proposed fix: Keep core-satellite optional in shadow design until Brett chooses.

Risk classification: Governance/design only.

## Validation Required

- Markdown review.
- Governance consistency review against `caerus_investment_doctrine.md`, `caerus_phase2_alpha_strategy.md`, and FR-105.
- No runtime tests required for the doctrine itself.
- Before implementation, add artifact tests that enforce the score provenance rule.

## Open Questions For Brett Approval

1. Should the primary portfolio-construction objective explicitly prioritize absolute return over Sharpe and diversification?
2. What maximum single-name weight should shadow Alpha Chase use first: 20%, 25%, or another cap?
3. What effective-N floor is non-negotiable?
4. Should sector caps be hard constraints or warning-only in shadow?
5. Should core-satellite be part of the first shadow comparison?
6. What score field is authoritative for Alpha Chase selection?
7. What observation window is required before any paper-only promotion?
