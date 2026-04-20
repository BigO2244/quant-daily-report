#!/usr/bin/env bash
# Phase 1: Precompute — 7:00 AM ET weekdays
# Generates signals, runs reconciliation, writes precompute bundle to
# outputs/precompute/<DATE>/ for Phase 2 execution.
set -euo pipefail

# --- Resolve repo root (works from cron or manual invocation) ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# --- Timezone ---
export TZ="America/New_York"

# --- Load credentials and config ---
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
else
    echo "FATAL: ${REPO_ROOT}/.env not found" >&2
    exit 1
fi

# --- Activate venv ---
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

# --- Compute report date ---
export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="paper"
export TRADING_MODE="paper"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/precompute_${REPORT_DATE}.log"

# --- Suppress emails during precompute (planning only) ---
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_PRETRADE=0
export EMAIL_TRADING_CONFIRMATION=0
export EMAIL_INTERNAL_DEBUG=0
export PLAN_ONLY=1
export ALLOW_OPTIONS_EXECUTION=0
export ALLOW_OPTIONS_SUBMISSION=0

echo "=== PHASE 1: PRECOMPUTE ===" | tee -a "${LOG_FILE}"
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
echo "report_date=${REPORT_DATE}" | tee -a "${LOG_FILE}"
echo "repo_root=${REPO_ROOT}" | tee -a "${LOG_FILE}"
echo "mode=${MODE} trading_mode=${TRADING_MODE} alpaca_paper=${ALPACA_PAPER}" | tee -a "${LOG_FILE}"

# --- Run precompute planner ---
EXIT_CODE=0
python3 daily_quant_report.py --plan-only --write-precompute-bundle >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

# --- Verify bundle was written ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
if [[ ${EXIT_CODE} -eq 0 ]]; then
    MISSING=""
    for f in contract.json daily_snapshot.json signals.json planned_execution_payload.json; do
        if [[ ! -f "${BUNDLE_DIR}/${f}" ]]; then
            MISSING="${MISSING} ${f}"
        fi
    done
    if [[ -n "${MISSING}" ]]; then
        echo "ERROR: precompute completed but bundle incomplete — missing:${MISSING}" | tee -a "${LOG_FILE}"
        EXIT_CODE=1
    else
        echo "OK: precompute bundle written to ${BUNDLE_DIR}" | tee -a "${LOG_FILE}"
    fi
else
    echo "ERROR: precompute failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}" | tee -a "${LOG_FILE}"

# --- Send precompute-complete email (best-effort, non-blocking) ---
if [[ ${EXIT_CODE} -eq 0 ]]; then
    python3 -m scripts.send_precompute_email >> "${LOG_FILE}" 2>&1 || {
        echo "WARN: precompute email send failed (non-blocking)" | tee -a "${LOG_FILE}"
    }
fi

exit ${EXIT_CODE}
