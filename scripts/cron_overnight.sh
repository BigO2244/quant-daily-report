#!/usr/bin/env bash
# Overnight Research — 8:00 PM ET weekdays
# Runs all 7 overnight agents and writes outputs/overnight_signals/YYYY-MM-DD.json
# Consumed at 7:00 AM by the precompute pipeline via the thematic overlay.
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

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
RUN_DATE="$(date +%F)"
LOG_FILE="${LOG_DIR}/overnight_${RUN_DATE}.log"

echo "=== OVERNIGHT AGENTS ===" | tee -a "${LOG_FILE}"
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
echo "run_date=${RUN_DATE}" | tee -a "${LOG_FILE}"

EXIT_CODE=0
python3 -m overnight_agents.orchestrator >> "${LOG_FILE}" 2>&1 || EXIT_CODE=$?

if [[ ${EXIT_CODE} -eq 0 ]]; then
    echo "OK: overnight signals written to outputs/overnight_signals/${RUN_DATE}.json" | tee -a "${LOG_FILE}"
else
    echo "WARN: overnight agents completed with errors (exit ${EXIT_CODE}) — check log" | tee -a "${LOG_FILE}"
    # Non-fatal: precompute will use last available signals (up to 3 days old)
    EXIT_CODE=0
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
exit ${EXIT_CODE}
