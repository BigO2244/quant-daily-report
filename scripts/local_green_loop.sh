#!/usr/bin/env bash
set -euo pipefail

echo "[GREEN_LOOP] Step 1/3: pytest"
python3 -m pytest -q

echo "[GREEN_LOOP] Step 2/3: deterministic planning run (offline fixture)"
# Use a far-future date to avoid picking up any existing signals file and triggering
# broker price fetches in local runs.
REPORT_DATE=2099-01-01 \
PAPER_TRADING=1 \
MODE=SHADOW \
TRADING_MODE=shadow \
OFFLINE_FIXTURE=1 \
OFFLINE_FIXTURE_DATE=2099-01-01 \
python3 daily_quant_report.py

echo "[GREEN_LOOP] Step 3/3: optional Alpaca smoke test"
if [[ -n "${ALPACA_API_KEY_ID:-}" && -n "${ALPACA_API_SECRET_KEY:-}" ]]; then
  python3 alpaca_smoke_test.py
else
  echo "Skipping Alpaca smoke test (no creds set)."
fi

echo "[GREEN_LOOP] PASS"
