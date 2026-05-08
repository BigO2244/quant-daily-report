# Codex Code Correctness Audit — Caerus Quant

**Purpose**: Systematic correctness review of the live execution pipeline. Work
through each section in order. For each target, read the referenced file, verify
the stated invariant, and flag any deviation with a specific line reference and
a proposed fix.

**Context**: This is a paper-trading quantitative equity + options system running
on Alpaca. Production runs on a GCP VM via cron. Canonical deployable source is
`origin/main`; normal VM deployment is git fast-forward and validation. SCP is
exception-only and requires later git reconciliation. The test suite (955 tests)
is the correctness baseline — run it first, then audit.

---

## 0. Baseline — Run Tests First

```bash
python3 -m pytest --tb=short -q
# Expected: 955 passed, 0 failed
```

If any tests fail, fix them before proceeding. Do not audit broken code.

---

## 1. Options Overlay — Feasibility & Contract Sizing

**Files**: `core/options_overlay_shadow.py`, `config/options_overlay_policy.json`

### 1a. Feasibility gate (budget-based)

Find the `_build_strategy_candidate` function. Verify:
- Feasibility uses `premium_budget >= min_contract_premium` (NOT `contracts_float >= min_contract_utilization`)
- `min_contract_premium` is read from `policy.get("min_contract_premium")` with a `50.0` default
- `contracts_recommended = max(1, int(math.floor(contracts_float + 1e-9)))` when feasible

**Invariant**: A $10K portfolio in crisis regime must return `feasible=True` and
`contracts_recommended >= 1` for `protective_put`. The old check would have
returned `feasible=False` (required $38K+).

### 1b. Put recommendation block (directional sizing)

Find the block starting `elif strategy in {"protective_put", "put_spread"}:` inside
`build_options_overlay_shadow`. Verify:
1. `strategy_premium_bps` is read from `strategy_cfg.get("premium_budget_bps")` first,
   falling back to `policy.get("premium_budget_bps")` — NOT hardcoded to policy-level only
2. `per_contract_estimate = _to_float(strategy_cfg.get("per_contract_cost_estimate_dollars"))`
3. `max_contracts_cfg = int(_to_float(strategy_cfg.get("max_contracts")) or 1)`
4. Sizing: `contracts_by_budget = floor(premium_budget_dollars / per_contract_estimate)`
5. `contracts_recommended = max(1, min(max_contracts_cfg, contracts_by_budget))`

**Math check**: On $9,713 equity with `protective_put` at 500bps:
- `premium_budget_dollars = 9713 × 0.05 = $485.65`
- `per_contract_estimate = 150.0`
- `floor(485.65 / 150) = 3`
- `min(5, 3) = 3` → expected `contracts_recommended = 3`

### 1c. Policy values

Verify `config/options_overlay_policy.json`:
- `"mode": "paper"` (not `"shadow_only"`)
- `"min_contract_premium": 50.0`
- `strategies.protective_put.premium_budget_bps = 500.0`
- `strategies.protective_put.max_contracts = 5`
- `strategies.protective_put.per_contract_cost_estimate_dollars = 150.0`
- `strategies.put_spread.premium_budget_bps = 200.0`
- `strategies.put_spread.max_contracts = 3`
- `strategies.put_spread.per_contract_cost_estimate_dollars = 75.0`

---

## 2. Options Execution Gate Chain

**Files**: `scripts/cron_execute.sh`, `core/options_execution.py`,
`config/options_execution_policy.json`

### 2a. ALLOW_OPTIONS_EXECUTION default

In `cron_execute.sh`, verify:
```bash
export ALLOW_OPTIONS_EXECUTION="${ALLOW_OPTIONS_EXECUTION:-1}"
export ALLOW_OPTIONS_SUBMISSION="${ALLOW_OPTIONS_SUBMISSION:-1}"
```
appear **before** the block that reads `OPTIONS_SUBMISSION_ENABLED`. The default
must be `1` (enabled) so that options fire without requiring manual `.env` edits.

### 2b. Allowlist enforcement

In `core/options_execution.py`, find the strategy allowlist check. Verify:
- `allowed_strategies` is read from `config/options_execution_policy.json`
- Only strategies in that list can submit orders
- Any strategy NOT in the list must log a clear skip reason and return without
  submitting — it must not silently no-op or raise an unhandled exception

### 2c. OCC symbol construction

Find `build_option_symbol(underlying, expiry, option_type, strike)`. Verify:
- Strike is encoded as 8 digits with 3 implied decimal places (e.g., $680.00 → `00680000`)
- Expiry is `YYMMDD` format (e.g., 2026-04-17 → `260417`)
- Option type is single char `P` or `C`
- The assembled symbol matches: `{root:6}{YYMMDD}{P|C}{strike:08d}`

Spot-check: `SPY, 2026-05-16, PUT, $470` → `SPY260516P00470000`

