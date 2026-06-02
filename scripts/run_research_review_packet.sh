#!/usr/bin/env bash
# Build the read-only Caerus research review packet. Safe for manual VM use.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}" || exit 1

export TZ="America/New_York"

if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
fi

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

TRADE_DATE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)
            TRADE_DATE="${2:-}"
            shift 2
            ;;
        *)
            echo "[RESEARCH_REVIEW][WARN] ignoring unsupported arg: $1" >&2
            shift
            ;;
    esac
done

if [[ -z "${TRADE_DATE}" ]]; then
    TRADE_DATE="$(python3 scripts/build_research_review_packet.py --print-date)"
fi

echo "[RESEARCH_REVIEW] trade_date=${TRADE_DATE}"
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
STEP_LOG="${LOG_DIR}/research_review_packet_steps_${TRADE_DATE}.log"
: > "${STEP_LOG}"

run_best_effort() {
    local label="$1"
    shift
    echo "[RESEARCH_REVIEW] ${label}"
    "$@" >> "${STEP_LOG}" 2>&1 || {
        local rc=$?
        echo "[RESEARCH_REVIEW][WARN] ${label} failed exit_code=${rc}; continuing with available artifacts" >&2
        tail -n 20 "${STEP_LOG}" >&2 || true
        return 0
    }
}

run_best_effort "building position attribution" \
    python3 scripts/build_position_attribution.py --date "${TRADE_DATE}"

NEEDS_HYDRATION="$(
    python3 -c 'import json,sys; from pathlib import Path; p=Path("outputs/attribution")/sys.argv[1]/"attribution_summary.json"; data=json.loads(p.read_text()) if p.exists() else {}; print("1" if data.get("is_price_source_fresh") is False else "0")' "${TRADE_DATE}" \
    || printf "0"
)"
if [[ "${NEEDS_HYDRATION}" == "1" && -f "scripts/hydrate_price_cache_only.py" && "${RESEARCH_REVIEW_SKIP_HYDRATION:-0}" != "1" ]]; then
    run_best_effort "hydrating canonical price cache" \
        python3 scripts/hydrate_price_cache_only.py --trade-date "${TRADE_DATE}" --strict
    run_best_effort "rebuilding position attribution after hydration" \
        python3 scripts/build_position_attribution.py --date "${TRADE_DATE}"
fi

run_best_effort "building decision attribution" \
    python3 scripts/build_decision_attribution.py --date "${TRADE_DATE}"

run_best_effort "building risk summary" \
    python3 scripts/build_risk_summary.py --date "${TRADE_DATE}"

run_best_effort "building execution timing counterfactual" \
    python3 scripts/build_execution_timing_counterfactual.py --date "${TRADE_DATE}"

run_best_effort "building promotion readiness windows" \
    python3 scripts/build_promotion_readiness_windows.py --date "${TRADE_DATE}"

run_best_effort "building strategy differentiation" \
    python3 scripts/build_strategy_differentiation.py --date "${TRADE_DATE}"

echo "[RESEARCH_REVIEW] building packet"
python3 scripts/build_research_review_packet.py --date "${TRADE_DATE}" >> "${STEP_LOG}" 2>&1 || {
    rc=$?
    echo "[RESEARCH_REVIEW][ERROR] packet build failed exit_code=${rc}" >&2
    tail -n 40 "${STEP_LOG}" >&2 || true
    exit "${rc}"
}

echo "[RESEARCH_REVIEW] outputs:"
echo "  outputs/research_review/${TRADE_DATE}/research_review.json"
echo "  outputs/research_review/${TRADE_DATE}/research_review.md"
echo "  outputs/research_review/${TRADE_DATE}/research_review.html"
echo "[RESEARCH_REVIEW] step log: ${STEP_LOG}"
