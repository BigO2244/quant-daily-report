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

# --- Concentrated-alpha construction (ALWAYS ON; regime-adaptive top-N) ---
# Concentration is the model: no enable flag, and top-N derives from the VIX
# regime inside the engine (clamped 3..7, fallback 5). CAERUS_CONCENTRATED_TOP_N
# exists only as an emergency env override (set it in .env; never defaulted here).
# The per-name ceiling below is also the risk-controls position-cap default so
# the concentrated weights are not re-clipped downstream.
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
_DEPLOY_SHA="$(python3 -c "import json,sys; d=json.load(open('outputs/deploy_state.json')) if __import__('pathlib').Path('outputs/deploy_state.json').exists() else {}; print(d.get('deployed_sha','unknown'))" 2>/dev/null || echo "unknown")"
echo "deployed_sha=${_DEPLOY_SHA}" | tee -a "${LOG_FILE}"
if [[ "${SELF_HEAL_PRECOMPUTE_ONLY}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
    echo "self_heal_precompute_only=1" | tee -a "${LOG_FILE}"
fi

# --- Run precompute planner ---
EXIT_CODE=0
python3 daily_quant_report.py --plan-only --write-precompute-bundle >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

# --- Seal the governed PAPER portfolio allocation ---
# The legacy daily planner remains available as quarantined research evidence,
# but it cannot publish the canonical signals or execution handoff. Every
# registered sleeve produces a terminal daily decision; the configured capital
# sleeves are allocated once and every downstream consumer is hash-bound to the
# resulting immutable account target.
if [[ ${EXIT_CODE} -eq 0 ]]; then
    if ! python3 -m scripts.seal_paper_precompute_target \
        --trade-date "${REPORT_DATE}" \
        --bundle-dir "${REPO_ROOT}/outputs/precompute/${REPORT_DATE}" >> "${LOG_FILE}" 2>&1; then
        echo "ERROR: unable to seal the PAPER portfolio allocation; precompute is non-executable" | tee -a "${LOG_FILE}"
        EXIT_CODE=1
    fi
fi

# --- Verify bundle was written ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
if [[ ${EXIT_CODE} -eq 0 ]]; then
    mkdir -p "${WORKFLOW_DIR}"
    if ! python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --require-sealed-paper-target \
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

# --- Price-source shadow comparison (best-effort; can NEVER fail or delay precompute) ---
# Gated on CAERUS_PRICE_SHADOW (default 1). Compares Alpaca vs yfinance prices
# and writes outputs/workflow/${REPORT_DATE}/price_source_shadow.json.
CAERUS_PRICE_SHADOW="${CAERUS_PRICE_SHADOW:-1}"
if [[ "${CAERUS_PRICE_SHADOW}" =~ ^(1|true|TRUE|yes|YES|y|Y|on|ON)$ ]]; then
    SHADOW_SUMMARY="$(python3 -m scripts.price_source_shadow_compare 2>>"${LOG_FILE}" || true)"
    echo "price_source_shadow: ${SHADOW_SUMMARY:-unavailable}" | tee -a "${LOG_FILE}" || true
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
