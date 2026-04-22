#!/usr/bin/env bash
# Best-effort daily shadow candidate generation. Non-blocking by design.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

export TZ="America/New_York"

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || {
    echo "[SHADOW] failed but non-blocking: runtime venv unavailable" >&2
    exit 0
}

TRADE_DATE="${REPORT_DATE:-$(date +%F)}"
SHADOW_START_DATE="${SHADOW_START_DATE:-2014-01-01}"
SHADOW_OUTPUT_DIR="${SHADOW_OUTPUT_DIR:-outputs/shadow_candidates}"
for ((i=1; i <= $#; i++)); do
    arg="${!i}"
    next_index=$((i + 1))
    next_value="${!next_index-}"
    case "${arg}" in
        --trade-date)
            [[ -n "${next_value}" ]] && TRADE_DATE="${next_value}"
            ;;
        --start-date)
            [[ -n "${next_value}" ]] && SHADOW_START_DATE="${next_value}"
            ;;
        --output-dir)
            [[ -n "${next_value}" ]] && SHADOW_OUTPUT_DIR="${next_value}"
            ;;
    esac
done
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/shadow_${TRADE_DATE}.log"

log() {
    local message="$1"
    echo "${message}" | tee -a "${LOG_FILE}"
}

log "[SHADOW] start trade_date=${TRADE_DATE}"
log "[SHADOW] output_dir=${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"

RC=0
python3 -m research.shadow_tracking.run \
    --trade-date "${TRADE_DATE}" \
    --start-date "${SHADOW_START_DATE}" \
    --end-date "${TRADE_DATE}" \
    --output-dir "${SHADOW_OUTPUT_DIR}" \
    "$@" >> "${LOG_FILE}" 2>&1 || RC=$?

if [[ ${RC} -eq 0 ]]; then
    log "[SHADOW] wrote ${SHADOW_OUTPUT_DIR}/${TRADE_DATE}/..."
else
    log "[SHADOW] failed but non-blocking: exit_code=${RC}"
fi

LATEST=$(find "$REPO_ROOT/outputs/shadow_candidates" -maxdepth 1 -type d -name "20*" | sort | tail -n 1)

if [[ -n "$LATEST" && -f "$LATEST/comparison.md" ]]; then
    ln -sf "$LATEST/comparison.md" ~/Desktop/Orion.md
    log "[SHADOW] updated Desktop Orion.md"
else
    log "[SHADOW] no valid comparison.md found for symlink"
fi

exit 0