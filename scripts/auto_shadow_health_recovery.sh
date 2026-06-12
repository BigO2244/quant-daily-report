#!/usr/bin/env bash
# Local Mac Studio operator automation for Shadow artifact recovery.
# This script is artifact-only: it may hydrate/sync Shadow artifacts, but it never
# submits trades and never runs execution.
set -euo pipefail

REPO_ROOT="/Users/brettolson/Documents/Caerus/quant-daily-report-main"
cd "${REPO_ROOT}"

export TZ="America/New_York"

REMOTE_HOST="${CAERUS_REMOTE_HOST:-brettolson@alpha-stack-scheduler}"
REMOTE_REPO="${CAERUS_REMOTE_REPO:-~/quant-daily-report}"
LOG_DIR="${REPO_ROOT}/logs"
LOG_FILE="${LOG_DIR}/auto_shadow_health_recovery.log"
mkdir -p "${LOG_DIR}"

TRADE_DATE="${REPORT_DATE:-$(date +%F)}"
if [[ $# -gt 0 ]]; then
    case "$1" in
        --trade-date)
            TRADE_DATE="${2:-}"
            shift 2
            ;;
        -h|--help)
            cat <<'EOF'
Usage:
  scripts/auto_shadow_health_recovery.sh [--trade-date YYYY-MM-DD]

Rules:
  GREEN: no action
  YELLOW + PRICE_CACHE_STALE: run local hydration and sync to VM
  YELLOW without PRICE_CACHE_STALE: monitor only
  RED: log and exit nonzero

Artifact-only. Does not submit trades or run execution.
EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 2
            ;;
    esac
fi

if [[ -z "${TRADE_DATE}" ]]; then
    echo "ERROR: --trade-date requires a value" >&2
    exit 2
fi

log() {
    local message="$1"
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${message}" | tee -a "${LOG_FILE}"
}

remote_health_json() {
    ssh "${REMOTE_HOST}" "cd ${REMOTE_REPO} && source scripts/runtime_env.sh && activate_runtime_venv ${REMOTE_REPO} >/dev/null && python3 -m scripts.caerus_daily_health_check --trade-date ${TRADE_DATE} >&2 && cat outputs/health/caerus_daily_health_check/latest/health_check.json"
}

json_value() {
    local expr="$1"
    python3 -c 'import json,sys; payload=json.load(sys.stdin); print('"${expr}"')'
}

json_has_reason() {
    local reason="$1"
    python3 -c 'import json,sys; payload=json.load(sys.stdin); target=sys.argv[1]; print("YES" if any(target in (check.get("reason_codes") or []) for check in payload.get("checks") or []) else "NO")' "${reason}"
}

log "[AUTO_SHADOW] start trade_date=${TRADE_DATE}"
HEALTH_JSON="$(remote_health_json)"
OVERALL_STATUS="$(printf '%s' "${HEALTH_JSON}" | json_value 'payload.get("overall_status", "")')"
HAS_PRICE_CACHE_STALE="$(printf '%s' "${HEALTH_JSON}" | json_has_reason "PRICE_CACHE_STALE")"

log "[AUTO_SHADOW] vm_health=${OVERALL_STATUS} price_cache_stale=${HAS_PRICE_CACHE_STALE}"

case "${OVERALL_STATUS}" in
    GREEN)
        log "[AUTO_SHADOW] GREEN; no action."
        exit 0
        ;;
    YELLOW)
        if [[ "${HAS_PRICE_CACHE_STALE}" == "YES" ]]; then
            log "[AUTO_SHADOW] YELLOW with PRICE_CACHE_STALE; running local hydration workflow."
            "${REPO_ROOT}/scripts/hydrate_shadow_locally_and_sync.sh" \
                --trade-date "${TRADE_DATE}" \
                --start-date 2014-01-01
            log "[AUTO_SHADOW] hydration workflow completed."
            exit 0
        fi
        log "[AUTO_SHADOW] YELLOW without PRICE_CACHE_STALE; monitor only."
        exit 0
        ;;
    RED)
        log "[AUTO_SHADOW] RED; automatic recovery disabled. Investigate before trading changes."
        exit 1
        ;;
    *)
        log "[AUTO_SHADOW] unknown health status '${OVERALL_STATUS}'; exiting nonzero."
        exit 1
        ;;
esac
