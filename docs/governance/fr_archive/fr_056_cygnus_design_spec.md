<!--
=============================================================================
SUPERSEDED / DUPLICATE — DO NOT TREAT AS A NEW ROADMAP ITEM
Disposition added 2026-06-08 by repo source-of-truth cleanup.

This design draft duplicates the canonical Cygnus research spec:
    fr_051_cygnus_research_spec.md  (FR-051, canonical)

DEFINITION DRIFT TO RESOLVE: this draft describes Cygnus as a generic
"persistent, slow-moving factor / price drift" sleeve, whereas the canonical
FR-051 spec and config/research/strategy_registry.json define Cygnus as an
EARNINGS / post-earnings DRIFT strategy (family = earnings_drift).

Action: fold any needed content into FR-051 and retire this file, OR record an
explicit decision to broaden Cygnus, in docs/governance/CURRENT_RESEARCH_ROADMAP.md
(Section 4, Conflict B). Pending that decision this file is non-canonical and
must not drive code, registry, or roadmap changes. FR-064 multi-asset framework
does not revive this draft. No content below altered.
=============================================================================
-->

# FR-056 Cygnus Drift Sleeve Design Spec

Status: Design Only — SUPERSEDED BY FR-051 (see header)
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Objective

Cygnus is a design-only research sleeve for persistent, slow-moving factor or
price drift. It must not submit orders, change allocations, alter cron timing,
or affect live/paper execution.

## Alpha Hypothesis

Some liquid equities underreact to durable medium-term information. A lower
turnover drift sleeve may capture continuation that is slower than Lyra/Orion
momentum selection and less crisis-specific than Phoenix.

## Required Data

- PIT-safe daily OHLCV panels.
- PIT-safe universe membership and symbol resolution.
- Sector and liquidity metadata.
- Existing shadow snapshots for comparison against Polaris, Orion, Lyra, and
  Phoenix.
- Regime labels available as of the signal date.

## PIT-Safety Requirements

- Signals use only rows available on or before the research date.
- Universe membership must be point-in-time or explicitly governance-blocked.
- Realized returns are evaluation-only and may not be used in same-date
  scoring.
- Aliases must resolve through audited security-master or ticker-exception
  artifacts.

## Regime Gating

Cygnus should be regime-aware but not crisis-specific. Initial research should
separate bull, neutral, bear, high-volatility, panic, and recovery regimes, then
require evidence that drift behavior is not a single-regime artifact.

## Candidate Scoring Concept

Candidate scoring may combine medium-term price drift, stability of trend,
liquidity, volatility normalization, and sector diversification. The design
intentionally avoids optimizing thresholds before governance evidence exists.

## Exclusion Rules

- Exclude names with missing current price, stale price coverage, or unresolved
  security-master aliases.
- Exclude names that fail minimum liquidity or price filters.
- Exclude structurally broken names only when supported by PIT-safe data.
- Exclude any candidate that would require an unresolved execution symbol.

## Artifacts To Produce Before Implementation

- `cygnus_research.json`
- `cygnus_research.md`
- candidate score diagnostics
- data coverage diagnostics
- no-lookahead validation report
- shadow-only comparison packet

## Tests Required Before Implementation

- Deterministic ranking.
- No look-ahead date usage.
- Missing price data degradation.
- Missing sector/security-master degradation.
- Regime bucket separation.
- No execution integration.

## Promotion Criteria

Cygnus may not be considered for promotion until it has independent shadow
history, clean attribution, sufficient regime coverage, acceptable turnover,
documented differentiation versus Lyra/Orion, and a governance packet showing
decision-grade evidence.

## Failure Modes

- Trend signal collapses into Lyra/Orion duplication.
- Medium-term drift is concentrated in a few symbols or sectors.
- PIT universe gaps create survivorship bias.
- Turnover or stale data makes measured edge non-repeatable.
- Security-master alias gaps block safe execution review.

## Explicit Boundary

This document is design-only. It creates no strategy implementation, no scoring
logic, no registry promotion, no shadow allocation, and no live/paper execution
behavior.
