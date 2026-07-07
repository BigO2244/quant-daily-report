# Phase 2 Live-Path Behavior Changes — FR Provenance

Workstream C moves the live pilot onto the shared Transition Engine (Option A). This
records every behavior change on the live path, each **tagged to its FR / doctrine
source**, so none is carried as an unattributed inherited engine default. Approved by
the operator on 2026-07-06.

---

## 1. max_orders selection: precompute-index → target-weight priority

**Change.** With `max_orders=1`, the single buy is selected by **highest target weight**,
not by precompute row index. Can change which symbol is bought vs. the old builder.

**Source.** Phase 2 prompt Task 1.3 (explicit); Architecture V2.1 §12–13 (engine owns
selection). **Approved FR decision.** Kept.

**Evidence.** `Tests/test_live_pilot_transition_phase2.py::test_clean_slate_selects_highest_weight_and_halts_dry_run` (selects BBB @ w=0.3 over AAA @ w=0.2, which is listed first). Transcript Scenario 2.

---

## 2. $100 min-trade floor now governs real live orders

**Change.** A sub-$100 residual need now produces **no order** (previously live had no
min-notional; the old builder could emit a dust order).

**Source.** Phase 2 prompt Task 1.4 (explicit) + confirmed decisions line 14
(`min_trade_usd=100.0`); Architecture V2.1 §14 / Decision Log #9 (order_policy: shares,
`min_trade_usd = 100`, market DAY). **Approved FR decision.** Kept.

**Evidence.** `test_live_pilot_transition_phase2.py::test_clean_slate_min_trade_floor_blocks_dust` (a $50 target → `live_pilot_transition_no_actionable_buy`, no submit).

---

## 3. (a′) Incremental top-up of a held target name WITHOUT rotation

**Change.** The merged capital gate blocked **any** buy while holding **any** position
(`required_sell_count = len(positions)`). The engine instead blocks only when a **sell is
actually required** (`required_sell_count = len(sell_intents)`): a held name that is still
in the target and merely undershoots it is topped up **incrementally** (no rotation), and
`required_sell_count` reports actual exits/reduces.

**Doctrine confirmation (operator-required).** This matches V2.1 exactly:

- **§13.3 — "Existing target holdings count."** *"If the account holds ALL and ALL remains
  a target, the engine computes whether current exposure satisfies, exceeds, or undershoots
  the target. It never blindly buys another share."* → The engine computes incremental need
  `max(0, target_shares − current_shares)` and buys only the shortfall. ✔ exact match.
- **§13.4 — "Sells before buys."** *"If capital is tied up in positions being reduced/closed,
  sell orders precede buy orders."* → Rotation is defined by positions being reduced/closed
  (i.e. sell intents), not by "holding anything." A pure top-up ties up no capital in a
  sell, so no rotation is required. ✔ match.
- **§32 (Phase 2) — "avoid duplicate exposure."** → Incremental sizing *is* the
  duplicate-exposure avoidance; the merged gate's block-any-hold was stricter than doctrine.
  ✔ match.
- **Option A.** Applies when rotation **is** required (a sell is needed) → block
  `EXISTING_POSITIONS_REQUIRE_ROTATION`. A no-sell top-up is not rotation, so Option A does
  not block it. If **any** exit/reduce exists alongside the target, rotation is required and
  the whole run blocks (including the top-up) — so the top-up is only ever permitted when
  there are zero sells. ✔ consistent.

**No gap found**, so the engine semantics are adopted per the operator's condition and
recorded here as an **explicit FR decision** (not an inherited default). Had a gap
existed, the pilot would have defaulted to the stricter merged-gate behavior.

**Evidence.** `test_live_pilot_transition_phase2.py::test_held_and_targeted_buys_only_incremental` (hold 1 sh AAA, target 3 → buys 2, not 3; `holdings_to_sell == []`). `test_july6_variant_blocks_on_buying_power_when_no_rotation` (hold only ALL, no rotation, blocks on buying power not rotation).

---

## 4. (c) Over-cap intent: fail-closed HALT (not clip-and-deploy)

