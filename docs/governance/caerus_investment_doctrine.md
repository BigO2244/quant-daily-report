# Caerus Investment Doctrine

## Purpose

This document defines the core investment philosophy, portfolio construction principles, strategy lifecycle, and governance framework for Caerus.

Its purpose is to ensure that future research, strategy development, promotion decisions, portfolio construction, and capital allocation remain aligned with the original objectives of the platform.

This document is intended to be the highest-level strategic reference for the Caerus research program.

If future FRs, research projects, or governance discussions conflict with this doctrine, the doctrine should be considered authoritative unless explicitly amended.

---

# 1. Primary Objective

## Mission

Caerus exists to maximize long-term capital appreciation through systematic, data-driven investment strategies.

The primary objective is:

> Maximum absolute return and long-term wealth creation.

Caerus is not designed to be a balanced portfolio.

Caerus is not designed to maximize Sharpe ratio.

Caerus is not designed to minimize volatility.

Caerus is not designed to mimic institutional benchmark construction.

The system exists to identify, evaluate, and allocate capital toward the highest-conviction opportunities available according to its research framework.

---

## Implications

The following are acceptable if they contribute to superior long-term returns:

- Concentration
- Turnover
- Volatility
- Temporary drawdowns
- Dynamic portfolio construction

The following are not objectives:

- Diversification for its own sake
- Benchmark tracking
- Sector neutrality
- Factor neutrality
- Maintaining constant exposure

Risk management exists to preserve the ability to compound capital, not to eliminate volatility.

---

# 2. Portfolio Construction Philosophy

## Portfolio of Alpha Sleeves

Caerus will evolve into a portfolio of specialized alpha-generating sleeves.

Examples include:

- Momentum
- Crisis
- Event
- Regime
- Drift

Additional sleeves may be introduced as research matures.

Each sleeve is:

- Independently researched
- Independently evaluated
- Independently promoted
- Independently retired

---

## Dynamic Allocation

Sleeves may receive any allocation between:

```text
0% → 100%
```

There is no requirement that every sleeve remain active.

Examples:

### Momentum Dominant

```text
Polaris:      100%
Phoenix:        0%
Argo:           0%
Cassiopeia:     0%
```

### Multi-Sleeve Portfolio

```text
Polaris:      40%
Phoenix:      30%
Argo:         20%
Cassiopeia:   10%
```

Both are valid outcomes.

The allocation should be determined by expected opportunity, not by diversification targets.

---

## Capital Allocation Principle

Capital should flow toward the highest expected-return opportunities.

There is no requirement that capital be evenly distributed.

A sleeve may receive:

- No allocation
- Partial allocation
- Full allocation

depending on market conditions and model conviction.

---

# 3. Regime Allocation Philosophy

## Long-Term Objective

Caerus should evolve toward fully automated allocation decisions.

The system should ultimately determine:

- Which sleeves are active
- Which sleeves are inactive
- How capital is allocated
- When allocations should change

The long-term target state is:

> Model-directed capital allocation with human-governed research oversight.

---

## Human Role

Humans are responsible for:

- Research direction
- Governance
- Promotion criteria
- Risk framework design
- Operational oversight

Humans should not be responsible for routine allocation decisions once sufficient evidence exists.

---

# 4. Strategy Lifecycle Framework

All strategies progress through the same lifecycle.

```text
Research
→ Shadow
→ Paper
→ Pilot Capital
→ Production
→ Retired
```

---

## Research

Strategy development and hypothesis testing.

Characteristics:

- Experimental
- Not capitalized
- Research only

---

## Shadow

Produces signals but receives no capital.

Purpose:

- Signal validation
- Operational validation
- Comparative analysis

---

## Paper

Receives simulated capital.

Purpose:

- Evaluate realistic implementation
- Measure execution behavior
- Observe operational characteristics

---

## Pilot Capital

Receives a small real-money allocation.

Purpose:

- Validate execution
- Validate broker behavior
- Validate operational processes
- Observe real-world performance

Pilot capital should be intentionally limited.

Pilot Capital includes a narrow evidence-collection mode before production-grade
certainty exists. A capped, manually approved pilot may collect forward
broker/operational evidence when its purpose is learning and validation, not
promotion, scaling, or production allocation.

Historical replay infrastructure gaps, including incomplete decision-grade
FR-068 replay, correctly block research conclusions and promotions. They should
not by themselves block a segregated, de minimis, explicitly approved pilot
whose purpose is to create forward evidence.

Pilot evidence collection must remain:

- capped;
- manual;
- reversible;
- broker-truth reconciled;
- artifact-isolated;
- non-cron by default;
- non-promotional until separate promotion gates pass.

Example range:

```text
$5,000 – $25,000
```

---

## Production

