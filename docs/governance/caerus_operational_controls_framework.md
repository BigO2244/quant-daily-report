# FR-075 Operational Controls Framework

Status: DRAFT_CONTROL_INVENTORY
Scope: Documentation only. This framework inventories active operational controls visible in the Caerus codebase as of 2026-06-19. It does not approve new gates, change trading behavior, or supersede existing FR ownership.

## Purpose

FR-075 centralizes the control map for execution safety, reconciliation integrity, sleeve allocation, reliability readiness, promotion surfaces, and governance validation. The goal is operator clarity: when Caerus halts, suppresses orders, routes to cash, emits WARN/FAIL, or marks readiness RED, the triggering control should be traceable to a source file and visible artifact.

## Control Taxonomy

| Category | Meaning | Typical behavior | Primary visibility |
|---|---|---|---|
| Hard execution halt | Prevents execution from reaching broker submission or marks execution `HALTED`/failed | No orders sent, or partial state preserved with halt reason | `execution_payload.json`, `execution_results.json`, `operator_summary.json`, logs |
| Buy suppression | Prevents some or all BUY orders while allowing sells or prior submitted orders to stand | BUY orders skipped, blocked, deferred, or clipped | `alpaca_submission_summary`, `cash_gate_diagnostics`, `operator_summary.json` |
| Sell suppression | Prevents sell order submission or defers sells before submission | Sell orders skipped/deferred; may avoid PDT/broker risk | `pdt_pretrade`, `blocked_reasons`, `operator_summary.json` |
| Cash routing | Converts residual or invalid allocation into CASH | Target CASH position or higher achieved cash | allocation output, daily snapshot, target-attainment report |
| WARN diagnostic | Operator attention required; not necessarily execution-blocking | Status `WARN`, warning flags, reliability YELLOW | reliability report, health banner, dashboard |
| FAIL diagnostic | Blocking or severe integrity condition | Status `FAIL`, reliability RED, deployment stop unless accepted | reliability report, integrity audit, validation output |
| Readiness gate | Promotion/deployment readiness surface; Phase B is observe-first unless otherwise stated | GREEN/YELLOW/RED or PASS/WARN/FAIL classification | readiness artifacts, dashboard, governance docs |

## Control Inventory