**Change.** An intent whose **full incremental need exceeds the approved cap** halts the
run (`live_pilot_total_notional_exceeds_cap`, no order) rather than being right-sized to
the cap. The shared engine's default is clip-and-deploy (paper semantics); the live lane
overrides it with a fail-closed halt.

**Source.** **Operator decision 2026-07-06** (fail-closed first for a first live-money
run; "an over-cap intent is an anomaly to halt on, not silently right-size"). Consistent
with V2.1 Principle 8 ("fail closed for capital risk") and §13.1 (cap is a ceiling). The
clip-and-deploy relaxation is explicitly **deferred** until live behavior is trusted.
Implemented as `BLOCK_OVER_CAP_INTENT` in `scripts/live_pilot_transition.py`
(`_apply_over_cap_policy`), preserving the original `live_pilot_total_notional_exceeds_cap`
reason code.

**Evidence.** `test_live_pilot_execution_path.py::test_over_cap_plan_does_not_submit_and_writes_operator_action` (6 sh @ $100 = $600 > $500 cap → BLOCKED, no submit). Transcript Scenario 3.

---

## Structural changes (not behavior, for reviewer context)

- `_build_live_pilot_capital_gate` is now a **thin wrapper** over the engine's
  `TransitionPlan` (Task 2.2), preserving the `live_pilot_capital_gate.v1` schema; it no
  longer re-implements capital logic. `required_sell_count` semantics shift per §3 above.
- The engine runs **after the account-hash match and before plan validation / open-order /
  market-hours** (the sequence specified by Task 2.1), replacing the old post-market-hours
  capital-gate position and the builder's buy-only narrowing.
- The builder emits a full `target_portfolio` block (schema `caerus.transition_target.v1`),
  additive; the legacy single-order fields remain for backward compatibility.

## Cap semantics (unchanged invariant)

Across all changes, the approved cap is a **ceiling, never spendable cash**:
`tradable_capital = min(cash, broker_buying_power, approved_cap, per-name need)`. The
July-6 forbidden outcome (buy justified by cap alone) remains impossible.

---

## 5. Fail-closed capital-safety guards (multi-round code review, 2026-07-06)

Four adversarial review rounds hardened the live path. All guards live in
`scripts/live_pilot_transition.py::compute_live_transition`, run **after** the shared
engine, in priority order **equity-regime → unpriceable-holdings → buying-power →
over-cap**, and each returns a fail-closed `_blocked_plan` (no buys, `deployed_buy_notional=0`).

