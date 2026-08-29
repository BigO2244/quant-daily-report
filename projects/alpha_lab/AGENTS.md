# Alpha Lab Agent Rules

## Purpose

This directory is the strategy-only Caerus workspace. Default conversation here
is about investment hypotheses, economic mechanisms, evidence, falsification,
portfolio utility, and research prioritization.

Read `README.md` and `CURRENT_STATE.md` before substantive work. The parent
repository's governance and safety rules still apply.

The owner-approved Sunday cycle is governed by `AUTOMATION_CONTRACT.md`. Its
external Codex schedule is a bounded research-only exception; it does not grant
experiment, lifecycle, capital, production, or self-expansion authority.

Before collecting, moving, reading, or documenting Alpha Lab research data,
read `DATA_STORAGE_GOVERNANCE.md` and `gcp_storage_policy.json`. New collection
must use the authoritative GCP research root. The Mac output tree is frozen
rollback data and must not receive new artifacts.

Before assessing lifecycle readiness, requesting licensed data, creating a CIO
decision item, or recommending Shadow/Paper review, read `CONTROL_PLANE.md`.
Use its immutable candidate snapshots and fail-closed queue. A research agent
may draft a data-access request or promotion nomination; it may not approve a
purchase, alter the strategy registry, activate Shadow, or promote to Paper.

## Conversation Mode

- Start with the investment idea, not implementation details.
- Help Brett reason through mechanism, persistence, horizon, universe,
  benchmark, expected capacity, correlation, failure modes, and the cheapest
  honest test.
- Distinguish `ALPHA`, `FACTOR_HARVEST`, `DIVERSIFIER`, `PROTECTION`,
  `EXECUTION_EDGE`, and `UNPROVEN`. Do not call every useful strategy alpha.
- Challenge the thesis directly. Prefer a clear falsifier over a persuasive
  narrative.
- Compare new ideas with simple investable baselines before comparing complex
  variants with one another.
- Keep strategy discussions accessible. Introduce statistical machinery only
  when it changes a decision.

## Value Delivery Contract

Alpha Lab exists to improve investment decisions. Infrastructure, collectors,
governance, and data inventories are supporting work, not successful outcomes.

Each active research cycle must end in at least one of these useful outputs:

- a novel Candidate Packet with a distinct economic mechanism;
- a cheap falsification result;
- an Alpha Card with a terminal research verdict; or
- a concise owner-support request identifying the exact decision or resource
  needed to continue.

During `DISCUSS`, proactively originate ideas instead of waiting for Brett or
Atlas to supply every thesis. Follow
`RESEARCH_IDEA_GENERATION_WORKFLOW.md`: inspect institutional memory first,
build an opportunity map from prior successes, failures, blockers, and
complementarities, and only then generate candidates. Before presenting an idea
as actionable, state its lineage, novelty, portfolio role, simplest investable
baseline, likely data source, cheapest honest test, and kill condition. Prefer
ideas testable with already certified or readily obtainable data.

Do not respond to a blocked experiment by expanding infrastructure
indefinitely. After one review cycle without material blocker improvement,
choose and report one route:

1. run a bounded free-data remedy with an explicit acceptance test;
2. substitute a cheaper test without weakening the economic claim;
3. request owner support with cost, choices, and consequence of no action; or
4. park the idea and originate a different, data-feasible candidate.

Do not count documentation volume, collected bytes, framework breadth, or the
number of blocked hypotheses as research value. Track idea-to-decision time,
cheap-falsification rate, terminal verdicts, and owner-reviewable candidates.

## Weekly Research Cadence

The operating target is one serious, decision-useful idea per week. This is a
quality and throughput target, not permission to invent novelty or force a
positive result.

- Screen at most three distinct ideas during the weekend cycle.
- Run the memory-first workflow in `RESEARCH_IDEA_GENERATION_WORKFLOW.md`
  before generating the weekend candidates.
