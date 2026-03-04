# Paper Trading Mode Enhancements

**Date:** March 4, 2026  
**Summary:** Switch default execution mode to paper trading, add weekend pause, increase turnover cap, fix holdings sync, and clean up directory naming.

---

## Changes

### 1. Default Trading Mode: `paper` (not `shadow`)

**Previous:** `TRADING_MODE` defaulted to `shadow` (planning only, no execution).  
**Now:** `TRADING_MODE` defaults to `paper` (paper trading with Alpaca broker).

**Location:** `daily_quant_report.py`

```python
# New constant
DEFAULT_TRADING_MODE = "paper"

# All references updated:
os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)  # was "shadow"
```

**Rationale:** Shadow mode was a transitional testing mode. Paper trading is the preferred daily workflow for model validation before live deployment.

---

### 2. Weekend Pause

**Feature:** Automatically skip order submission on Saturday/Sunday (ET timezone).

**Implementation:**
- New function: `paper/paper_broker.py::_is_weekend_et(now_et)`
- Checks if current day is Saturday (5) or Sunday (6)
- Orders no longer generated on weekends
- Execution status set to `SKIPPED_WEEKEND`
- Reports still generated (for continuity)

**Example Log:**
```
[WEEKEND_PAUSE] Skipping order submission — current day is Saturday (ET)
[EXEC_GATE] mode=paper allow=False market_open=False plan_only=False blocked=False is_weekend=True
```

**Rationale:** Markets are closed on weekends. Order submission on weekends creates stale orders that execute Monday at potentially unfavorable prices.

---

### 3. Turnover Cap Increased to 75%

**Previous:** `max_turnover_pct: 0.0` (disabled)  
**Now:** `max_turnover_pct: 0.75` (75% of equity per day)

**Location:** `paper/config_paper.json`

```json
"risk": {
  "max_turnover_pct": 0.75,
  ...
}
```

**Display Fix:** When turnover cap is infinite (disabled), display as `"Disabled (∞)"` instead of `"$0.00"`.

**Location:** `daily_quant_report.py::build_execution_email_payload()`

```python
# Format turnover_cap — if infinite, show as Disabled
if turnover_cap is None:
    turnover_cap_display = "unavailable"
elif math.isinf(turnover_cap):
    turnover_cap_display = "Disabled (∞)"
else:
    turnover_cap_display = f"${turnover_cap:,.2f}"
```

**Rationale:** 75% turnover cap balances responsive rebalancing with transaction cost management. Previous 0% cap was a safety default during testing.

---

### 4. Holdings Sync from Alpaca

**Problem:** Missing SELL orders when Alpaca broker has positions not reflected in ledger.

**Solution:** Before planning trades, sync `holdings_prev` from live Alpaca positions (weekdays only).

**Implementation:** `paper/paper_broker.py::run_paper_day()`

```python
# Sync holdings from Alpaca broker for weekday paper/alpaca runs
# This ensures SELLs are generated when broker has positions not in targets
if mode in {"paper", "alpaca"} and not is_weekend:
    try:
        alpaca = AlpacaBroker.from_env()
        broker_positions = alpaca.get_positions()
        if broker_positions:
            broker_holdings_df, _ = _alpaca_positions_to_holdings(broker_positions, "main")
            if not broker_holdings_df.empty:
                logger.info(
                    "[HOLDINGS_SYNC] Synced %d positions from Alpaca broker",
                    len(broker_holdings_df)
                )
                # Replace holdings_prev with broker positions for diff engine
                holdings_prev = broker_holdings_df[["ticker", "shares"]].copy()
    except Exception as exc:
        logger.warning(
            "[HOLDINGS_SYNC] Failed to sync from Alpaca broker: %s (continuing with ledger holdings)",
            exc
        )
```

**Behavior:**
- **Weekdays:** Fetch real positions from Alpaca API → use as `holdings_prev` for diff engine
- **Weekends:** Skip sync (no execution anyway)
- **Fallback:** If sync fails, use ledger holdings

