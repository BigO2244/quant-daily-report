# Workstream B — Integration Plan (Task 2, report only)

**Status:** planning artifact. **Nothing in this document is wired.** All diff blocks
below are *illustrative and unapplied* — they show the intended call-site changes for
Phases 2–3 (separate operator approval per Architecture V2.1 §32–33). This branch
(`workstream-b-transition-engine`) ships Task 0 (fail-closed gates) and Task 1
(`transition/engine.py` + tests) only.

**Base:** `origin/HEAD` = `0fb7779`. Line numbers are against that SHA.
**Operator decisions already captured:** capital-gate branch confirmed = `6243757`;
live sells = **Option A** (block on rotation; `ModeConstraints.sells_supported=False`).

---

## 0. The seam (where the engine plugs in)

Both modes must call `transition.compute_transition(...)` at the point where they have
(a) the target portfolio and (b) a broker-confirmed snapshot, and *before* any order is
sized or submitted. Today each mode has its own bespoke logic there:

| Mode | Current transition logic | File:lines (0fb7779) |
|---|---|---|
| Live | buy-only narrowing → `_trades_from_plan` → `validate_live_pilot_plan` → submit | `scripts/live_pilot_build_plan_from_precompute.py` (narrowing); `scripts/live_pilot_execute.py:1002-1006, 1129-1160` |
| Paper | `build_rebalance_trades` (diff) → sell phase → `_post_sell_buy_budget` → `_rebuild_post_sell_buy_trades` → `_apply_buy_budget` | `paper/paper_broker.py:4767, 5360, 5458, 5812` |
| Capital gate (unmerged `6243757`) | `_build_live_pilot_capital_gate` before submit | `scripts/live_pilot_execute.py:1307` (on `6243757`) |

The engine unifies all three into one `TransitionPlan`. The mode-specific residue that
remains is only: the adapter (broker calls), the governance gates, the cap value, and
the artifact namespace — never the transition arithmetic.

---

## 1. Live path — consume `TransitionPlan` between snapshot and submission

**Target seam:** `scripts/live_pilot_execute.py`, inside `run_live_pilot`, immediately
after the broker snapshot + account-match block (ends line 998) and *replacing* the
buy-only `_trades_from_plan` narrowing at `1002`. This is the audit's earliest-correction
point and exactly where `6243757` inserts its capital gate (`1307`).

**Retire the buy-only narrowing.** `scripts/live_pilot_build_plan_from_precompute.py`
currently drops sells, filters to one sleeve, and truncates to `max_orders=1`
(`build:279-284, 421`). Under the engine, live loads the **full** target portfolio and
lets `compute_transition` decide keep/sell/buy. For Option A the mode constraint
`sells_supported=False` turns any required rotation into a block (no behavior surprise:
live still never sells today).

Illustrative, **unapplied**:

```diff
--- a/scripts/live_pilot_execute.py
+++ b/scripts/live_pilot_execute.py
@@ run_live_pilot: after pre-snapshot (975) + account match (998)
-    source_trades = _trades_from_plan(plan)
-    plan_validation = validate_live_pilot_plan(
-        source_trades,
-        env=environ,
-        capital_cap_usd=float(gate.capital_cap_usd or 0.0),
-        max_orders=int(gate.max_orders or 0),
-        run_id=run_id,
-    )
+    # Build engine contracts from the FULL target portfolio + broker snapshot.
+    transition_plan = compute_transition(
+        current_holdings=_holdings_from_snapshot(pre_snapshot),
+        target_holdings=_target_from_precompute(plan),          # full target, not buy-only
+        account_snapshot=_account_from_snapshot(pre_snapshot, as_of=_now_utc()),
+        capital_policy=CapitalPolicy(approved_cap_usd=float(gate.capital_cap_usd or 0.0)),
+        order_policy=OrderPolicy(fractional=_fractional_allowed(environ), min_trade_usd=100.0),
+        mode_constraints=ModeConstraints(sells_supported=False,   # Option A
+                                         max_orders=int(gate.max_orders or 0)),
+    )
+    _write_json(run_root / "live_pilot_transition_plan.json", _plan_to_artifact(transition_plan))
+    if transition_plan.blocked:
+        return _write_blocked_artifacts(
+            run_root=run_root, run_id=run_id, trade_date=trade_date, env=environ,
+            reason_code=transition_plan.block_reason,
+            operator_action=_operator_action_for(transition_plan.block_reason),
+            preflight=preflight,
+        )
+    # Only engine-approved BUY intents proceed to the existing plan validator, which
+    # keeps its symbol/asset/cap re-checks as defense in depth.
+    source_trades = [_buy_intent_to_trade(b) for b in transition_plan.buy_orders_intended]
+    plan_validation = validate_live_pilot_plan(
+        source_trades, env=environ,
+        capital_cap_usd=float(gate.capital_cap_usd or 0.0),
+        max_orders=int(gate.max_orders or 0), run_id=run_id,
+    )
```

