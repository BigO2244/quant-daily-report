#!/usr/bin/env bash
# cron_broker_ledger.sh — nightly broker-truth ledger append + realized report.
#
# READ-ONLY against Alpaca (GET only; no order placement or cancellation).
# Never sources lane env files into the shell: credentials are read directly by
# the python script from the canonical repository .env (paper) and
# ~/.caerus/live_pilot.env (live). Touches no trading gate or lane parameter.
#
# Scheduled at 19:15 ET as the sole actual-PAPER accounting source. The 19:45
# canonical NAV projection consumes this ledger. Idempotent and read-only.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

echo "[cron_broker_ledger] start $(date -u +%FT%TZ)"

rc=0
python3 scripts/build_broker_truth_ledger.py --account both || rc=$?
python3 scripts/broker_ledger_report.py || rc=$?

# Refresh the intended target-book NAV (read-only artifact builder), then TCA.
TRADE_DATE="$(TZ=America/New_York date +%F)"
python3 scripts/run_operational_drag_analysis.py --date "${TRADE_DATE}" >/dev/null || rc=$?
python3 scripts/build_tca.py --account both || rc=$?

echo "[cron_broker_ledger] done rc=${rc} $(date -u +%FT%TZ)"
exit "${rc}"
