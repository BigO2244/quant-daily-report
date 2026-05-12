#!/usr/bin/env bash
# VM-side post-close full shadow hydration fallback.
# Artifact-only: updates price/shadow artifacts, never submits orders.
#
# Routine cache-only refreshes must use:
#   python3 -m scripts.hydrate_price_cache_only
#
# This wrapper intentionally remains available for manual full shadow artifact
# repair. It should not be the routine cron path for price cache hydration.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

export TZ="America/New_York"

TRADE_DATE=""
STRICT=0
SHADOW_START_DATE="${SHADOW_START_DATE:-2014-01-01}"
SHADOW_OUTPUT_DIR="${SHADOW_OUTPUT_DIR:-outputs/shadow_candidates}"
CACHE_PATH="${PRICE_CACHE_PATH:-outputs/research/flow_detection_v1/price_panel.parquet}"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/price_hydration.log"

usage() {
    cat <<'EOF'
Usage:
  scripts/hydrate_shadow_price_cache_vm.sh [--trade-date YYYY-MM-DD] [--start-date YYYY-MM-DD] [--strict]

Options:
  --trade-date YYYY-MM-DD  Optional completed trading day to hydrate.
  --start-date YYYY-MM-DD  Shadow history start date. Default: 2014-01-01.
  --strict                 Exit non-zero if hydration or cache verification fails.

Artifact-only. Does not submit orders, run execution, or touch broker state.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --trade-date)
            TRADE_DATE="${2:-}"
            shift 2
            ;;
        --start-date)
            SHADOW_START_DATE="${2:-}"
            shift 2
            ;;
        --strict)
            STRICT=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

mkdir -p "${LOG_DIR}"

log() {
    local message="$1"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${message}" | tee -a "${LOG_FILE}"
}

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || {
    log "[PRICE_HYDRATION] runtime venv unavailable"
    if [[ "${STRICT}" -eq 1 ]]; then
        exit 1
    fi
    exit 0
}

RESOLVE_CMD=(python3 -m core.price_hydration resolve-date)
if [[ -n "${TRADE_DATE}" ]]; then
    RESOLVE_CMD+=(--trade-date "${TRADE_DATE}")
fi
AS_OF_DATE="$("${RESOLVE_CMD[@]}")"
STATUS_DIR="${REPO_ROOT}/outputs/price_hydration/${AS_OF_DATE}"
STATUS_PATH="${STATUS_DIR}/status.json"

log "[PRICE_HYDRATION] start"
log "[PRICE_HYDRATION] as_of_date=${AS_OF_DATE}"
log "[PRICE_HYDRATION] start_date=${SHADOW_START_DATE}"
log "[PRICE_HYDRATION] cache_path=${CACHE_PATH}"

HYDRATION_EXIT=0
DOWNLOAD_ATTEMPTED=true
PRE_MAX_CACHE_DATE="$(python3 - <<PYEOF
from pathlib import Path
from core.price_hydration import cache_max_date
print(cache_max_date(Path("${CACHE_PATH}")) or "")
PYEOF
)"
if [[ -n "${PRE_MAX_CACHE_DATE}" && "${PRE_MAX_CACHE_DATE}" > "${AS_OF_DATE}" || -n "${PRE_MAX_CACHE_DATE}" && "${PRE_MAX_CACHE_DATE}" == "${AS_OF_DATE}" ]]; then
    DOWNLOAD_ATTEMPTED=false
    log "[PRICE_HYDRATION] cache already covers as_of_date; skipping download"
else
    python3 -m research.shadow_tracking.run \
        --trade-date "${AS_OF_DATE}" \
        --start-date "${SHADOW_START_DATE}" \
        --end-date "${AS_OF_DATE}" \
        --output-dir "${SHADOW_OUTPUT_DIR}" \
        --allow-download >> "${LOG_FILE}" 2>&1 || HYDRATION_EXIT=$?
fi

log "[PRICE_HYDRATION] hydration_exit=${HYDRATION_EXIT}"

STATUS_JSON="$(python3 -m core.price_hydration write-status \
    --as-of-date "${AS_OF_DATE}" \
    --cache-path "${CACHE_PATH}" \
    --status-path "${STATUS_PATH}" \
    --hydration-exit-code "${HYDRATION_EXIT}" \
    --download-attempted "${DOWNLOAD_ATTEMPTED}")"

STATUS="$(printf '%s' "${STATUS_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("status", "FAILED"))')"
MAX_CACHE_DATE="$(printf '%s' "${STATUS_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("max_cache_date") or "")')"
REASON="$(printf '%s' "${STATUS_JSON}" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("reason", ""))')"

log "[PRICE_HYDRATION] max_cache_date=${MAX_CACHE_DATE:-UNAVAILABLE}"
log "[PRICE_HYDRATION] status=${STATUS} reason=${REASON}"
log "[PRICE_HYDRATION] status_path=${STATUS_PATH}"
log "[PRICE_HYDRATION] finished"

if [[ "${STRICT}" -eq 1 && "${STATUS}" != "OK" ]]; then
    exit 1
fi

exit 0