| Control name | Source file | Trigger condition | Severity | Resulting behavior | Related FR | Operator visibility |
|---|---|---|---|---|---|---|
| Precompute bundle availability gate | `scripts/run_precomputed_alpaca_execution.py`; `core/precompute_contract.py` | Missing `outputs/precompute/<trade_date>` bundle, invalid contract, wrong date/mode, incomplete files, not `validated_for_execution` | FAIL | Writes halted payload/email/operator summary; exits before broker execution | FR-074-adjacent; precompute governance | `pretrade_status=HALTED`, `pretrade_halt_reason`, latest run pointer |
| Exact planned payload schema gate | `scripts/run_precomputed_alpaca_execution.py` | Planned payload missing/malformed, date mismatch, status not `PLANNED`, invalid trade row, missing price/notional, count mismatch | FAIL | Raises terminal exception; persisted as halted pre-execution state | FR-074 Phase A | `execution_payload.json`, `operator_summary.json`, exception fields |
| Planned-payload security master gate | `scripts/run_precomputed_alpaca_execution.py`; `core/security_master.py` | Exact planned payload symbols fail security-master resolution | FAIL | Runtime error before execution; terminal state persisted | FR-073/FR-074 adjacent | `halt_reason=planned_execution_payload_security_master_resolution_failed:*` |
| Single-flight execution lock | `scripts/run_precomputed_alpaca_execution.py` | Existing `outputs/execution_locks/<date>.lock` and not explicit buy-only continuation | FAIL / safe temp exit | Aborts with exit code 75 to prevent duplicate orders | FR-070/FR-074 operational reliability | Logs `[LOCK]`; no new execution |
| Pretrade reconciliation block | `scripts/run_precomputed_alpaca_execution.py`; `reconciliation.py` | `pre_trade_reconcile_and_classify()` returns `BLOCK`, `SELF_HEAL` follow-up remains blocking, or unknown non-PASS decision | FAIL | Writes halted payload and operator summary; exits before broker execution | FR-070 | `pretrade_reconciliation_decision`, `pretrade_reconciliation_report_path`, `pretrade_halt_reason` |
| Position reconciliation hard fail | `reconciliation.py` | Missing broker/model symbols, quantity mismatches, parse/errors in model/broker state | FAIL | Reconciliation verdict `FAIL`; can drive pretrade block | FR-070 | `recon_pretrade_<date>.json`, `recon_posttrade_<date>.json`, operator summary |
| Cash/equity drift reconciliation warning | `reconciliation.py` | Cash or equity delta breaches tolerance but positions are clean | WARN by default; FAIL if hard-fail flag enabled | Warns without blocking by default | FR-070 | reconciliation report verdict and block reason class |
| Canonical snapshot recovery | `reconciliation.py` | Preferred canonical snapshot missing but legacy snapshot usable | WARN/Recovery | Rewrites preferred canonical snapshot from legacy source | FR-070 | reconciliation source notes |
| Broker preflight policy warning | `brokers/alpaca_snapshot.py`; `scripts/run_precomputed_alpaca_execution.py` | Account not active, blocked flags, non-positive buying power, PDT markers | WARN | Observe-first in runner; surfaces risk flags | FR-070/FR-074 | broker preflight banner, `operator_summary.json` |
| PDT risk warning | `brokers/alpaca_snapshot.py`; `paper/paper_broker.py` | Pattern day trader, high daytrade count, non-positive daytrading buying power | WARN | Operator-visible; may defer planned sells in broker path | FR-070 | `broker_pdt_*`, logs `[PDT][WARN]` |
| Market/session guard | `paper/paper_broker.py`; `daily_quant_report.py` | Market closed, weekend, not in trading session, plan-only mode | HALTED/PLANNED/SKIPPED_WEEKEND depending path | Avoids live submission outside allowed session | Legacy execution control | `market_guard`, `execution_status`, `market_reason` |
| Open-window validation | `paper/paper_broker.py` | Execution outside configured workflow/open window | WARN/blocked metadata depending validation result | May add blocked reasons or validation details | Timing policy / FR-074 adjacent | `open_window_validation`, `operator_summary.json` |
| Asset validation block | `paper/paper_broker.py` | Alpaca asset lookup finds invalid/non-tradable symbols or validation errors | FAIL | Blocks execution, clears orders, marks buys blocked with `buy_blocked_asset_validation_failed` | FR-070/FR-074 | `asset_validation_<date>.json`, `asset_validation_status`, operator summary |
| Security master runtime resolution | `paper/paper_broker.py`; `core/security_master.py` | Execution trade symbols unknown, inactive, non-tradable, stale universe warnings | FAIL or WARN | FAIL blocks execution; WARNs surface stale/unavailable universe | FR-073-adjacent | `security_master_resolution_*`, `blocked_reasons` |
| Planned payload drop invariant | `scripts/run_precomputed_alpaca_execution.py`; `core/operational_invariants.py` | Planned payload count > 0 but executable count or submitted count is 0 without explicit halt reason | FAIL; RELIABILITY_RED | Marks execution `HALTED` with `planned_payload_trades_dropped_before_execution`; reliability RED | FR-074 Phase A | reliability report, operator summary, execution results |
| Execution status reason invariant | `core/operational_invariants.py` | Terminal `NO_ACTION`, `SKIPPED`, `HALTED`, `FAILED`, or partial state lacks explicit reason and upstream intent exists | FAIL; RELIABILITY_RED | Observe-first reliability failure; prevents misleading “reason none” reporting | FR-074 | reliability report top failure reason |
| Submitted-without-acceptance invariant | `core/operational_invariants.py` | `submitted_count > 0` and `accepted_count == 0` | FAIL; RELIABILITY_RED | Observe-first critical reliability failure | FR-074 | reliability report, operator actions |
| Accepted-zero-fills unresolved invariant | `core/operational_invariants.py` | Accepted orders exist, zero fills confirmed, unresolved order status remains | WARN; RELIABILITY_YELLOW unless other FAIL | Observe-first warning; operator action to refresh broker state | FR-074 | reliability report |
| Target cash drift invariant | `core/operational_invariants.py`; `core/execution_target_attainment.py` | Actual/achieved cash weight differs from target beyond tolerance | WARN; can contribute to YELLOW | Surfaces underdeployment/cash drift; no trading change | FR-070/FR-074 | target-attainment artifact, reliability report |
| Model/broker reconciliation invariant | `core/operational_invariants.py` | Posttrade reconciliation status is present and not clean | FAIL; RELIABILITY_RED | Observe-first critical reliability failure | FR-074 | reliability report, top failure reason |
| Required precompute artifact invariant | `core/operational_invariants.py` | Planned payload execution expected but precompute payload missing or stale/failed markers exist | FAIL; RELIABILITY_RED | Observe-first fail-closed recommendation | FR-074 | reliability report |
| Sleeve numeric finiteness invariant | `core/operational_invariants.py`; `core/sleeve_numeric_diagnostics.py`; `daily_quant_report.py` | Sleeve numeric trace status `BLOCKING`/`FAIL`/`FAILED`, non-finite terminal equity/strength/input value | FAIL; RELIABILITY_RED | Keeps affected sleeve output out of clean execution interpretation; upstream may route allocation to cash | FR-073/FR-074 | `sleeve_numeric_trace_*`, reliability report |
| Execution integrity intended-vs-payload gate | `core/execution_integrity.py` | Intended order count differs from payload count without explicit exception | FAIL | Integrity audit FAIL; folds into reliability RED | FR-074 | `audit/execution_integrity.json`, operator summary |
| Intended BUY missing from payload | `core/execution_integrity.py` | Intended BUY absent from execution payload without block/defer/continuation metadata | FAIL | Integrity audit FAIL; folds into reliability RED | FR-074 | execution integrity findings |
| Pending buys without submitted buys | `core/execution_integrity.py` | `pending_buy_count > 0` and `submitted_buy_count == 0`; severity escalates if run appears success/ready | WARN or FAIL | Prevents clean-success interpretation | FR-070/FR-074 | execution integrity findings |
| Broker response/count consistency | `core/execution_integrity.py` | Broker response count, accepted count, rejected count, or submitted/accepted counts disagree | WARN | Integrity audit warning; reliability may become YELLOW | FR-074 | execution integrity findings |
| Buy-only continuation sell guard | `core/execution_integrity.py`; `scripts/run_precomputed_alpaca_execution.py` | Buy-only continuation payload contains SELL orders | FAIL | Integrity audit FAIL; continuation loader filters only BUY rows | FR-070/FR-074 | execution integrity findings, continuation metadata |
| Continuation eligibility guard | `scripts/run_precomputed_alpaca_execution.py` | Pending buys exist after submitted orders, before deadline, no broker reject, allowed outcome/reason, and capital allows | Gate / WARN if missing source | Only allows explicit buy-only continuation under narrow conditions | FR-070 | `continuation_*`, `pending_buy_orders` |
| Retry eligibility guard | `core/live_retry_policy.py`; `scripts/run_precomputed_alpaca_execution.py` | No previous retry, zero submissions, within auto-trade window, retryable reason | Gate | Allows one retry for retryable non-submission failures; blocks retry after submissions/deadline | FR-070/FR-074 | `retry_eligible`, `retry_reason` |
| Sell phase completion gate | `paper/paper_broker.py` | Sell orders do not resolve to terminal status or sell observation fails/times out | FAIL/PARTIAL | Blocks remaining buys; records sell phase reason | FR-070 | `sell_phase_status`, `sell_phase_completion_reason`, `buy_phase_block_reason` |
| Post-sell buy budget gate | `paper/paper_broker.py` | Post-sell cash/buying power/risk cash target leaves no or insufficient buy budget | Buy suppression; PARTIAL if sells submitted | Clips/skips buys, records `buy_blocked_*`; may mark cash rebalance incomplete | FR-070 | `post_sell_rebudget`, `budget_skipped_orders`, `cash_gate_diagnostics` |
| Pending-sell budget exclusion | `paper/paper_broker.py` | Pending sells remain at buy decision; notional excluded from buy budget unless explicitly allowed | Buy suppression | Prevents unsafe buy continuation before sell proceeds authoritative | FR-070 | `pending_sell_count_at_buy_decision`, rebudget reason codes |
| Broker rejection policy | `paper/paper_broker.py`; Alpaca broker policy modules | Alpaca rejects an order, e.g. PDT/broker asset/account rejection | FAIL/PARTIAL | Aborts remaining order flow according to reject policy, preserves submitted subset | FR-070 | `broker_reject_status`, `broker_reject_message`, `execution_outcome` |
| Post-submit artifact failure | `paper/paper_broker.py`; `scripts/run_precomputed_alpaca_execution.py` | Submitted orders exist but posttrade state capture/reconciliation artifacts fail | PARTIAL / retry-compatible | Marks partial/post-submit artifact failure; may allow continuation if otherwise safe | FR-070 | `artifact_failure_stage`, `execution_outcome`, retry metadata |
| Buy fill observation contract | `scripts/run_precomputed_alpaca_execution.py` | Submitted buys exist and posttrade evidence exists but fill observation fields are missing | WARN/PARTIAL diagnostic | Marks buy status unknown and unresolved count; reliability may WARN | FR-070/FR-074 | `buy_phase_status`, `buy_fill_*`, reliability report |
| Final reconciliation override | `core/execution_payload.py` | Raw partial broker-abort plus OK posttrade reconciliation, no skipped/blocked/pending buys, no rejects, submissions exist | Recovery classification | Surfaces `RECONCILED_SUCCESS` while preserving raw status/reason | FR-070 | `raw_*`, `final_*`, `reconciliation_override_applied` |
| Target-attainment incomplete execution | `core/execution_target_attainment.py` | Execution incomplete, missing artifacts, stale/pre-buy snapshot, cash drift, or reconciled target miss | UNKNOWN/WARN/FAIL | Diagnostic only; feeds reliability cash drift invariant | FR-070/FR-074 | `execution_target_attainment_<date>.json` |
| Risk-off stash allocation | `core/portfolio_alloc.py` | Allocator `risk_off` true | Cash/stash routing | Routes to stash sleeve or 100% CASH if stash unavailable | Legacy allocation control | allocation result, cash reason |
| No eligible assets cash route | `core/portfolio_alloc.py` | Combined allocation empty after sleeve outputs/constraints | Cash routing | 100% CASH with `NO_ELIGIBLE_ASSETS` | FR-073-adjacent | allocation output, `cash_reason` |
| Prepare-for-next-day cash route | `core/portfolio_alloc.py` | `prepare_for_buy_next_day` true | Cash routing | Residual kept as CASH with `PREPARE_FOR_BUY_NEXT_DAY` | Legacy allocation control | allocation output, `cash_reason` |
| Constraint residual cash route | `core/portfolio_alloc.py` | Position caps/turnover/min gross constraints leave residual weight | Cash routing | Residual stays CASH with `CONSTRAINTS`; avoids renormalizing past limits | Legacy allocation control | allocation result, skipped trades |
| Max-position cap | `core/portfolio_alloc.py` | Absolute target weight exceeds `max_position_pct` | WARN / allocation constraint | Caps position; residual can route to cash | Legacy allocation control | skipped trades, allocation summary |
| Turnover cap | `core/portfolio_alloc.py` | Turnover exceeds `max_turnover` | WARN / allocation constraint | Scales target changes; may suppress buys/sells relative to target | Legacy allocation control | skipped trades |
| Sleeve budget redistribution | `core/portfolio_alloc.py` | Sleeve allocation exceeds absorbable capacity under max position cap | WARN / allocation constraint | Redistributes excess to sleeves with headroom; residual may route to cash | Legacy allocation control | skipped trades |
| Allocation validation warning | `core/portfolio_alloc.py`; `daily_quant_report.py` | Allocation result fails validation checks | WARN | Logs allocation validation errors; no direct broker change in this layer | Legacy allocation control | logs `[WARN] Allocation validation errors` |
| Sleeve output validity check | `daily_quant_report.py`; `core/sleeve_numeric_diagnostics.py` | Empty equity, missing equity column, zero rows, non-finite terminal equity, non-positive equity | WARN/FAIL-like sleeve invalidation | Invalid sleeve output excluded; non-finite trace written when run context exists | FR-073 | sleeve route logs, numeric trace artifact |
| SGOV cash-proxy filter | `daily_quant_report.py` | Trade row `ticker=SGOV` and reason exit `cash_proxy_fund_entries` | Trade suppression | Removes cash-proxy entries from trade list | Legacy cash proxy control | trade output/report |
| Breaker allocation diagnostics | `daily_quant_report.py` | Breaker/risk controls active | WARN/diagnostic | Adjusts/surfaces allocation diagnostics; can influence proposed trades | Legacy risk control | daily snapshot/report |
| Reliability classification | `core/operational_invariants.py` | Score/invariant results after execution | GREEN/YELLOW/RED | Observe-first readiness classification; no trading change in Phase B | FR-074 Phase B | reliability report, operator summary, execution results |
| Reliability RED | `core/operational_invariants.py` | Score < 80 or any FAIL invariant | RED | Promotion-readiness warning; no automatic trading block yet | FR-074 Phase B | `execution_reliability_report_*`, `reliability_readiness.json` |
| Reliability YELLOW | `core/operational_invariants.py` | Score 80-94, WARN allowed, no FAIL | YELLOW | Operator attention; no trading block | FR-074 Phase B | reliability report/readiness |
| Reliability GREEN | `core/operational_invariants.py` | Score >= 95 and no FAIL | GREEN | Clean readiness classification | FR-074 Phase B | reliability report/readiness |
| Reliability history ledger | `core/operational_invariants.py` | Every reliability report write | Ledger | Appends score/classification/fail/warn/top reason | FR-074 Phase B | `outputs/reliability/reliability_history.json` |
| Reliability readiness surface | `core/operational_invariants.py` | Every reliability report write | Readiness | Writes current classification, score, streak, days since fail, last fail reason, trailing 20 score | FR-074 Phase B | `outputs/reliability/reliability_readiness.json` |
| Dashboard system health console | `scripts/research/build_dashboard_v1.py` | Health checks contain RED/FAIL/ERROR or WARN/YELLOW, missing health checks, stale shadow NAV | PASS/WARN/FAIL | Dashboard diagnostic; no runtime change | Dashboard governance | dashboard JSON/HTML |
| Shadow strategy promotion readiness | `scripts/research/build_dashboard_v1.py` | Insufficient valid days, no data/status issue, challenger behind baseline excess | WATCHLIST/NOT_READY/PROMOTION_ELIGIBLE | Research/promotion surface only | FR-028/FR-069 adjacent | dashboard shadow command center |
| Live readiness dashboard | `scripts/research/build_dashboard_v1.py` | Validation failures, missing artifacts, stale shadow continuity, operational health fail/warn | PASS/WARN/FAIL; deployment confidence WATCH/HIGH | Readiness diagnostic only | Dashboard governance | dashboard live readiness section |
| Options overlay paper review gate | `core/options_overlay_paper.py` | Shadow inactive, allocator gate not ready, recommendation infeasible, shadow status not ready | WATCH/blocked/readiness states | Review-only; does not place paper options trades | Options overlay governance | options overlay paper review JSON/MD |
| Operational validation deployment gate | `scripts/operational_validation.py`; `docs/operational_validation.md` | Workflow YAML parse errors, mutable GitHub Action tags, workflow `contents: write`, missing Dependabot coverage | WARN/FAIL | Deployment should stop on FAIL unless operator accepts risk | FR-008 / governance | `[OPERATIONAL_VALIDATION]`, JSON/console |
| VM deployment attestation gate | `scripts/deploy.sh`, `scripts/finalize_deployment.py`, `scripts/ops/run_vm_validation.sh` | Dirty VM tree, non-fast-forward target, candidate validation failure, missing/invalid v2 full-SHA attestation, attestation/HEAD drift, missing VM venv/python/pytest | FAIL | Candidate is not published; routine validation and live submission fail closed | Governance/deploy/live safety | `outputs/deploy_state.json`, `[VM_VALIDATION][FAIL/PASS]` |
| AIOps verify gate | `aiops/verify.py` | Spec parse failure, invalid mode, Python unavailable, pytest failure in BUILD/HARDEN | FAIL | Writes approval pack with gate outcomes; non-zero verify | AIOps governance | `reports/ai_runs/<run_id>` |