Receives normal capital allocation.

Characteristics:

- Approved for deployment
- Eligible for dynamic allocation
- Participates in portfolio construction

---

## Retired

No longer receives capital.

Retired strategies remain available for:

- Research
- Attribution
- Historical comparison
- Lessons learned

---

# 5. Strategy Promotion Philosophy

## Evidence-Based Promotion

Promotion decisions must be data driven.

Strategies are promoted because evidence supports promotion.

Strategies are not promoted because:

- They are newer
- They are more complex
- They are preferred by operators
- They have compelling narratives

---

## Evidence-Based Retirement

No strategy is protected from retirement.

Existing strategies may be:

- Replaced
- Reduced
- Retired

if superior alternatives emerge.

The data decides.

---

## Competitive Framework

All strategies compete for capital.

Promotion and retirement are expected outcomes of the research process.

---

# 6. Sleeve Count Objective

## Current Objective

Maintain approximately:

```text
3–5 active sleeves
```

This is expected to provide:

- Sufficient diversification of alpha sources
- Manageable governance complexity
- Clear attribution

---

## Long-Term Objective

Expand to:

```text
6+ sleeves
```

when supported by:

- Research quality
- Operational maturity
- Compute economics

Architecture should be designed to support expansion without major redesign.

---

# 7. Crisis Investing Philosophy (Phoenix)

## Strategic Purpose

Phoenix is intended to be a:

> Contrarian Crisis Sleeve

Its purpose is to exploit panic-driven market dislocations.

Phoenix exists to profit from fear-driven mispricing.

---

## Phoenix Is Not

Phoenix is not intended to:

- Hide from risk
- Act as a defensive allocation
- Serve as a low-volatility strategy
- Function as a capital-preservation sleeve

---

## Phoenix Objective

The objective is:

> Buy opportunities created by panic when expected future returns become unusually attractive.

This makes Phoenix complementary to momentum-based sleeves rather than duplicative.

---

# 8. Existing Strategy Philosophy

## Lyra and Orion

Owner amendment, 2026-08-08: Orion is approved as the PAPER-only operational
execution authority. The approval is implemented by wrapping the exact same-day
governed Orion shadow snapshot into immutable Decision, Risk, Execution, and
Audit packages. Decision alone authors targets; Risk may only constrain; Trader
may only consume the approved package; Auditor is read-only. The operating target
retains 5% cash and a 2% post-trade attainment tolerance. This amendment does not
authorize live capital, weaken the separate FR-104 gates, or erase outstanding
research-evidence limitations. Polaris remains the historical research control.

Lyra and Orion will not be selected based on preference.

Their future status will be determined through:

- Differentiation analysis
- Promotion readiness analysis
- Comparative performance
- Operational evidence

The data will determine which strategy survives.

---

## Polaris_Alpha and Orion_Alpha

Concentrated variants may be promoted to Shadow when research evidence supports
forward observation and when baseline sleeves remain preserved for comparison.

Polaris_Alpha and Orion_Alpha are Shadow-only concentration tests. They exist to
measure whether fewer holdings improve alpha capture and alpha per deployed
dollar without creating unacceptable drawdown, turnover, concentration, or cash
drag.

They do not replace Polaris or Orion. They receive no capital while in Shadow.
Their first review checkpoints are 20 and 60 trading days, and any promotion
beyond Shadow requires separate owner approval and decision-grade evidence.

---

## General Principle

This philosophy applies to all strategies.

No strategy receives permanent status.

Every strategy must continue earning its place in the portfolio.

---

# 9. Research Program Principles

## Research Before Capital

Research should precede deployment.

New ideas should demonstrate evidence before receiving capital.

---

## Architecture Before Optimization

The platform should prioritize:

1. Correctness
2. Observability
3. Governance
4. Scalability
5. Optimization

in that order.

---

## Evidence Over Narrative

Research conclusions should be based on:

- Data
- Testing
- Measurement
- Attribution

rather than stories, opinions, or market narratives.

---

# 10. Success Definition

Caerus is successful if it can:

1. Continuously generate new investment hypotheses.
2. Evaluate those hypotheses objectively.
3. Promote superior strategies.
4. Retire inferior strategies.
5. Allocate capital dynamically.
6. Compound capital at rates materially above passive alternatives over long periods of time.

The objective is not to build a static portfolio.

The objective is to build a continuously improving investment system.

---

# Doctrine Amendment Process

This doctrine is intended to be durable.

Changes should be rare and deliberate.

Any future amendment should:

1. Be documented explicitly.
2. Include rationale.
3. Include expected impact.
4. Be reviewed alongside the existing doctrine.
5. Be recorded in governance history.

Unless amended, this document remains the authoritative statement of Caerus investment philosophy.
