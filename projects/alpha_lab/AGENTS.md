# Alpha Lab Agent Rules

## Purpose

This directory is the strategy-only Caerus workspace. Default conversation here
is about investment hypotheses, economic mechanisms, evidence, falsification,
portfolio utility, and research prioritization.

Read `README.md` and `CURRENT_STATE.md` before substantive work. The parent
repository's governance and safety rules still apply.

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

## State Transitions

Conversation alone does not authorize research code or experiments.

1. `DISCUSS` — explore the thesis conversationally; no files need change.
2. `FREEZE HYPOTHESIS` — assign the next `HYP-YYYY-NNN` ID and create a
   preregistered hypothesis from `templates/HYPOTHESIS.md`.
3. `RUN EXPERIMENT` — implement or execute only the frozen experiment spec.
4. `REVIEW EVIDENCE` — produce an Alpha Card using
   `templates/ALPHA_CARD.md`; do not tune the candidate during review.
5. `REJECT`, `ITERATE`, or `EVIDENCE_READY_FOR_OWNER_REVIEW` — record the
   research verdict. Only Brett may then record `PURSUE`, `PARK`, or `KILL` in
   `DECISION_LOG.md`.

After `EVIDENCE_READY_FOR_OWNER_REVIEW`, route the candidate through the
discovery-to-decision control plane. Owner decisions permit separately governed
next-step work; they do not themselves perform lifecycle transitions.

If Brett has not used the relevant transition phrase, remain in the current
state and continue strategy discussion.

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
