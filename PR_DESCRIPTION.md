# Pull Request: Paper Mode Enhancements

## Summary

Switch default execution from **SHADOW** to **PAPER**, enforce weekend pause, lift turnover cap to 75%, ensure holdings synced from Alpaca so SELLs appear, and clean up directory naming.

**Commit:** `e20ef9d`

---

## Changes

### 1. ✅ Default Trading Mode: `paper`

**Before:** `TRADING_MODE` defaulted to `"shadow"` (planning only)  
**After:** `TRADING_MODE` defaults to `"paper"` (paper trading with Alpaca)

**Files:** `daily_quant_report.py`

- Added constant: `DEFAULT_TRADING_MODE = "paper"`
- Updated all `os.getenv("TRADING_MODE", "shadow")` → `os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)`

**Lines Changed:** 8 locations in `daily_quant_report.py` (1205, 1642, 3654, 3655, 4287, 4439, 4503, 4527)

---

### 2. ✅ Weekend Pause (Sat/Sun in ET)

**Feature:** Automatically skip order submission on weekends

**Files:** `paper/paper_broker.py`

- New function: `_is_weekend_et(now_et)` → checks if weekday in (5, 6)
- Updated `execution_enabled` logic to include `and not is_weekend`
- Updated return dict: `execution_status = "SKIPPED_WEEKEND"` on weekends
- Logs: `[WEEKEND_PAUSE] Skipping order submission — current day is {weekday}`

**Rationale:** Markets closed on weekends → order generation creates stale orders

---

### 3. ✅ Turnover Cap: 75% + Display Fix

**Before:** `max_turnover_pct: 0.0` (disabled, displayed as `$0.00`)  
**After:** `max_turnover_pct: 0.75` (75% cap, infinite displays as `"Disabled (∞)"`)

**Files:**
- `paper/config_paper.json` — changed `"max_turnover_pct": 0.0` → `0.75`
- `daily_quant_report.py` — added display logic for `math.isinf(turnover_cap)` → `"Disabled (∞)"`

**Code:**
```python
if turnover_cap is None:
    turnover_cap_display = "unavailable"
elif math.isinf(turnover_cap):
    turnover_cap_display = "Disabled (∞)"
else:
    turnover_cap_display = f"${turnover_cap:,.2f}"
```

---

### 4. ✅ Holdings Sync from Alpaca (Fixes Missing SELLs)

**Problem:** Ledger can drift from broker → missing SELL orders when broker has positions not in targets

**Solution:** Sync `holdings_prev` from Alpaca API before planning trades (weekdays only)

**Files:** `paper/paper_broker.py`

**Implementation:**
```python
# Before building rebalance trades:
if mode in {"paper", "alpaca"} and not is_weekend:
    try:
        alpaca = AlpacaBroker.from_env()
        broker_positions = alpaca.get_positions()
        if broker_positions:
            broker_holdings_df, _ = _alpaca_positions_to_holdings(broker_positions, "main")
            if not broker_holdings_df.empty:
                logger.info("[HOLDINGS_SYNC] Synced %d positions from Alpaca broker", len(broker_holdings_df))
                holdings_prev = broker_holdings_df[["ticker", "shares"]].copy()
    except Exception as exc:
        logger.warning("[HOLDINGS_SYNC] Failed: %s (continuing with ledger)", exc)
```

**Behavior:**
- **Weekdays:** Fetch broker positions → use as `holdings_prev` for diff engine
- **Weekends:** Skip (no execution anyway)
- **Fallback:** If sync fails, use ledger holdings

**Impact:** Diff engine (`build_rebalance_trades`) now generates accurate SELLs when broker has positions not in targets

---

### 5. ✅ Rename Directory: `shadow_orders` → `orders_sent`

**Before:** `outputs/shadow_orders/orders_sent.csv`  
**After:** `outputs/orders_sent/orders_sent.csv`

**Files Updated:**
- `daily_quant_report.py` (line 4287)
- `paper/paper_broker.py` (PaperConfig default)
- `paper/config_paper.json` (sent_ledger_path)

**Rationale:** "shadow_orders" was a misnomer; orders are sent (not shadowed)

---

## Tests

**File:** `Tests/test_paper_mode_switches.py` (248 lines)

**Coverage:**
- ✅ Weekend detection (Saturday, Sunday, Monday, Friday)
- ✅ Turnover cap display (infinite → "Disabled", finite → "$X,XXX.XX")
- ✅ Holdings sync from Alpaca → generates SELLs
- ✅ DEFAULT_TRADING_MODE == "paper"
- ✅ TRADING_MODE env fallback logic
- ✅ Weekend execution status == "SKIPPED_WEEKEND"

**Run:**
```bash
pytest Tests/test_paper_mode_switches.py -v
```

---

## Documentation

**File:** `docs/PAPER_MODE_ENHANCEMENTS.md` (345 lines)

**Contents:**
- How TRADING_MODE is resolved
- How weekend pause works
- How holdings are synced from Alpaca
- Testing instructions
- Migration notes
- FAQ
- Rollback plan

---

## Impact Analysis

### Breaking Changes
❌ **None** — shadow mode still works (set `TRADING_MODE=shadow`)

### Behavioral Changes
✅ Default mode is now **paper** (not shadow)  
✅ Weekend runs skip order submission (no stale orders)  
✅ Holdings auto-sync prevents drift (no manual reconciliation)  
✅ Turnover cap active (75% daily limit)

### Migration Required
⚠️ Update cron jobs:
```bash
# OLD (explicit shadow mode)
TRADING_MODE=shadow python daily_quant_report.py

# NEW (defaults to paper)
python daily_quant_report.py

# Override to shadow if needed
TRADING_MODE=shadow python daily_quant_report.py
```

---

## Rollback Plan

If issues arise:

1. **Revert to shadow mode:**
   ```bash
   TRADING_MODE=shadow python daily_quant_report.py
   ```

2. **Revert commit:**
   ```bash
   git revert e20ef9d
   ```

3. **Lower turnover cap:**
   ```json
   "max_turnover_pct": 0.3
   ```

---

## Files Changed

```
Tests/test_paper_mode_switches.py  | 248 +++++++++++++++++++++
daily_quant_report.py              |  33 +++-
docs/PAPER_MODE_ENHANCEMENTS.md    | 345 ++++++++++++++++++++++++++++++
paper/config_paper.json            |   4 +-
paper/paper_broker.py              |  49 +++++-
5 files changed, 664 insertions(+), 15 deletions(-)
```

---

## Verification

✅ Syntax checks passed: `python3 -m py_compile`  
✅ Line count: +664 insertions, -15 deletions  
✅ All referenced paths exist  
✅ Tests compile (runtime execution requires full env)

---

## Next Steps

1. **Review** this PR
2. **Merge** to main
3. **Deploy** to production
4. **Monitor** weekend runs (should show `SKIPPED_WEEKEND`)
5. **Verify** holdings sync logs: `[HOLDINGS_SYNC] Synced N positions from Alpaca broker`

---

## Questions?

Contact quant team or see `docs/PAPER_MODE_ENHANCEMENTS.md` for detailed FAQ.