---

## 3. Paper Broker — Two-Phase Execution

**File**: `paper/paper_broker.py`

### 3a. Sell-before-buy gate

Find `_split_orders_for_execution`. Verify it correctly separates orders into
`sell_orders` and `buy_orders` lists based on side. Sells must execute first.

### 3b. Post-sell snapshot → buy phase gate

Find the block following `_wait_for_alpaca_sell_phase_completion`. Verify:
1. `buy_phase_allowed = bool(execution_outcome is None and sell_phase_status in {"COMPLETED", "NO_SELLS"})`
2. If `postsell_account_snapshot` write fails → `halt_remaining_buys = True`, `buy_budget_computed = 0.0`,
   `execution_outcome = EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE`
3. If `postsell_account_snapshot` is empty/falsy (broker returned nothing) → `buy_budget_computed = 0.0`
   (no exception, but buys are still blocked by `buy_phase_allowed` staying False)

**Invariant**: Buys must NEVER execute if the sell phase status is not `COMPLETED`
or `NO_SELLS`. A partial-fill sell phase must block all buys.

### 3c. Capital budget math

Find `_compute_capital_budget`. Verify:
```
available_for_buys = max(0, cash + expected_sell_proceeds_conservative - reserve_cash)
allowed_buy_notional = min(requested_buy_notional, available_for_buys)
```
- `expected_sell_proceeds_conservative = sell_proceeds × CAPITAL_SELL_PROCEEDS_HAIRCUT`
- `reserve_cash = max(CAPITAL_RESERVE_MIN_CASH, equity × CAPITAL_RESERVE_EQUITY_PCT)`
- Both constants default from env vars with `float(os.getenv(..., "100.0"))` / `"0.005"`
- `capital_constraint_triggered` must be `True` when `requested > allowed + 1e-9`

---

## 4. Regime Engine — Classification Correctness

**Files**: `alpha_stack/regime/state_machine.py`, `alpha_stack/regime/context.py`,
`alpha_stack/regime/hysteresis.py`

### 4a. Dimension thresholds

Verify thresholds match `alpha_stack/config/alpha_stack.yaml` exactly:
- **Volatility**: crisis `VIX > 30`, elevated `22–30`, normal `16–22`, calm `< 16`
- **Breadth**: washed_out `< 30%`, deteriorating `30–45%`, mixed `45–65%`, healthy `> 65%`
- **Trend**: strong_down `T1 < -5%`, weak_down `T1 < -2%`, neutral `-2% to 0%`, etc.

### 4b. `is_risk_off` property

In `context.py`, verify:
```python
is_risk_off = (
    vol_state in (VolatilityState.CRISIS,)
    or trend_state in (TrendState.STRONG_DOWN,)
    or breadth_state in (BreadthState.WASHED_OUT,)
)
```
Any ONE of those conditions alone must flip `is_risk_off` to `True`.

### 4c. Hysteresis — crisis bypass

In `hysteresis.py`, verify that `crisis_bypass_dwell: true` is honored: when
VIX crosses into crisis state, the 5-day min-dwell and 2-close confirmation are
bypassed and the transition happens immediately.

---

## 5. Allocator — Regime → Sleeve Budgets

**File**: `alpha_stack/portfolio/allocator.py`

### 5a. Washed-out breadth note

Find `_apply_breadth_modifiers`. Verify that `BreadthState.WASHED_OUT` only
appends a note (`"no new trend entries"`) but does **not** zero out the trend
budget. The entry suppression must happen in the sleeve layer, not the allocator.

### 5b. Hard drawdown breaker

Find `_apply_drawdown_breaker`. Verify:
- `current_dd >= hard (0.20)` → `budgets = {"cash": 1.0}` (full cash, no sleeves)
- `current_dd >= soft (0.12)` → each non-cash sleeve halved, difference added to cash
- Below soft threshold → no change, note is empty string

### 5c. Crisis vol modifier

In `_apply_vol_modifiers`, verify that when `vol_state == CRISIS`:
- `trend_budget × (1 - trend_reduction)` where `trend_reduction = 0.30`
- Released trend budget flows to cash (not quality)
- `mean_reversion_budget = 0` (zeroed, not halved)

---

## 6. Dashboard Builder — Broker Snapshot Fallback

**File**: `scripts/research/build_quant_dashboard.py`

### 6a. Skip failed snapshots

Find `_artifact_broker_snapshot`. Verify the candidate loop:
```python
for path, trust_level, mode in candidates:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        continue
    if payload.get("ok") is False or payload.get("error"):
        continue   # ← must skip snapshots from failed runs
```
A snapshot with `"ok": false` or any `"error"` key must be skipped entirely,
not used as a fallback.

### 6b. Positions fallback