New thin adapter helpers (live-side, ~40 lines, all pure mapping):
`_holdings_from_snapshot`, `_target_from_precompute`, `_account_from_snapshot`,
`_plan_to_artifact`, `_buy_intent_to_trade`, `_operator_action_for`.

**Downstream unchanged:** `open_order_check` (`1097`), `market_hours_gate` (`1115`), the
submit loop (`1129-1160`), reconciliation, and evidence all keep working on
`plan_validation.orders`. The account-match gate wired in Task 0 stays as-is.

**Net effect for the July 6 scenario:** live loads the full ABBV/ALL/C + ALL target,
`compute_transition` sees ABBV/C as exits → rotation required → `sells_supported=False`
→ `blocked = EXISTING_POSITIONS_REQUIRE_ROTATION`, and `_write_blocked_artifacts`
records it. No ALL buy is ever constructed. (Verified by `Tests/transition/test_july6_fixture.py`.)

---

## 2. Capital gate (`6243757`) becomes a thin wrapper over the engine

`6243757`'s `_build_live_pilot_capital_gate` (`scripts/live_pilot_execute.py:344` on that
branch) is a **parallel** implementation of the same capital logic the engine now owns:
rotation detection, buying-power-vs-notional, and the `min(buying_power, cap, allocation)`
formula. Under integration it must not survive as a second code path (V2.1 principle 10,
Decision 2).

Recommended reconciliation when `6243757` merges:

- Keep the branch's **artifact schema** (`live_pilot_capital_gate.v1`) and the block-reason
  string constants — they are the operator-facing evidence and tests depend on them.
- Replace the branch's hand-rolled decision body with a mapping from `TransitionPlan`:

```diff
 def _build_live_pilot_capital_gate(*, pre_snapshot, intended, approved_cap_usd):
-    ... hand-rolled required_sell_count / tradable_capital / decision logic ...
+    plan = compute_transition(
+        current_holdings=_holdings_from_snapshot(pre_snapshot),
+        target_holdings=_target_from_intended(intended),
+        account_snapshot=_account_from_snapshot(pre_snapshot, as_of=_now_utc()),
+        capital_policy=CapitalPolicy(approved_cap_usd=_safe_float(approved_cap_usd)),
+        order_policy=OrderPolicy(fractional=False, min_trade_usd=100.0),
+        mode_constraints=ModeConstraints(sells_supported=LIVE_PILOT_SELL_FIRST_SUPPORTED),
+    )
+    return {
+        "schema_version": "live_pilot_capital_gate.v1",
+        "decision": "BLOCKED" if plan.blocked else "ALLOWED",
+        "block_reason": plan.block_reason,
+        "tradable_capital_usd": plan.diagnostics["tradable_capital"],
+        "planned_buy_notional_usd": plan.diagnostics["planned_buy_notional"],
+        "required_sell_count": plan.diagnostics["required_sell_count"],
+        # ... remaining v1 fields mapped from plan.diagnostics ...
+    }
```

Block-reason mapping (engine → branch constant), all equivalent:

| Engine `block_reason` | `6243757` constant |
|---|---|
| `EXISTING_POSITIONS_REQUIRE_ROTATION` | `LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION` |
| `BLOCKED_BUYING_POWER_UNAVAILABLE` | `LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE` |
| `BLOCKED_INSUFFICIENT_BUYING_POWER` | `LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER` |

Once §1 lands, the gate call is redundant with the in-line `transition_plan.blocked`
check and can be deleted entirely; if `6243757` merges *before* §1, ship it as the wrapper
above so there is only one capital implementation from day one.

---

## 3. Paper path — swap onto the engine with artifact parity

Paper is the richer path (real sell phase, fill confirmation, post-sell refresh). The
engine replaces the **decision** functions; the **orchestration** (submit, poll, refresh,
reconcile) stays in `run_paper_day`.

Three call sites in `paper/paper_broker.py`:

- `4767` `build_rebalance_trades(...)` → engine diff (sell intents + buy needs).
- `5360` `_post_sell_buy_budget(...)` + `5458` `_rebuild_post_sell_buy_trades(...)` →
  engine buy sizing against the **post-sell** snapshot (the canonical rebudget).
- `5812` `_apply_buy_budget(...)` → subsumed by the engine's greedy fit.

Illustrative, **unapplied**, at the post-sell rebudget (`~5458`):

```diff
-                            rebuilt_frame, rebuild_meta, rebuild_skipped = (
-                                _rebuild_post_sell_buy_trades(
-                                    holdings=post_sell_holdings, targets=targets,
-                                    prices=rebudget_prices, total_equity=equity,
-                                    buy_budget=buy_budget_computed, cfg=cfg,
-                                    max_buy_orders=max_buy_orders,
-                                )
-                            )
+                            transition_plan = compute_transition(
+                                current_holdings=_holdings_df_to_contract(post_sell_holdings, rebudget_prices),
+                                target_holdings=_targets_df_to_contract(targets, rebudget_prices, cfg),
+                                account_snapshot=_account_to_contract(postsell_account_snapshot, as_of=_utc_now_iso()),
+                                capital_policy=CapitalPolicy(
+                                    approved_cap_usd=None,           # paper has no cap ceiling
+                                    reserve=CAPITAL_POSTSELL_RESERVE_MIN_CASH,
+                                    risk_cash_weight=target_cash_weight),
+                                order_policy=OrderPolicy(fractional=cfg.allow_fractional,
+                                                         min_trade_usd=cfg.min_trade_dollars),
+                                mode_constraints=ModeConstraints(sells_supported=True,
+                                                                 max_orders=max_buy_orders),
+                            )
+                            rebuilt_frame = _buys_to_frame(transition_plan.buy_orders_intended)
```