## Question-Oriented Summary

### 1. What can halt execution?

- Missing/invalid precompute bundle.
- Exact planned payload validation failures.
- Planned payload security-master resolution failure.
- Execution lock collision.
- Pretrade reconciliation `BLOCK` or unresolved `SELF_HEAL`.
- Market/session guards and open-window validation when treated as blocking by the broker path.
- Asset validation failure.
- Security-master runtime resolution failure.
- Planned payload drop invariant.
- Terminal exceptions from stale prices or other `[HALT]` exception paths.
- Broker rejection policy and post-submit artifact failure can halt remaining flow after partial submissions.

### 2. What can suppress buys?

- Post-sell buy budget and risk cash target safeguards.
- Pending sell state at buy decision.
- Insufficient buying power/cash.
- PDT/broker reject policy.
- Asset validation failure.
- Security-master resolution failure during original or rebudgeted buy construction.
- Buy-only continuation eligibility guard.
- Buy-fill observation/post-submit artifact failure after partial execution.
- Turnover/position constraints before execution can reduce buy targets.

### 3. What can suppress sells?

- Market/session/plan-only guards.
- Pretrade reconciliation failure before any broker submission.
- Asset/security-master failures before submission.
- PDT preflight sell deferral path.
- Exit-only/runtime constraints can alter the order mix.
- Turnover constraints can reduce sell target changes before execution.

