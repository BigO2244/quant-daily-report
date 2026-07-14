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
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"

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

# Backstop sweep: confirm EVERY terminal run for the trade date exactly once
# (dedupe ledger), and FAIL LOUD (email alert) if there is no run to confirm or
# a confirmation email fails. This replaces the prior "last-sorted run dir at a
# fixed time" race that let a real armed submit (2026-07-10 10:09) go unreported
# and the silent exit-0 when no run was found. The execute lane also invokes the
# same sweep on completion so a run finishing after this scheduled time is still
# confirmed; dedupe makes the two paths idempotent.
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/live_pilot_confirm_lib.sh"

SWEEP_RC=0
live_pilot_confirm_sweep \
    "outputs/live_pilot/runs" \
    "outputs/live_pilot/state/confirm_sent_ledger.jsonl" || SWEEP_RC=$?

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${SWEEP_RC}"
exit ${SWEEP_RC}
