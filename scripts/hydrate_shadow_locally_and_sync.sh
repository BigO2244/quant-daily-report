#!/usr/bin/env bash
# Operator-only workflow: run heavy shadow hydration locally, sync artifacts to VM,
# then refresh VM reconciliation and health-check artifacts. Does not touch trading.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

TRADE_DATE=""
SHADOW_START_DATE="${SHADOW_START_DATE:-2014-01-01}"
SHADOW_OUTPUT_DIR="${SHADOW_OUTPUT_DIR:-outputs/shadow_candidates}"
REMOTE_HOST="${CAERUS_REMOTE_HOST:-brettolson@alpha-stack-scheduler}"
REMOTE_REPO="${CAERUS_REMOTE_REPO:-~/quant-daily-report}"
RUN_REMOTE_REFRESH=1
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage:
  scripts/hydrate_shadow_locally_and_sync.sh --trade-date YYYY-MM-DD [options]

Options:
  --trade-date YYYY-MM-DD     Required trade date to hydrate.
  --start-date YYYY-MM-DD     Shadow backtest start date. Default: 2014-01-01.
  --output-dir PATH           Local shadow output dir. Default: outputs/shadow_candidates.
  --remote-host HOST          SSH target. Default: brettolson@alpha-stack-scheduler.
  --remote-repo PATH          Remote repo path. Default: ~/quant-daily-report.
  --no-remote-refresh         Sync artifacts only; skip VM reconciliation/health refresh.
  --dry-run                   Print commands without running them.
  -h, --help                  Show this help.

This script is artifact-only. It does not submit orders and does not run execution.
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
        --output-dir)
            SHADOW_OUTPUT_DIR="${2:-}"
            shift 2
            ;;
        --remote-host)
            REMOTE_HOST="${2:-}"
            shift 2
            ;;
        --remote-repo)
            REMOTE_REPO="${2:-}"
            shift 2
            ;;
        --no-remote-refresh)
            RUN_REMOTE_REFRESH=0
            shift
            ;;
        --dry-run)
            DRY_RUN=1
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

if [[ -z "${TRADE_DATE}" ]]; then
    echo "ERROR: --trade-date is required" >&2
    usage >&2
    exit 2
fi

run_cmd() {
    if [[ "${DRY_RUN}" -eq 1 ]]; then
        printf '[DRY_RUN]'
        printf ' %q' "$@"
        printf '\n'
    else
        "$@"
    fi
}

log() {
    echo "[LOCAL_SHADOW] $*"
}

if [[ "${DRY_RUN}" -ne 1 ]]; then
    if [[ -f "${REPO_ROOT}/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "${REPO_ROOT}/.env"
        set +a
    fi
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/runtime_env.sh"
    activate_runtime_venv "${REPO_ROOT}" || {
        echo "ERROR: runtime venv unavailable" >&2
        exit 1
    }
fi

log "hydrate trade_date=${TRADE_DATE} start_date=${SHADOW_START_DATE}"
run_cmd python3 -m research.shadow_tracking.run \
    --trade-date "${TRADE_DATE}" \
    --start-date "${SHADOW_START_DATE}" \
    --end-date "${TRADE_DATE}" \
    --output-dir "${SHADOW_OUTPUT_DIR}" \
    --allow-download

DATED_DIR="${SHADOW_OUTPUT_DIR}/${TRADE_DATE}"
LATEST_DIR="${SHADOW_OUTPUT_DIR}/latest"
LATEST_FILES=("comparison.md" "comparison.json" "delta.json" "shadow_evaluation.json")

if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "would publish local latest artifacts to ${LATEST_DIR}/"
else
    if [[ ! -f "${DATED_DIR}/comparison.md" ]]; then
        echo "ERROR: local hydration did not produce ${DATED_DIR}/comparison.md" >&2
        exit 1
    fi
    mkdir -p "${LATEST_DIR}"
    for artifact in "${LATEST_FILES[@]}"; do
        if [[ -f "${DATED_DIR}/${artifact}" ]]; then
            cp -f "${DATED_DIR}/${artifact}" "${LATEST_DIR}/${artifact}"
        fi
    done
    log "published local latest artifacts to ${LATEST_DIR}/"
fi

REMOTE_BASE="${REMOTE_HOST}:${REMOTE_REPO}"
log "sync price cache and shadow artifacts to ${REMOTE_BASE}"
run_cmd ssh "${REMOTE_HOST}" "mkdir -p ${REMOTE_REPO}/outputs/research/flow_detection_v1 ${REMOTE_REPO}/${SHADOW_OUTPUT_DIR}/${TRADE_DATE} ${REMOTE_REPO}/${SHADOW_OUTPUT_DIR}/latest"
run_cmd rsync -av "outputs/research/flow_detection_v1/price_panel.parquet" "${REMOTE_BASE}/outputs/research/flow_detection_v1/price_panel.parquet"
run_cmd rsync -av --delete "${DATED_DIR}/" "${REMOTE_BASE}/${SHADOW_OUTPUT_DIR}/${TRADE_DATE}/"
run_cmd rsync -av --delete "${LATEST_DIR}/" "${REMOTE_BASE}/${SHADOW_OUTPUT_DIR}/latest/"

if [[ "${RUN_REMOTE_REFRESH}" -eq 1 ]]; then
    log "refresh VM reconciliation and health check"
    REMOTE_REFRESH_CMD="cd ${REMOTE_REPO} && source scripts/runtime_env.sh && activate_runtime_venv ${REMOTE_REPO} && python3 -m scripts.live_vs_shadow_reconciliation --trade-date ${TRADE_DATE} --shadow-dir ${SHADOW_OUTPUT_DIR} --output-dir outputs/reconciliation/live_vs_shadow && python3 -m scripts.caerus_daily_health_check --trade-date ${TRADE_DATE}"
    log "remote refresh command: ${REMOTE_REFRESH_CMD}"
    run_cmd ssh "${REMOTE_HOST}" "${REMOTE_REFRESH_CMD}"
else
    log "remote refresh skipped"
fi