### 4. What can force cash?

- Risk-off stash unavailable or residual reserve.
- No eligible assets.
- `PREPARE_FOR_BUY_NEXT_DAY`.
- Position caps, turnover constraints, max gross exposure limitations, or uncappable sleeve residual.
- Invalid/non-finite sleeve output excluded from allocation.
- Cash-proxy filtering/legacy cash proxy logic.
- Risk cash target and post-sell buy budget can leave higher actual cash after execution.

### 5. What can generate WARN?

- Cash/equity reconciliation drift without position mismatch.
- Broker preflight/PDT risk flags.
- Broker response/count mismatches.
- Intended/payload mismatch with explicit exception metadata.
- Pending buys without submitted buys when not falsely marked success.
- Cash target drift.
- Accepted orders with zero fills and unresolved state.
- Target-attainment underdeployment/stale snapshot.
- Allocation weight sum/validation warnings.
- Dashboard health, artifact completeness, shadow continuity, and live readiness issues.
- Operational validation non-blocking findings.

### 6. What can generate FAIL?

- Reconciliation position mismatch or model/broker parse errors.
- Missing/invalid precompute/exact payload failures.
- Asset validation failure.
- Security-master resolution failure.
- Intended-vs-payload mismatch without explicit exception.
- Intended BUY missing from payload without explicit exception.
- Buy-only continuation containing SELL orders.
- Reliability invariants with critical failures.
- Operational validation deployment failures.
- VM validation failures.

