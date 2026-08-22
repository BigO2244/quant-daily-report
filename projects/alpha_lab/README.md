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
- `EXPERIMENT_LEDGER.md` — preserved human experiment history and projection.
- `factory/research_ledger.py` — typed global wave/family/experiment/trial,
  verified-inference, single-use challenge, and independent-review authority
  backed by the GCP append-only event chain.
- `DECISION_LOG.md` — owner decisions to pursue, park, or kill.
- `templates/HYPOTHESIS.md` — preregistration contract.
- `templates/ALPHA_CARD.md` — standard evidence and classification packet.
- `hypotheses/` — immutable frozen specifications.
- `factory/` — PIT contracts, provider gates, deterministic manifests, and the
  append-only evidence store.
- `data_spine/` — credential-safe external-source collectors, immutable data
  bundles, vendor sample gates, and consolidated access readiness.
- `experiments/` — frozen four-lane data contracts and pure signal composition.
- `options_proxy/` — standalone automated, forward-only yfinance chain collection and
  non-executing HYP-2026-004 proxy observation infrastructure.
- `evidence/` — compact Alpha Cards and research verdicts.
- `reports/` — repository-local readiness and adversarial audit reports.
- `DATA_STORAGE_GOVERNANCE.md` — canonical GCP landing, integrity, access,
  retention, and recovery rules for all Alpha Lab data.
- `gcp_storage_policy.json` — machine-readable form of the storage policy and
  authoritative GCP paths.
- `CONTROL_PLANE.md` — end-to-end research, licensed-data, Shadow checkpoint,
  and CIO Paper-nomination workflow.
- `control_plane/` — generic evaluator contract, lifecycle assessor, paid-data
  requests, and deterministic CIO queue generation.

Raw experiment artifacts belong under
`outputs/research/alpha_lab/<HYP-ID>/<run-id>/` in the parent repository.
Alpha Lab commits only compact hypothesis specifications and summary verdict
cards; generated panels, caches, and search results are not a second source of
truth.

All new collection runs land on the dedicated GCP research disk. The Mac output
tree is a frozen rollback copy and must not be used for new acquisition. See
`DATA_STORAGE_GOVERNANCE.md` for the exact GCP root and access commands.

## Canonical dependencies

Alpha Lab reuses rather than duplicates the parent repo's:

- point-in-time data and research architecture under FR-069;
- research registry and provenance graph;
- broker-truth ledger and TCA;
- investment doctrine and promotion ladder.

Alpha Lab can produce research evidence. It cannot change production behavior
or confer promotion authority.

## Four-lane factory command

Run the frozen point-in-time data gate from the repository root:

```bash
python -m projects.alpha_lab.experiments.run_data_gate --all
```

The command verifies each hypothesis hash, hashes the research code and visible
input snapshot, checks provider certifications, and writes an append-only run
packet under `outputs/research/alpha_lab/<HYP-ID>/<run-id>/`. It does not read
forward returns or the locked challenge period. A `BLOCKED_DATA` result is a
completed, fail-closed experiment gate—not evidence against or for alpha.

Alpha Lab v1/v2 code and outputs are historical lineage only. They may seed
ideas or parity tests, but their static-universe and legacy validation results
are not inherited as decision-grade `PASS` verdicts.

## Forward options proxy command

The HYP-2026-004 proxy collector is research-only and scheduled separately from
Caerus production cron. It is not wired to shadow runtime, paper, live,
allocation, or execution:

```bash
python -m projects.alpha_lab.options_proxy.cli --repo-root . daily
```

It runs at or after 15:45 ET for a decision-time-eligible observation. The first
snapshot records the IV-skew level but fails closed for scoring because no prior
snapshot exists. See `options_proxy/README.md` for collection, rebuild,
maturation, and boundary-validation commands.

## Discovery-to-decision queue

Alpha Lab can test any preregistered technique through a bounded evaluator
adapter, stop for owner review when licensed data is required, and nominate
qualified Shadow candidates for a Paper decision. It cannot perform purchases,
Shadow activation, registry changes, or promotions.

```bash
python -m projects.alpha_lab.control_plane.cli build-queue \
  --candidate /path/to/sealed_candidate_snapshot.json
```

Persisted queue bundles must be built on the authoritative GCP research root.
See `CONTROL_PLANE.md` for the lifecycle, adapter contract, data-request packet,
and Paper nomination gates.

## Twelve-family workload

`STRATEGY_BACKLOG.md` contains the owner-prioritized 12-family research
workload. It is not a strategy registry: the four 2026-07-14 families retain
their frozen hypotheses, while the remaining rows stay in `DISCUSS` until Brett
uses `FREEZE HYPOTHESIS` and later `RUN EXPERIMENT`.

Every dated-return evaluator uses the common point-in-time regime envelope in
`evaluators/regime_diagnostics.py`. Regime cells require 30 independent
observations, the full technique needs 252 total observations before
regime-selection coverage is reviewable, and regime slices cannot rescue a
failed unconditional test. A separate frozen regime-interaction holdout is
still required for a regime-specific claim.