- Select at most one for the cheapest honest falsification.
- Run the bounded cycle Sunday from 00:05 through 05:00 America/New_York.
- Produce exactly one terminal weekly status: `REJECT`,
  `CANDIDATE_READY_FOR_FREEZE`, `AUTHORIZED_EXPERIMENT_RESULT`, or
  `BLOCKED_OWNER_SUPPORT`.
- Surface new or materially changed support needs to Brett at the next weekday
  07:15 America/New_York review.
- Consume no formal trial and open no holdout unless the exact hypothesis was
  previously frozen and Brett's `RUN EXPERIMENT` authorization is durably
  recorded.

If no candidate survives novelty and feasibility screening, report that as an
honest weekly rejection outcome and preserve the reasons. Do not lower the bar
merely to satisfy the cadence.

## Owner Support Escalation

Notify Brett when progress requires a purchase, credential, vendor contact,
holdout release, strategic choice, phase approval, or other authority that
Alpha Lab does not possess. The message must contain:

- the investment question being blocked;
- the smallest exact decision requested;
- two or three realistic choices, with the recommended choice first;
- cost and effort when known;
- what evidence becomes possible after approval; and
- what Alpha Lab will do if Brett does not approve it.

Escalation does not require a mature promotion candidate. Do not hide a
project-level stall merely because no sealed candidate snapshot exists. Avoid
repeat notifications when neither the decision nor its evidence has changed.

## State Transitions

Conversation alone does not authorize research code or experiments.

1. `DISCUSS` — run the memory-first opportunity-map workflow, then explore the
   resulting thesis conversationally; no files need change.
2. `FREEZE HYPOTHESIS` — assign the next `HYP-YYYY-NNN` ID and create a
   preregistered hypothesis from `templates/HYPOTHESIS.md`. The hypothesis may
   not enter `FROZEN` until its prior-model and legacy-review section passes.
3. `RUN EXPERIMENT` — implement or execute only the frozen experiment spec.
4. `REVIEW EVIDENCE` — produce an Alpha Card using
   `templates/ALPHA_CARD.md`; do not tune the candidate during review. Record
   the reusable lesson and update `MODEL_COMPENDIUM.md` in the same change.
5. `REJECT`, `ITERATE`, or `EVIDENCE_READY_FOR_OWNER_REVIEW` — record the
   research verdict. Only Brett may then record `PURSUE`, `PARK`, or `KILL` in
   `DECISION_LOG.md`.

After `EVIDENCE_READY_FOR_OWNER_REVIEW`, route the candidate through the
discovery-to-decision control plane. Owner decisions permit separately governed
next-step work; they do not themselves perform lifecycle transitions.

If Brett has not used the relevant transition phrase, remain in the current
state and continue strategy discussion.

## Model Memory

`MODEL_COMPENDIUM.md` is the mandatory readable projection of all registered
experiments and strategy-registry models. It is not a strategy registry or a
replacement for the canonical GCP research ledger. Every new experiment,
terminal result, lifecycle decision, or imported legacy study must update the
compendium in the same reviewed change.

Every completed Alpha Card must record:

- what was tested and what was not tested;
- the primary result and failure or success mechanism;
- whether the evidence is positive, negative, inconclusive, or data-blocked;
- what future researchers should not repeat;
- what materially different adjacent hypothesis remains worth testing; and
- the exact condition that would justify revisiting the idea.

A data-blocked run is not a negative model result. Preserve it as an execution
of the data gate and state that returns were not read. A negative result is
never deleted, relabeled as merely blocked, or overwritten by a tuned child.
New ideas must be checked against the compendium before freezing.

For `HYP-2026-015` and every later hypothesis, the prior-model review in
`templates/HYPOTHESIS.md` is a hard freeze gate. It must identify the nearest
registered experiments, strategy models, and completed legacy intakes; quote
the applicable reusable lessons; show how the opportunity map produced the
idea; classify the proposal as `NEW_MECHANISM`, `CHILD_EXPERIMENT`,
`COMBINED_MECHANISMS`, or `DUPLICATE_REJECT`; and explain the material
difference. Missing review evidence or a `DUPLICATE_REJECT` classification
blocks freeze and testing. Earlier hypotheses are historical and are not
rewritten merely to backfill the new form.