**Fail-closed contract (operator directive 2026-07-06; V2.1 Principle 8 "fail closed for
capital risk", Principle 2 "broker truth beats internal assumptions").** On the live
lane, any missing / ambiguous / degenerate broker fact HALTS rather than proceeds. The
shared engine is paper-oriented (it falls back to cash and clips to fit); the live guards
override those defaults where they would be unsafe.

### 5a. Shared real-holding predicate — closes the rotation bypass *by construction*
The rotation guard and `holdings_from_snapshot` now derive from **one** predicate,
`_position_is_real_holding` / `_holding_rejection_reasons` — not two parallel field lists
that merely agree. A snapshot position counts as a real open holding **iff**: truthy
`symbol` **AND** finite non-zero `qty` **AND** finite positive `market_value` **AND** a
finite-positive derived `price = abs(market_value/qty)`. `holdings_from_snapshot` includes
exactly the positions that pass; the guard blocks (`EXISTING_POSITIONS_REQUIRE_ROTATION`)
on any Mapping position that fails. Because both sites key off the identical predicate —
including the exact reference price the engine's exit loop uses (`if price <= 0: continue`)
— **no missing/blank/non-finite field (symbol, qty, market_value) and no degenerate
quotient (underflow to 0.0, overflow to inf) can hide a held position from the engine.**
The final review (200k-case fuzz) found zero residual bypasses.
- *Source:* review rounds 2–4; V2.1 §10 (forbidden divergence: "live buying duplicate
  exposure because it ignored current holdings"), Principle 8.
- *Evidence:* `test_live_pilot_transition_phase2.py` — `test_unpriceable_held_position_missing_market_value_blocks`, `test_uncountable_held_position_missing_qty_blocks`, `test_symbol_missing_holding_blocks_rotation`, `test_nonfinite_qty_holding_blocks_rotation`, `test_degenerate_price_underflow_blocks_rotation`, `test_degenerate_price_overflow_blocks_rotation`, `test_cleanly_priced_and_counted_holding_proceeds`.

### 5b. buying_power ≤ 0 → UNAVAILABLE block
A real `0.0` buying power (not just `None`) blocks (`LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE`);
cash must never substitute for broker buying power. Restores the merged-gate behavior the
shared engine's cash-fallback would have lost — this is the live account's condition today
(~$8 free cash). Fires on `bp<=0 AND (buy_orders_intended OR planned_buy_notional>0)`.
- *Source:* review #1; V2.1 Principle 2 (broker truth), Principle 8. **This is the highest-value fix.**
- *Evidence:* `test_buying_power_zero_blocks_unavailable`, `test_bp_zero_with_planned_but_clipped_blocks`.

### 5c. Equity cap-regime collar ($520) and equity-unavailable block
The engine sizes targets to `weight × live-account-equity` and applies the cap **per run**,
which only bounds total exposure while `equity ≈ cap`. Equity **> $520** (a tight collar
over the ~$500 pilot funding) → block (`live_pilot_equity_exceeds_cap_regime`); equity
**None** → distinct block (`live_pilot_equity_unavailable`, its own operator action, for a
feed outage).
- *Source:* operator decision 2026-07-06 (funding ~$500 ≈ cap; ceiling set to $520);
  V2.1 §13.1 (cap is a ceiling). **Deferred TODO (§7).**
- *Evidence:* `test_equity_above_cap_regime_blocks`, `test_equity_tight_collar_blocks_just_above_ceiling`, `test_missing_equity_blocks`.

---

## 6. Kill-switch proof (unchanged safeguard, re-verified on the rewired path)
The kill switch remains the operator-controlled go-live gate and is upstream of the entire
engine/submit rewire. In `run_live_pilot`: `gate = build_live_pilot_gate_result(...)` (kill
switch fails closed per Task 0) then `if gate.status != "PASS": return _write_blocked_artifacts(...)`
**before** the broker snapshot, the engine step, and the submit loop. With the switch
engaged no path reaches `broker.submit_market_order`.
- *Source:* Task 0 (kill switch fail-closed) + Phase 2 confirmed decisions.
- *Evidence:* `test_live_pilot_execution_path.py::test_kill_switch_blocks_before_broker_submission_and_writes_gate_state` (kill switch "1", `dry_run="0"`, asserts `submit_calls==0`), against the refactored path.
- **Go-live checklist item (operator-only):** setting the explicit kill-switch off-token is the single remaining step before the pilot resumes.

---

## 7. Documented TODOs (deferred, non-blocking — not fixed in this workstream)

Per the operator directive, remaining PLAUSIBLE/LOW items are recorded here, not fixed now:
1. **Exposure-aware cap (FR, deferred).** When the pilot account is funded **above** the
   cap, replace the equity-regime collar (§5c) with `cap_remaining = approved_cap −
   current_exposure` so weight-based targets can be sized correctly above the cap. Until
   then the collar halts. (`LIVE_PILOT_EQUITY_CAP_REGIME_CEILING_USD`.)
2. **Over-cap relaxation (deferred).** Relax the fail-closed over-cap **halt** (§4) to
   clip-and-deploy once live behavior is trusted.
3. **Blocked-plan evidence nit (LOW).** `_blocked_plan` leaves `holdings_to_increase` /
   `buy_needs` populated on a blocked plan (a symbol whose buy was removed still appears
   under "increase"). Execution is unaffected (blocked paths early-return before
   submission); artifact-consistency only.
4. **Recompute vs reuse (LOW/style).** `holdings_from_snapshot` recomputes
   `abs(market_value/qty)` rather than reusing the predicate's value — deterministic float
   ops, no divergence; a cleanup, not a defect.
5. **Contradictory short data (PLAUSIBLE/LOW).** A `market_value>0, qty<0` position
   (impossible from Alpaca; a real short has `mv<0`, which is rejected) is *included* (not
   dropped) and handled by the engine without crash/inf; long-only sizing merely grows the
   buy need. Not a bypass.
