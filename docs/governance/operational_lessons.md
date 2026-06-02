# Operational Lessons

## Purpose

This document preserves operational lessons from FR deployment, recovery, and
governance work. It is not an implementation spec. Use it to shape future FR
scope, rollout sequencing, validation, rollback, and observation criteria.

## Deployment Lessons

- Wave-based deployment keeps blast radius visible and gives each group of
  changes a clear rollback boundary.
- Runtime smoke checks can expose orchestration issues that static shell syntax
  checks do not catch.
- `git revert`, push, and VM fast-forward is the preferred deployed-source
  rollback path.
- SCP is emergency-only. Any SCP source change must be verified and reconciled
  back through git.
- Local implementation status is not deployment status. FRs are not deployed
  until they pass through the canonical git and VM flow.

## Recovery Lessons

- Degraded-state simulation is necessary for recovery work; happy-path tests are
  not enough.
- Recovery paths should validate their own outputs before allowing execution to
  continue.
- Partial recovery output must fail closed when execution-critical artifacts are
  incomplete.
- Runtime evidence should be preserved during rollback. Deleting artifacts or
  logs to make a state look clean is not a rollback strategy.

## Execution Reliability Lessons

- On 2026-05-26, the paper run halted `PARTIAL` after four sell orders were
  accepted and before five planned buys were submitted. The immediate trigger
  was `post_submit_artifact_failure:posttrade_state_capture_failed`.
- The failure pattern was sell-phase timeout plus a transient Alpaca
  `PARTIALLY_FILLED` order state without a resolvable `filled_qty`. The system
  halted conservatively instead of submitting buys against uncertain sell
  completion, which prevented state corruption and overbuying risk.
- The missing artifact was posttrade reconciliation evidence, not order
  submission evidence. Pretrade, postsell, and posttrade broker snapshots were
  preserved; `recon_posttrade_2026-05-26.json` was missing because posttrade
  state capture raised before publishing reconciliation.
- Hotfix `40fce71` keeps the conservative buy block when sell state is unsafe,
  but preserves posttrade evidence. If a partial sell can be resolved from
  broker position deltas, reconciliation proceeds. If it cannot be resolved,
  posttrade reconciliation is written as `NOT_COMPARABLE` with unresolved order
  metadata instead of failing artifact capture.
- Future execution-adjacent fixes should preserve this distinction: never mask
  unresolved broker state, but avoid losing available posttrade evidence.
- On 2026-05-27, buy-leg suppression required explicit governance because a
  suppressed buy phase can look superficially similar to a clean no-buy day if
  operator surfaces only show submitted orders. Planned buys, submitted buys,
  budget-skipped buys, guard-suppressed buys, and repair-eligible buys need
  separate state labels.
- HOTFIX-2026-05-27 observation criteria: preserve incident artifacts and logs;
  verify that operator/email evidence distinguishes planned-versus-submitted
  buy counts; verify that suppressed buys carry an explicit guard or budget
  reason; verify that repair guidance, if present, is broker-authoritative; and
  verify on the next buy-capable paper run that buys are either submitted or
  blocked with an explicit reason.
- The durable follow-up is FR-031 execution integrity contract. That contract
  should be designed before additional execution-contract changes so future
  fixes do not mix order planning, broker submission, artifact publication,
  suppression, and recovery eligibility into one ambiguous status.
- FR-031 implements that follow-up as an additive audit artifact and compact
  operator-summary status. The validator should remain non-routing in its first
  deployed version: it observes and classifies integrity failures, but does not
  create a new post-submit halt path unless a later FR explicitly promotes that
  behavior with its own validation and rollback boundary.

## Artifact Lessons

- Additive observability artifacts are safer than overwriting or deleting
  canonical runtime artifacts.
- `latest` files are convenience surfaces, not trustworthy state unless they
  carry freshness and provenance metadata.
- Generated markdown, diagnostics, reports, and runtime artifacts should not sit
  beside canonical operator docs without clear generated-file labeling.