**Parity is already proven at the unit level.** `Tests/transition/test_parity.py` drives
the real `build_rebalance_trades`, `_rebuild_post_sell_buy_trades`, and
`_post_sell_buy_budget` and asserts the engine matches. Phase 3 adds an **end-to-end**
artifact-parity gate: run a recent paper day through both the legacy path and the engine
path and diff `shadow_orders_*.json` / `post_sell_rebudget_*.json`. Freeze begins only
after that end-to-end parity is green (V2.1 §33).

**Adapter helpers needed (paper-side, pure):** `_holdings_df_to_contract`,
`_targets_df_to_contract`, `_account_to_contract`, `_buys_to_frame`. Note paper feeds the
engine `slippage_bps` at the adapter boundary (engine notionals use reference price);
paper keeps applying slippage in its own order construction as today.

---

## 4. `execute_alpaca_orders.py` idempotency folds into the shared adapter (Decision #12)

`scripts/execute_alpaca_orders.py` is a fourth, standalone submit path with BUY+SELL and
the strongest idempotency in the repo:

- deterministic `client_order_id = alpaca_client_order_id(internal_order_id)` (`:401`);
- remote replay guard `find_order_by_client_id` → `IDEMPOTENT_REPLAY` (`:402, :414`);
- run-level locks `_detect_existing_submission_lock` (`:231, :944`) and sent-ledger lock.

Per Decision #12, no fourth path survives the migration. The plan:

1. Extract its idempotency primitives (`alpaca_client_order_id`, `find_order_by_client_id`
   replay check, submission-lock detection) into the **shared broker adapter** (V2.1
   Module Registry "Broker Adapter"), which both live and paper submit loops call.
2. Live's existing `_open_pilot_order_check` (`live_pilot_execute.py:208-255`) and paper's
   two-layer `find_order_by_client_id` + open-order sweep (`paper_broker.py:3836-3948`)
   become callers of that shared primitive rather than duplicating it.
3. Retire `execute_alpaca_orders.run_execution` as an entry point once its callers
   (`core/execution_audit.py`, its CLI/cron) are migrated to the orchestrator + adapter.

This is **Phase 4 (post-freeze, adapter interface)** work — sequenced after both modes are
on the engine, so it does not block the freeze.

---

## 5. Open questions for the operator

1. **Merge order of `6243757` vs §1.** If the operator merges the capital gate before the
   live-engine integration lands, ship it as the §2 wrapper (one capital implementation).
   If §1 lands first, `6243757`'s gate body is deleted, not merged. **Which sequence?**
2. **Live sells — Option A confirmed for the pilot.** Recorded: `sells_supported=False`;
   rotation blocks with `EXISTING_POSITIONS_REQUIRE_ROTATION` and the operator flattens
   manually. Option B (automated sell-first in live) remains a future item and is the
   riskiest new live code (partial fills on thin capital). **Confirm A stays for the full
   pilot window, or set a capital threshold at which B is revisited.**
3. **Paper reserve provenance.** The engine reproduces paper's env-frozen
   `CAPITAL_POSTSELL_RESERVE_MIN_CASH` / `CAPITAL_RESERVE_EQUITY_PCT` as explicit
   `CapitalPolicy` fields. Phase 3 must decide whether these stay env-driven (read once at
   the adapter boundary) or move into config. **Env or config?**
4. **`order_policy` fractional per mode.** Paper allows fractional (config); live floors to
   `cap/price`. Pinned v1: `min_trade_usd=100` both modes. **Confirm live keeps
   `fractional` per the existing `CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL` env, or force whole
   shares for the pilot.**
5. **End-to-end parity fixture (Phase 3).** Which recent paper run's artifacts become the
   golden end-to-end parity fixture? Needs an operator-blessed run with sells + buys.

---

## 6. Sequencing summary

| Phase | Change | Gated by |
|---|---|---|
| 0 (done) | Fail-closed gates (Task 0) | committed `d1b58c4` |
| 1 (done) | Pure engine + parity (Task 1) | committed `05aa1e7` |
| 2 | Live consumes `TransitionPlan` (§1); `6243757` becomes wrapper (§2) | operator approval; Option A |
| 3 | Paper swaps onto engine (§3); end-to-end artifact parity | operator approval; **freeze begins after** |
| 4 (post-freeze) | Shared adapter absorbs `execute_alpaca_orders` idempotency (§4) | post-freeze |

No code in Phases 2–4 is written or wired on this branch.
