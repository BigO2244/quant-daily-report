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
