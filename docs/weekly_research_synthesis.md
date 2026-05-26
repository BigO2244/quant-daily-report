# Weekly Research Synthesis Packet

## Purpose

The Weekly Research Synthesis Packet is the planning boundary for turning daily
FR-030 packets into weekly CIO-style research interpretation.

This document is planning only. It does not implement a generator, alter FR-030,
change promotion logic, change execution behavior, change accounting or timing
semantics, hydrate data, or create dashboard automation.

## Objective

The weekly packet should answer:

- What changed across the week?
- Which observations repeated?
- Which strategy behaviors looked durable versus one-day noise?
- Which exposures, concentrations, or regime dependencies persisted?
- Which evidence is trustworthy enough to carry into governance review?
- Which questions should be queued for FR-028, FR-029, attribution research, or
  capital deployment readiness?

## Inputs

Future implementation should consume dated, already-generated evidence:

- FR-030 daily research packets;
- research clarity artifacts from FR-024 through FR-027;
- source-readiness and price-hydration diagnostics;
- exposure and concentration outputs;
- regime and fragility outputs;
- operator notes when available.

It should not query broker APIs, submit orders, refresh hydration, trigger cron,
or reinterpret historical NAV chains.

## Interpretation Hierarchy

1. Source readiness and freshness quality.
2. Confidence floor and unresolved timing caveats.
3. Strategy ranking persistence.
4. Concentration and sector exposure persistence.
5. Turnover and composition stability.
6. Regime dependency and fragility.
7. Research follow-ups and governance questions.

Evidence quality should frame all performance discussion. A strong weekly return
with stale or incomplete evidence should remain a research question, not a
promotion claim.

## Weekly Sections

| Section | Question Answered | Notes |
|---|---|---|
| Executive Summary | What mattered this week? | Concise, operator-readable, no promotion claims. |
| Evidence Quality | Were daily packets fresh and complete? | Count READY, PARTIAL, and incomplete days. |
| Strategy Persistence | Did Polaris, Orion, or Lyra consistently lead? | Separate daily return ranking from cumulative NAV context. |
| Exposure Persistence | Were gains tied to concentration or sector exposure? | Highlight repeated top-three or sector concentration. |
| Research Intelligence | What changed in holdings, weights, turnover, and composition? | Use daily Research Intelligence sections as inputs. |
| Regime Context | Which regimes helped or hurt each strategy? | Advisory only until regime evidence is stronger. |
| Fragility Review | Which flags repeated? | Repeated flags matter more than isolated one-day observations. |
| Governance Queue | What should be reviewed next? | FR-028/FR-029 questions, attribution gaps, data-quality concerns. |

## Confidence Rules

- Operational shadow NAV remains LOW confidence until FR-028 resolves timing
  semantics.
- A week with incomplete source readiness should not produce high-confidence
  strategy-quality conclusions.
- Repeated exposure or concentration evidence can strengthen a risk hypothesis,
  but it does not prove selection alpha.
- Governance conclusions should cite dated packet and artifact evidence.

## What Must Not Appear

The weekly packet must not include:

- trade recommendations;
- promotion readiness claims;
- timing-corrected performance claims before FR-028;
- hidden confidence upgrades;
- stale latest artifacts presented as canonical evidence;
- broker-authoritative claims derived from shadow-only artifacts.

## Future Implementation Requirements

Any future generator should:

- read only existing dated artifacts;
- write additive dated outputs;
- preserve source paths and confidence caveats;
- summarize missing or incomplete daily evidence;
- avoid auto-hydration and workflow triggering;
- include tests for missing days, incomplete packets, stale evidence, and LOW
  confidence preservation.

Until then, this document serves as the operator and governance planning
boundary for weekly synthesis work.
