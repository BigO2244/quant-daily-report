# Portfolio Construction Doctrine

Status: Draft for governed use
Owner: Caerus Research Program
Runtime impact: documentation only
Related governance: `docs/governance/caerus_investment_doctrine.md`, `docs/governance/decision_records/ADR-001_portfolio_construction_strategy.md`

## Purpose

This document defines how Caerus should reason about portfolio construction,
concentration, diversification, and promotion evidence.

It does not approve optimizer, sizing, broker, execution, paper, live-pilot,
cron, or production allocation changes. It defines the evidence standard that
must exist before those changes can be considered.

## Investment Objective

Caerus exists to maximize long-term capital appreciation through systematic,
artifact-backed investment decisions.

Portfolio construction should therefore prioritize:

- expected alpha and conviction;
- evidence quality;
- risk controls that preserve compounding ability;
- capital allocation toward the best available opportunity set.

It should not prioritize diversification, benchmark resemblance, sleeve parity,
or low turnover for their own sake.

## Relationship To Investment Doctrine

`docs/governance/caerus_investment_doctrine.md` is the high-level doctrine.
This document is a portfolio-construction layer beneath it.

If these documents conflict, the high-level investment doctrine controls unless
Brett explicitly amends it.

## Current Sleeve-Merge Philosophy

The current Caerus production-shaped architecture is sleeve-merge:

```text
sleeve-local candidates
        |
        v
sleeve-local ranks / targets
        |
        v
regime and sleeve allocation
        |
        v
portfolio allocator and constraints
        |
        v
targets, trades, execution artifacts
```

This architecture is valid as the current baseline. It allows multiple
researched sleeves to contribute candidates while the system is still building
evidence about which return streams deserve more capital.

It also naturally produces broader portfolios because capital can be preserved
inside sleeve-local books before candidates are compared globally.

## Future Alpha Chase Philosophy

Alpha Chase is the future research hypothesis that Caerus may improve as an
investor by ranking candidates globally and concentrating capital into the best
artifact-backed opportunities.

Alpha Chase is not currently approved for trading influence.

The future Alpha Chase research flow is:

```text
PIT universe + source-labeled candidates
        |
        v
global candidate ranking
        |
        v
shadow-only portfolio comparison
        |
        v
governed evidence review
        |
        v
explicit promotion decision
```

Alpha Chase must remain default-off until promotion gates are satisfied.

## Concentration Philosophy

Concentration is desirable when:

- top candidates have source-backed score separation;
- expected alpha improvement justifies turnover and liquidity cost;
- concentration guardrails pass;
- lower-ranked holdings appear to dilute expected return;
- the evidence is point-in-time safe;
- shadow evidence supports the change.

Concentration is not desirable when:

- scores are unavailable or tightly clustered;
- score sources are not explicit;
- alpha is inferred from target weights or allocation weights;
- sector, liquidity, turnover, or drawdown risk dominates expected benefit;
- source artifacts are stale, missing, or incomplete.

## Diversification Philosophy

Diversification is a risk control, not an objective by itself.

Diversification should be used to:

- prevent one-name fragility;
- avoid accidental sector or factor concentration;
- preserve liquidity and execution viability;
- reduce unrewarded operational and data risk.

Diversification should not be used to force capital into low-conviction names.

## When Conviction Overrides Diversification

Conviction may override breadth only when all of the following are true:

- candidate score/rank evidence is source-labeled and PIT-safe;
- missing fields are explicitly marked `UNAVAILABLE`;
- target weights and allocation weights are not used as alpha scores;
- guardrails pass;
- current-policy baseline is reproducible;
- shadow comparison shows a plausible improvement;
- Brett approves the promotion stage.

## Required Risk Controls

The following controls are non-negotiable for Alpha Chase evaluation:

- max single-name weight;
- effective-N floor;
- sector exposure reporting and caps or warnings;
- liquidity and capacity checks;
- turnover cap and transaction-cost sensitivity;
- cash target and cash-drag visibility;
- min-notional viability;
- no duplicate ticker exposure unless deliberately consolidated;
- no look-ahead data in selection;
- explicit source artifact paths for claims.

## Promotion Requirements

Research to shadow requires:

- Phase 0/1 FR-105 artifact completeness;
- PIT universe and score provenance;
- current-policy baseline reproduction;
- deterministic shadow artifacts.

Shadow to paper requires:

- retained observation evidence;
- current-vs-shadow comparison;
- turnover, liquidity, drawdown, and concentration review;
- Brett approval.

Paper to live-pilot requires:

- paper evidence;
- FR-104 live-pilot controls;
- exact reviewed order plan;
- rollback and reconciliation path;
- Brett approval.

Live-pilot to production requires:

- retained multi-period evidence;
- execution residual review;
- no unresolved artifact gaps;
- rollback plan;
- Brett approval.

## Default Governance Position

Default position:

- Current sleeve-merge remains the trading baseline.
- Alpha Chase is research/shadow only.
- Alpha Chase is disabled by default.
- No paper/live/trading influence is permitted without explicit promotion.
- Missing evidence must be reported, not worked around.

## Reporting Requirement

Every portfolio-construction report must identify:

- source artifacts;
- unavailable fields;
- score provenance;
- constraints;
- suppression reasons;
- current-vs-target weights;
- whether the output is trading, paper, shadow, or research only.

No report may describe target weights or allocation weights as alpha scores.

## Open Questions

- What max single-name weight should be used for the first Alpha Chase shadow comparison?
- What effective-N floor is required?
- Should sector exposure begin as hard cap or warning-only in shadow?
- Should the first Alpha Chase variant be equal-weight, score-weighted, or Phase 3 selected?
- Should a core-satellite variant be evaluated in the first shadow framework?