In `DashboardBuilder.build()`, verify:
1. `posttrade_positions.json` is read from `outputs/broker/posttrade_positions.json`
2. The positions list is extracted as `_pt_pos_raw.get("positions")` only if it's a `list`
3. `positions_fallback` is passed to `_build_position_diagnostics`
4. Inside `_build_position_diagnostics`, when `broker_day_snapshot.positions_current`
   is unavailable, `positions_fallback or []` is used instead

### 6c. Equity inference

When broker equity is `None` or `0` but positions exist, verify:
```python
if equity in (None, 0) and positions:
    total_mv = sum(p.get("market_value") for p in positions ...)
    if total_mv > 0:
        equity = total_mv + (cash or 0.0)
```
This prevents `top_positions` from showing 0 entries when the broker account
snapshot is stale but individual position data is available.

---

## 7. Overnight Agents — Isolation & Schema

**Files**: `overnight_agents/orchestrator.py`, `overnight_agents/base.py`

### 7a. Agent failure isolation

In `orchestrator.py`, verify that each agent runs inside a `try/except` that:
- Catches all exceptions
- Logs the error with agent name and traceback
- Returns a neutral/stub result (does not propagate the exception)
- Allows remaining agents to continue running

**Invariant**: A single agent failure must never cause the orchestrator to exit
with a non-zero code or block downstream pipeline phases.

### 7b. Output schema

Verify the JSON written to `outputs/overnight_signals/YYYY-MM-DD.json` includes:
- `"signals"` key with a list of agent outputs
- Each entry has `"agent"`, `"signal"`, `"confidence"`, `"as_of"` fields
- Missing or None values are explicitly set to `null`, not omitted

---

## 8. Thematic Overlay — Score Boost Application

**File**: `alpha_stack/research_signal/thematic_overlay.py`

### 8a. Age gating

Verify that digest and overnight signal files older than 3 trading days are
rejected. The cutoff must use trading days (not calendar days). Files exactly
3 days old must be accepted; files 4+ days old must be rejected and logged.

### 8b. Boost weight application

Verify the boost is applied **before** percentile ranking in the trend sleeve:
```
S_final = S_adj + (thematic_boost_weight × thematic_score)
```
Where `thematic_boost_weight = 0.15` from `alpha_stack.yaml`. The boost must
clamp at `[0, 1]` before multiplication. After the boost, percentile ranking
is recomputed across all tickers.

### 8c. Bearish haircut

Verify that bearish research items receive a 50% score reduction before being
merged with bullish items. A net-bearish ticker must have a lower final boost
than a net-neutral ticker.

---

## 9. Cron Schedule — Time and Day Correctness

**File**: `scripts/crontab.txt`

Verify each entry:

| Phase | Expected cron | Verify |
|---|---|---|
| 0a Overnight agents | `0 1 * * 1-5` | 1:00 AM ET, weekdays only |
| 0b Research digest | `30 6 * * 1-5` | 6:30 AM ET, weekdays only |
| 1 Precompute | `0 7 * * 1-5` | 7:00 AM ET, weekdays only |
| 2 Execute | `35 9 * * 1-5` | 9:35 AM ET (5 min after open), weekdays only |
| 3 Confirm | `0 10 * * 1-5` | 10:00 AM ET, weekdays only |
| Weekly review | `0 8 * * 1` | Monday 8:00 AM ET only |

Verify `CRON_TZ=America/New_York` is set at the top of the file (handles EST/EDT
automatically — no manual offset adjustment needed).

---

## 10. Execution Integrity — Key Invariants

Cross-file checks that span multiple modules:

1. **No live orders without credentials**: All execution paths must check
   `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY` are non-empty before
   submitting any order. A missing key must halt with a logged error, not a
   silent no-op.

2. **Paper endpoint enforcement**: All cron scripts must set
   `ALPACA_BASE_URL=https://paper-api.alpaca.markets` and `ALPACA_PAPER=1`.
   Verify no path can reach `api.alpaca.markets` (live endpoint) through the
   normal cron execution flow.

3. **Duplicate guard**: Verify `paper/paper_broker.py` checks for existing open
   orders for the same symbol before submitting. A second identical order on the
   same day must be blocked or logged as a duplicate, not submitted.

4. **Run-date consistency**: The `REPORT_DATE` env var set in Phase 1 must be
   the same value consumed in Phase 2. Verify both cron scripts derive it the
   same way: `${REPORT_DATE:-$(date +%F)}`.

---

## Audit Completion Checklist

- [ ] All 10 sections reviewed
- [ ] No invariant violations found (or all violations filed as issues)
- [ ] Test suite still passes after any fixes applied
- [ ] `git diff` reviewed — no unintended side effects
- [ ] AGENTS.md updated if any behavioral change was made

**Report format**: For each finding, output:
```
FILE: path/to/file.py
LINE: ~NNN
ISSUE: one-sentence description
FIX: specific change required
SEVERITY: critical | high | medium | low
```