### 7. What can create RELIABILITY_RED?

- Any FAIL invariant in `core/operational_invariants.py`.
- Score below 80.
- Folded execution-integrity FAIL findings.
- Planned-payload nonempty zero execution.
- Submitted orders with zero acceptance.
- Model/broker reconciliation not clean.
- Missing/stale required precompute artifacts.
- Blocking sleeve numeric traces.
- Terminal skipped/halted/failed/no-action status missing reason.

### 8. What controls exist around reconciliation?

- Pretrade reconciliation compares broker positions/account state to canonical model state before broker execution.
- Position mismatches always FAIL.
- Cash/equity drift WARNs by default and can be made hard-fail via explicit setting.
- `SELF_HEAL` reruns reconciliation after canonical refresh.
- Posttrade reconciliation artifacts feed target-attainment and reliability.
- Final execution status can upgrade raw partial to `RECONCILED_SUCCESS` only when posttrade reconciliation is OK, there are no skipped/blocked/pending buys, no rejects, and submissions exist.
- Repair suggestions and affected symbols surface in operator summary and paper repair helper.

### 9. What controls exist around sleeve allocation?

- Sleeve validity requires non-empty equity data, equity column, finite positive terminal equity.
- Non-finite sleeve traces are written with reason code and downstream effect.
- Inactive/invalid sleeves receive zero active allocation.
- Sleeve budget is allocated by active sleeve strength, with equal weighting if total strength is non-positive.
- Max-position caps, turnover caps, min-gross-exposure boost, and uncappable sleeve redistribution constrain target weights.
- Residual allocation routes to CASH rather than breaking risk limits.

