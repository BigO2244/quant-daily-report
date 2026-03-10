# Execution Summary & History Export

## Overview

The **Execution Summary** module provides deterministic, per-run observability artifacts without changing trading logic or order execution behavior.

It generates:
1. **Per-run text summary**: `outputs/runs/<RUN_ID>/execution_summary.txt`
2. **Latest pointer copy**: `outputs/latest_execution_summary.txt`  
3. **Rolling CSV export**: `outputs/execution_history.csv`

This is an **observability-only** feature. It does not affect:
- Strategy signal generation
- Order submission logic
- Broker execution behavior
- Trade decision logic

## Data Sources

The summary gathers information from authoritative sources already present in production:

### Execution Payload (`execution_payload.json`)
- **Run ID, trade date, execution status**
- **Trade list** decomposed by side (buy/sell) → pipe-delimited ticker strings
- **Executable trade count**
- **Halt reason** (if execution was blocked)

### Paper/Health Payload
- **Planner status** (SUCCESS / FAILED / UNKNOWN)
- **Broker status** (OK / ERROR)
- **Reconciliation status** (PASS / FAIL)
- **Planned/executed trade counts**
- **Turnover metrics**

### NAV Snapshot
- **Current equity** (net asset value)
- **Cash balance**
- **Position count** (total assets held)
- **Turnover percentage**

### Holdings & Performance
- Via `daily_snapshot` dict and `portfolio_stats`
- Extracted at the time of summary generation

## Output Format

### Per-Run Text Summary

**File:** `outputs/runs/{RUN_ID}/execution_summary.txt`

**Example:**
```
RUN_ID: 2026-03-10_0935
DATE: 2026-03-10

PIPELINE STATUS
  Planner: SUCCESS
  Execution Payload: CREATED
  Executor: SUCCESS
  Broker: OK
  Reconciliation: PASS

TRADING SUMMARY
  Buys: AAPL|MSFT|NVDA
  Sells: XOM|CVX
  Signals Generated: 8
  Orders Submitted: 3
  Orders Filled: 3
  Orders Rejected: 0

PORTFOLIO SNAPSHOT
  Total Assets Held: 11
  Net Asset Value: 104382.15
  Cash: 12411.30
  Turnover: 3.20%

DECISION
  Trade Decision: EXECUTED
  Primary Reason: orders_submitted
```

### Latest Pointer Copy

**File:** `outputs/latest_execution_summary.txt`

Same format as per-run summary, overwritten on each run for quick access to the latest execution state.

### Rolling CSV Export

**File:** `outputs/execution_history.csv`

**Columns** (in order):
- `date` — YYYY-MM-DD trading date
- `run_id` — unique run identifier
- `buys` — pipe-delimited ticker list (sorted, deduplicated)
- `sells` — pipe-delimited ticker list (sorted, deduplicated)
- `total_assets_held` — count of open positions (excluding cash)
- `net_asset_value` — current portfolio equity
- `cash` — available cash balance
- `planner_status` — SUCCESS / FAILED / UNKNOWN
- `payload_status` — CREATED / MISSING / UNKNOWN
- `execution_status` — SUCCESS / FAILED / HALTED / UNKNOWN
- `broker_status` — OK / ERROR / UNKNOWN
- `reconciliation_status` — PASS / FAIL / UNKNOWN
- `signals_generated` — count of generated signals
- `orders_submitted` — count of orders sent to broker
- `orders_filled` — count of orders filled
- `orders_rejected` — count of orders rejected
- `turnover` — daily turnover as percentage (e.g., "3.20%")
- `trade_decision` — EXECUTED / NO_TRADES_MODEL / EXECUTION_BLOCKED / UNKNOWN
- `primary_reason` — machine-readable reason (e.g., "orders_submitted", "market_closed", "stale_prices")

**Example rows:**
```csv
date,run_id,buys,sells,total_assets_held,net_asset_value,cash,planner_status,payload_status,execution_status,broker_status,reconciliation_status,signals_generated,orders_submitted,orders_filled,orders_rejected,turnover,trade_decision,primary_reason
2026-03-10,2026-03-10_0935,AAPL|MSFT|NVDA,XOM|CVX,11,104382.15,12411.30,SUCCESS,CREATED,SUCCESS,OK,PASS,8,3,3,0,3.20%,EXECUTED,orders_submitted
2026-03-11,2026-03-11_0940,AAPL|MSFT,CVX|XOM,10,105120.50,13500.00,SUCCESS,CREATED,SUCCESS,OK,PASS,9,2,2,0,2.80%,EXECUTED,orders_submitted
```

