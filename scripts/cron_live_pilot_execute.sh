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

# Deployments take the same lock exclusively. Holding a shared lock for the
# entire execution lifecycle prevents HEAD from changing between validation,
# dry-run planning, and broker submission.
mkdir -p "${HOME}/.caerus"
exec 8>"${HOME}/.caerus/source_deploy.lock"
DEPLOY_LOCK_BLOCKED=0
if ! flock -s -w 30 8; then
    DEPLOY_LOCK_BLOCKED=1
fi

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
# Shared behavior params (MIN_TRADE_USD, MAX_ORDERS, CAP_PCT,
# CONCENTRATED_MAX_WEIGHT) default via scripts/lane_params.sh — sourced above,
# shared by BOTH lanes, env-file overrides (tighter operator values) always win.
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
echo "capital_cap_mode=explicit_approved_ceiling"
echo "lane_params_fingerprint=${CAERUS_LANE_PARAMS_FINGERPRINT}"
echo "max_orders=${CAERUS_LIVE_PILOT_MAX_ORDERS}"
echo "schedule_enabled=${CAERUS_LIVE_PILOT_SCHEDULE_ENABLED}"
echo "cron_approved=${CAERUS_LIVE_PILOT_CRON_APPROVED}"
echo "submit_approved=${CAERUS_LIVE_PILOT_SUBMIT_APPROVED}"

# Owner policy, Choice 2 (2026-08-12): live capital is structurally disabled.
# Environment arming flags cannot override this code-level boundary.
echo "FATAL: live_capital_disabled_by_owner_policy" >&2
LIVE_DISABLED_RUN_ID="${GATE_RUN_ID}" LIVE_DISABLED_RUN_ROOT="${GATE_RUN_ROOT}" \
    "${PYTHON_BIN}" - <<'PY'