### 10. What controls exist around promotion readiness?

- FR-074 reliability readiness writes current classification, score, clean-run streak, last fail reason, and trailing 20-day score.
- Dashboard shadow readiness marks candidates `CONTROL`, `WATCHLIST`, `NOT_READY`, or `PROMOTION_ELIGIBLE` based on valid days, status/data availability, and challenger excess return.
- Dashboard live readiness combines validation integrity, artifact completeness, shadow continuity, and operational health into deployment confidence.
- Options overlay paper review requires active shadow trigger, feasible recommendation, and allocator review gate not `not_ready`.
- Operational validation and VM validation are deployment gates, not strategy promotion gates.

## Coverage Gaps

1. Reliability readiness is still observe-first. `RELIABILITY_RED` does not automatically block trading.
2. Control ownership is distributed across runner, broker, reconciliation, allocator, dashboard, and governance scripts; there is no machine-readable control registry.
3. Several controls emit logs and summary fields but do not share a common severity enum.
4. Sell suppression controls are less explicitly enumerated than buy suppression controls.
5. Promotion readiness surfaces exist in multiple systems but are not unified with FR-074 reliability history.
6. Some legacy allocation/risk controls are not mapped to FR identifiers.
7. Incident retention policy for broker/reconciliation/runtime evidence is documented directionally but not enforced by code.
8. Operational validation covers deployment governance, not runtime artifact completeness or reliability trends.

## Recommended Next Controls

1. Build a deterministic `controls_registry.json` from this document with control IDs, owner files, severities, artifacts, and related FRs.
2. Add an operator-reviewed Phase C reliability gate: block promotion if `RELIABILITY_RED` appears in trailing 20 runs or clean-run streak is below threshold.
3. Add a daily controls summary artifact under `outputs/runs/<RUN_ID>/audit/operational_controls_<TRADE_DATE>.json`.
4. Normalize WARN/FAIL/RED semantics across reconciliation, execution integrity, reliability, dashboard, and deployment validators.
5. Add explicit sell-suppression reason codes equivalent to buy-phase reason codes.
6. Add retention checks for broker snapshots, reconciliation reports, execution payloads, reliability reports, and incident bundles.
7. Add dashboard ingestion of FR-074 reliability readiness so promotion surfaces and reliability surfaces are visible together.
8. Add tests that prove every hard execution halt writes a non-empty reason, operator action, and artifact pointer.