## Ticker Format (Buys/Sells)

**Buys and sells columns contain **pipe-delimited sorted ticker strings**, not counts.**

This preserves which specific tickers were identified for the run, enabling:
- Historical analysis of model signals
- Audit of which positions were considered
- Comparison with actual execution results

**Examples:**
- Empty run: `(none)` or empty string
- Single buy: `AAPL`
- Multiple buys: `AAPL|MSFT|NVDA`
- Duplicates are automatically deduplicated and sorted alphabetically

## Best-Effort Values

If optional data is unavailable:
- Missing fields are set to `UNKNOWN` or empty  
- The summary is still written (non-blocking)
- No hard failure occurs

For example:
- If `orders_rejected` cannot be determined, it shows as `UNKNOWN`
- If turnover data is missing, it shows `UNKNOWN`
- If cash balance is unavailable, it defaults to computed value

## Non-Blocking Behavior

The execution summary module is **designed to fail gracefully**:

1. **Individual artifact failures do not stop summary generation**
   - Per-run text fails → log warning, continue
   - CSV update fails → log warning, continue
   - Latest pointer fails → log warning, continue

2. **Missing input data does not fail**
   - Missing execution payload → set fields to UNKNOWN
   - Missing NAV data → set fields to UNKNOWN
   - Missing health payload → infer best-effort status

3. **Integration point is non-blocking**
   - Placed after execution, before email
   - Wrapped in try/except with warning logging
   - Email and reporting continue regardless

## CSV Update Semantics

**Row uniqueness:** Run ID

**Update behavior:**
- If `run_id` already exists in CSV → replace entire row (update semantics)
- If `run_id` is new → append to end of CSV
- No row duplication even on reprocessing

**Sorting:** By `date` (ascending), then `run_id` (ascending)

## Integration Point

Called in `daily_quant_report.py` main() after:
- Health payload is finalized
- Reconciliation is complete
- All performance data is ready

**Timing:** Right before building execution emails

**Inputs available:**
- `_RUN_CONTEXT.run_id`, `_RUN_CONTEXT.run_root`
- `execution_payload` from planner/executor
- `health_payload` with validation results
- `daily_snapshot` with holdings and performance
- `paper_summary` from paper trading
- `portfolio_stats` with equity and turnover

## Example Usage

### Reading the CSV

```python
import pandas as pd

df = pd.read_csv("outputs/execution_history.csv")

# Find runs with no trades
no_trades = df[df["trade_decision"] == "NO_TRADES_MODEL"]

# Get latest execution
latest = df.iloc[-1]

# Filter by date range
march_runs = df[df["date"] >= "2026-03-01"]

# Analyze turnover trend
turnover_series = df["turnover"].str.rstrip("%").astype(float)
```

### Accessing Per-Run Summary

```bash
# Latest run
cat outputs/latest_execution_summary.txt

# Specific run
cat outputs/runs/2026-03-10_0935/execution_summary.txt
```

## Testing

Run the test suite:

```bash
pytest Tests/test_execution_summary.py -v
```

Tests cover:
- Ticker extraction and deduplication
- Status inference from multiple sources
- Summary building with missing data
- Text artifact writing
- CSV creation and row updates
- Duplicate row prevention
- Non-blocking error handling

## Files Changed

### New
- [core/execution_summary.py](core/execution_summary.py) — Main module
- [Tests/test_execution_summary.py](Tests/test_execution_summary.py) — Test suite

### Modified
- [daily_quant_report.py](daily_quant_report.py) — Added import and integration call

## FAQs

**Q: Will this slow down the main workflow?**

A: No. Summary generation is lightweight and runs after execution is complete. The try/except wrapper ensures failures do not block email or other reports.

**Q: Can I use this data for trading decisions?**

A: No. This is observability only. Do not feed summary data back into strategy logic.

**Q: What if execution fails?**

A: The summary is still written with best-effort values. `execution_status` will show FAILED, trade_decision will show EXECUTION_BLOCKED or similar.

**Q: How do I know if a summary is stale?**

A: Check the `date` column (trading date) and compare with your system date. Each row in the CSV corresponds to one production run.

**Q: Can I edit the CSV?**

A: Generally no. The CSV is overwritten/appended to on each run. For manual adjustments, back up the file first or use alternate documentation.