- Artifact ownership, freshness, retention, and consumer semantics should be
  documented before cleanup automation is introduced.
- Source-readiness diagnostics should distinguish expected waiting states from
  actual failure states. Before the post-close hydration window, stale same-day
  shadow artifacts can be expected; after the window, missing hydration evidence
  becomes an operator action item.

## Governance Lessons

- `DEPLOYED_OBSERVING` needs explicit exit criteria. Otherwise it becomes a
  vague holding state rather than an operational control.
- Documentation drift is operational risk when docs define deployment,
  rollback, cron, scheduler, dashboard, or artifact contracts.
- Deferred FRs should retain rationale and re-entry criteria so they do not
  silently return as unscoped implementation work.
- Low-blast-radius governance work should remain additive first; code paths can
  adopt the model after operator trust semantics are clear.

## Anti-Patterns To Avoid

- Treating VM working tree drift as canonical source.
- Marking an FR `DEPLOYED` without observation evidence when runtime behavior
  still needs proof.
- Using `latest` artifacts as authoritative state without trade date,
  publication time, producer, and source path metadata.
- Combining docs cleanup, runtime producer changes, and scheduler changes in one
  rollback boundary.
- Introducing distributed orchestration to solve unclear state ownership.

## Phase 4 Application

Phase 4 should continue the move from execution automation toward operational
state governance. The highest-leverage work is still explicit artifact ownership,
freshness semantics, operator health synthesis, retention policy, validation
isolation, and documentation hygiene.


## 2026-05-28 — Precomputed Execution Source Contract Recovery

## Status: DEPLOYED / VALIDATED

Issue:
Cron-driven execution halted with `stale_prices` even though the prior-close
precompute bundle for `trade_date=2026-05-28` was complete and validated. The
bundle carried `planned_execution_payload.json` with `pricing_source=PREV_CLOSE`,
`pricing_asof=2026-05-27`, and 12 planned trades, but the runner rebuilt from
signals because exact planned-payload mode was still opt-in.

Root cause:
Cron validated the precompute bundle but did not make the canonical
`planned_execution_payload.trades` the default execution source. Without
`PRECOMPUTE_EXECUTE_EXACT_PLAN=1`, execution entered the `rebuilt_from_signals`
path, invoked same-day open-price freshness checks, and correctly failed closed
on stale open-market prices. The stale-price guard was correct; the execution
source contract was ambiguous.

Fix:
- Updated cron execution to use exact planned payload mode by default.
- Preserved stale same-day open-price fail-closed behavior for
  `rebuilt_from_signals`.
- Fixed execution-integrity order lineage matching when intended orders lack
  generated broker order IDs but payload orders carry them.
- Fixed `scripts/precompute_bundle_status.py` direct repo-root invocation for
  operator recovery use.
- Added deterministic execution lifecycle timeline artifacts for run-level
  incident review and future real-capital governance.
- Added explicit execution provenance:
  - `execution_source=planned_payload_exact`
  - `planning_price_basis=PREV_CLOSE`
  - `pricing_asof=2026-05-27`
  - `execution_price_requirement=PRECOMPUTE_VALIDATED`
  - `price_freshness_scope=precompute_bundle`

Fix commits:
- `d003f93` — Fix cron precomputed execution source contract.
- `fc9c2f0` — Fix execution integrity order lineage matching.
- `f3399d6` — Fix precompute bundle status direct invocation.
- `6ae0e12` — Add execution lifecycle timeline artifacts.

Validation:
- VM fast-forward deployed through `6ae0e12`.
- Planned-payload-exact paper rerun completed successfully.
- Final execution provenance recorded `planned_payload_exact`, `PREV_CLOSE`,
  and `precompute_bundle` freshness scope.
- Alpaca accepted 13 orders: 13 submitted, 13 accepted, 0 rejected.
- Execution integrity WARN findings were separated into lineage false positive
  and real post-submit cash drift observation.

