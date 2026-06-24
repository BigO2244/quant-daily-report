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
LOG_FILE="${LOG_DIR}/execute_${REPORT_DATE}.log"
WORKFLOW_DIR="${REPO_ROOT}/outputs/workflow/${REPORT_DATE}"
EXECUTION_SELF_HEAL_STATUS_PATH="${WORKFLOW_DIR}/execution_self_heal.json"
BUNDLE_VALIDATION_PATH="${WORKFLOW_DIR}/execution_bundle_validation.json"
EXECUTION_READINESS_CERTIFICATION_ENABLED="${EXECUTION_READINESS_CERTIFICATION_ENABLED:-1}"

# --- Options overlay execution (default enabled for paper trading) ---
export ALLOW_OPTIONS_EXECUTION="${ALLOW_OPTIONS_EXECUTION:-1}"
export ALLOW_OPTIONS_SUBMISSION="${ALLOW_OPTIONS_SUBMISSION:-1}"

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

# --- Verify precompute bundle integrity ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
mkdir -p "${WORKFLOW_DIR}"
RECOVERY_ATTEMPTED=0
RECOVERY_RESULT="not_attempted"
RECOVERY_STARTED_AT=""
RECOVERY_FINISHED_AT=""

if ! python3 -m core.precompute_bundle_validation \
    --bundle-dir "${BUNDLE_DIR}" \
    --trade-date "${REPORT_DATE}" \
    --json-output "${BUNDLE_VALIDATION_PATH}" >> "${LOG_FILE}" 2>&1; then
    echo "WARN: precompute bundle validation failed; details=${BUNDLE_VALIDATION_PATH}" | tee -a "${LOG_FILE}"
    echo "WARN: attempting self-heal by rebuilding today's precompute bundle before giving up." | tee -a "${LOG_FILE}"
    RECOVERY_ATTEMPTED=1
    RECOVERY_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if SELF_HEAL_PRECOMPUTE_ONLY=1 REPORT_DATE="${REPORT_DATE}" "${REPO_ROOT}/scripts/cron_precompute.sh" >> "${LOG_FILE}" 2>&1; then
        RECOVERY_RESULT="completed"
    else
        RECOVERY_RESULT="failed"
        echo "ERROR: self-heal precompute rebuild failed" | tee -a "${LOG_FILE}"
    fi
    RECOVERY_FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    if ! python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --json-output "${BUNDLE_VALIDATION_PATH}" >> "${LOG_FILE}" 2>&1; then
        python3 -m core.precompute_bundle_validation \
            --bundle-dir "${BUNDLE_DIR}" \
            --trade-date "${REPORT_DATE}" \
            --json-output "${BUNDLE_VALIDATION_PATH}" \
            --recovery-status-output "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
            --previous-recovery-status "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
            --recovery-attempted \
            --recovery-result "${RECOVERY_RESULT}" \
            --execution-continued false \
            --recovery-started-at "${RECOVERY_STARTED_AT}" \
            --recovery-finished-at "${RECOVERY_FINISHED_AT}" >> "${LOG_FILE}" 2>&1 || true
        echo "FATAL: precompute bundle validation failed after self-heal; details=${BUNDLE_VALIDATION_PATH}" | tee -a "${LOG_FILE}"
        echo "FATAL: execution halted to avoid degraded bundle execution." | tee -a "${LOG_FILE}"
        echo "self_heal_status=${EXECUTION_SELF_HEAL_STATUS_PATH}" | tee -a "${LOG_FILE}"
        echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1" | tee -a "${LOG_FILE}"
        exit 1
    fi

    python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --json-output "${BUNDLE_VALIDATION_PATH}" \
        --recovery-status-output "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
        --previous-recovery-status "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
        --recovery-attempted \
        --recovery-result "${RECOVERY_RESULT}" \
        --execution-continued true \
        --recovery-started-at "${RECOVERY_STARTED_AT}" \
        --recovery-finished-at "${RECOVERY_FINISHED_AT}" >> "${LOG_FILE}" 2>&1
    echo "self_heal_status=${EXECUTION_SELF_HEAL_STATUS_PATH}" | tee -a "${LOG_FILE}"
