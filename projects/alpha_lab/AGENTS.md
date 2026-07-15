# Alpha Lab Agent Rules

## Purpose

This directory is the strategy-only Caerus workspace. Default conversation here
is about investment hypotheses, economic mechanisms, evidence, falsification,
portfolio utility, and research prioritization.

Read `README.md` and `CURRENT_STATE.md` before substantive work. The parent
repository's governance and safety rules still apply.

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

If Brett has not used the relevant transition phrase, remain in the current
state and continue strategy discussion.

## Research Integrity

- All observations must be point-in-time and available to the model at the
  decision timestamp. Survivorship, publication-time, corporate-action, or
  missing-name ambiguity fails closed.
- Predeclare the hypothesis family, primary metric, benchmark, risk model,
  holding horizon, cost model, trial budget, pass criteria, and kill criteria.
- Record every attempted variant. Negative results are permanent evidence.
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
