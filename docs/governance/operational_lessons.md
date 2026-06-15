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

## 2026-06-12 — GCP Billing Suspension and VM External IP Drift

- Billing suspension caused `alpha-stack-scheduler` to become unreachable and
  enter `TERMINATED` state.
- After restart, the external IP changed.
- Agent validation failed because it used the stale IP `34.61.147.38`.
- Required practice: use `gcloud compute ssh` by instance name and zone.
- Direct IPs should be resolved dynamically and must not be embedded into
  governance prompts or runbooks.

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
- On 2026-06-09, a fractional-trading execution audit confirmed that
  `allow_fractional_shares=true` is intended system behavior for the paper
  broker, not an accidental config setting. Target-weight construction,
  rebalance sizing, turnover scaling, capital-budget clipping, and Alpaca
  submission are all compatible with fractional quantities.
- The defect was downstream whole-share normalization in the execution path:
  final executable-trade filtering and shadow-order construction treated
  sub-1-share orders as zero-share drops even when fractional trading was
  enabled. That made high-price target allocations appear as explicit target
  weights but disappear before order eligibility/submission.
- Historical artifact review found 53 impacted days, 230 zero-share drops,
  approximately $93.7k of aggregate buy capacity lost after capital-budget
  limits but before executable-order construction, and approximately 16.72%
  average underdeployment on impacted days. Treat these as operational-drag
  evidence, not a strategy target-cash decision.
- Commit `e249f61` deployed the fractional-share execution fix. Future
  execution validation must verify that `allow_fractional_shares=true` survives
  every stage: target-weight conversion, raw target shares, risk controls,
  capital-budget clipping, executable-order filtering, intended/shadow order
  construction, and broker payload submission. A downstream conversion to
  integer shares is a defect unless fractional trading is explicitly disabled.
- Future execution-adjacent reviews should distinguish target cash, capital
  reserve/cash-budget clipping, min-notional filtering, whole-share-only
  behavior, fractional-enabled execution, and market-guard/plan-only states.
  A reported achieved-cash or gross-exposure value is not sufficient evidence
  that portfolio construction intentionally targeted cash.
- The 2026-06-09 run also exposed a staged-execution planning defect:
  `planned_payload_exact` submitted the precomputed buy list after sells
  without rebuilding buys from confirmed post-sell cash and refreshed broker
  positions. The model's raw target cash was near 0%, the risk-control target
  cash was 5%, and ending cash remained approximately $2,196.67 versus a
  $530.09 risk cash target. Confirmed sell proceeds of approximately $1,106.06
  were available, but the stale buy list limited deployment; rebudgeting would
  have increased buy notional by about $1,666.58.
- Commit `aaf5961` deployed post-sell buy rebudgeting. If sell orders exist,
  the execution path must treat the buy leg as dependent on broker-confirmed
  post-sell state, not as a replay of stale precompute rows. The runtime now
  emits `post_sell_rebudget_<date>.json` with pre-sell cash, submitted sells,
  sell-phase status, confirmed proceeds, post-sell cash/buying power, buy
  budget before and after safeguards, original versus recomputed buy notional,
  final buy orders, and ending cash versus risk target.
- Partial, rejected, or timed-out sells must release only confirmed cash and
  buying power. The buy leg may still proceed against confirmed available cash,
  but it must never assume proceeds from unfilled or unresolved sell orders.
- Buy-only/no-sell runs intentionally preserve exact-plan behavior. Post-sell
  rebudgeting is a sell-leg invariant, not a license to rebuild every
  precomputed buy plan.
- June 10 monitoring should review the next `post_sell_rebudget_<date>.json`,
  confirm fractional quantities appear in intended/shadow orders when
  applicable, confirm ending cash moves toward the 5% risk target, confirm
  posttrade reconciliation remains `OK_RECONCILED`, and confirm rejected orders
  remain zero.
