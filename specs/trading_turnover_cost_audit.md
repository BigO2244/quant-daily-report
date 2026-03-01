# SPEC — Trading Turnover and Cost Audit

OBJECTIVE: Deterministic audit of turnover, friction, and feasibility prior to live capital deployment.
MODE: BUILD
PROJECT_TYPE: Trading Analysis
RISK_TIER: Medium

## CONTEXT

Evaluate portfolio turnover, holding periods, and transaction costs to validate deployment readiness. Output is deterministic (no randomness, no date-based sorting on mtime only), enabling reproducible risk assessment.

## FILES

create:
- specs/trading_turnover_cost_audit.md
- trading_audit.py
- reports/trading_audit/turnover_cost_audit.md
- tests/test_trading_audit_turnover.py
- tests/test_trading_audit_holding_period.py
- tests/test_trading_audit_slippage.py

modify:
- none

## ACCEPTANCE CRITERIA

- Deterministic output given identical inputs (no randomness, stable sorting/formatting).
- No trading logic mutation (read-only analysis).
- No broker connectivity or external API calls.
- Report includes:
  - Annualized turnover (%), weekly turnover distribution (mean/median/p90)
  - Average holding period (days)
  - Position churn per rebalance (% replaced)
  - Slippage sensitivity table for [0, 5, 10, 20] bps
  - ADV exposure summary (position notional / ADV) if volume data available; otherwise explicit "N/A" with explanation
  - Integer-share rounding drift summary (target vs actual weight drift) if targets available; otherwise "N/A"
  - Concentration metrics (max position %, HHI)
  - Deployment gate pass/fail summary
- Unit tests cover turnover calculation, holding period analysis, and slippage adjustment.
- Artifact discovery is deterministic:
  - Prefer date-parsable filenames (YYYY-MM-DD) with newest date; tie-break lexicographic path
  - If no date in filename, fall back to lexicographic path only (never mtime)
  - Use allowlist: outputs/, reports/, backtests/ only

## INVARIANTS

- Read-only analysis; do not modify backtest outputs
- No file writes outside reports/trading_audit/ and test outputs
- No randomness; all sorting is deterministic
- Stable CLI output: identical inputs → identical report
- Output markdown has stable section ordering
- If artifacts missing, render corresponding sections with explicit "N/A" and explanation

## CLI

```bash
# Run audit for specific date, write to specified output
python trading_audit.py --asof YYYY-MM-DD --out reports/trading_audit/turnover_cost_audit.md

# Discover available artifacts and print JSON summary
python trading_audit.py --discover --print-json
```

## NOTES

- Analysis derives from existing artifacts (trades csv, positions, ledger, targets, volume) if present
- This is an extension of existing paper trading and backtesting infrastructure
- Deterministic behavior enables audit reproducibility and diff-friendly reporting
