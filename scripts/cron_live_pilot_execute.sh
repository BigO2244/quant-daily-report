#!/usr/bin/env bash
# LIVE_PILOT scheduled execution lane. This mirrors the paper Phase 2 lifecycle
# while keeping live broker submission behind live-pilot-only approval gates.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

export TZ="America/New_York"

# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "FATAL: python interpreter not found or not executable: ${PYTHON_BIN}" >&2
    exit 1
fi

ENV_FILE="${HOME}/.caerus/live_pilot.env"
if [[ ! -f "${ENV_FILE}" ]]; then
    echo "FATAL: ${ENV_FILE} not found" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

# Shared lane behavior params — sourced AFTER the env file. lane_params.sh
# provides SHARED DEFAULTS with env-wins semantics (single source for BOTH the
# paper 9:35 lane and this live lane): a tighter operator-set value in
# ~/.caerus/live_pilot.env (e.g. CAP_PCT/MAX_ORDERS/CONCENTRATED_MAX_WEIGHT)
# always wins. Approval/arming gates stay env-file-owned.
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lane_params.sh"

truthy() {
    case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
        1|true|yes|y|on|approve_live_pilot) return 0 ;;
        *) return 1 ;;
    esac
}

export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="live_pilot"
export TRADING_MODE="live_pilot"
export WORKFLOW_KIND="live_pilot"
export CAERUS_LIVE_PILOT_CRON_CONTEXT="1"
export ALPACA_PAPER="0"
export ALPACA_BASE_URL="https://api.alpaca.markets"
GATE_RUN_TS="$(date +%Y%m%dT%H%M%S%z)"
GATE_RUN_ID="${REPORT_DATE}T${GATE_RUN_TS}_live_pilot_cron_gate"
GATE_RUN_ROOT="outputs/live_pilot/runs/${GATE_RUN_ID}"
export CAERUS_LIVE_PILOT_CAPITAL_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP:-}"
# Shared behavior params (MIN_TRADE_USD, MAX_ORDERS, CAP_PCT, CONCENTRATED_*,
# USE_BROAD_TARGETS) default via scripts/lane_params.sh — sourced above, shared
# by BOTH lanes, env-file overrides (tighter operator values) always win.
export CAERUS_LIVE_PILOT_SLEEVE_ID="${CAERUS_LIVE_PILOT_SLEEVE_ID:-orion}"
export CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH="${CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH:-cfdc5d0aa0e3fdc38adadc78f1ebc30cbc83df187a4223c22597e787cd8a7c85}"
export CAERUS_LIVE_PILOT_APPROVED="${CAERUS_LIVE_PILOT_APPROVED:-0}"
export CAERUS_LIVE_PILOT_SUBMIT_APPROVED="${CAERUS_LIVE_PILOT_SUBMIT_APPROVED:-0}"
export CAERUS_LIVE_PILOT_SCHEDULE_ENABLED="${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED:-0}"
export CAERUS_LIVE_PILOT_CRON_APPROVED="${CAERUS_LIVE_PILOT_CRON_APPROVED:-0}"

LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}" outputs/live_pilot/logs outputs/live_pilot/plans "outputs/workflow/${REPORT_DATE}"
LOG_FILE="${LOG_DIR}/live_pilot_execute_${REPORT_DATE}.log"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== LIVE_PILOT SCHEDULED EXECUTION ==="
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "report_date=${REPORT_DATE}"
echo "repo_root=${REPO_ROOT}"
echo "trading_mode=${TRADING_MODE}"
echo "alpaca_paper=${ALPACA_PAPER}"
echo "alpaca_base_url=${ALPACA_BASE_URL}"
echo "approved_sleeve=${CAERUS_LIVE_PILOT_SLEEVE_ID}"
echo "capital_cap_mode=dynamic_portfolio_value"
echo "lane_params_fingerprint=${CAERUS_LANE_PARAMS_FINGERPRINT}"
echo "max_orders=${CAERUS_LIVE_PILOT_MAX_ORDERS}"
echo "schedule_enabled=${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED}"
echo "cron_approved=${CAERUS_LIVE_PILOT_CRON_APPROVED}"
echo "submit_approved=${CAERUS_LIVE_PILOT_SUBMIT_APPROVED}"

