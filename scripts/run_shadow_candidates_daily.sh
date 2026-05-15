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
SHADOW_ALLOW_DOWNLOAD="${SHADOW_ALLOW_DOWNLOAD:-0}"
ALLOW_DOWNLOAD_ENABLED=0
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
WORKFLOW_DIR="${REPO_ROOT}/outputs/workflow/${TRADE_DATE}"
SHADOW_STATUS_PATH="${WORKFLOW_DIR}/shadow.json"
mkdir -p "${WORKFLOW_DIR}"

log() {
    local message="$1"
    echo "${message}" | tee -a "${LOG_FILE}"
}

write_step_status() {
    local step_name="$1"
    local status="$2"
    local exit_code="$3"
    local detail="$4"
    local path="${WORKFLOW_DIR}/shadow_${step_name}.json"
    {
        printf '{\n'
        printf '  "detail": "%s",\n' "${detail}"
        printf '  "exit_code": %s,\n' "${exit_code}"
        printf '  "status": "%s",\n' "${status}"
        printf '  "step": "%s",\n' "${step_name}"
        printf '  "trade_date": "%s"\n' "${TRADE_DATE}"
        printf '}\n'
    } > "${path}"
}

log "[SHADOW] start trade_date=${TRADE_DATE}"
log "[SHADOW] output_dir=${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"

RC=0
LATEST_PUBLISH_STATUS="SKIPPED"
RECONCILIATION_STATUS="SKIPPED"

generate_shadow_artifacts() {
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
    if [[ "${SHADOW_ALLOW_DOWNLOAD}" =~ ^(1|true|TRUE|yes|YES|y|Y)$ ]]; then
        ALLOW_DOWNLOAD_ENABLED=1
        RUN_CMD+=(--allow-download)
    fi
    "${RUN_CMD[@]}" >> "${LOG_FILE}" 2>&1
}

generate_shadow_artifacts || RC=$?

if [[ ${RC} -eq 0 ]]; then
    log "[SHADOW] wrote ${SHADOW_OUTPUT_DIR}/${TRADE_DATE}/..."
    write_step_status "generate" "OK" "${RC}" "${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"
else
    log "[SHADOW] failed but non-blocking: exit_code=${RC}"
    write_step_status "generate" "FAILED" "${RC}" "shadow generation failed but remains non-blocking"
fi

DATED_DIR="${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"
LATEST_DIR="${SHADOW_OUTPUT_DIR}/latest"
LATEST_FILES=(
    "comparison.md"
    "comparison.json"
    "delta.json"
    "shadow_evaluation.json"
)

NO_DATA_REASON=""
if [[ -f "${DATED_DIR}/comparison.json" ]]; then
    NO_DATA_REASON="$(sed -n 's/.*"reason_code"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${DATED_DIR}/comparison.json" | head -n 1)"
    if [[ -z "${NO_DATA_REASON}" ]]; then
        NO_DATA_REASON="$(sed -n 's/.*"data_reason"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "${DATED_DIR}/comparison.json" | head -n 1)"
    fi
fi
if [[ "${ALLOW_DOWNLOAD_ENABLED}" -ne 1 && "${NO_DATA_REASON}" == "PRICE_CACHE_STALE" ]]; then
    log "[SHADOW] price cache stale; run local hydration workflow to refresh."
fi

publish_shadow_latest() {
    mkdir -p "${LATEST_DIR}"
    LATEST_PUBLISH_STATUS="OK"
    for artifact in "${LATEST_FILES[@]}"; do
        if [[ -f "${DATED_DIR}/${artifact}" ]]; then
            cp -f "${DATED_DIR}/${artifact}" "${LATEST_DIR}/${artifact}"
        else
            log "[SHADOW] latest artifact missing source: ${DATED_DIR}/${artifact}"
            LATEST_PUBLISH_STATUS="PARTIAL"
        fi
    done
    log "[SHADOW] latest artifacts published to ${LATEST_DIR}/"

    if [[ -d "${HOME}/Desktop" ]]; then
        ln -sf "$(realpath "${LATEST_DIR}/comparison.md")" "${HOME}/Desktop/Orion.md"
        log "[SHADOW] updated Desktop Orion.md"
    else
        log "[SHADOW] desktop path unavailable; latest artifacts published to ${LATEST_DIR}/"
    fi
}

reconcile_live_vs_shadow() {
    RECON_RC=0
    python3 -m scripts.live_vs_shadow_reconciliation \
        --trade-date "${TRADE_DATE}" \
        --shadow-dir "${SHADOW_OUTPUT_DIR}" \
        --output-dir outputs/reconciliation/live_vs_shadow >> "${LOG_FILE}" 2>&1 || RECON_RC=$?
    if [[ ${RECON_RC} -eq 0 ]]; then
        RECONCILIATION_STATUS="OK"
    else
        RECONCILIATION_STATUS="FAILED"
        log "[SHADOW] live-vs-shadow reconciliation failed but non-blocking"
    fi
    return 0
}

if [[ ${RC} -eq 0 && -f "${DATED_DIR}/comparison.md" ]]; then
    publish_shadow_latest
    write_step_status "latest" "${LATEST_PUBLISH_STATUS}" "0" "${LATEST_DIR}"
    reconcile_live_vs_shadow
    write_step_status "reconciliation" "${RECONCILIATION_STATUS}" "0" "outputs/reconciliation/live_vs_shadow"
else
    log "[SHADOW] no valid comparison.md found for latest publish"
    if [[ ${RC} -eq 0 ]]; then
        LATEST_PUBLISH_STATUS="FAILED"
    fi
    write_step_status "latest" "${LATEST_PUBLISH_STATUS}" "0" "comparison.md unavailable"
    write_step_status "reconciliation" "${RECONCILIATION_STATUS}" "0" "skipped because latest publish did not complete"
fi

SHADOW_STATUS="$(
    if [[ ${RC} -eq 0 && "${LATEST_PUBLISH_STATUS}" == "OK" && "${RECONCILIATION_STATUS}" != "FAILED" ]]; then
        printf "OK"
    elif [[ ${RC} -eq 0 ]]; then
        printf "PARTIAL"
    else
        printf "FAILED"
    fi
)"
COMPARISON_PATH="${DATED_DIR}/comparison.json"
{
    printf '{\n'
    printf '  "comparison_path": "%s",\n' "${COMPARISON_PATH}"
    printf '  "latest_publish_status": "%s",\n' "${LATEST_PUBLISH_STATUS}"
    printf '  "notes": "%s",\n' "${NO_DATA_REASON:-non-blocking shadow lane status}"
    printf '  "reconciliation_status": "%s",\n' "${RECONCILIATION_STATUS}"
    printf '  "shadow_command_exit_code": %s,\n' "${RC}"
    printf '  "status": "%s",\n' "${SHADOW_STATUS}"
    printf '  "trade_date": "%s"\n' "${TRADE_DATE}"
    printf '}\n'
} > "${SHADOW_STATUS_PATH}"
log "[SHADOW] workflow status written to ${SHADOW_STATUS_PATH}"

exit 0
