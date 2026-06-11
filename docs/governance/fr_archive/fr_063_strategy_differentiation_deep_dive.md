# FR-063 Strategy Differentiation Deep Dive

Status: ACTIVE_RESEARCH
Owner: Caerus Research Program
Last Updated: 2026-06-08
Governance Label: RESEARCH_ONLY
Execution Impact: NON_EXECUTIONAL

## Purpose

FR-063 evaluates whether Caerus strategies are meaningfully distinct or
redundant. The first research wave focuses on Polaris, Orion, Lyra, Phoenix, and
registered research strategies when evidence exists. Lyra versus Orion is the
highest-priority pair because both live in the core-momentum family.

## Required Evidence

The deep dive should use only existing dated artifacts and must degrade visibly
when evidence is missing:

- shadow strategy snapshots and NAV series
- model tournament and promotion-readiness artifacts
- position, decision, and factor attribution artifacts
- risk, concentration, turnover, and sector summaries
- strategy registry metadata
- regime attribution where available

## Artifact Contract

The model-quality artifact must include:

- pairwise holdings overlap
- pairwise active share
- pairwise sector difference
- pairwise turnover difference
- pairwise concentration difference
- pairwise return correlation, when available
- pairwise attribution spread
- regime-specific behavior
- redundancy classification:
  `DISTINCT`, `PARTIALLY_OVERLAPPING`, `NEAR_DUPLICATE`, or
  `INSUFFICIENT_EVIDENCE`
- retirement watchlist entries with strategy ID, reason, confidence,
  decision-grade flag, and reason codes

## Decision Policy

This FR may put a strategy on a watchlist. It must not recommend actual
retirement unless decision-grade evidence exists. Sparse history, missing
attribution, missing return streams, or missing snapshots force
`decision_grade: false`.

## Non-Goals

- No strategy retirement.
- No strategy promotion or demotion.
- No paper/live trading behavior change.
- No broker submission change.
- No cron timing change.
- No production order generation change.
