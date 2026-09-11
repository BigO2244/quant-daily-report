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

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

TRADE_DATE="$(TZ=America/New_York date +%F)"
ACCOUNTING_STAGE="runtime"
on_accounting_exit() {
    local result=$?
    trap - EXIT
    if [[ ${result} -ne 0 ]]; then
        python3 -m scripts.send_workflow_failure_email \
            --workflow broker_ledger --report-date "${TRADE_DATE}" \
            --stage "${ACCOUNTING_STAGE}" --reason-code workflow_failed \
            --exit-code "${result}" --log-path "${REPO_ROOT}/logs/cron_broker_ledger.log" || \
            echo "[cron_broker_ledger] failure notification unsuccessful; see workflow receipt" >&2
    fi
    echo "[cron_broker_ledger] done rc=${result} $(date -u +%FT%TZ)"
    exit "${result}"
}
trap on_accounting_exit EXIT

source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

echo "[cron_broker_ledger] start $(date -u +%FT%TZ)"

# A failed producer must not let stale inputs masquerade as a fresh close.
ACCOUNTING_STAGE="capture"
python3 scripts/build_broker_truth_ledger.py --account paper
ACCOUNTING_STAGE="ownership"
python3 scripts/build_causal_paper_ledger.py
# The separate Live reader cannot strand a valid PAPER ownership refresh.
ACCOUNTING_STAGE="capture"
python3 scripts/build_broker_truth_ledger.py --account live
ACCOUNTING_STAGE="accounting"
python3 scripts/broker_ledger_report.py

# Refresh the intended target-book NAV (read-only artifact builder), then TCA.
ACCOUNTING_STAGE="audit"
python3 scripts/run_operational_drag_analysis.py --date "${TRADE_DATE}" >/dev/null
python3 scripts/build_tca.py --account both