**Rationale:**
- Ledger can drift from broker due to manual adjustments, failed orders, or initialization gaps.
- Syncing ensures the diff engine (`build_rebalance_trades`) generates accurate SELLs.
- Bootstrap flag (`--bootstrap-model-ledger-from-broker`) is now optional for one-time init; daily sync handles ongoing drift.

---

### 5. Directory Rename: `shadow_orders` → `orders_sent`

**Previous:** `outputs/shadow_orders/orders_sent.csv`  
**Now:** `outputs/orders_sent/orders_sent.csv`

**Locations Updated:**
- `daily_quant_report.py` (hardcoded path)
- `paper/paper_broker.py` (PaperConfig default)
- `paper/config_paper.json` (sent_ledger_path)

**Rationale:** "shadow_orders" was a misnomer. Orders are sent (in paper or alpaca mode), not shadowed. New name is clearer.

---

## How TRADING_MODE is Resolved

```
1. Environment variable: TRADING_MODE
   ↓ (if not set)
2. Constant: DEFAULT_TRADING_MODE = "paper"
   ↓
3. Normalized:
   - "paper" → paper trading with Alpaca
   - "shadow" → planning only (no execution)
   - "alpaca" → same as paper (alias)
   - "live" → BLOCKED (not implemented)
```

**Precedence:**
```bash
# Override to shadow mode
TRADING_MODE=shadow python daily_quant_report.py

# Default (paper mode)
python daily_quant_report.py
```

---

## How Weekend Pause Works

```
run_paper_day()
  ↓
1. Determine now_et (current time in ET timezone)
   ↓
2. Check: is_weekend = _is_weekend_et(now_et)
   ↓ (if Saturday or Sunday)
3. Log: [WEEKEND_PAUSE] Skipping order submission
   ↓
4. Set: execution_enabled = False (blocks order gen)
   ↓
5. Set: execution_status = "SKIPPED_WEEKEND"
   ↓
6. Continue with report generation (no orders)
```

**Environment Override:** None. Weekend pause is unconditional (by design).

---

## How Holdings Are Synced from Alpaca

```
run_paper_day()
  ↓
1. Read holdings_prev from ledger
   ↓
2. Check: mode in {"paper", "alpaca"} AND NOT is_weekend
   ↓ (if True)
3. Fetch: alpaca.get_positions()
   ↓
4. Convert: _alpaca_positions_to_holdings(positions, "main")
   ↓
5. Replace: holdings_prev = broker_holdings_df
   ↓
6. Log: [HOLDINGS_SYNC] Synced N positions from Alpaca broker
   ↓
7. Proceed with: build_rebalance_trades(holdings_prev, targets, ...)
```

**Key Points:**
- Sync happens **before** trade planning → ensures diff engine has accurate state
- Sync only on **weekdays** (weekends skip execution anyway)
- Sync only for **paper/alpaca** modes (shadow mode has no broker)
- **Graceful fallback:** If sync fails, use ledger holdings

**Diff Engine Behavior:**
```python
# Example: broker has [AAPL: 10 shares], target [] (empty)
holdings_prev = [{"ticker": "AAPL", "shares": 10.0}]
targets = []

→ build_rebalance_trades() generates:
  [{"ticker": "AAPL", "side": "SELL", "shares": 10.0, ...}]
```

---

## Testing

Run tests:
```bash
pytest Tests/test_paper_mode_switches.py -v
```

