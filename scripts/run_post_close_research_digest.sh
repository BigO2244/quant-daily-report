#!/usr/bin/env bash
# Post-close read-only research digest automation.
set -euo pipefail

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
activate_runtime_venv "${REPO_ROOT}" || exit 1

EXPLICIT_DATE=""
NO_EMAIL=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --date)
            EXPLICIT_DATE="${2:-}"
            shift 2
            ;;
        --no-email)
            NO_EMAIL=1
            shift
            ;;
        *)
            echo "[POST_CLOSE_DIGEST][ERROR] unsupported arg: $1" >&2
            exit 2
            ;;
    esac
done

mkdir -p "${REPO_ROOT}/logs"
PRE_TARGET_LABEL="${EXPLICIT_DATE:-auto}"
LOG_FILE="${REPO_ROOT}/logs/post_close_research_digest_${PRE_TARGET_LABEL}.log"
: > "${LOG_FILE}"

echo "[POST_CLOSE_DIGEST] repo=${REPO_ROOT}"
echo "[POST_CLOSE_DIGEST] explicit_date=${EXPLICIT_DATE:-auto}"

run_step() {
    local label="$1"
    shift
    echo "[POST_CLOSE_DIGEST] ${label}"
    "$@" >> "${LOG_FILE}" 2>&1
}

HYDRATE_CMD=(python3 scripts/hydrate_price_cache_only.py --strict)
if [[ -n "${EXPLICIT_DATE}" ]]; then
    HYDRATE_CMD+=(--trade-date "${EXPLICIT_DATE}")
fi
run_step "hydrating canonical price cache" "${HYDRATE_CMD[@]}"

if [[ -n "${EXPLICIT_DATE}" ]]; then
    TARGET_DATE="${EXPLICIT_DATE}"
else
    TARGET_DATE="$(python3 scripts/send_post_close_research_digest.py --print-target-date)"
fi
echo "[POST_CLOSE_DIGEST] target_date=${TARGET_DATE}"

if [[ "${PRE_TARGET_LABEL}" == "auto" ]]; then
    FINAL_LOG_FILE="${REPO_ROOT}/logs/post_close_research_digest_${TARGET_DATE}.log"
    if [[ "${FINAL_LOG_FILE}" != "${LOG_FILE}" ]]; then
        mv "${LOG_FILE}" "${FINAL_LOG_FILE}"
        LOG_FILE="${FINAL_LOG_FILE}"
    fi
fi

run_step "building position attribution" \
    python3 scripts/build_position_attribution.py --date "${TARGET_DATE}"

run_step "building decision attribution" \
    python3 scripts/build_decision_attribution.py --date "${TARGET_DATE}"

run_step "building risk summary" \
    python3 scripts/build_risk_summary.py --date "${TARGET_DATE}"

run_step "building risk coverage" \
    python3 scripts/build_risk_coverage.py --date "${TARGET_DATE}"

run_step "hydrating execution timing minute bars" \
    python3 scripts/build_execution_timing_cache.py --date "${TARGET_DATE}"

run_step "building execution timing counterfactual" \
    python3 scripts/build_execution_timing_counterfactual.py --date "${TARGET_DATE}"

run_step "building promotion readiness windows" \
    python3 scripts/build_promotion_readiness_windows.py --date "${TARGET_DATE}"

run_step "building strategy differentiation" \
    python3 scripts/build_strategy_differentiation.py --date "${TARGET_DATE}"

run_step "building position sizing research" \
    python3 scripts/build_position_sizing_research.py --date "${TARGET_DATE}"

run_step "building universe governance" \
    python3 scripts/build_universe_governance.py --date "${TARGET_DATE}"

run_step "building regime attribution" \
    python3 scripts/build_regime_attribution.py --date "${TARGET_DATE}"

run_step "building promotion governance" \
    python3 scripts/build_promotion_governance.py --date "${TARGET_DATE}"

run_step "building dynamic strategy allocation" \
    python3 scripts/build_dynamic_strategy_allocation.py --date "${TARGET_DATE}"

run_step "building governance blocker audit" \
    python3 scripts/build_governance_blocker_audit.py --date "${TARGET_DATE}"

run_step "building security master reconciliation" \
    python3 scripts/build_security_master_reconciliation.py --date "${TARGET_DATE}"

run_step "building execution payload audit" \
    python3 scripts/build_execution_payload_audit.py --date "${TARGET_DATE}"

run_step "building differentiation diagnostic" \
    python3 scripts/build_differentiation_diagnostic.py --date "${TARGET_DATE}"

run_step "building concentration diagnostic" \
    python3 scripts/build_concentration_diagnostic.py --date "${TARGET_DATE}"

run_step "building governance maturity" \
    python3 scripts/build_governance_maturity.py --date "${TARGET_DATE}"

run_step "building governance calibration" \
    python3 scripts/build_governance_calibration.py --date "${TARGET_DATE}"

run_step "building research review packet" \
    python3 scripts/build_research_review_packet.py --date "${TARGET_DATE}"

EMAIL_CMD=(python3 scripts/send_post_close_research_digest.py --date "${TARGET_DATE}")
if [[ "${NO_EMAIL}" -eq 1 ]]; then
    EMAIL_CMD+=(--no-email)
fi
run_step "sending research digest email" "${EMAIL_CMD[@]}"

cat "${LOG_FILE}"

echo "[POST_CLOSE_DIGEST] outputs:"
echo "  outputs/research_review/${TARGET_DATE}/research_review.html"
echo "  outputs/research_review/${TARGET_DATE}/research_review.md"
echo "  outputs/research_review/${TARGET_DATE}/research_review.json"
echo "[POST_CLOSE_DIGEST] log=${LOG_FILE}"