fi
echo "OK: precompute bundle validated at ${BUNDLE_DIR}" | tee -a "${LOG_FILE}"
echo "bundle_validation=${BUNDLE_VALIDATION_PATH}" | tee -a "${LOG_FILE}"

# --- Certify execution readiness without submitting orders ---
if [[ ! "${EXECUTION_READINESS_CERTIFICATION_ENABLED}" =~ ^(0|false|FALSE|no|NO|n|N|off|OFF)$ ]]; then
    CERTIFICATION_PATH="${BUNDLE_DIR}/execution_readiness_certification.json"
    if ! python3 -m scripts.certify_execution_readiness \
        --trade-date "${REPORT_DATE}" \
        --mode paper \
        --no-submit \
        --output-path "${CERTIFICATION_PATH}" >> "${LOG_FILE}" 2>&1; then
        echo "FATAL: execution readiness certification failed; details=${CERTIFICATION_PATH}" | tee -a "${LOG_FILE}"
        echo "FATAL: execution halted before broker submission." | tee -a "${LOG_FILE}"
        echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1" | tee -a "${LOG_FILE}"
        exit 1
    fi
    echo "OK: execution readiness certification written to ${CERTIFICATION_PATH}" | tee -a "${LOG_FILE}"
fi

# --- Set bundle environment (replaces GitHub artifact download step) ---
export PRECOMPUTE_BUNDLE_REQUIRED=true
export PRECOMPUTE_BUNDLE_FOUND=true
export BUNDLE_STATUS=bundle_ready
export BUNDLE_SOURCE=local
export BUNDLE_REPORT_DATE="${REPORT_DATE}"
export EVENT_FRESHNESS_STATUS=fresh
export EXECUTION_WINDOW_STATUS=on_time
export PRECOMPUTE_EXECUTE_EXACT_PLAN=1
echo "EXECUTION_SOURCE=planned_payload_exact" | tee -a "${LOG_FILE}"
echo "PRICE_BASIS=PREV_CLOSE" | tee -a "${LOG_FILE}"
echo "FRESHNESS_SCOPE=PRECOMPUTE_VALIDATED" | tee -a "${LOG_FILE}"

# --- Execute from precompute bundle ---
EXIT_CODE=0
python3 -m scripts.run_precomputed_alpaca_execution --retry-attempt 0 >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "OK: execution completed successfully" | tee -a "${LOG_FILE}"
    OPTIONS_SUBMISSION_ENABLED="$(printf '%s' "${ALLOW_OPTIONS_EXECUTION:-${ALLOW_OPTIONS_SUBMISSION:-0}}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${OPTIONS_SUBMISSION_ENABLED}" == "1" || "${OPTIONS_SUBMISSION_ENABLED}" == "true" || "${OPTIONS_SUBMISSION_ENABLED}" == "yes" || "${OPTIONS_SUBMISSION_ENABLED}" == "y" || "${OPTIONS_SUBMISSION_ENABLED}" == "on" ]]; then
        PAPER_REVIEW_PATH="${REPO_ROOT}/outputs/options_overlay_paper/options_overlay_paper_review_${REPORT_DATE}.json"
        if [[ ! -f "${PAPER_REVIEW_PATH}" ]]; then
            echo "ERROR: options submission requested but paper review is missing at ${PAPER_REVIEW_PATH}" | tee -a "${LOG_FILE}"
            EXIT_CODE=1
        else
            echo "=== PHASE 2: OPTIONS OVERLAY EXECUTION ===" | tee -a "${LOG_FILE}"
            python3 scripts/execute_options_overlay.py \
                --run-root "${REPO_ROOT}/outputs/options_execution/${REPORT_DATE}" \
                --output-dir "${REPO_ROOT}/outputs/options_execution" \
                --paper-review "${PAPER_REVIEW_PATH}" \
                --trade-date "${REPORT_DATE}" \
                --submit >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?
            if [[ ${EXIT_CODE} -eq 0 ]]; then
                echo "OK: options overlay execution review completed" | tee -a "${LOG_FILE}"
            else
                echo "ERROR: options overlay execution failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
            fi
        fi
    fi
else
    echo "ERROR: execution failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}" | tee -a "${LOG_FILE}"
exit ${EXIT_CODE}
