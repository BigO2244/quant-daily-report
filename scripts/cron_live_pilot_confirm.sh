#!/usr/bin/env bash
# LIVE_PILOT scheduled confirmation lane. Sends confirmation from the latest
# isolated live-pilot run without changing the paper execution workflow pointer.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

export TZ="America/New_York"

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
else
    echo "FATAL: ${REPO_ROOT}/.env not found" >&2
    exit 1
fi

ENV_FILE="${HOME}/.caerus/live_pilot.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "FATAL: ${ENV_FILE} not found" >&2
    exit 1
fi
set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

truthy() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|y|on|approve_live_pilot) return 0 ;;
        *) return 1 ;;
    esac
}

export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="live_pilot"
export TRADING_MODE="live_pilot"
export WORKFLOW_KIND="live_pilot"
export ALPACA_PAPER="0"
export ALPACA_BASE_URL="https://api.alpaca.markets"
export CAERUS_LIVE_PILOT_SCHEDULE_ENABLED="${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED:-0}"

LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/live_pilot_confirm_${REPORT_DATE}.log"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== LIVE_PILOT CONFIRMATION ==="
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "report_date=${REPORT_DATE}"
echo "schedule_enabled=${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED}"

if ! truthy "${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED}"; then
    echo "LIVE_PILOT confirmation skipped: schedule disabled."
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
    exit 0
fi

RUN_ROOT="$(find outputs/live_pilot/runs -maxdepth 1 -type d -name "${REPORT_DATE}T*" -print 2>/dev/null | sort | tail -1)"
if [[ -z "${RUN_ROOT}" ]]; then
    echo "WARN: no LIVE_PILOT run found for ${REPORT_DATE}; nothing to confirm."
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
    exit 0
fi

RESULTS_PATH="${RUN_ROOT}/execution_results.json"
if [[ ! -f "${RESULTS_PATH}" ]]; then
    echo "FATAL: LIVE_PILOT execution results missing: ${RESULTS_PATH}"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
fi

export EMAIL_PRETRADE=0
export EMAIL_TRADING_CONFIRMATION=1
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_INTERNAL_DEBUG=0
export TRADING_CONFIRMATION_RESULTS_PATH="${RESULTS_PATH}"
export TRADING_CONFIRMATION_RUN_ROOT="${RUN_ROOT}"

echo "run_root=${RUN_ROOT}"
echo "results_path=${RESULTS_PATH}"
python3 -m scripts.send_trading_confirmation_email

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
