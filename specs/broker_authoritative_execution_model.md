Broker-Authoritative Execution Model — Architecture Specification

  Caerus Quant | Alpaca Paper Trading Workflow                                                                                                                                              
  Status: Design Spec — For Review Before Implementation
  Author: Lead Systems Architect                                                                                                                                                            
                                                                                                                                                                                          
  ---
  1. Executive Summary

  The Design Change

  The current workflow treats the internal canonical ledger (canonical_positions.json) as a parallel authority alongside Alpaca broker state. Pre-trade reconciliation compares the two and
  hard-blocks execution when they diverge. This is logically inverted: for a paper-trading account where Alpaca is the custodian of all assets, Alpaca is the only entity that knows what's
  actually in the account. The internal ledger is a convenience cache, not a co-equal authority.

  The new model formalizes the authority split explicitly:

  - Alpaca is authoritative for: current positions, current cash, buying power, and live account valuation
  - The quant model is authoritative for: desired target portfolio, signal rankings, and allocation weights
  - Execution bridges the two: computes the delta from Alpaca actuals to model target, executes it safely and in sequence, then refreshes canonical state from Alpaca post-trade
  - The internal ledger is a derived, cached, audit artifact — not an authority

  Why the Current Model is Brittle

  The current design creates a circular trust problem. The system writes a canonical snapshot from Alpaca state, then at next run, compares Alpaca against that cached snapshot to validate
  it — but if anything went wrong during the write (missed a trade, rounding error, stale write), the snapshot is wrong and now blocks the next legitimate run. The "fix" is manual
  bootstrap, which is itself a fragile out-of-band operation. Every partial execution, interrupted run, or workflow retry creates a fresh opportunity for the snapshot to go stale.

  Additionally, the current design treats all reconciliation failures as structurally equivalent. A 2-cent cash difference from market movement gets the same hard-block response as a
  position quantity mismatch from a missed order — that's wrong.

  Why the Broker-Authoritative Model is Safer

  If Alpaca is always the starting point, the system can never be "out of sync" with reality in a dangerous way. You cannot over-trade because you're computing deltas from confirmed broker
   state. You cannot miss a fill because you're reading confirmed broker positions, not a cached approximation. The internal ledger becomes a derived, verifiable artifact rather than a
  gate. Reconciliation becomes an audit/drift-detection layer, not a permission gate.

  ---
  2. Current-State Diagnosis

  Likely Current Execution Flow

  Based on the repository structure:

  1. Run sleeves, generate signals
  2. Build target allocation (model authority)
  3. pre_trade_reconcile_or_exit() — compare canonical_positions.json vs Alpaca
     → If drift detected: HARD BLOCK (exits)
  4. run_paper_day() — execute orders
  5. refresh_canonical_snapshot_from_broker() — update canonical snapshot
  6. Write artifacts, emails, reports

  The reconciliation check at step 3 is the primary brittleness point.

  Where Drift Is Introduced

  ┌───────────────────────────────────────────────────────────────────────┬────────────────────────────────────────────────────────────┬───────────────────────────────┐
  │                                Source                                 │                         Drift Type                         │           Frequency           │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ Interrupted run mid-execution                                         │ Some orders sent, snapshot not refreshed                   │ Low frequency, high severity  │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ Partial fills on prior day                                            │ Internal ledger records intention, broker records reality  │ Every non-trivial trading day │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ Market price movement                                                 │ Valuation delta between snapshot write and next-day read   │ Every run                     │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ Rounding differences                                                  │ Float precision in cash/equity reconciliation              │ Every run                     │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ GitHub Actions cache miss                                             │ Canonical snapshot from cache is stale by one or more days │ Occasional                    │
  ├───────────────────────────────────────────────────────────────────────┼────────────────────────────────────────────────────────────┼───────────────────────────────┤
  │ bootstrap_model_ledger_from_broker() not called after forced recovery │ Snapshot remains stale                                     │ Operational error             │
  └───────────────────────────────────────────────────────────────────────┴────────────────────────────────────────────────────────────┴───────────────────────────────┘

  Why Pre-Trade Reconciliation Is Too Rigid

  pre_trade_reconcile_or_exit() appears to block on any material divergence between canonical_positions.json and Alpaca broker state. The problems:

  1. Price-movement-induced deltas are legitimate and expected. If SPY moved 1.5% overnight, equity will differ. This should not block.
  2. An empty canonical snapshot on first run (cache miss) should self-heal, not block. The system should bootstrap from Alpaca, not abort.
  3. The canonical file is written by the system itself. Any write failure in a prior run creates a blocking condition for all future runs — a self-inflicted denial of service.
  4. The tolerance model is likely binary (match/no-match) rather than graduated. No distinction between benign drift, stale data, and genuinely dangerous discrepancies.

  Likely Risks in the Current Design

  - Silent stale canonical snapshot: the file exists but reflects state from 2+ runs ago
  - Bootstrap loops: failed bootstraps leaving the system in an unrecoverable state
  - Over-blocking on first-of-month or holiday-adjacent runs
  - No audit trail for WHY reconciliation blocked execution on a given day
  - The self-heal path (AUTO_BOOTSTRAP_ON_RECON_FAIL) may mask real errors by adopting incorrect broker state

  ---
  3. Target Operating Model

  The end-to-end flow for a successful run under the new model:

  Phase A: Pre-Trade Broker Snapshot

  1. Authenticate with Alpaca (fail hard if auth fails — this is a genuine blocker)
  2. Query /v2/account → capture cash, buying power, equity, account status
  3. Query /v2/positions → capture all open positions with current market values
  4. Query /v2/orders?status=open → capture any outstanding orders
  5. Persist this as broker/pretrade_account_snapshot.json and broker/pretrade_positions.json (immutable per-run artifacts)
  6. This is now the authoritative "current state" for the entire run — no further live queries for current state until execution completes

  Hard blocks here (abort run):
  - Auth failure
  - Account not ACTIVE status
  - Position fetch failure
  - Open orders present (configurable: warn or block; default: warn + list)

  Phase B: Target Portfolio Generation

  1. Run sleeve signals (sleeve_trend, etc.) — model authority
  2. Apply VIX regime, risk-off, breaker config
  3. Run portfolio allocator → produce target weights
  4. Convert target weights to target shares at current market prices
  5. This produces the desired end-state — expressed as {ticker: target_shares}

  Target portfolio generation has no dependency on the broker snapshot except for available capital. Capital available for new positions is derived from the broker snapshot.

  Phase C: Delta Computation

  delta = target_state - broker_actual_state

  for each ticker:
    broker_qty = pretrade_positions.get(ticker, 0)
    target_qty = target_portfolio.get(ticker, 0)
    delta_qty = target_qty - broker_qty

    if delta_qty < 0: add to SELL list
    if delta_qty > 0: add to BUY list
    if delta_qty == 0: no action (hold)

  Delta computation is deterministic and fully logged. The execution payload expresses deltas, not absolute positions.

  Produce: execution_payload.json containing sell_orders[], buy_orders[], no_action_tickers[], halt_reason (if any), executable_count, and a hash of the pretrade snapshot it was computed
  against.

  Phase D: Risk Gates

  Before any execution:
  - Gross exposure limits: does the target portfolio exceed max exposure?
  - Per-position caps: does any single position exceed the limit?
  - Sector concentration: per-sector cap enforcement
  - Cash floor: ensure target leaves minimum cash buffer
  - Breaker state: if circuit breaker is active, restrict or block buys
  - Stale signal gate: reject execution if signals are from more than N hours ago

  These are model-side gates, applied to the target portfolio, not reconciliation gates.

  Phase E: Sell-First Execution

  1. Submit all sell orders (market orders for simplicity)
  2. Log each order: client_order_id, ticker, side, quantity, status
  3. Wait for sells to fill or timeout
  4. After fills (or timeout): query Alpaca for updated positions and cash

  Sell completion criteria: all sells in FILLED or CANCELED state, or timeout reached. Record which fills confirmed.

  Phase F: Cash Confirmation and Buy Budget

  1. Query /v2/account for current cash and buying_power after sells
  2. Log this as interim state: broker/postsell_account_snapshot.json
  3. Compute available buy budget: min(confirmed_cash, buying_power) * (1 - cash_buffer_pct)
  4. If sell proceeds less than expected (partial fills): reduce buy budget proportionally, do not abort

  Phase G: Buy-Second Execution

  1. Submit buy orders in descending conviction order (highest-ranked signal first)
  2. Enforce buy budget constraint: if adding next buy would exceed budget, skip
  3. For each buy: log client_order_id, ticker, side, quantity, estimated_value, status
  4. After all buys submitted: record submission results

  Important: Do not wait for buy fills to confirm before closing the run. Alpaca market orders fill asynchronously. The post-trade snapshot captures reality.

  Phase H: Post-Trade Broker Refresh

  1. Query /v2/account → broker/posttrade_account_snapshot.json
  2. Query /v2/positions → broker/posttrade_positions.json
  3. This is the new authoritative canonical state

  Phase I: Persistence and Canonicalization

  1. Write outputs/paper_state/canonical_positions.json from posttrade_positions — not from the execution payload, not from the ledger, from the broker
  2. Write execution_results.json with fill confirmations
  3. Write operator_summary.json with terminal status
  4. Compute post-trade reconciliation (expected vs actual) for audit: broker/recon_posttrade.json
  5. Update ledger, NAV, execution history
  6. Write latest_run.json with terminal status

  ---
  4. Source-of-Truth Contract

  What Alpaca Is Authoritative For

  ┌──────────────────────────────────────┬───────────────────────────────┬────────────────────────────────────┐
  │                Field                 │            Source             │            When Queried            │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Current positions (ticker, quantity) │ Alpaca /v2/positions          │ Pre-trade + post-trade             │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Current cash balance                 │ Alpaca /v2/account            │ Pre-trade + post-sell + post-trade │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Buying power                         │ Alpaca /v2/account            │ Post-sell (before buys)            │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Account equity                       │ Alpaca /v2/account            │ Pre-trade + post-trade             │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Open orders                          │ Alpaca /v2/orders?status=open │ Pre-trade                          │
  ├──────────────────────────────────────┼───────────────────────────────┼────────────────────────────────────┤
  │ Fill confirmations                   │ Alpaca order status           │ During execution                   │
  └──────────────────────────────────────┴───────────────────────────────┴────────────────────────────────────┘

  What the Model Is Authoritative For

  ┌─────────────────────────────┬──────────────────────────────────────┬───────────────────────┐
  │            Field            │                Source                │         Notes         │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Target ticker weights       │ Signal generation + allocator        │ Run each day          │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Target share quantities     │ Model target + current market prices │ Computed from weights │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Execution priority/ordering │ Signal rankings                      │ Buys only             │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Cash buffer requirement     │ config.py / VIX regime               │ Risk parameter        │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Circuit breaker state       │ engine/breaker.py                    │ Safety parameter      │
  ├─────────────────────────────┼──────────────────────────────────────┼───────────────────────┤
  │ Maximum position size       │ trend_cfg / allocator                │ Risk parameter        │
  └─────────────────────────────┴──────────────────────────────────────┴───────────────────────┘

  What Cached Canonical Files Are For

  canonical_positions.json, ledger2.csv, nav2.csv are derived audit artifacts, not authorities. They exist for:
  - Historical performance tracking and NAV computation
  - Cross-run delta verification
  - Morning report / CIO dashboard
  - Offline analysis and research

  They should never block execution by themselves. A mismatch between a cached file and broker state is a signal to refresh, not to halt.

  When Canonical Files Should Be Refreshed

  - Always: at end of every successful run (post-trade, from Alpaca)
  - On startup: if the cached file is older than max_age_hours (recommended: 28 hours), refresh from Alpaca before proceeding
  - On recovery: after any interrupted run, force-refresh before attempting rerun
  - Never: from the execution payload or internal ledger (write from broker or not at all)

  Artifact Classification

  ┌───────────────────────────────────────┬──────────────────────────────────────────┐
  │               Artifact                │              Classification              │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ broker/pretrade_positions.json        │ Execution-critical                       │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ broker/pretrade_account_snapshot.json │ Execution-critical                       │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ execution_payload.json                │ Execution-critical                       │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ execution_results.json                │ Execution-critical                       │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ canonical_positions.json              │ Derived, refreshed post-trade            │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ operator_summary.json                 │ Observability                            │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ latest_run.json                       │ Observability / routing                  │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ broker/posttrade_positions.json       │ Execution-critical (for canonical write) │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ broker/recon_pretrade.json            │ Audit                                    │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ broker/recon_posttrade.json           │ Audit                                    │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ planner_failure.json                  │ Observability / audit                    │
  ├───────────────────────────────────────┼──────────────────────────────────────────┤
  │ ledger2.csv, nav2.csv                 │ Audit / derived                          │
  └───────────────────────────────────────┴──────────────────────────────────────────┘

  ---
  5. Reconciliation Redesign

  New Reconciliation Philosophy

  Pre-trade reconciliation becomes drift detection, not an execution gate. Post-trade reconciliation becomes fill verification, not a success check.

  Drift Classification

  ┌────────────────────────────────────────────────────┬───────────────────────────┬───────────────────────────────────────────────────────┐
  │                     Condition                      │     Current Behavior      │                 Recommended Behavior                  │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Cash difference < $5                               │ Likely blocks             │ Warn only — log in recon_pretrade.json                │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Cash difference < 1% of equity                     │ Likely blocks             │ Warn only                                             │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Cash difference > 5% of equity                     │ Blocks                    │ Self-heal: refresh canonical from broker, log warning │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Missing canonical snapshot (cache miss)            │ Blocks (manual bootstrap) │ Auto-refresh from Alpaca, log, proceed                │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Position quantity mismatch ≤ 1 share rounding      │ Likely blocks             │ Warn only                                             │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Position quantity mismatch (partial fill residual) │ Blocks                    │ Self-heal: adopt Alpaca quantity, log                 │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Valuation delta (live price movement)              │ Blocks                    │ Never block — these are expected                      │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Ticker in canonical but not in Alpaca              │ Blocks                    │ Self-heal if Alpaca shows zero position               │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Ticker in Alpaca but not in canonical              │ Likely warns              │ Self-heal: add to canonical from broker               │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Account status not ACTIVE                          │ May not check             │ Hard block                                            │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Auth failure                                       │ Hard block                │ Hard block                                            │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Alpaca unreachable                                 │ Hard block                │ Hard block                                            │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Open orders from prior session                     │ May not check             │ Hard block with detailed log                          │
  ├────────────────────────────────────────────────────┼───────────────────────────┼───────────────────────────────────────────────────────┤
  │ Account equity below floor                         │ May not check             │ Hard block if at risk                                 │
  └────────────────────────────────────────────────────┴───────────────────────────┴───────────────────────────────────────────────────────┘

  Recommended Gate Structure

  Hard Block (halt execution, write planner_failure.json):
  - Auth failure / Alpaca unreachable
  - Account status not ACTIVE
  - Position fetch returns error (not empty — error)
  - Open orders present for tickers in today's target (configurable)
  - Equity below absolute minimum floor

  Self-Heal + Warn (proceed, log remediation):
  - Missing canonical snapshot → bootstrap from Alpaca
  - Canonical ticker not in Alpaca → remove from canonical
  - Alpaca ticker not in canonical → add to canonical
  - Cash difference within tolerance (< 1% equity)
  - Quantity difference of 0–1 shares

  Warn Only (log, proceed, no mutation):
  - Valuation difference due to price movement
  - Minor cash difference (< $5 absolute)
  - Stale canonical file within 48-hour window

  Recon Artifacts

  broker/recon_pretrade.json should be written on every run regardless of outcome, containing:
  run_id, trade_date, alpaca_positions, canonical_positions,
  per_ticker_delta, cash_delta, equity_delta, drift_classification,
  hard_blocks[], self_heals[], warnings[], reconciliation_decision

  broker/recon_posttrade.json: same structure, comparing execution_payload targets vs actual Alpaca post-trade positions.

  ---
  6. Execution Sequencing Contract

  Canonical Order of Operations

  1. Sell orders submitted (all at once, market orders)
  2. Wait for sell fills [configurable timeout, default: 90 seconds for paper]
  3. Query Alpaca: current cash + buying_power
  4. Compute buy budget from confirmed cash
  5. Buy orders submitted in priority order, budget-constrained
  6. Record all order statuses
  7. Query Alpaca: final positions + account state (post-trade snapshot)

  Fill Confirmation Behavior

  For paper trading with market orders, fills are near-instantaneous. The workflow should:
  - Poll order statuses for sells after submission: every 5s for up to 90s
  - If sell is still PENDING at timeout: record as "fill_pending", treat quantity as zero for buy budget purposes
  - Do NOT wait indefinitely — the run has a hard deadline (market hours)

  Cash / Buying Power Refresh

  After sells settle (or timeout), query Alpaca /v2/account fresh. Do not use the pre-trade snapshot for buy budget — it doesn't reflect sell proceeds. Store this as
  broker/postsell_account_snapshot.json.

  Available buy capital:
  buy_budget = min(confirmed_cash_after_sells, buying_power) * (1 - cash_buffer_pct)

  When Buys Are Allowed to Start

  - All sell orders have reached a terminal state (FILLED, CANCELED, EXPIRED) OR timeout elapsed
  - buy_budget > 0
  - Circuit breaker is not in LOCK mode
  - At least one buy order exists in the payload

  If buy_budget < min_trade_dollars: skip all buys, record as no_buys_insufficient_capital.

  Partial Fill Handling

  Sells: If a sell is partially filled (uncommon for paper trading but possible in live):
  - Record partial fill quantity in execution_results.json
  - Use confirmed filled quantity only for position delta computation
  - Do not assume the remainder will fill — treat as unexecuted

  Buys: If buy rejected or partially filled:
  - Record rejection reason
  - Continue with remaining buy orders (do not abort the buy phase)
  - Post-trade snapshot captures reality

  Insufficient Sell Proceeds

  If post-sell cash < expected (partial fills, commission deduction, etc.):
  - Reduce buy budget to confirmed available capital
  - Skip lowest-priority buys if budget exhausted
  - Record skipped buys in execution_results.json with reason buy_budget_exhausted
  - Do NOT abort the buy phase entirely — execute what is affordable

  Only Some Orders Accepted

  If Alpaca rejects some orders (risk limits, market hours edge cases):
  - Record rejected orders with Alpaca's rejection reason
  - Continue with accepted orders
  - Terminal status: executed_with_rejections (not a failure — this is an expected case)
  - Operator summary flags rejection count

  Rerun Safety

  The run is idempotent if the same run_id produces the same execution_payload.json. Detection:
  1. Before submitting any order, check the sent ledger for today's run_id
  2. If run_id already in sent ledger: terminal status idempotent_replay, skip all execution, post-trade snapshot is refreshed from Alpaca anyway
  3. If orders already in Alpaca for today's tickers (open orders from partial run): hard block, log open order details, require manual resolution or --force-execution override

  ---
  7. Failure / Recovery Modes

  ┌─────────────────────────────────────┬─────────────────────────────┬─────────────────┬───────────────────────┬─────────────────────────────────────────────────────────────┐
  │            Failure Mode             │       Terminal Status       │ Trading Halts?  │  Self-Heal Allowed?   │                     Required Artifacts                      │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Alpaca auth failure                 │ failed_broker_probe         │ Yes             │ No                    │ planner_failure.json, operator_summary.json                 │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Account snapshot failure            │ failed_broker_probe         │ Yes             │ No (retry once)       │ same                                                        │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Positions fetch failure             │ failed_broker_probe         │ Yes             │ No                    │ same                                                        │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Account not ACTIVE                  │ failed_broker_probe         │ Yes             │ No                    │ same                                                        │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Model target generation failure     │ failed_pre_payload          │ Yes             │ No                    │ planner_failure.json, operator_summary.json                 │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Stale market data                   │ no_action or halted         │ Configurable    │ No                    │ operator_summary.json, execution_payload.json               │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Open orders already present         │ halted                      │ Yes             │ No (requires human)   │ operator_summary.json, execution_results.json               │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Pre-trade recon: benign drift       │ success or no_action        │ No              │ Yes (self-heal)       │ recon_pretrade.json                                         │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Pre-trade recon: dangerous mismatch │ halted                      │ Yes             │ No                    │ recon_pretrade.json, planner_failure.json                   │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Sell execution timeout              │ executed_with_partial_fills │ No              │ N/A                   │ execution_results.json, post-trade snapshot                 │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Partial buy execution (rejections)  │ executed_with_rejections    │ No              │ N/A                   │ execution_results.json                                      │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ All orders rejected                 │ executed_with_zero_fills    │ No              │ No                    │ execution_results.json                                      │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Execution timeout (hard wall)       │ failed_pre_execution        │ Yes             │ No                    │ operator_summary.json, whatever was written                 │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Post-trade snapshot failure         │ success (but flagged)       │ No              │ Retry once            │ operator_summary.json with broker_probe_ok=false post-trade │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Post-trade mismatch (significant)   │ success with warning        │ No              │ Self-heal on next run │ recon_posttrade.json with warning                           │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Canonical position write failure    │ success (but flagged)       │ No              │ Retry                 │ operator_summary.json with warning                          │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Email/reporting failure             │ success                     │ No              │ No                    │ All trading artifacts still written                         │
  ├─────────────────────────────────────┼─────────────────────────────┼─────────────────┼───────────────────────┼─────────────────────────────────────────────────────────────┤
  │ Duplicate run detected              │ idempotent_replay           │ Yes (by design) │ N/A                   │ operator_summary.json                                       │
  └─────────────────────────────────────┴─────────────────────────────┴─────────────────┴───────────────────────┴─────────────────────────────────────────────────────────────┘

  Key principle: Reporting failures should NEVER affect trading terminal status. Trading and reporting are independent concerns.

  ---
  8. Operator / Artifact Contract

  Artifact Taxonomy

  Execution-Critical (must be written for the run to have meaning):

  broker/pretrade_account_snapshot.json
  {
    "run_id", "trade_date", "as_of",
    "cash", "buying_power", "equity", "portfolio_value",
    "account_status", "account_number_masked",
    "source": "authoritative_live"
  }

  broker/pretrade_positions.json
  {
    "run_id", "trade_date", "as_of",
    "positions": [{"ticker", "qty", "market_value", "cost_basis", "unrealized_pl"}],
    "position_count", "source": "authoritative_live"
  }

  execution_payload.json
  {
    "run_id", "trade_date", "mode",
    "execution_status",  // NO_ACTION | READY | HALTED
    "halt_reason", "no_trade_reason",
    "pretrade_snapshot_hash",  // hash of pretrade_positions used for delta
    "sell_orders": [{"ticker", "qty", "delta", "reason"}],
    "buy_orders": [{"ticker", "qty", "delta", "target_weight", "signal_rank"}],
    "no_action_tickers": [{"ticker", "reason"}],
    "executable_trades_count", "suggested_sell_count", "suggested_buy_count"
  }

  execution_results.json
  {
    "run_id", "trade_date",
    "status",  // EXECUTED | EXECUTED_WITH_REJECTIONS | IDEMPOTENT_REPLAY | etc.
    "sell_results": [{"ticker", "client_order_id", "alpaca_order_id", "status", "filled_qty", "filled_avg_price"}],
    "buy_results": [...same...],
    "orders_submitted", "orders_accepted", "orders_rejected", "orders_filled",
    "buy_budget_used", "buy_budget_remaining",
    "postsell_cash_confirmed"
  }

  broker/posttrade_account_snapshot.json — same schema as pretrade, but "as_of" reflects post-trade query time.

  broker/posttrade_positions.json — same schema as pretrade, used as source for canonical write.

  ---
  Observability (always written, even on failure):

  operator_summary.json
  {
    "run_id", "trade_date", "mode",
    "terminal_status",  // the 6 defined statuses
    "planner_completed", "execution_payload_written", "execution_stage_reached",
    "pretrade_status",  // NO_ACTION | READY | HALTED
    "broker_probe_ok",  // bool | null
    "suggested_order_count", "submitted_order_count", "accepted_order_count",
    "no_trade_reason", "halt_reason",
    "exception_type", "exception_message",
    "generated_at"
  }

  latest_run.json
  {
    "run_id", "trade_date", "mode", "run_root",
    "status",  // uses terminal_status constants
    "created_at"
  }

  broker/account_probe.json
  {
    "run_id", "broker_name", "probe_attempted", "probe_ok",
    "as_of", "source_type",  // authoritative_live | derived
    "base_url", "error"
  }

  ---
  Audit (written when available, non-blocking):

  broker/recon_pretrade.json — diff between canonical_positions.json and pretrade_positions
  broker/recon_posttrade.json — diff between execution_payload targets and posttrade_positions
  planner_failure.json / preflight_failure.json — early-exit diagnostics (already implemented)

  ---
  Removed / Demoted:
  The canonical_positions.json file should continue to exist but its role changes: it is a derived artifact written from Alpaca post-trade data, not an execution input. Its absence or
  staleness triggers a refresh, not a block.

  ---
  9. Idempotency / Rerun Design

  Order Deduplication

  The system already uses client_order_id for deduplication, which is the correct primitive. The recommended scheme:

  client_order_id = f"{run_id}_{ticker}_{side}"

  This makes each order globally unique per run-id, ticker, and side.

  Open-Order Detection

  At pre-trade time (before execution_payload is built):
  1. Query Alpaca open orders
  2. If any open orders exist for tickers in today's signal universe: hard block by default
  3. Log all open orders in broker/pretrade_open_orders.json
  4. Allow --allow-open-orders flag for operator override with explicit risk acknowledgment

  Already-Filled Orders

  If re-running after a partial completion where some orders already filled:
  1. Query all orders submitted with today's run_id prefix
  2. For already-FILLED orders: exclude from re-submission, record in results as already_filled
  3. For already-PENDING orders: do not re-submit (idempotent), include in pending count
  4. For CANCELED orders: allow re-submission if within force-execution override

  Repeated Payload Execution

  If execution_payload.json already exists for this run_id:
  - Check the sent ledger for this run_id
  - If run_id already in sent ledger: idempotent_replay terminal status
  - If NOT in sent ledger (payload was written but execution never started): allow execution, proceed normally

  The sent ledger (orders_sent.csv) is the idempotency gate. The execution_payload is the intent; the sent ledger is the evidence of commitment.

  Safe Handling of Partial-Completion Runs

  If a previous run started execution but failed mid-way:
  1. On next run, pre-trade open order query will detect leftover open orders → hard block
  2. Operator must inspect and resolve (cancel orphaned orders via Alpaca dashboard or CLI)
  3. After resolution, run with --force-execution to reset the sent ledger for that date
  4. The new run will query Alpaca fresh and compute a new delta from actual current state

  This is intentional. Partial execution states require human verification before proceeding. The system should detect and surface them clearly, not silently resolve them.

  ---
  10. Tolerances / Thresholds

  Recommended Tolerance Values

  ┌───────────────────────────────────┬─────────────────────────────┬───────────┬─────────────────────────────────┐
  │               Field               │         Comparison          │ Tolerance │        Action on Exceed         │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Cash (absolute)                   │ canonical vs Alpaca         │ $10       │ Warn only                       │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Cash (relative to equity)         │ canonical vs Alpaca         │ 1.0%      │ Warn; > 5%: self-heal           │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Equity                            │ canonical vs Alpaca         │ 2.0%      │ Warn only (price movement)      │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Per-position quantity             │ canonical vs Alpaca         │ 1 share   │ Warn only; > 1: self-heal       │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Position count                    │ canonical vs Alpaca         │ 0 (exact) │ Mismatch in count: self-heal    │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Buy budget vs target              │ execution_payload vs actual │ 5%        │ Warn; skip lowest-priority buys │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Post-trade position quantity      │ target vs filled            │ 1 share   │ Warn + log in recon_posttrade   │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Time since last canonical refresh │ age of canonical file       │ 28 hours  │ Auto-refresh from Alpaca        │
  ├───────────────────────────────────┼─────────────────────────────┼───────────┼─────────────────────────────────┤
  │ Signal age                        │ age of signals file         │ 4 hours   │ Warn; > 8 hours: halt           │
  └───────────────────────────────────┴─────────────────────────────┴───────────┴─────────────────────────────────┘

  Timing Tolerances

  - Broker snapshot age within a single run: 10-minute validity window. If execution takes longer than 10 minutes, refresh account state before computing buy budget.
  - Post-sell settle window for cash confirmation: 90 seconds, then proceed with available balance
  - Post-trade snapshot delay: query 15 seconds after last order submission to allow broker to process

  Market Movement During Run

  Valuation differences caused by live price movement during the run window are expected and benign. The system should:
  - Never use valuation comparison as a reconciliation gate
  - Use quantity comparison (shares, not dollar values) for position matching
  - Use confirmed cash from Alpaca API, not a computed estimate

  ---
  11. Migration Plan

  Guiding Principle

  Each phase should be independently deployable and independently rollback-able. No phase should require a simultaneous change to more than 3 files. Each phase should have a clear
  acceptance test.

  ---
  Phase 1: Broker-Authoritative Pre-Trade State Capture

  Objective: Capture authoritative Alpaca state at run start and write it as an immutable per-run artifact. Do not change execution logic yet.

  Changes:
  - daily_quant_report.py: add pre-trade broker query block before run_paper_day()
  - paper/paper_broker.py or new broker/alpaca_snapshot.py: implement fetch_pretrade_snapshot()
  - Write broker/pretrade_account_snapshot.json and broker/pretrade_positions.json
  - Update GitHub Actions artifact upload to include broker/ directory

  Acceptance Criteria:
  - Every run produces pretrade_account_snapshot.json and pretrade_positions.json
  - These files are present in run artifacts for both success and failure runs
  - No change to execution behavior

  Rollback: Delete the two write calls. Zero risk.

  Test Strategy: Integration test that verifies artifacts exist after a run; unit test the snapshot serialization.

  ---
  Phase 2: Reconciliation Redesign

  Objective: Change reconciliation from hard-blocker to graduated drift-detector. Implement tolerance-based classification and self-heal for benign cases.

  Changes:
  - reconciliation.py: introduce classify_drift() returning (WARN | SELF_HEAL | BLOCK) per field
  - Replace pre_trade_reconcile_or_exit() with pre_trade_reconcile_and_classify()
  - Write broker/recon_pretrade.json always
  - Self-heal path: if drift is SELF_HEAL, refresh canonical from Alpaca and proceed
  - Only BLOCK on genuine dangerous conditions (auth fail, account not active, etc.)
  - daily_quant_report.py: update call site to use new function, handle BLOCK vs WARN outcomes

  Acceptance Criteria:
  - A run with a stale canonical file auto-bootstraps and proceeds without operator intervention
  - A run with a missing canonical file auto-bootstraps and proceeds
  - A run with <1% cash drift logs a warning and proceeds
  - A run with auth failure still hard-blocks
  - recon_pretrade.json produced on every run

  Rollback: Keep old pre_trade_reconcile_or_exit() function intact, toggle via env var RECON_V2=1.

  Test Strategy: Unit tests for classify_drift() covering all classification buckets; integration test for bootstrap-and-proceed path.

  ---
  Phase 3: Sell-First / Buy-Second Sequencing Hardening

  Objective: Make the sell → confirm cash → buy sequence explicit and auditable. Add the post-sell cash refresh step.

  Changes:
  - paper/paper_broker.py: split execution into execute_sells() and execute_buys() with explicit cash confirmation step between
  - Add fetch_postsell_account_state() call between sell and buy phases
  - Write broker/postsell_account_snapshot.json
  - Enforce buy_budget from confirmed postsell cash, not estimated cash
  - execution_results.json: add postsell_cash_confirmed and buy_budget_computed fields

  Acceptance Criteria:
  - Execution logs explicitly separate sell phase from buy phase
  - postsell_account_snapshot.json present in every run that reached execution
  - Buy budget computed from confirmed post-sell cash
  - No regression in no_action and halted runs

  Rollback: Consolidate back to single execute call. The new functions can be composed into the old path.

  Test Strategy: Unit test sell→confirm→buy sequencing; test that buy budget is reduced when sell proceeds are below expected.

  ---
  Phase 4: Post-Trade Broker-Refresh Canonicalization

  Objective: Write canonical_positions.json exclusively from Alpaca post-trade data. Remove any path that writes canonical positions from internal ledger computations.

  Changes:
  - reconciliation.py / daily_quant_report.py: ensure canonical_positions.json write always uses posttrade_positions from Alpaca
  - Remove (or clearly gate) any code path that writes canonical from the execution payload or the ledger
  - Write broker/posttrade_positions.json and broker/posttrade_account_snapshot.json
  - Write broker/recon_posttrade.json comparing target vs actual

  Acceptance Criteria:
  - canonical_positions.json always matches posttrade_positions.json (they are written from the same source)
  - recon_posttrade.json produced on every execution run
  - No code path writes canonical from execution_payload

  Rollback: Re-enable old canonical write path via env var. The post-trade Alpaca query is additive.

  Test Strategy: Assert canonical positions == posttrade positions in integration test; unit test posttrade recon comparison.

  ---
  Phase 5: Artifact / Dashboard / Email Harness Updates

  Objective: Surface the new artifacts in the dashboard, morning report, and operator summary.

  Changes:
  - scripts/research/build_quant_dashboard.py: consume pretrade/posttrade snapshots, surface delta, display broker trust level
  - core/operator_summary.py: add broker_pretrade_snapshot_ok, broker_posttrade_snapshot_ok
  - core/trading_day_summary.py: include pretrade/posttrade context
  - web/dashboard/: update dashboard JS/HTML to show broker-authoritative fields
  - GitHub Actions: ensure all new broker/ artifacts are uploaded

  Acceptance Criteria:
  - Dashboard correctly shows "today's run used broker-authoritative state"
  - Morning report includes pre/post-trade position counts and cash
  - Operator summary includes broker snapshot status

  Rollback: Dashboard and email changes are display-only. Safe to roll back independently.

  ---
  12. Risks / Tradeoffs

  Self-Heal vs Adopting Bad Broker State

  Risk: If Alpaca returns stale or incorrect data (rare but possible: API error returns 200 with empty positions), the system adopts an incorrect "canonical" state and executes deltas
  against wrong actuals.

  Mitigation: Validate broker responses before adopting: position count sanity check (if account has non-trivial history, zero positions is suspicious), equity range check (>0 and within
  plausible range), account status explicitly ACTIVE. Fail safe: if response validation fails, hard block.

  Blocking vs Warning

  Tradeoff: Moving recon from hard-block to warn-and-proceed increases execution frequency but reduces safety margin.

  Recommendation: Be conservative in Phase 2. Start with only three cases as self-heal (missing canonical, ticker not in canonical, ticker not in Alpaca). Keep all other cases as warn.
  Loosen tolerance over time as operational confidence grows.

  Paper Trading vs Live Trading Differences

  The design is documented as paper-trading scope, but the architecture should be written as if it might be promoted to live. Key differences:
  - Paper trading: fills are nearly instantaneous → 90s timeout is generous
  - Live trading: fills may take longer, partial fills are common, slippage is real
  - Paper trading: Alpaca API errors are very rare → error handling is defensive but untested
  - Live trading: every error path matters

  Recommendation: Build the error handling correctly now even if paper trading never hits those paths.

  Cache Use vs Live Query Dependence

  Tradeoff: Removing canonical_positions as an authority increases dependence on Alpaca API availability. If Alpaca has a service degradation, the run cannot proceed at all.

  Mitigations:
  - The pre-trade snapshot is written early and can be used as a fallback if a mid-run query fails
  - Log alpaca_api_health at run start for observability
  - For paper trading, this is an acceptable tradeoff (no real capital at risk)

  Operational Simplicity vs Strict Consistency

  The graduated reconciliation model (warn / self-heal / block) is more complex to reason about than the current binary model. Operators need to understand what self-heal did on any given
  run.

  Mitigation: recon_pretrade.json always logs the classification decision and the remediation taken. The dashboard and morning report must surface self-heal events explicitly so they are
  not invisible.

  ---
  13. Final Recommendation

  Recommended Target Design

  The broker-authoritative model as specified: Alpaca is authoritative for current state, the model is authoritative for desired target state, and the canonical position file is a derived
  post-trade artifact — not an authority, not an execution gate.

  Reconciliation becomes a graduated drift detector. Execution becomes explicitly sequenced: sells → cash confirmation → buys → post-trade refresh → canonical write. Every phase of the run
   produces an authoritative immutable artifact.

  Recommended Phased Implementation Order

  Phase 1 (lowest risk, highest value): Broker-authoritative pre-trade snapshot capture
  Phase 2 (highest brittleness reduction): Reconciliation redesign
  Phase 3 (correctness improvement): Sell-first / buy-second sequencing with cash confirmation
  Phase 4 (canonicalization correctness): Post-trade canonical write from broker
  Phase 5 (observability): Artifact / dashboard / email harness updates

  Do not attempt to compress Phase 1+2 into a single deployment. They have different risk profiles and should be validated independently.

  Highest-Risk Area

  Phase 2, the reconciliation redesign, is the highest-risk change. This is the current execution gate. Incorrectly classifying a BLOCK condition as WARN could allow execution against a
  materially incorrect account state. The tolerance thresholds must be reviewed by you before implementation. The self-heal path must be validated against real broker API response shapes
  before deployment.

  Smallest First Change Codex Should Implement After Approval

  Phase 1 only: Add the pre-trade broker snapshot capture block in daily_quant_report.py, write broker/pretrade_account_snapshot.json and broker/pretrade_positions.json as immutable
  per-run artifacts, update the GitHub Actions upload manifest.

  This is additive, non-breaking, zero execution risk, and immediately provides authoritative broker-state artifacts for every run — which all subsequent phases depend on.

  ---
  This spec is ready for review. Once approved, Phase 1 implementation is the recommended first Codex task. All thresholds in Section 10 should be reviewed and adjusted based on observed
  account behavior before Phase 2 is implemented.
