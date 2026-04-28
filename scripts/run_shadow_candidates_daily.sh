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
PASSTHROUGH_ARGS=()
for ((i=1; i <= $#; i++)); do
    arg="${!i}"
    next_index=$((i + 1))
    next_value="${!next_index-}"
    case "${arg}" in
        --trade-date)
            [[ -n "${next_value}" ]] && TRADE_DATE="${next_value}"
            ((i++))
            ;;
        --start-date)
            [[ -n "${next_value}" ]] && SHADOW_START_DATE="${next_value}"
            ((i++))
            ;;
        --output-dir)
            [[ -n "${next_value}" ]] && SHADOW_OUTPUT_DIR="${next_value}"
            ((i++))
            ;;
        *)
            PASSTHROUGH_ARGS+=("${arg}")
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
RUN_CMD=(
    python3 -m research.shadow_tracking.run
    --trade-date "${TRADE_DATE}"
    --start-date "${SHADOW_START_DATE}"
    --end-date "${TRADE_DATE}"
    --output-dir "${SHADOW_OUTPUT_DIR}"
)
if [[ ${#PASSTHROUGH_ARGS[@]} -gt 0 ]]; then
    RUN_CMD+=("${PASSTHROUGH_ARGS[@]}")
fi
"${RUN_CMD[@]}" >> "${LOG_FILE}" 2>&1 || RC=$?

if [[ ${RC} -eq 0 ]]; then
    log "[SHADOW] wrote ${SHADOW_OUTPUT_DIR}/${TRADE_DATE}/..."
else
    log "[SHADOW] failed but non-blocking: exit_code=${RC}"
fi

DATED_DIR="${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"
LATEST_DIR="${SHADOW_OUTPUT_DIR}/latest"
LATEST_FILES=(
    "comparison.md"
    "comparison.json"
    "delta.json"
    "shadow_evaluation.json"
)

if [[ ${RC} -eq 0 && -f "${DATED_DIR}/comparison.md" ]]; then
    mkdir -p "${LATEST_DIR}"
    for artifact in "${LATEST_FILES[@]}"; do
        if [[ -f "${DATED_DIR}/${artifact}" ]]; then
            cp -f "${DATED_DIR}/${artifact}" "${LATEST_DIR}/${artifact}"
        else
            log "[SHADOW] latest artifact missing source: ${DATED_DIR}/${artifact}"
        fi
    done
    log "[SHADOW] latest artifacts published to ${LATEST_DIR}/"

    if [[ -d "${HOME}/Desktop" ]]; then
        ln -sf "${REPO_ROOT}/${LATEST_DIR}/comparison.md" "${HOME}/Desktop/Orion.md"
        log "[SHADOW] updated Desktop Orion.md"
    else
        log "[SHADOW] desktop path unavailable; latest artifacts published to ${LATEST_DIR}/"
    fi
else
    log "[SHADOW] no valid comparison.md found for latest publish"
fi

exit 0
