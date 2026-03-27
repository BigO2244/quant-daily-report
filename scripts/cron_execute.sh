#!/usr/bin/env bash
# Phase 2: Order Execution — 9:35 AM ET weekdays
# Reads the precompute bundle from outputs/precompute/<DATE>/ (written by Phase 1)
# and executes trades via Alpaca paper API.
set -euo pipefail

# --- Resolve repo root ---
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
if [[ -f "${REPO_ROOT}/venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/venv/bin/activate"
else
    echo "FATAL: ${REPO_ROOT}/venv/bin/activate not found" >&2
    exit 1
fi

# --- Compute report date ---
export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="paper"
export TRADING_MODE="paper"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="${ALPACA_BASE_URL:-https://paper-api.alpaca.markets}"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/execute_${REPORT_DATE}.log"

# --- Suppress emails during execution (Phase 3 handles email) ---
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_PRETRADE=0
export EMAIL_TRADING_CONFIRMATION=0
export EMAIL_INTERNAL_DEBUG=0

# --- Workflow context (replaces GitHub Actions environment) ---
export WORKFLOW_KIND=live
export WORKFLOW_STARTED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "=== PHASE 2: ORDER EXECUTION ===" | tee -a "${LOG_FILE}"
echo "started_at=${WORKFLOW_STARTED_AT_UTC}" | tee -a "${LOG_FILE}"
echo "report_date=${REPORT_DATE}" | tee -a "${LOG_FILE}"
echo "mode=${MODE} trading_mode=${TRADING_MODE} alpaca_paper=${ALPACA_PAPER}" | tee -a "${LOG_FILE}"

# --- Verify precompute bundle exists ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
if [[ ! -f "${BUNDLE_DIR}/contract.json" ]]; then
    echo "FATAL: precompute bundle not found at ${BUNDLE_DIR}/contract.json" | tee -a "${LOG_FILE}"
    echo "Phase 1 (cron_precompute.sh) must complete successfully before Phase 2." | tee -a "${LOG_FILE}"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1" | tee -a "${LOG_FILE}"
    exit 1
fi
echo "OK: precompute bundle found at ${BUNDLE_DIR}" | tee -a "${LOG_FILE}"

# --- Set bundle environment (replaces GitHub artifact download step) ---
export PRECOMPUTE_BUNDLE_REQUIRED=true
export PRECOMPUTE_BUNDLE_FOUND=true
export BUNDLE_STATUS=bundle_ready
export BUNDLE_SOURCE=local
export BUNDLE_REPORT_DATE="${REPORT_DATE}"
export EVENT_FRESHNESS_STATUS=fresh
export EXECUTION_WINDOW_STATUS=on_time

# --- Execute from precompute bundle ---
EXIT_CODE=0
python3 -m scripts.run_precomputed_alpaca_execution --retry-attempt 0 >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "OK: execution completed successfully" | tee -a "${LOG_FILE}"
else
    echo "ERROR: execution failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}" | tee -a "${LOG_FILE}"
exit ${EXIT_CODE}
