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

# --- Concentrated-alpha construction (top-5 conviction-weighted, 50% cap) ---
# Enabled by default for the pilot; overridable via .env. Applied here at signals.json
# construction, and (via the same flag) raises the risk-controls position cap so the
# concentrated weights are not re-clipped downstream. Set CAERUS_CONCENTRATED_ALPHA=0
# in .env to revert paper to the broad book.
export CAERUS_CONCENTRATED_ALPHA="${CAERUS_CONCENTRATED_ALPHA:-1}"
export CAERUS_CONCENTRATED_TOP_N="${CAERUS_CONCENTRATED_TOP_N:-5}"
export CAERUS_CONCENTRATED_MAX_WEIGHT="${CAERUS_CONCENTRATED_MAX_WEIGHT:-0.50}"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/precompute_${REPORT_DATE}.log"
SELF_HEAL_PRECOMPUTE_ONLY="${SELF_HEAL_PRECOMPUTE_ONLY:-0}"
WORKFLOW_DIR="${REPO_ROOT}/outputs/workflow/${REPORT_DATE}"
SELF_HEAL_STATUS_PATH="${WORKFLOW_DIR}/precompute_self_heal.json"
BUNDLE_VALIDATION_PATH="${WORKFLOW_DIR}/precompute_bundle_validation.json"
EXECUTION_READINESS_CERTIFICATION_ENABLED="${EXECUTION_READINESS_CERTIFICATION_ENABLED:-1}"

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
if [[ "${SELF_HEAL_PRECOMPUTE_ONLY}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    echo "self_heal_precompute_only=1" | tee -a "${LOG_FILE}"
fi

# --- Run precompute planner ---
EXIT_CODE=0
python3 daily_quant_report.py --plan-only --write-precompute-bundle >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

# --- Verify bundle was written ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
if [[ ${EXIT_CODE} -eq 0 ]]; then
    mkdir -p "${WORKFLOW_DIR}"
    if ! python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --json-output "${BUNDLE_VALIDATION_PATH}" >> "${LOG_FILE}" 2>&1; then
        echo "ERROR: precompute completed but bundle validation failed; details=${BUNDLE_VALIDATION_PATH}" | tee -a "${LOG_FILE}"
        EXIT_CODE=1
    else
        echo "OK: precompute bundle written to ${BUNDLE_DIR}" | tee -a "${LOG_FILE}"
        echo "bundle_validation=${BUNDLE_VALIDATION_PATH}" | tee -a "${LOG_FILE}"
        if [[ ! "${EXECUTION_READINESS_CERTIFICATION_ENABLED}" =~ ^(0|false|FALSE|no|NO|n|N|off|OFF)$ ]]; then
            CERTIFICATION_PATH="${BUNDLE_DIR}/execution_readiness_certification.json"
            if ! python3 -m scripts.certify_execution_readiness \
                --trade-date "${REPORT_DATE}" \
                --mode paper \
                --no-submit \
                --output-path "${CERTIFICATION_PATH}" >> "${LOG_FILE}" 2>&1; then
                echo "ERROR: execution readiness certification failed; details=${CERTIFICATION_PATH}" | tee -a "${LOG_FILE}"
                EXIT_CODE=1
            else
                echo "OK: execution readiness certification written to ${CERTIFICATION_PATH}" | tee -a "${LOG_FILE}"
            fi
        fi
    fi
else
    echo "ERROR: precompute failed with exit code ${EXIT_CODE}" | tee -a "${LOG_FILE}"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}" | tee -a "${LOG_FILE}"

if [[ "${SELF_HEAL_PRECOMPUTE_ONLY}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    mkdir -p "${WORKFLOW_DIR}"
    {
        printf '{\n'
        printf '  "bundle_dir": "%s",\n' "${BUNDLE_DIR}"
        printf '  "bundle_validation_path": "%s",\n' "${BUNDLE_VALIDATION_PATH}"
        printf '  "exit_code": %s,\n' "${EXIT_CODE}"
        printf '  "mode": "self_heal_precompute_only",\n'
        printf '  "noncritical_side_effects_suppressed": true,\n'
        printf '  "suppressed_side_effects": ["email", "shadow", "shadow_latest", "shadow_reconciliation"],\n'
        printf '  "status": "%s",\n' "$([[ ${EXIT_CODE} -eq 0 ]] && printf "OK" || printf "FAILED")"
        printf '  "trade_date": "%s"\n' "${REPORT_DATE}"
        printf '}\n'
    } > "${SELF_HEAL_STATUS_PATH}"
    echo "self_heal_status=${SELF_HEAL_STATUS_PATH}" | tee -a "${LOG_FILE}"
    exit ${EXIT_CODE}
fi

# --- Send precompute-complete email (best-effort, non-blocking) ---
if [[ ${EXIT_CODE} -eq 0 ]]; then
    python3 -m scripts.send_precompute_email >> "${LOG_FILE}" 2>&1 || {
        echo "WARN: precompute email send failed (non-blocking)" | tee -a "${LOG_FILE}"
    }
    bash "${REPO_ROOT}/scripts/run_shadow_candidates_daily.sh" --trade-date "${REPORT_DATE}" >> "${LOG_FILE}" 2>&1 || true
fi

exit ${EXIT_CODE}