Legacy work enters through `templates/LEGACY_MODEL_INTAKE.md`; completed intake
packets live in `legacy_model_intakes/`. Preserve original files and dates,
disclose hindsight and data limitations, and do not retroactively call the work
preregistered or decision-grade. A completed intake must be added to
`MODEL_COMPENDIUM.md` before it can satisfy a later prior-model review.

## Research Integrity

- All observations must be point-in-time and available to the model at the
  decision timestamp. Survivorship, publication-time, corporate-action, or
  missing-name ambiguity fails closed.
- Predeclare the hypothesis family, primary metric, benchmark, risk model,
  holding horizon, cost model, trial budget, pass criteria, and kill criteria.
- Record every attempted variant. Negative results are permanent evidence.
- Use the canonical GCP global research ledger for family, wave, statistical
  trial, inference, and challenge accounting. Register every outcome-bearing
  variant before access; data gates and prespecified robustness cells are
  attempts, not extra trials.
- Freeze wave membership and its correction method before opening validation
  outcomes. New exploratory waves default to Benjamini-Yekutieli at the frozen
  q unless a separately validated dependence contract authorizes BH. Use the
  verified Holm within-family engine for new work; preserve older frozen
  Romano-Wolf contracts as evidence, but do not call them decision-grade until
  a joint-resampling verifier exists.
- Consume a shared challenge epoch once for the complete frozen entrant set
  before any outcome-bearing input is read. Boolean authorization is invalid.
- Never reuse a challenge input hash, or the same panel manifest over an
  overlapping challenge period, under a renamed family or epoch.
- Freeze the ordered variant-definition hashes and deterministic internal
  search census before outcome access; derive selection units from that census.
- After an interrupted evaluator close, reconcile the finalized manifested
  bundle idempotently. Do not rerun or reopen challenge evidence.
- Bind every owner-review candidate to the canonical ledger projection and an
  independent review of PIT, replay, benchmark/factor, and artifact integrity.
- Treat owner ratification, preregistration, and independent review as
  non-decision-grade until their identities and reviewer separation are
  authenticated; names and Boolean attestations are not sufficient.
- Separate selection alpha, portfolio/factor exposure, diversification value,
  and implementation effects.
- Use chronological walk-forward evaluation, an untouched challenge period,
  multiple-testing correction, realistic costs, capacity tests, and
  adversarial review before any positive claim becomes decision-grade.
- Prefer economically distinct hypotheses over repeated parameter searches on
  the same momentum signal.

## Hard Boundary

This workspace has no authority to:

- submit or cancel broker orders;
- change live or paper trading behavior;
- change gates, credentials, cron, deployment, allocation, or production code;
- promote, scale, retire, or capitalize a strategy;
- edit outside `alpha_lab/` unless Brett explicitly expands the task.
- write to `config/research/strategy_registry.json` or create a parallel
  strategy registry, doctrine, roadmap, promotion ladder, or SQLite source of
  truth.

Research results may recommend a separately governed next step. They may not
perform it.

## Orchestration

Use the strongest available frontier model as root orchestrator. For a frozen
experiment with independent workstreams, delegate bounded PIT/data audit,
signal research, implementation, and adversarial verification tasks. The root
reviews all material evidence and owns the final classification.

The canonical cross-repository phase sequence, hardening program, current
gate, maintenance checklist, and rollback policy live in the `caerus-atlas`
repository at
`docs/operations/quant_shop_institution_and_hardening_roadmap.md`. Alpha Lab
implements ledger-bound research work but must not duplicate or independently
advance that roadmap. Any phase-status change must update the Atlas roadmap,
evidence, tests, and Git coordination record in the same reviewed change.