Lesson:
Execution source, price basis, freshness scope, and integrity findings must be
explicit and operator-visible. A validated precompute bundle is not enough if
the downstream runner can silently choose a different execution source. Future
execution-adjacent fixes should preserve stale-price fail-closed guards while
making source selection and provenance auditable in a single run narrative.

Non-blocking follow-up:
Continue observing post-submit cash drift in the execution integrity artifact.
If drift persists after broker fills and reconciliation, promote it as a small
accounting/reconciliation FR; do not weaken the current warning globally.


## 2026-06-02 — Governance Blocker Audit Classification (FR-037 / FR-038)

## Status: DEPLOYED_OBSERVING

Issue:
Tier 3 promotion governance surfaced eight "blockers" against 2026-06-02
research inputs (security_master_missing, planned_execution_payload_missing,
no_planned_orders, missing_timing_coverage, universe_governance_incomplete,
weak_differentiation, hit_rate_deteriorated, concentration_above_caps). All
eight were treated equivalently by the conservative final control summary,
which made operator triage harder than it needed to be: a missing
`data/security_master/latest.json` looked superficially like the same kind
of finding as a measured `hit_rate_deteriorated` even though one is a data
hygiene gap and the other is a strategy signal.

Pattern:
FR-038 introduces a four-class taxonomy for every governance blocker:

| Classification | Meaning |
|---|---|
| `REAL` | The blocker reflects an actual finding about the strategy or its evidence (e.g. measured weak differentiation, measured hit-rate deterioration). |
| `DATA_QUALITY` | The blocker exists only because an upstream artifact is missing or stale (e.g. security master not bootstrapped, planned execution payload missing for the target date). Once the artifact is refreshed the blocker disappears without any strategy change. |
| `CONFIGURATION` | The strategy is operating as designed but the governance gate threshold conflicts with the design (e.g. a 5-position equal-weight strategy mathematically forces `max_single_name_weight=0.20`, above the 0.10 cap). Resolved by gate-threshold review, not strategy change. |
| `OBSERVATION_WINDOW` | The blocker would resolve with more observation history; nothing is wrong with the strategy or the data, the evidence window is just too short to trust the verdict. |

Each classification carries a `root_cause`, `confidence`, `severity`, and
`remediation` so the operator can act without re-deriving the audit.

Result:
On 2026-06-02 the audit split the eight blockers into 5 `DATA_QUALITY`
(local) / 0 on VM (artifacts present), 1 `CONFIGURATION`, and 2 `REAL`.
The final control summary now exposes `blockers_eliminated`,
`blockers_remaining`, `data_quality_issues`, and `actual_strategy_issues`
as distinct fields, plus a deterministic seven-component
`governance_maturity_tier` (`IMMATURE` / `EMERGING` / `DEVELOPING` /
`MATURE` / `PROMOTION_READY`) that replaces the prior subjective evidence
maturity assessment.

Lesson:
A blocker count is not the same as a promotion risk count. Without a
classification layer between raw governance gates and the operator-facing
summary, data hygiene noise is indistinguishable from real strategy
signals. Audits should classify before they aggregate, and the rollup
surface should preserve the split so operators can triage data quality
work and strategy work on the right timelines.

Operational implications:
- Eliminating a `DATA_QUALITY` blocker is a hygiene task (e.g. bootstrap
  the security master, run the precompute pipeline for the target date)
  and does not change the strategy verdict.
- A `CONFIGURATION` blocker requires an explicit governance decision
  (raise the cap, change the strategy design, or accept the mismatch);
  it should never be silently elided.
- A `REAL` blocker is a strategy concern that warrants research action,
  not docs or pipeline work.
- An `OBSERVATION_WINDOW` blocker is a waiting state, not a failure;
  governance should track it but not escalate it.

Non-blocking follow-up:
Track day-over-day movement of `governance_maturity_tier` and the
classification mix (`REAL` / `DATA_QUALITY` / `CONFIGURATION` /
`OBSERVATION_WINDOW`) so promotion-readiness trends become operator
visible rather than implicit. Trajectory tooling is scoped as FR-041
in the strategic backlog.