import json, os
from datetime import datetime, timezone
from pathlib import Path
path = Path("outputs/workflow") / os.environ["REPORT_DATE"] / "live_pilot_execution.json"
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps({
    "stage": "live_pilot_execution",
    "trade_date": os.environ["REPORT_DATE"],
    "mode": "LIVE_PILOT",
    "run_id": os.environ["LIVE_DISABLED_RUN_ID"],
    "run_root": os.environ["LIVE_DISABLED_RUN_ROOT"],
    "status": "blocked",
    "substatus": "live_capital_disabled_by_owner_policy",
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
exit 1

# BLOCKER 4 (§f): the ONE running-truth SHA is `git rev-parse HEAD` — the code
# this process actually runs — NOT the deploy marker. The deploy-drift guard
# (scripts/live_pilot_sha_guard.py) compares HEAD against deploy_state.json's
# deployed_sha and reports a dirty working tree. Drift/dirty fails the SUBMIT
# path closed (enforced below, immediately before submission); the DRY path
# proceeds but flags the drift loudly here.
refresh_sha_guard() {
    local guard_rc=0
    SHA_GUARD_JSON="$("${PYTHON_BIN}" scripts/live_pilot_sha_guard.py --repo-root "${REPO_ROOT}" 2>/dev/null)" || guard_rc=$?
    # Malformed/empty guard output is itself a fail-closed verdict. The submit
    # path must never inherit a stale prior value after a guard failure.
    eval "$(
        SHA_GUARD_JSON="${SHA_GUARD_JSON}" SHA_GUARD_RC="${guard_rc}" "${PYTHON_BIN}" - <<'PY'
import json, os, shlex
try:
    d = json.loads(os.environ.get("SHA_GUARD_JSON") or "{}")
except Exception:
    d = {}
def emit(k, v):
    print(f"{k}={shlex.quote('' if v is None else str(v))}")
valid = isinstance(d, dict) and isinstance(d.get("block_submit"), bool)
emit("RUNNING_SHA", d.get("running_sha") or "unknown")
emit("_DEPLOY_SHA", d.get("deployed_sha") or "unknown")
emit("SHA_DRIFT", d.get("sha_drift"))
emit("SHA_TREE_DIRTY", d.get("tree_dirty"))
emit("SHA_BLOCK_SUBMIT", d.get("block_submit") if valid else True)
emit("SHA_DRIFT_REASON", d.get("reason_code") if valid else "live_pilot_deploy_guard_unreadable")
emit("SHA_DRIFT_MESSAGE", d.get("message") if valid else "DEPLOY DRIFT GUARD: verdict output missing or malformed")
emit("SHA_GUARD_RC", os.environ.get("SHA_GUARD_RC") or "0")
PY
    )"
}

RUNNING_SHA="$(git rev-parse HEAD 2>/dev/null || echo "unknown")"
echo "running_sha=${RUNNING_SHA}"
if [[ "${DEPLOY_LOCK_BLOCKED}" == "0" ]]; then
    refresh_sha_guard
else
    _DEPLOY_SHA="unknown"
    SHA_DRIFT="unknown"
    SHA_TREE_DIRTY="unknown"
    SHA_BLOCK_SUBMIT="True"
    SHA_DRIFT_REASON="live_pilot_deployment_in_progress"
    SHA_DRIFT_MESSAGE="LIVE_PILOT blocked: deployment holds the source lock"
    SHA_GUARD_RC="lock_timeout"
fi
echo "deployed_sha=${_DEPLOY_SHA:-unknown}"
echo "deploy_sha_drift=${SHA_DRIFT:-unknown} working_tree_dirty=${SHA_TREE_DIRTY:-unknown}"
if [[ "${SHA_BLOCK_SUBMIT}" == "True" ]]; then
    echo "!!! DEPLOY DRIFT DETECTED: ${SHA_DRIFT_MESSAGE}"
    echo "!!! reason=${SHA_DRIFT_REASON} — SUBMIT will fail closed; DRY continues with drift flagged."
fi

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

json_file_field() {
    local path="$1"
    local field="$2"
    JSON_FILE_PATH="${path}" JSON_FILE_FIELD="${field}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["JSON_FILE_PATH"])
if not path.exists():
    raise SystemExit(1)
payload = json.loads(path.read_text(encoding="utf-8"))
print(payload.get(os.environ["JSON_FILE_FIELD"], ""))
PY
}

confirm_completed_runs() (
    # Execute-completion hook: immediately confirm every terminal run for today
    # that is not yet confirmed. This closes the race that let the 2026-07-10
    # 10:09 armed submit go unreported: the scheduled confirm sweep runs at a
    # fixed time and cannot see a run that finishes later, so the lane that just
    # produced the run triggers the same sweep here. Dedupe (the JSONL sent
    # ledger) makes this idempotent with the scheduled cron. Best-effort: never
    # changes the execute lane's exit code.
    if [[ -f "${REPO_ROOT}/.env" ]]; then
        set -a
        # shellcheck disable=SC1091
        source "${REPO_ROOT}/.env"
        set +a
    fi
    # The repository .env supplies SMTP settings but is paper-lane-oriented and
    # may also define Alpaca credentials/endpoint flags. Restore the dedicated
    # live-pilot environment *after* loading it, exactly as the scheduled
    # confirmation wrapper does. Keep this hook in a subshell so neither source
    # can leak changed credentials back into the execution wrapper.
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
    export MODE="live_pilot"
    export TRADING_MODE="live_pilot"
    export WORKFLOW_KIND="live_pilot"
    export CAERUS_LIVE_PILOT_CRON_CONTEXT="1"
    export ALPACA_PAPER="0"
    export ALPACA_BASE_URL="https://api.alpaca.markets"
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/scripts/live_pilot_confirm_lib.sh"
    live_pilot_confirm_sweep \
        "outputs/live_pilot/runs" \
        "outputs/live_pilot/state/confirm_sent_ledger.jsonl" \
        || echo "WARN: execute-completion confirm hook reported problems (non-blocking)"
)

write_gate_state_blocked() {
    local reason="$1"
    "${PYTHON_BIN}" scripts/live_pilot_write_gate_state.py \
        --run-id "${GATE_RUN_ID}" \
        --trade-date "${REPORT_DATE}" \
        --decision BLOCKED \
        --block-reason "${reason}" \
        --running-sha "${RUNNING_SHA:-}" \
        --deployed-sha "${_DEPLOY_SHA:-}" \
        --tree-dirty "${SHA_TREE_DIRTY:-}" \
        --guard-message "${SHA_DRIFT_MESSAGE:-}" \
        --output-root outputs/live_pilot >/dev/null || true
}

finish_blocked_run() {
    local reason="$1"
    local exit_code="${2:-1}"
    write_gate_state_blocked "${reason}"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "${reason}"
    confirm_completed_runs || true
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${exit_code}"
    exit "${exit_code}"
}

if [[ "${DEPLOY_LOCK_BLOCKED}" == "1" ]]; then
    echo "FATAL: LIVE_PILOT source lock unavailable; deployment is in progress" >&2
    finish_blocked_run "live_pilot_deployment_in_progress" 1
fi

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

# Live money requires an explicit dollar ceiling. Missing, malformed, non-positive,
# or above-program values fail closed before plan construction. The executor later
# tightens this ceiling to current portfolio value, so it can never expand here.
CAP_RESOLVE="$(
    "${PYTHON_BIN}" - <<'PY'
import math
import os
try:
    from core.live_pilot_guardrails import (
        LIVE_PILOT_APPROVED_MAX_CAP_USD,
        LIVE_PILOT_CAPITAL_CAP_ENV,
    )
    raw = str(os.environ.get(LIVE_PILOT_CAPITAL_CAP_ENV) or "").strip()
    cap = float(raw)
    if not math.isfinite(cap) or cap <= 0:
        raise ValueError("cap_must_be_positive_and_finite")
    if cap > LIVE_PILOT_APPROVED_MAX_CAP_USD:
        raise ValueError(
            f"cap_exceeds_approved_max:{cap:g}>{LIVE_PILOT_APPROVED_MAX_CAP_USD:g}"
        )
except Exception as exc:
    print("", f"invalid_explicit_cap:{exc}", sep="\t")
else:
    print(f"{cap:.2f}", "explicit_approved_cap", sep="\t")
PY
)"
PLAN_CAP="$(printf '%s' "${CAP_RESOLVE}" | cut -f1)"
CAP_SOURCE="$(printf '%s' "${CAP_RESOLVE}" | cut -f2)"
if [[ -z "${PLAN_CAP}" ]]; then
    echo "FATAL: explicit live capital cap is missing or invalid (source=${CAP_SOURCE})"
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
        --lane live_pilot \
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
    confirm_completed_runs || true
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=0"
    exit 0
