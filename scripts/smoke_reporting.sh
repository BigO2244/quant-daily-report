#!/usr/bin/env bash
set -euo pipefail

pytest -q
python3 daily_quant_report.py || true

test -f outputs/ledger/trades.csv
test -f outputs/perf/nav_timeseries.csv
latest_date=$(date +%F)
test -f "outputs/ledger/positions_${latest_date}.csv" || true
test -f "outputs/perf/holdings_mtm_${latest_date}.csv" || true

echo "smoke complete"
