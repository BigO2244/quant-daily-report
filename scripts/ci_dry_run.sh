#!/usr/bin/env bash
# ci_dry_run.sh — Daily/weekend pressure-test harness.
#
# PURPOSE
# -------
# Validates the full planning + execution pipeline in an OFFLINE, credential-free
# mode suitable for CI and weekend health checks.  No real orders are submitted.
#
# STEPS (in order)
# ----------------
#   (a) Validate the precompute bundle if present (core.precompute_bundle_validation).
#       Missing bundle is SKIP, not FAIL.
#   (b) Build the full-rebalance plan via scripts/live_pilot_build_plan_from_precompute.py
#       in PAPER staging config (lane_params.sh; CAPITAL_CAP=10000; PLANNING_EQUITY_CAP).
#       Uses --capital-cap override so CI can run without broker credentials.
#   (c) Run the full targeted pytest battery.
#   (d) bash -n syntax-check every scripts/cron_*.sh + lane_params.sh.
#   (e) Print PASS/FAIL summary; exit nonzero on any failure.
#
# USAGE
#   # Default: today
#   bash scripts/ci_dry_run.sh
#
#   # Explicit date:
#   REPORT_DATE=2026-04-10 bash scripts/ci_dry_run.sh
#
#   # With explicit capital cap (overrides the default $10k staging pin):
#   bash scripts/ci_dry_run.sh --capital-cap 5000
#
# OFFLINE GUARANTEE
# -----------------
# The plan builder hits yfinance if entry_prices are absent from the bundle.
# CI passes a fixture capital cap so the plan build degrades gracefully when
# the bundle is absent (SKIP) or prices cannot be fetched (plan BLOCKED, not
# CI FAIL).  Tests are hermetic and must not require network.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
CAPITAL_CAP_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --capital-cap)
            CAPITAL_CAP_OVERRIDE="$2"
            shift 2
            ;;
        --capital-cap=*)
            CAPITAL_CAP_OVERRIDE="${1#*=}"
            shift
            ;;
        *)
            echo "Unknown argument: $1" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
export TZ="America/New_York"
REPORT_DATE="${REPORT_DATE:-$(date +%F)}"

# --- Load .env if present (non-fatal) ---
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env" || true
    set +a
fi

# --- Source shared lane behavior params ---
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lane_params.sh"

# --- Paper staging pins (mirror of cron_execute.sh paper-lane pins) ---
export CAERUS_LIVE_PILOT_CAPITAL_CAP="${CAPITAL_CAP_OVERRIDE:-10000}"
export CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP}"
export MODE="paper"
export TRADING_MODE="paper"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"
export CAERUS_LIVE_PILOT_SLEEVE_ID="${CAERUS_LIVE_PILOT_SLEEVE_ID:-caerus_orion}"

# Suppress any real-money arming
unset CAERUS_LIVE_PILOT_KILL_SWITCH 2>/dev/null || true

# --- Resolve Python (same activation path as the cron lanes) ---
# runtime_env.sh knows where the runtime venv lives on each machine (Mac: .venv/,
# VM: ~/.venvs/...). Falling back to bare system python3 silently loses all deps,
# which made the harness FAIL on the VM while every manual run passed.
if [[ -f "${REPO_ROOT}/scripts/runtime_env.sh" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/runtime_env.sh"
    activate_runtime_venv "${REPO_ROOT}" >/dev/null 2>&1 || true
fi
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 2>/dev/null || echo '')}"
if [[ ! -x "${PYTHON_BIN:-}" && -x "${REPO_ROOT}/.venv/bin/python3" ]]; then
    PYTHON_BIN="${REPO_ROOT}/.venv/bin/python3"
fi
if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
    echo "FATAL: no usable python3 found" >&2
    exit 1
fi
if ! "${PYTHON_BIN}" -c "import pandas" >/dev/null 2>&1; then
    echo "FATAL: resolved python (${PYTHON_BIN}) lacks runtime deps (pandas) — venv not activated?" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0
SKIP_COUNT=0
declare -a RESULTS=()

_pass() {
    local label="$1"
    PASS_COUNT=$(( PASS_COUNT + 1 ))
    RESULTS+=("  PASS  ${label}")
    echo "  [PASS] ${label}"
}