- The June 9 operational-drag repair restored current-date decision-grade
  attribution by fixing artifact freshness and lineage gaps, not by changing
  strategy behavior. Root causes included stale actual NAV not being extended
  by current broker/run artifacts, split account and position artifacts not
  being merged, `normalized_positions` dictionaries not being parsed, gross
  exposure not being derived when cash/equity were available, and freshness
  diagnostics that were too coarse to distinguish current-date blockers from
  historical caveats.
- Commits `d13e804`, `c55f2ba`, and `67911c9` made operational-drag
  diagnostics explicit: source paths, source dates, stale components, blocking
  components, current-date status, decision-grade readiness, and confidence are
  surfaced in the artifact family. For 2026-06-09, operational drag is
  decision-grade with MEDIUM confidence and latest aligned date 2026-06-09.
- Broker-state reconciliation is necessary but not sufficient. `OK_RECONCILED`
  means broker positions match expected post-execution broker state; it does
  not prove the actual portfolio attained the risk-adjusted target. The
  2026-06-09 run reconciled cleanly while actual cash remained 20.8417% versus
  the 5.0% risk target.
- The June 12 execution investigation classified the cash discrepancy as an
  `ARTIFACT_TIMING_FAILURE`, not a failed sell-first rebudget. Post-sell
  rebudgeting remained correct; the open issue was post-buy snapshot timing.
  Future validation must check the post-buy terminal/timeout stage and the
  target-attainment diagnostic before treating cash attainment as proven.
- Commits `81a0468` and `5663313` deployed target-attainment reconciliation as
  observability only. The artifact
  `outputs/target_attainment/<date>/target_attainment_<date>.json` compares
  target portfolio, risk-adjusted portfolio, intended orders, executed orders,
  broker holdings, and actual portfolio. The CLI is
  `python3 -m research.target_attainment --date YYYY-MM-DD`.
- The 2026-06-09 target-attainment baseline records target cash 5.0%, actual
  cash 20.8417%, cash gap 15.8417%, deployment efficiency 83.3245%,
  attainment score 37.15, and excess cash of approximately $1,669.68.
- Observability artifacts must expose enough context to diagnose deployment
  gaps: reason codes, source paths, source dates, stale inputs, blocking
  components, top drift contributors, and confidence. A single green
  reconciliation status is not enough for deployment-integrity monitoring.
- On 2026-06-15, Alpaca filled both submitted sell orders after the original
  sell observation boundary, but Caerus persisted zero fills, did not advance to
  post-sell buy submission, and surfaced an `EXECUTED` label. Sell observation
  must include a bounded authoritative recovery refresh by stable broker order
  ID before suppressing buys or producing final operator reporting.
- A sell-first lifecycle may only proceed to post-sell rebudgeting when sell
  terminality is established authoritatively or by an explicitly documented
  safe fallback. If the sell phase remains unresolved after bounded recovery,
  the buy phase must be skipped with an explicit reason such as
  `sell_phase_timeout`; accepted-only activity is not a completed execution.
- Confirmation emails and target-attainment diagnostics must agree with the
  lifecycle: planned-but-unsubmitted buys, recovered broker state, unresolved
  sell state, and target-attainment uncertainty are separate facts and must not
  be collapsed into a green headline.

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
- Dashboard auth recovery should use direct `htpasswd` updates and a local
  authenticated curl check, not ad hoc credential staging or secret printing.

## Governance Lessons

- `DEPLOYED_OBSERVING` needs explicit exit criteria. Otherwise it becomes a
  vague holding state rather than an operational control.
- Documentation drift is operational risk when docs define deployment,
  rollback, cron, scheduler, dashboard, or artifact contracts.
- Deferred FRs should retain rationale and re-entry criteria so they do not
  silently return as unscoped implementation work.
- Low-blast-radius governance work should remain additive first; code paths can
  adopt the model after operator trust semantics are clear.

## Next Live Run Checklist

