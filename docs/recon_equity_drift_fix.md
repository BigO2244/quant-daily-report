# Root-Cause Analysis: 2026-03-06 No-Trade Event

## What Happened

The 2026-03-06 Alpaca paper-trading run was silently blocked at the pre-trade
reconciliation gate before any orders were submitted.

### Timeline

| Time (ET) | Event |
|---|---|
| 09:08 | Run started in alpaca paper mode |
| ~09:08 | Pre-trade reconcile ran; verdict = FAIL |
| ~09:08 | `SystemExit(2)` raised; `run_paper_day()` never called |
| — | No orders submitted; sent-ledger row_count = 0 |

### Root Cause

`pre_trade_reconcile_or_exit()` is configured `strict=True` by default
(`RECON_STRICT_PRE` env var). In `verdict_from_diffs()`, any breach of the
equity or cash tolerance while `strict=True` returns `"FAIL"`.

On this day:
- **Broker equity**: \$9,932.52
- **Model equity**: \$9,991.06
- **Delta**: −\$58.54 (0.59%)

Tolerances:
- `RECON_EQUITY_TOL_ABS` = \$10
- `RECON_EQUITY_TOL_PCT` = 0.1%

The delta exceeded **both** absolute and relative thresholds.
But **all positions matched exactly** — no missing symbols, no qty
mismatches. The \$58.54 gap is normal intraday mark-to-market drift
between when the canonical snapshot was last written (prior close) and when
live broker prices are read at open (09:08 ET).

### Why It Was a False Block

The reconcile gate is designed to catch **state integrity failures**: the model
thinks it holds positions the broker does not, or vice-versa. A \$58.54
mark-to-market drift with perfectly matching positions is not a state integrity
failure — it is expected market-close-to-open price movement.

The existing `strict=True` gate treated *any* equity breach identically to a
position mismatch, which is too coarse.

---

## Fix Design

### Policy Change: Separate equity drift from position integrity

| Condition | Old behaviour | New behaviour |
|---|---|---|
| Missing broker position | FAIL | FAIL (unchanged) |
| Unexpected broker position | FAIL | FAIL (unchanged) |
| Quantity mismatch | FAIL | FAIL (unchanged) |
| Parse / stale / broker-read error | FAIL | FAIL (unchanged) |
| Equity/cash drift only (no position issue) | FAIL (strict) / WARN | **WARN always** |
| Equity/cash drift with `RECON_EQUITY_HARD_FAIL=1` | — | FAIL (opt-in escape hatch) |

**`pre_trade_reconcile_or_exit` gate change**: previously blocked on
`strict_pre and verdict != "PASS"` (so WARN also blocked). Changed to block
only on `verdict == "FAIL"`, so WARN verdicts continue to execution.

### Additions

- **`block_reason` field** added to every recon report JSON and log line.
  Values: `positions_mismatch`, `quantity_mismatch`, `equity_only_drift`,
  `equity_cash_drift`, `cash_only_drift`, `stale_state`,
  `broker_read_failure`, `none`.
- **`recon_execution_blocked_{date}.json`** artifact written whenever
  `pre_trade_reconcile_or_exit` raises SystemExit(2), recording block reason
  and recon report path. Distinguishes "blocked execution" from "no signal".
- **`RECON_EQUITY_HARD_FAIL`** env var (default `0`): set to `1` to restore
  the old behaviour of hard-failing on equity drift.

---

## Files Changed

| File | Change |
|---|---|
| `reconciliation.py` | Policy fix + helpers + structured logging |
| `Tests/test_reconciliation.py` | Updated + new scenario tests |
| `Tests/test_recon_posttrade_refresh.py` | Updated for 3-tuple `_reconcile` return |

---

## Rollback Plan

Set `RECON_EQUITY_HARD_FAIL=1` in the workflow environment or `.env` to
immediately restore the original fail-closed behaviour for equity drift.
No code change required.

To fully revert the code change:
```
git revert HEAD  # revert the fix commit
```

---

## New Env/Config Flags

| Variable | Default | Effect |
|---|---|---|
| `RECON_EQUITY_HARD_FAIL` | `0` | When `1`, equity/cash drift escalates to FAIL (restores old behaviour) |

Existing variables are unchanged:

| Variable | Default | Notes |
|---|---|---|
| `RECON_ENABLE` | `1` | Master on/off switch |
| `RECON_STRICT_PRE` | `1` | No longer controls whether WARN blocks pre-trade |
| `RECON_STRICT_POST` | `0` | Post-trade behaviour unchanged |
| `RECON_EQUITY_TOL_ABS` | `10.0` | Absolute equity tolerance for WARN threshold |
| `RECON_EQUITY_TOL_PCT` | `0.001` | Relative equity tolerance for WARN threshold |
| `RECON_MAX_QTY_DIFF` | `0.0` | Max allowed qty diff before position FAIL |
| `RECON_CASH_TOL` | `5.0` | Cash drift tolerance |

---

## Acceptance Criteria Status

| Criterion | Status |
|---|---|
| Matching positions + equity drift → does NOT block | Fixed |
| Position integrity problems still fail-close | Unchanged / verified by tests |
| Artifacts distinguish "no signal" from "blocked execution" | `recon_execution_blocked_*.json` added |
| Tests pass | Tests added/updated |
| Minimal, safe change | Yes — single policy flag; rollback is one env var |