_fail() {
    local label="$1"
    local detail="${2:-}"
    FAIL_COUNT=$(( FAIL_COUNT + 1 ))
    RESULTS+=("  FAIL  ${label}${detail:+ | ${detail}}")
    echo "  [FAIL] ${label}${detail:+ | ${detail}}"
}

_skip() {
    local label="$1"
    local detail="${2:-}"
    SKIP_COUNT=$(( SKIP_COUNT + 1 ))
    RESULTS+=("  SKIP  ${label}${detail:+ | ${detail}}")
    echo "  [SKIP] ${label}${detail:+ | ${detail}}"
}

# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  ci_dry_run.sh — Caerus Daily/Weekend Pressure-Test Harness         ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
echo "  report_date        = ${REPORT_DATE}"
echo "  capital_cap        = \$${CAERUS_LIVE_PILOT_CAPITAL_CAP}"
echo "  python             = ${PYTHON_BIN}"
echo "  lane_params_fp     = ${CAERUS_LANE_PARAMS_FINGERPRINT}"
echo ""

# ---------------------------------------------------------------------------
# (a) Precompute bundle validation — SKIP if bundle is absent
# ---------------------------------------------------------------------------
echo "── Step (a): Precompute bundle validation ──────────────────────────────"
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
BUNDLE_VALIDATION_PATH="/tmp/ci_dry_run_bundle_validation_${REPORT_DATE}.json"

if [[ ! -d "${BUNDLE_DIR}" ]]; then
    _skip "precompute_bundle_validation" "bundle dir absent: ${BUNDLE_DIR}"
else
    set +e
    "${PYTHON_BIN}" -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --json-output "${BUNDLE_VALIDATION_PATH}" \
        2>&1
    BUNDLE_EXIT=$?
    set -e

    if [[ "${BUNDLE_EXIT}" -eq 0 ]]; then
        _pass "precompute_bundle_validation"
    else
        _fail "precompute_bundle_validation" "exit=${BUNDLE_EXIT}"
    fi
fi

# ---------------------------------------------------------------------------
# (b) Plan build — PAPER staging config; fails gracefully without credentials
# ---------------------------------------------------------------------------
echo ""
echo "── Step (b): Plan build (PAPER staging, no broker creds) ───────────────"
PAPER_PLANS_DIR="${REPO_ROOT}/outputs/paper_lane/plans"
PAPER_STATE_DIR="${REPO_ROOT}/outputs/paper_lane/state"
mkdir -p "${PAPER_PLANS_DIR}" "${PAPER_STATE_DIR}"

# Find the precompute payload to use.  Prefer REPORT_DATE; fall back to latest.
PAYLOAD_ARG=""
if [[ -f "${BUNDLE_DIR}/planned_execution_payload.json" ]]; then
    PAYLOAD_ARG="--trade-date ${REPORT_DATE}"
else
    # Try to find any payload under outputs/precompute
    LATEST_PAYLOAD="$(
        find "${REPO_ROOT}/outputs/precompute" -name "planned_execution_payload.json" \
            2>/dev/null | sort | tail -1
    )"
    if [[ -n "${LATEST_PAYLOAD}" ]]; then
        PAYLOAD_ARG="--payload-path ${LATEST_PAYLOAD}"
        echo "  INFO: no bundle for ${REPORT_DATE}; using latest: ${LATEST_PAYLOAD}"
    fi
fi

if [[ -z "${PAYLOAD_ARG}" ]]; then
    _skip "plan_build" "no precompute payload found"
else
    set +e
    # shellcheck disable=SC2086
    BUILD_OUTPUT="$(
        "${PYTHON_BIN}" scripts/live_pilot_build_plan_from_precompute.py \
            ${PAYLOAD_ARG} \
            --lane paper \
            --approved-sleeve "${CAERUS_LIVE_PILOT_SLEEVE_ID}" \
            --capital-cap "${CAERUS_LIVE_PILOT_CAPITAL_CAP}" \
            --max-orders "${CAERUS_LIVE_PILOT_MAX_ORDERS}" \
            --output-dir "${PAPER_PLANS_DIR}" \
            --state-dir "${PAPER_STATE_DIR}" \
            2>&1
    )"
    BUILD_EXIT=$?
    set -e

    if [[ "${BUILD_EXIT}" -eq 0 ]]; then
        BUILD_STATUS="$(printf '%s' "${BUILD_OUTPUT}" | "${PYTHON_BIN}" -c "