- Confirm the execution email status for the live run.
- Inspect the latest `run_id` and execution artifacts.
- Check `execution_results.json` for `submitted_count`, `accepted_count`,
  `rejected_count`, `buy_phase_status`, `filled_buy_count`, and
  `pending_buy_count`.
- Check `execution_timeline.json` or the matching markdown summary for
  `posttrade_snapshot_stage` and buy-phase completion.
- Check the execution target-attainment MCP output for `target_cash_weight`,
  `achieved_cash_weight`, `cash_target_drift`, and diagnostic status.
- If buys fill, require `posttrade_snapshot_stage=post_buy` and
  `pending_buy_count=0`.
- If buys time out or reject, require an explicit classified warning rather
  than a stale pre-buy snapshot masquerading as final state.

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

## 2026-06-10 — PIT Universe Remediation and Polaris Rebaseline (FR-067/FR-068)

## Status: RESEARCH / NON_EXECUTIONAL

Lesson: a static current-universe (`data/universe.csv`, 200 survivors) projected
backward is severely survivorship-biased, and the distortion is **risk-adjusted**,
not just return-level. The Polaris priced rebaseline on the honest large-cap PIT
universe (Sharadar; 1,600 names incl. 354 delisted, full SEP prices) showed:

- Sharpe **overstated** 1.054 → 0.851 (−19%).
- Max drawdown **understated** −43.2% → −54.4% (+11.2 pts deeper).
- Raw CAGR slightly **understated** (28.83% → 30.68%).

Counter-intuitively the dominant channel for this large-cap momentum strategy was
universe **curation** (the 200 quietly omits volatile high-momentum large-caps like
ENPH/PLUG/GME/NVAX), not delisted-loser drag — delisted names did not appear among
the top contributors. Survivorship can flatter *quality metrics* even when raw
return is unaffected.

Governance changes adopted:
- Legacy current-universe backtests are **non-decision-grade**; retain them as
  `legacy_current_universe` for lineage only.
- All promotion evidence must carry `universe_method = pit_universe`.
- Verify the vendor's *delisted* coverage with a paid trial before committing
  (FR-067): the free preview confirmed everything except delisted prices — the one
  thing that mattered. Also: a verifier that imports a heavy optional dependency
  (pandas via the trading calendar) can fail silently and null out every metric;
  keep validation scoring dependency-free and deterministic.

Non-blocking follow-up:
Orion/Lyra PIT rebaselines; DAILY-marketcap PIT large-cap family (current
scalemarketcap is PIT-approximate); index membership families. Modular sleeve
architecture to standardize PIT-first evidence is designed in FR-069.

## 2026-06-14 — Shadow NAV Same-Day Observation Restatement

## Status: RESOLVED / GOVERNANCE AND PRESENTATION FOLLOW-UP

Issue:
The Shadow scorecard incident that reported impossible cumulative performance
was resolved through a governed restatement of the operational Shadow NAV series.
The owner approved the dated same-day close-to-close convention as the canonical
operational Shadow observation methodology.

Canonical state:
- Methodology: `dated_same_day_close_to_close_v1`.
- Observation inception: `2026-05-12`.
- Recovered observation window: `2026-05-12` through `2026-06-12`.
- Recovered NAV rows: `23`.
- Scorecard health after recovery: Fresh.
- NAV integrity after recovery: OK.

Lesson:
Operational Shadow observation is not calendar-year YTD, and it is not the same
truth surface as FR-066 broker-authoritative portfolio NAV. Legacy
mixed-convention Shadow history is lineage-only and non-decision-grade. Future
promotion, retirement, differentiation, or Orion/Lyra disposition evidence must
not combine the superseded legacy series with the canonical observation series.

Operational implication:
Operator-facing scorecards and governance docs should say "Since Observation
Inception (2026-05-12)" for the recovered observation window rather than "YTD
from 2026-05-12". Presentation labels must not imply a promotion, retirement,
allocation, or strategy lifecycle decision.
