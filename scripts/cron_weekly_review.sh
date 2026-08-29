#!/usr/bin/env bash
# Weekly model review wrapper — Monday 8:00 AM ET
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
LOG_FILE="${LOG_DIR}/weekly_review.log"

python3 scripts/build_operating_truth.py \
    --repo-root "${REPO_ROOT}" \
    --home "${HOME}" \
    --output-dir "${REPO_ROOT}/outputs/operating_state/current" \
    --strict >> "${LOG_FILE}" 2>&1
python3 scripts/weekly_model_review.py >> "${LOG_FILE}" 2>&1