import sys, json
raw = sys.stdin.read()
# Find last valid JSON object in the stream
import json as _j
dec = _j.JSONDecoder()
obj = {}
idx = 0
while True:
    start = raw.find('{', idx)
    if start == -1: break
    try:
        cand, end = dec.raw_decode(raw, start)
        if isinstance(cand, dict): obj = cand
        idx = end
    except _j.JSONDecodeError:
        idx = start + 1
print(obj.get('status', 'UNKNOWN'))
" 2>/dev/null || echo "UNKNOWN")"
        echo "  plan_status=${BUILD_STATUS}"
        if [[ "${BUILD_STATUS}" == "READY_FOR_MANUAL_APPROVAL" ]]; then
            _pass "plan_build (status=${BUILD_STATUS})"
        elif [[ "${BUILD_STATUS}" == "BLOCKED" ]]; then
            # BLOCKED is expected when yfinance is unavailable in CI — treat as SKIP
            _skip "plan_build" "plan BLOCKED (likely no price data in CI; status=${BUILD_STATUS})"
        else
            _fail "plan_build" "unexpected status=${BUILD_STATUS}"
        fi
    else
        _fail "plan_build" "build script exit=${BUILD_EXIT}"
    fi
fi

# ---------------------------------------------------------------------------
# (c) Pytest battery
# ---------------------------------------------------------------------------
echo ""
echo "── Step (c): Pytest battery ────────────────────────────────────────────"
PYTEST_TARGETS=(
    "Tests/test_unified_paper_lane.py"
    "Tests/test_live_pilot_guardrails.py"
    "Tests/test_live_pilot_execution_path.py"
    "Tests/test_live_pilot_transition_phase2.py"
    "Tests/test_cron_live_pilot.py"
    "Tests/test_concentration.py"
    "Tests/test_concentrated_alpha_policy.py"
    "Tests/test_live_pilot_build_plan_from_precompute.py"
    "Tests/test_precompute_contract.py"
    "Tests/parity/test_live_full_rebalance_parity.py"
    "Tests/test_live_regime_allocation.py"
)

set +e
"${PYTHON_BIN}" -m pytest "${PYTEST_TARGETS[@]}" -q --tb=short 2>&1
PYTEST_EXIT=$?
set -e

if [[ "${PYTEST_EXIT}" -eq 0 ]]; then
    _pass "pytest_battery (${#PYTEST_TARGETS[@]} test files)"
else
    _fail "pytest_battery" "exit=${PYTEST_EXIT}"
fi

# ---------------------------------------------------------------------------
# (d) bash -n syntax check on cron scripts + lane_params.sh
# ---------------------------------------------------------------------------
echo ""
echo "── Step (d): bash -n syntax checks ─────────────────────────────────────"
SYNTAX_FILES=(
    "${REPO_ROOT}/scripts/lane_params.sh"
    "${BASH_SOURCE[0]}"
)
while IFS= read -r -d '' f; do
    SYNTAX_FILES+=("$f")
done < <(find "${REPO_ROOT}/scripts" -maxdepth 1 -name "cron_*.sh" -print0 | sort -z)

ALL_SYNTAX_OK=1
for f in "${SYNTAX_FILES[@]}"; do
    fname="${f##*/}"
    if bash -n "${f}" 2>/dev/null; then
        echo "  [OK ]  ${fname}"
    else
        echo "  [ERR]  ${fname}"
        ALL_SYNTAX_OK=0
    fi
done

if [[ "${ALL_SYNTAX_OK}" -eq 1 ]]; then
    _pass "bash_syntax_checks (${#SYNTAX_FILES[@]} files)"
else
    _fail "bash_syntax_checks"
fi

# ---------------------------------------------------------------------------
# (e) Summary
# ---------------------------------------------------------------------------
echo ""
echo "╔══════════════════════════════════════════════════════════════════════╗"
echo "║  ci_dry_run.sh — Summary                                            ║"
echo "╚══════════════════════════════════════════════════════════════════════╝"
for r in "${RESULTS[@]}"; do
    echo "${r}"
done
echo ""
echo "  PASS=${PASS_COUNT}  FAIL=${FAIL_COUNT}  SKIP=${SKIP_COUNT}"
echo ""

if [[ "${FAIL_COUNT}" -gt 0 ]]; then
    echo "RESULT: FAIL (${FAIL_COUNT} failure(s))"
    exit 1
else
    echo "RESULT: PASS"
    exit 0
fi
