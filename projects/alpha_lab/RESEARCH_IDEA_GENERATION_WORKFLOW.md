# Alpha Lab Memory-First Idea Generation Workflow

Status: `CANONICAL_ALPHA_LAB_RESEARCH_WORKFLOW`

Purpose: require Alpha Lab to learn from Caerus's complete model history before
it proposes the next model. The archive is an idea-generation input, not merely
a duplicate check performed after an idea already exists.

This workflow changes no strategy, lifecycle state, allocation, broker,
scheduler, Paper, Shadow, or Live behavior. It does not authorize a formal
experiment or holdout read.

## Required sequence

```text
Review institutional memory
→ build an opportunity map
→ generate candidates from prior evidence and external mechanisms
→ classify each candidate
→ test novelty and feasibility
→ select the cheapest honest falsifier
→ freeze only after the owner-authorized gate passes
```

## 1. Review institutional memory

Before generating candidates, read:

1. `MODEL_COMPENDIUM.md` for positive, negative, blocked, lifecycle, and
   cross-model lessons.
2. `EXPERIMENT_LEDGER.md` and the canonical GCP ledger projection for complete
   attempt and trial lineage.
3. `STRATEGY_BACKLOG.md` for ideas already in discussion or frozen work.
4. `config/research/strategy_registry.json` and canonical operating evidence
   for existing model identities and lifecycle posture.
5. Every completed packet under `legacy_model_intakes/`.

Do not infer a model result from a data-blocked experiment. Do not infer alpha
from registry promotion or short operational performance. Preserve the exact
evidence boundary of each source.

## 2. Build the opportunity map

Summarize the research memory into five lists:

- **Surviving mechanisms:** effects with positive or operationally promising
  evidence, including the conditions and caveats under which they survived.
- **Failed mechanisms or constructions:** negative results and the economic,
  cost, concentration, or robustness reason they failed.
- **Untested mechanisms:** data-blocked ideas whose return claim remains
  unknown.
- **Complementarities:** models that could plausibly improve one another
  because they operate at different horizons, information sources, regimes, or
  portfolio roles.
- **Repeated constraints:** data, turnover, capacity, crowding, correlation,
  timing, or implementation problems that should shape the next idea.

The opportunity map must distinguish a failed economic mechanism from a failed
implementation and from a model that was never tested.

## 3. Generate candidates

Generate candidates only after the opportunity map exists. Draw from four
lanes:

1. **Extend a surviving mechanism** into a new, explicitly incremental claim.
2. **Combine complementary mechanisms** whose interaction has an economic
   reason to improve selection or portfolio utility.
3. **Solve a documented failure mode** with a materially different information
   source or implementation—not a nearby parameter.
4. **Introduce an unexplored mechanism** supported by primary research, market
   structure, or observable economic behavior.

External research and market observations remain necessary. Alpha Lab must not
become a closed system that only recombines its own archive.

For each candidate, state:

- the economic mechanism and expected persistence;
- the prior evidence that caused Alpha to propose it;
- portfolio role and likely correlation with current Caerus sleeves;
- data feasibility and expected capacity;
- simplest investable baseline;
- cheapest honest falsifier; and
- kill condition.

## 4. Classify each candidate

Use exactly one relationship classification:

- `NEW_MECHANISM` — an economically distinct return source.
- `CHILD_EXPERIMENT` — a legitimate new generation of an existing family with
  a disclosed post-result change and new trial identity.
- `COMBINED_MECHANISMS` — a predeclared interaction or portfolio combination
  with an incremental claim beyond each component.
- `DUPLICATE_REJECT` — the same mechanism or a parameter-only variation without
  sufficient new information value.

`DUPLICATE_REJECT` stops before freeze and consumes no statistical trial.

## 5. Combined-mechanism guardrails

- Two failed models do not become promising merely because they are combined.
- The interaction must have a causal or portfolio rationale stated before
  outcome access.
- The combined candidate must beat both standalone components and a simple
  allocation baseline under the same costs and evidence window.
- Attribute selection, factor exposure, diversification, protection, and
  implementation effects separately.
- Freeze the interaction, weights or combination rule, baselines, and
  incremental metric before testing.
- Do not select a combination after inspecting which pairing happened to win.

## 6. Novelty and feasibility gate

The chosen candidate must complete the **Memory-derived opportunity map and
prior-model review** section in `templates/HYPOTHESIS.md`. The gate records:

- source paths and hashes;
- closest experiments, strategies, and legacy studies;
- relevant success, failure, and blocker lessons;
- why those lessons generated this proposal;
- classification and material economic difference;
- forbidden parameter-only repetitions;
- exact data path and availability boundary; and
- the incremental claim and baselines for a combination.

Missing evidence, unavailable essential data, or `DUPLICATE_REJECT` blocks
freeze. A data purchase, vendor contact, or strategic choice becomes an exact
owner-support request rather than an open-ended infrastructure project.

## 7. Freeze and feedback loop

Only Brett's explicit `FREEZE HYPOTHESIS` instruction permits preregistration,
and only a durable `RUN EXPERIMENT` authorization permits the frozen test.
Challenge access remains separately governed.

After evidence review:

```text
result
→ immutable GCP evidence and global ledger
→ Alpha Card with reusable lessons
→ EXPERIMENT_LEDGER.md
→ MODEL_COMPENDIUM.md
→ next opportunity map
```

Positive, negative, inconclusive, and data-blocked outcomes all return to the
institutional memory. Negative results are never removed or converted into a
new parameter search without child-experiment lineage.

## Weekly application

During the Sunday 00:05–05:00 America/New_York cycle:

1. build or refresh the opportunity map;
2. screen at most three candidates across the four generation lanes;
3. select at most one candidate by expected information gain per unit of data,
   compute, and trial budget;
4. run only the cheapest work already authorized; and
5. return `REJECT`, `CANDIDATE_READY_FOR_FREEZE`,
   `AUTHORIZED_EXPERIMENT_RESULT`, or `BLOCKED_OWNER_SUPPORT`.

Do not lower the novelty or evidence bar to satisfy the weekly cadence.

