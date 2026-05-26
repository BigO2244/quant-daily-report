# Daily Research Interpretation Packet

## Purpose

The Daily Research Interpretation Packet is FR-030. It is the first
operator-facing consumption layer for Caerus research telemetry.

The packet turns provenance, holdings lineage, exposure intelligence, and regime
fragility artifacts into a concise daily synthesis. It is advisory, research
only, and confidence-aware.

It does not change execution behavior, accounting semantics, timing semantics,
broker behavior, dashboard behavior, promotion logic, cron, or workflow state.

Deployment state: deployed/current. The packet is a telemetry consumption layer,
not a promotion gate or execution recommendation layer.

## Packet Philosophy

The packet should answer what matters today before showing telemetry detail.

It should optimize for:

- fast operator interpretation;
- explicit provenance;
- explicit freshness;
- explicit confidence caveats;
- concise risk language;
- separation of evidence from claims.

It should not optimize for:

- exhaustive artifact dumping;
- false precision;
- promotion decisions;
- execution recommendations;
- autonomous interpretation.

## Daily Questions

Each packet should help answer:

- What changed today?
- What matters today?
- What looks fragile?
- What confidence should we have?
- What exposures are emerging?
- Why did a strategy outperform?
- Is outperformance concentration-driven?
- Are regime conditions favorable?
- Is telemetry trustworthy and fresh?

## Source Readiness Boundary

The current operational bottleneck is post-close source readiness, not packet
rendering. If shadow artifacts are stale, `NO_DATA`, or generated before price
hydration is complete, the packet must clearly downgrade interpretation.

Incomplete-source packets should say that strategy ordering is context only and
not analytically meaningful. Operators should wait for post-close hydration and
shadow artifact refresh before drawing exposure-adjusted conclusions.

## Interpretation Hierarchy

Read the packet in this order:

1. Executive Summary
2. Operational Trust Summary
3. Strategy Comparison Summary
4. Exposure + Concentration Review
5. Regime Interpretation
6. Fragility Observations
7. Confidence + Freshness Caveats
8. What Changed Today
9. Key Risks
10. Research Follow-Ups

This ordering is intentional. Confidence and provenance frame interpretation
before the operator draws conclusions from returns.

## Advisory Semantics

The packet is not an execution instruction. It must not:

- recommend trades;
- promote Orion or Lyra;
- alter scorecards;
- reinterpret historical NAV chains;
- claim timing-corrected performance;
- substitute shadow NAV for broker NAV.

The packet can:

- summarize research telemetry;
- identify concentration and fragility;
- surface stale or missing evidence;
- explain confidence limits;
- prepare questions for operator review.

## Confidence Caveats

Operational shadow NAV remains LOW confidence until FR-028 timing semantics are
governed. The packet must preserve this caveat even when Orion or Lyra
outperform.

Confidence should be lowered when:

- expected input artifacts are missing;
- freshness is unknown;
- source surfaces are advisory;
- regime evidence is incomplete;
- attribution uses proxy measures rather than realized position returns;
- concentration explains a large share of outperformance.

## Freshness Interpretation

Fresh evidence means the artifact trade date matches the packet trade date.

Stale or unknown freshness does not always make evidence useless, but it changes
how strongly the operator should rely on it. Latest-style publications are
convenience surfaces and must be checked against dated source artifacts before
they are treated as evidence.

## Provenance Interpretation

Every performance or risk statement should preserve its truth surface:

- broker NAV is broker-authoritative when reconciled;
- operational shadow NAV is model-portfolio evidence;
- research backtest NAV is synthetic research evidence;
- latest publications are convenience pointers.

The packet should never blend these into one performance narrative.

## Regime Interpretation

Regime language is advisory. The packet should identify whether regime evidence
is present, missing, or low confidence.

The operator should treat regime-positive outperformance differently from
regime-independent robustness. Strong performance in one regime may indicate
fragility rather than durable alpha.

## Concentration Interpretation

High position, top-three, or sector concentration can amplify reported returns.
The packet should distinguish:

- selection evidence;
- concentration amplification;
- momentum sensitivity;
- sector exposure;
- regime dependency.

Outperformance with high concentration deserves follow-up before it is treated
as durable.

## What Must Not Appear

The packet must not include:

- unsupported attribution claims;
- promotion readiness claims;
- execution recommendations;
- timing-corrected performance claims before FR-028;
- hidden upgrades from LOW confidence to MEDIUM or HIGH;
- stale latest artifacts presented as canonical evidence.

## Delivery Preparation

FR-030 prepares three delivery surfaces:

- `packet.md` for operator review;
- `packet.json` for structured telemetry consumption;
- `packet.html` for future email-ready rendering;
- `summary.json` for future dashboard-compatible summaries.

Delivery automation is not part of FR-030. Future email, dashboard, and MCP
integration should consume these artifacts as read-only evidence.

Orion.command / `scripts/open_shadow_comparison_latest.command` is the current
operator launcher for the FR-030 workflow. It is operational tooling only: it
builds/retrieves packet evidence and surfaces readiness warnings, but it does
not trigger execution, hydration, scheduling, promotion, or broker activity.
