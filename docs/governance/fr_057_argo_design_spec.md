<!--
=============================================================================
CONFLICT / QUARANTINED — DO NOT TREAT AS A NEW ROADMAP ITEM
Disposition added 2026-06-08 by repo source-of-truth cleanup.

This design draft CONTRADICTS the canonical Argo research spec:
    docs/governance/fr_053_argo_research_spec.md  (FR-053, canonical)

CONFLICT: FR-053 (canonical) defines Argo as a REGIME ALLOCATION OVERLAY that
classifies market regimes and produces allocation recommendations and "is not
initially expected to select securities." config/research/strategy_registry.json
agrees (caerus_argo: type=overlay, family=regime_overlay).

This draft instead defines Argo as an EVENT-DRIVEN event sleeve. That is the
role canonically assigned to CASSIOPEIA (FR-052), not Argo. This file appears to
be misfiled work describing the Cassiopeia event-driven strategy under the wrong
name.

See docs/governance/CURRENT_RESEARCH_ROADMAP.md Section 4, Conflict A for the
full three-way taxonomy collision (Cassiopeia code = regime/meta-model selector;
event-driven role specified twice and implemented zero times).

Action: requires explicit owner decision. Do NOT use this file to drive code,
registry, strategy-ID, or roadmap changes. FR-053 Phase B validation must use
the canonical Argo regime-overlay spec, not this event-sleeve draft. No content
below altered.
=============================================================================
-->

# FR-057 Argo Event Sleeve Design Spec

Status: RETIRED (Option A, 2026-06-08) — event-driven content belongs to Cassiopeia (FR-052); Argo is the regime / model-selection layer (FR-053). Superseded; do not use.
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Objective

Argo is a design-only event-driven research sleeve. It must not submit orders,
change allocations, alter cron timing, or affect live/paper execution.

## Alpha Hypothesis

Certain discrete events can produce repeatable post-event drift or reversal when
the event data is captured with strong provenance and evaluated without
look-ahead. Candidate event families include earnings, guidance, corporate
actions, analyst revisions, unusual volume, and news shocks.

## Required Data

- PIT-safe event timestamps and source provenance.
- PIT-safe daily or intraday OHLCV panels.
- Security-master alias and tradability diagnostics.
- Sector and liquidity metadata.
- Corporate action handling.
- Event-source coverage diagnostics and licensing review.

## PIT-Safety Requirements

- Event timestamps must reflect when Caerus could have known the event.
- Preliminary, revised, and corrected event records must preserve revision
  history.
- Same-day scoring cannot use future close, later news, restatements, or
  post-event realized return.
- Events without provenance must be excluded or marked non-decision-grade.

## Regime Gating

Argo should report event behavior by market regime and volatility state. It
should not assume an earnings or news edge transfers unchanged across panic,
recovery, bull, bear, or high-volatility regimes.

## Candidate Scoring Concept

Candidate scoring may combine event surprise direction, magnitude, source
confidence, liquidity, gap behavior, post-event confirmation, and risk filters.
The design intentionally excludes threshold tuning until event data provenance
is complete.

## Exclusion Rules

- Exclude events with unknown publication time.
- Exclude events observed only after the trade date.
- Exclude symbols with unresolved security-master aliases.
- Exclude illiquid or stale-price candidates.
- Exclude event families with incomplete provenance diagnostics.

## Artifacts To Produce Before Implementation

- `argo_event_inventory.json`
- `argo_event_inventory.md`
- event-source provenance diagnostics
- candidate score diagnostics
- event attribution packet
- no-lookahead validation report
- shadow-only comparison packet

## Tests Required Before Implementation

- Event timestamp PIT enforcement.
- Missing provenance degradation.
- Deterministic ranking.
- No future-return leakage.
- Corporate action handling.
- Security-master alias blocking.
- No execution integration.

## Promotion Criteria

Argo may not be considered for promotion until event data provenance is audited,
coverage is sufficient across event families and regimes, attribution is clean,
and a governance packet shows decision-grade evidence distinct from existing
security-selection sleeves.

## Failure Modes

- Event timestamps are vendor-corrected after the fact.
- Survivorship bias enters through event coverage.
- Measured returns are driven by a small number of events.
- Corporate actions contaminate event returns.
- Event source gaps make the sleeve non-repeatable.

## Explicit Boundary

This document is design-only. It creates no strategy implementation, no scoring
logic, no registry promotion, no shadow allocation, and no live/paper execution
behavior.
