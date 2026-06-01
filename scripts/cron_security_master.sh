#!/usr/bin/env bash
# Security master refresh — artifact-only daily universe snapshot.
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

export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/security_master_${REPORT_DATE}.log"

echo "=== SECURITY MASTER REFRESH ===" | tee -a "${LOG_FILE}"
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
echo "report_date=${REPORT_DATE}" | tee -a "${LOG_FILE}"

python3 scripts/update_security_master.py --asof-date "${REPORT_DATE}" >> "${LOG_FILE}" 2>&1

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
