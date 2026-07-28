# ADR-001: Portfolio Construction Strategy

Status: Accepted baseline; temporary paper recovery observation approved 2026-07-28
Date: 2026-06-26
Owner: Caerus Research Program
Runtime impact: documentation only

## Context

Caerus currently constructs portfolios by merging sleeve-local candidate books.
This architecture helped Phase 1 make the platform trustworthy: execution,
reconciliation, observability, and governance are now materially stronger.

Phase 2 shifts the program toward becoming a better investor. Engineering work
should now improve investment decisions or improve the evidence needed to
change those decisions.

## Current Architecture

Current production-shaped construction:

```text
sleeve candidates -> sleeve weights -> regime allocation -> allocator
-> targets -> trade plan -> execution artifacts
```

The system applies portfolio and execution constraints after sleeve-level
candidate generation. This can preserve breadth because candidates are selected
locally before being compared globally.

## Why The Current Architecture Exists

The sleeve-merge architecture exists because it:

- supports independent sleeve research and promotion;
- keeps current trading behavior stable;
- makes it possible to compare differentiated return streams;
- limits premature concentration before evidence is complete;
- fits the current governance lifecycle.

## Observed Limitations

The architecture may:

- keep too many low-conviction names;
- allocate capital by sleeve membership rather than global opportunity rank;
- make holding count an emergent result instead of an explicit research output;
- obscure why a higher-ranked candidate was not held;
- make CIO reporting depend on post-hoc reconstruction rather than first-class
  construction provenance.

## Evidence Gathered

Recent review work found:

- current construction is sleeve-merge, not active global Alpha Chase;
- local FR-105 generated artifacts are sparse and not yet decision-grade;
- construction provenance and candidate lifecycle artifacts can explain current
  decisions when source artifacts are present;
- missing artifacts must degrade to `UNAVAILABLE` or `MISSING`;
- target weights and allocation weights must not be treated as alpha scores.

## Decision

Keep sleeve-merge as the current trading baseline.

Evaluate Alpha Chase through FR-105 research and shadow artifacts only. Do not
change optimizer, sizing, broker, execution, paper, live-pilot, cron, or
production allocation behavior.

## Alpha Chase Evaluation Rationale

Alpha Chase is worth evaluating because Caerus' investment objective prioritizes
absolute return and capital flow toward the best available opportunities.

The evaluation must prove whether global concentration improves the current
portfolio after accounting for:

- score provenance;
- PIT universe lineage;
- turnover;
- liquidity;
- sector exposure;
- cash drag;
- concentration risk;
- suppression and execution residuals.

## Success Metrics

Alpha Chase evaluation succeeds only if artifacts can answer:

- Do we have every artifact needed to evaluate Alpha Chase?
- Can we reproduce every portfolio construction decision?
- Why was every candidate selected, retained, reduced, removed, skipped, or suppressed?
- How does current sleeve-merge compare with future Alpha Chase without affecting trading?
- Can an investment committee review evidence before any capital is placed at risk?

## Promotion Gates

Research to shadow:

- Phase 0/1 artifact completeness passes.
- Score and universe lineage are PIT-safe.
- Current-policy baseline is reproducible.

Shadow to paper:

- Shadow evidence is retained and reviewed.
- Guardrails pass.
- Brett approves paper-only promotion.

Paper to live-pilot:

- Paper evidence passes.
- Exact order plan is reviewed.
- Brett approves live-pilot influence.

Live-pilot to production:

- Multi-period evidence passes.
- Execution residuals are understood.
- Rollback is documented.
- Brett approves production influence.

## Risks

- Score provenance may be sparse or ambiguous.
- Concentration may increase drawdown, turnover, liquidity pressure, or sector exposure.
- A shadow artifact could be misread as a trading recommendation.
- Stale artifacts could produce misleading CIO reporting.
- Accidental staging of execution files could alter live or paper behavior.

## Rejected Alternatives

### Replace the allocator now

Rejected. Current artifacts are sparse and no Alpha Chase evidence has passed
shadow review.

### Change paper configuration to force concentration

Rejected. This would be paper-impacting and would not answer whether global
ranking improves investment quality.

### Let execution rebudgeting chase alpha

Rejected. Execution should preserve the reviewed plan and expose residuals; it
should not become an implicit optimizer.

### Use target weights as alpha scores

Rejected. Target weights and allocation weights are construction outputs, not
source alpha evidence.

## Consequences

- Current sleeve-merge remains active.
- FR-105 artifact completeness becomes the next implementation step.
- Shadow Alpha Chase remains disabled by default.
- Any future behavior change requires explicit governance approval.

## 2026-07-28 Temporary Paper Recovery Addendum

Brett approved execution of the drawdown recovery sequence on 2026-07-28 after
the broker-truth ledgers showed a paper max drawdown of 6.205% through 2026-07-27
and a live max drawdown of 9.020% through 2026-07-27.

This approval creates a bounded exception to the earlier prohibition on paper
allocation changes. It does not replace the sleeve-merge baseline and does not
promote Alpha Chase, Orion, or any other strategy.

The approved paper observation policy is `weekly_rotation_guard_v1`:

- use the first retained target decision in each ISO week;
- measure the relative 10-session performance of QQQ, SMH, and MTUM versus SPY
  and RSP using strictly prior adjusted closes;
- retain full exposure when clear, use 50% exposure at the warning threshold,
  and 25% exposure at the lock threshold;
- route undeployed weight to cash;
- apply only in the paper execution lane;
- fail closed if the weekly source, factor history, approval config, or
  paper-lane identity cannot be proved.

The retained replay from 2026-05-12 through 2026-07-27 produced the following
10-basis-point-cost diagnostics:

| Policy | Total return | Max drawdown | Average one-way turnover |
|---|---:|---:|---:|
| Stored daily targets | -7.565% | -11.622% | 41.725% |
| Stored weekly targets | +1.425% | -4.399% | 10.073% |
| Weekly targets plus rotation guard | +2.405% | -4.263% | 9.479% |

The replay has 51 observations and zero missing price weight for the selected
candidate. It is explicitly non-decision-grade for any broad-versus-concentrated
claim because the post-2026-07-07 dynamic pre-concentration books were not
retained. Its scope is sufficient only to authorize forward paper observation,
not live influence.

Before any future live review, the policy requires at least:

- 20 forward paper sessions;
- 10 sessions with clean target-attainment evidence;
- a fresh owner decision.

Live rearm is not authorized. The live account remains flat, the kill switch
remains engaged, the live plan builder rejects the current target/Orion identity
mismatch, and live execution requires an explicit positive capital cap no
greater than $500.

The machine-readable approval and thresholds live in
`config/paper_recovery_policy.json`. Operating and rollback instructions live in
`docs/runbooks/drawdown_recovery_2026-07-28.md`.