fi

# Dry-run validation is intentionally safe with the kill switch engaged. A live
# submission still rechecks it here, after the dry run and immediately before
# the submission-only guards/path.
if [[ "${CAERUS_LIVE_PILOT_KILL_SWITCH:-1}" != "0" ]]; then
    echo "FATAL: CAERUS_LIVE_PILOT_KILL_SWITCH is engaged; submission remains blocked." >&2
    write_gate_state_blocked "live_pilot_kill_switch_enabled"
    write_live_pilot_pointer "blocked" "${GATE_RUN_ID}" "${GATE_RUN_ROOT}" "live_pilot_kill_switch_enabled"
    exit 1
fi

# BLOCKER 4 (§f) drift guard — SUBMIT fails closed if the running HEAD is not the
# deployed/audited SHA, if the working tree is dirty, or if HEAD is unresolvable.
# This makes "audited SHA == deployed SHA" mechanically enforced, not aspirational.
# (The DRY run above already ran and flagged any drift; only submission is gated.)
# Re-read the guard immediately before submission. The shared deployment lock
# prevents legitimate deploy movement, while this fresh verdict also catches
# any unexpected tree mutation that occurred during plan/dry-run processing.
refresh_sha_guard
echo "pre_submit_deployed_sha=${_DEPLOY_SHA:-unknown} guard_rc=${SHA_GUARD_RC:-unknown}"
if [[ "${SHA_BLOCK_SUBMIT}" == "True" ]]; then
    echo "FATAL: LIVE_PILOT submission blocked by deploy-drift guard: ${SHA_DRIFT_MESSAGE}"
    finish_blocked_run "${SHA_DRIFT_REASON:-live_pilot_deploy_sha_drift}" 1
fi

echo "=== LIVE_PILOT SCHEDULED SUBMISSION ==="
LIVE_RUN_ID="${REPORT_DATE}T${RUN_TS}_live_pilot_cron_submit"
LIVE_RUN_ROOT="outputs/live_pilot/runs/${LIVE_RUN_ID}"
set +e
LIVE_OUTPUT="$(CAERUS_LIVE_PILOT_DRY_RUN=0 "${PYTHON_BIN}" scripts/live_pilot_execute.py --plan "${PLAN_PATH}" --run-id "${LIVE_RUN_ID}")"
LIVE_STATUS=$?
set -e
echo "${LIVE_OUTPUT}"
LIVE_TERMINAL_STATUS="$(json_file_field "${LIVE_RUN_ROOT}/live_pilot_operator_summary.json" terminal_status || true)"
write_live_pilot_pointer "${LIVE_TERMINAL_STATUS:-unknown}" "${LIVE_RUN_ID}" "${LIVE_RUN_ROOT}" "scheduled_submission_completed"

# Execute-completion hook: confirm the just-completed submit run (and the dry
# run) now, so an armed submission is reported even if it finished after the
# scheduled confirm sweep. Dedupe keeps it idempotent with the 09:45 cron.
confirm_completed_runs || true

# Confirmation refreshes broker truth for the exact run. Re-read that canonical
# result and supersede the pre-refresh workflow pointer so downstream reporting
# cannot remain stuck on DRY_RUN or SUBMITTED_UNFILLED.
LIVE_FINAL_STATUS="$(json_file_field "${LIVE_RUN_ROOT}/execution_results.json" status || true)"
if [[ -n "${LIVE_FINAL_STATUS}" ]]; then
    write_live_pilot_pointer "${LIVE_FINAL_STATUS}" "${LIVE_RUN_ID}" "${LIVE_RUN_ROOT}" "scheduled_submission_confirmed"
    if [[ "${LIVE_FINAL_STATUS}" == "SUBMITTED" && "${LIVE_STATUS}" -ne 0 ]]; then
        LIVE_STATUS=0
    fi
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${LIVE_STATUS}"
exit "${LIVE_STATUS}"
