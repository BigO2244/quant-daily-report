# Caerus Alpha Lab

Alpha Lab is a dedicated place to discuss and test investment strategies
without mixing strategy research with execution, deployment, or live-pilot
operations.

## The question

For every idea, Alpha Lab asks:

> What economic behavior should create this return, why might it persist, what
> simpler exposure could explain it, and what result would make us abandon it?

## How to use this workspace

Open a Codex project rooted at:

`/Users/brettolson/Documents/Caerus/alpha-lab-project/projects/alpha_lab`

Then talk naturally about strategies. Examples:

- "Could earnings revisions produce an edge in our large-cap universe?"
- "What would make an activist 13D signal persistent rather than crowded?"
- "Is cross-asset trend alpha or mainly a diversifier for Caerus?"
- "Compare quality-at-a-reasonable-price with our existing momentum exposure."

The default is discussion. When an idea is ready to become a controlled research
claim, say `FREEZE HYPOTHESIS`. When its preregistration is complete and you want
the frozen test executed, say `RUN EXPERIMENT`.

## Durable surfaces

- `CURRENT_STATE.md` — honest baseline of what Caerus presently knows.
- `STRATEGY_BACKLOG.md` — candidate mechanisms, not a leaderboard of Sharpe.
- `EXPERIMENT_LEDGER.md` — append-only record of every frozen experiment.
- `DECISION_LOG.md` — owner decisions to pursue, park, or kill.
- `templates/HYPOTHESIS.md` — preregistration contract.
- `templates/ALPHA_CARD.md` — standard evidence and classification packet.

Raw experiment artifacts belong under
`outputs/research/alpha_lab/<HYP-ID>/<run-id>/` in the parent repository.
Alpha Lab commits only compact hypothesis specifications and summary verdict
cards; generated panels, caches, and search results are not a second source of
truth.

## Canonical dependencies

Alpha Lab reuses rather than duplicates the parent repo's:

- point-in-time data and research architecture under FR-069;
- research registry and provenance graph;
- broker-truth ledger and TCA;
- investment doctrine and promotion ladder.

Alpha Lab can produce research evidence. It cannot change production behavior
or confer promotion authority.

Alpha Lab v1/v2 code and outputs are historical lineage only. They may seed
ideas or parity tests, but their static-universe and legacy validation results
are not inherited as decision-grade `PASS` verdicts.