write_live_pilot_pointer() {
    local status="$1"
    local run_id="$2"
    local run_root="$3"
    local status_message="$4"
    LIVE_PILOT_POINTER_STATUS="${status}" \
    LIVE_PILOT_POINTER_RUN_ID="${run_id}" \
    LIVE_PILOT_POINTER_RUN_ROOT="${run_root}" \
    LIVE_PILOT_POINTER_MESSAGE="${status_message}" \
    "${PYTHON_BIN}" - <<'PY'
import datetime as dt
import json
import os
from pathlib import Path

trade_date = os.environ["REPORT_DATE"]
path = Path("outputs") / "workflow" / trade_date / "live_pilot_execution.json"
path.parent.mkdir(parents=True, exist_ok=True)
payload = {
    "stage": "live_pilot_execution",
    "trade_date": trade_date,
    "mode": "LIVE_PILOT",
    "run_id": os.environ.get("LIVE_PILOT_POINTER_RUN_ID", ""),
    "run_root": os.environ.get("LIVE_PILOT_POINTER_RUN_ROOT", ""),
    "status": os.environ.get("LIVE_PILOT_POINTER_STATUS", ""),
    "status_message": os.environ.get("LIVE_PILOT_POINTER_MESSAGE", ""),
    "created_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
}
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"live_pilot_workflow_pointer={path}")
PY
}

summary_field() {
    local summary_json="$1"
    local field="$2"
    SUMMARY_JSON="${summary_json}" SUMMARY_FIELD="${field}" "${PYTHON_BIN}" - <<'PY'
import json
import os

payload = json.loads(os.environ["SUMMARY_JSON"])
print(payload.get(os.environ["SUMMARY_FIELD"], ""))
PY
}

write_gate_state_blocked() {
    local reason="$1"
    "${PYTHON_BIN}" scripts/live_pilot_write_gate_state.py \
        --run-id "${GATE_RUN_ID}" \
        --trade-date "${REPORT_DATE}" \
        --decision BLOCKED \
        --block-reason "${reason}" \
        --output-root outputs/live_pilot >/dev/null || true
}

if ! truthy "${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED}"; then
    echo "LIVE_PILOT schedule disabled: set CAERUS_LIVE_PILOT_SCHEDULE_ENABLED=1 to enable scheduled dry-run/live-pilot lifecycle."
    write_live_pilot_pointer "disabled" "" "" "schedule_disabled"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
    exit 0
fi

if ! truthy "${CAERUS_LIVE_PILOT_CRON_APPROVED}"; then
    echo "FATAL: CAERUS_LIVE_PILOT_CRON_APPROVED must be 1 for scheduled LIVE_PILOT execution."
    write_gate_state_blocked "missing_live_pilot_cron_approval"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "missing_live_pilot_cron_approval"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
fi

if ! truthy "${CAERUS_LIVE_PILOT_APPROVED}"; then
    echo "FATAL: CAERUS_LIVE_PILOT_APPROVED must be 1 for scheduled LIVE_PILOT execution."
    write_gate_state_blocked "missing_live_pilot_approval"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "missing_live_pilot_approval"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
fi

if [[ "${ALPACA_BASE_URL:-}" != "https://api.alpaca.markets" && "${ALPACA_BASE_URL:-}" != "https://api.alpaca.markets/" ]]; then
    echo "FATAL: ALPACA_BASE_URL must be https://api.alpaca.markets for LIVE_PILOT" >&2
    write_gate_state_blocked "invalid_live_pilot_endpoint"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "invalid_live_pilot_endpoint"
    exit 1
fi

if [[ "${CAERUS_LIVE_PILOT_KILL_SWITCH:-0}" == "1" ]]; then
    echo "FATAL: CAERUS_LIVE_PILOT_KILL_SWITCH=1" >&2
    write_gate_state_blocked "live_pilot_kill_switch_enabled"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_kill_switch_enabled"
    exit 1
fi

# Resolve the capital cap dynamically from the account's portfolio value (no fixed
# program ceiling; an optional CAERUS_LIVE_PILOT_CAPITAL_CAP only tightens it). The
# same resolver is used by the execution path, so plan sizing and execution agree.
CAP_RESOLVE="$(
    "${PYTHON_BIN}" - <<'PY'
import os, sys
try:
    from brokers.alpaca_broker import AlpacaBroker
    from core.live_pilot_guardrails import resolve_dynamic_cap
    acct = AlpacaBroker.from_env().get_account() or {}
    pv = acct.get("portfolio_value") or acct.get("equity")
    cap, src = resolve_dynamic_cap(pv, os.environ)
except Exception as exc:  # fail-closed: any error -> unresolved -> block
    print("", f"error:{exc}", sep="\t")
    sys.exit(0)
print("" if cap is None else f"{cap:.2f}", src, sep="\t")
PY
)"
PLAN_CAP="$(printf '%s' "${CAP_RESOLVE}" | cut -f1)"
CAP_SOURCE="$(printf '%s' "${CAP_RESOLVE}" | cut -f2)"
if [[ -z "${PLAN_CAP}" ]]; then
    echo "FATAL: could not resolve live capital cap from portfolio value (source=${CAP_SOURCE})"
    write_gate_state_blocked "live_pilot_capital_cap_unresolved"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_capital_cap_unresolved"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
fi
echo "resolved_capital_cap=${PLAN_CAP} (source=${CAP_SOURCE})"

BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
BUNDLE_VALIDATION_PATH="${REPO_ROOT}/outputs/workflow/${REPORT_DATE}/live_pilot_bundle_validation.json"
if ! "${PYTHON_BIN}" -m core.precompute_bundle_validation \
    --bundle-dir "${BUNDLE_DIR}" \
    --trade-date "${REPORT_DATE}" \
    --json-output "${BUNDLE_VALIDATION_PATH}"; then
    echo "FATAL: precompute bundle validation failed for LIVE_PILOT; details=${BUNDLE_VALIDATION_PATH}"
    write_gate_state_blocked "precompute_bundle_validation_failed"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "precompute_bundle_validation_failed"
    exit 1
fi
echo "OK: precompute bundle validated at ${BUNDLE_DIR}"
echo "bundle_validation=${BUNDLE_VALIDATION_PATH}"

set +e
BUILD_OUTPUT="$(
    "${PYTHON_BIN}" scripts/live_pilot_build_plan_from_precompute.py \
        --trade-date "${REPORT_DATE}" \
        --approved-sleeve "${CAERUS_LIVE_PILOT_SLEEVE_ID}" \
        --capital-cap "${PLAN_CAP}" \
        --max-orders "${CAERUS_LIVE_PILOT_MAX_ORDERS}" \
        --output-dir outputs/live_pilot/plans
)"
BUILD_STATUS=$?
set -e
echo "${BUILD_OUTPUT}"
if [[ "${BUILD_STATUS}" -ne 0 ]]; then
    echo "live_pilot_plan_builder_exit_code=${BUILD_STATUS}"
    write_gate_state_blocked "live_pilot_plan_blocked"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_plan_blocked"
    exit "${BUILD_STATUS}"
fi

set +e
PLAN_PATH="$(
    BUILD_OUTPUT="${BUILD_OUTPUT}" "${PYTHON_BIN}" - <<'PY'
import json
import os

payload = json.loads(os.environ["BUILD_OUTPUT"])
if payload.get("status") != "READY_FOR_MANUAL_APPROVAL":
    raise SystemExit(f"FATAL: live pilot plan blocked: {payload.get('status')}")
print(str(payload.get("json_path") or ""))
PY
)"
PLAN_STATUS=$?
set -e
if [[ "${PLAN_STATUS}" -ne 0 ]]; then
    echo "FATAL: live pilot plan blocked"
    write_gate_state_blocked "live_pilot_plan_blocked"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_plan_blocked"
    exit "${PLAN_STATUS}"
fi
if [[ -z "${PLAN_PATH}" ]]; then
    echo "FATAL: live pilot plan did not report json_path"
    write_gate_state_blocked "live_pilot_plan_missing_path"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_plan_missing_path"
    exit 1
fi
echo "plan_path=${PLAN_PATH}"

RUN_TS="$(date +%Y%m%dT%H%M%S%z)"
DRY_RUN_ID="${REPORT_DATE}T${RUN_TS}_live_pilot_cron_dry"
echo "=== LIVE_PILOT SCHEDULED DRY RUN ==="
DRY_OUTPUT="$(CAERUS_LIVE_PILOT_DRY_RUN=1 "${PYTHON_BIN}" scripts/live_pilot_execute.py --plan "${PLAN_PATH}" --run-id "${DRY_RUN_ID}")"
echo "${DRY_OUTPUT}"
DRY_RUN_ROOT="$(summary_field "${DRY_OUTPUT}" run_root)"
write_live_pilot_pointer "dry_run" "${DRY_RUN_ID}" "${DRY_RUN_ROOT}" "dry_run_completed"

if [[ "${CAERUS_LIVE_PILOT_SUBMIT_APPROVED}" != "1" ]]; then
    echo "LIVE_PILOT scheduled submission paused: CAERUS_LIVE_PILOT_SUBMIT_APPROVED is not 1."
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
    exit 0
fi

echo "=== LIVE_PILOT SCHEDULED SUBMISSION ==="
LIVE_RUN_ID="${REPORT_DATE}T${RUN_TS}_live_pilot_cron_submit"
set +e
LIVE_OUTPUT="$(CAERUS_LIVE_PILOT_DRY_RUN=0 "${PYTHON_BIN}" scripts/live_pilot_execute.py --plan "${PLAN_PATH}" --run-id "${LIVE_RUN_ID}" 2>&1)"
LIVE_STATUS=$?
set -e
echo "${LIVE_OUTPUT}"
LIVE_RUN_ROOT="$(summary_field "${LIVE_OUTPUT}" run_root || true)"
LIVE_TERMINAL_STATUS="$(summary_field "${LIVE_OUTPUT}" terminal_status || true)"
if [[ -n "${LIVE_RUN_ROOT}" ]]; then
    write_live_pilot_pointer "${LIVE_TERMINAL_STATUS:-unknown}" "${LIVE_RUN_ID}" "${LIVE_RUN_ROOT}" "scheduled_submission_completed"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${LIVE_STATUS}"
exit "${LIVE_STATUS}"