**Coverage:**
- `test_weekend_pause_saturday` — weekend detection (Saturday)
- `test_weekend_pause_sunday` — weekend detection (Sunday)
- `test_not_weekend_on_monday` — weekday detection (Monday)
- `test_not_weekend_on_friday` — weekday detection (Friday)
- `test_turnover_cap_display_disabled` — infinite cap displays as "Disabled (∞)"
- `test_turnover_cap_display_normal_value` — finite cap displays as "$X,XXX.XX"
- `test_holdings_sync_generates_sells` — Alpaca positions → holdings_prev → SELLs
- `test_default_trading_mode_is_paper` — DEFAULT_TRADING_MODE == "paper"
- `test_trading_mode_fallback_uses_default` — env fallback logic
- `test_weekend_execution_status_is_skipped` — SKIPPED_WEEKEND status on weekends

---

## Migration Notes

**Action Required:** Update cron jobs / scheduled runs

```bash
# OLD (explicit shadow mode)
TRADING_MODE=shadow python daily_quant_report.py

# NEW (defaults to paper mode)
python daily_quant_report.py

# Override to shadow if needed
TRADING_MODE=shadow python daily_quant_report.py
```

**Existing Behavior Preserved:**
- Shadow mode still works (set `TRADING_MODE=shadow`)
- Bootstrap flag still available for one-time broker → ledger init
- Turnover scaling logic unchanged (just cap increased)

**New Behavior:**
- Weekend runs no longer generate orders (previously would generate stale orders)
- Holdings sync prevents drift (previously required manual reconciliation)
- Directory renamed (check any scripts referencing `shadow_orders/`)

---

## FAQ

### Q: What happens if I run on a weekend?
**A:** Report generates normally, but execution status is `SKIPPED_WEEKEND`. No orders are created or sent.

### Q: Can I disable weekend pause?
**A:** No. Weekend pause is unconditional (by design). Markets are closed; order generation is pointless.

### Q: What if Alpaca sync fails?
**A:** Logs a warning and falls back to ledger holdings. Trade planning continues.

### Q: How do I revert to shadow mode?
**A:** Set `TRADING_MODE=shadow` environment variable.

### Q: What if I want 100% turnover cap (no limit)?
**A:** Set `"max_turnover_pct": 0.0` in `paper/config_paper.json`. Display will show "Disabled (∞)".

### Q: Does holdings sync overwrite the ledger?
**A:** No. `holdings_prev` is ephemeral (for trade planning only). Ledger is only written after execution.

---

## Rollback Plan

If issues arise:

1. **Revert to shadow mode:**
   ```bash
   TRADING_MODE=shadow python daily_quant_report.py
   ```

2. **Disable holdings sync:** Temporarily set mode to `shadow` (skips Alpaca calls).

3. **Restore old paths:**
   ```bash
   # In daily_quant_report.py, paper/paper_broker.py, paper/config_paper.json
   sed -i '' 's|outputs/orders_sent|outputs/shadow_orders|g' *.py paper/*.py paper/*.json
   ```

4. **Lower turnover cap:** Edit `paper/config_paper.json`:
   ```json
   "max_turnover_pct": 0.3
   ```

---

## Commit / PR Details

**Branch:** `feature/paper-mode-enhancements`

**Files Changed:**
- `daily_quant_report.py` (DEFAULT_TRADING_MODE, turnover display, path rename)
- `paper/paper_broker.py` (weekend pause, holdings sync, path rename)
- `paper/config_paper.json` (turnover cap, path rename)
- `Tests/test_paper_mode_switches.py` (new tests)
- `docs/PAPER_MODE_ENHANCEMENTS.md` (this file)

**Commit Message:**
```
feat: paper mode enhancements — weekend pause, holdings sync, 75% turnover cap

- Default TRADING_MODE: paper (not shadow)
- Weekend pause: skip order submission Sat/Sun (ET)
- Turnover cap: 75% (from 0%), display ∞ as "Disabled"
- Holdings sync: fetch Alpaca positions before planning (fixes missing SELLs)
- Rename: outputs/shadow_orders → outputs/orders_sent

Tests: pytest Tests/test_paper_mode_switches.py -v
Docs: docs/PAPER_MODE_ENHANCEMENTS.md
```

---

**Questions?** Contact the quant team or file an issue.
