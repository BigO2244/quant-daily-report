#!/usr/bin/env bash
# Phase 2: Order Execution — 9:35 AM ET weekdays (UNIFIED PAPER LANE).
#
# One engine, two endpoints: this paper lane runs the SAME engine as the live
# pilot lane (scripts/cron_live_pilot_execute.sh) — plan via
# scripts/live_pilot_build_plan_from_precompute.py, execution via
# scripts/live_pilot_execute.py — but pointed at the Alpaca PAPER endpoint with
# MODE=paper. The old paper path (scripts/run_precomputed_alpaca_execution.py ->
# paper/paper_broker.run_paper_day) is DORMANT: no longer called here, files
# remain in tree.
#
# Lane isolation: paper artifacts live under outputs/paper_lane/{plans,state,runs},
# never under outputs/live_pilot. Confirm-flow compatibility: this script writes
# the outputs/workflow/<date>/execution.json stage pointer (plus a lane-scoped
# paper_lane_execution.json) via scripts/paper_lane_write_execution_pointer.py so
# the 10:00 ET cron_confirm.sh flow keeps working unchanged.
set -euo pipefail

# --- Resolve repo root ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# --- Timezone ---
export TZ="America/New_York"

# The outer retry harness owns the same-day process lock. Acquire it before this
# script publishes or replaces any workflow pointer so a concurrent/manual
# invocation cannot clobber the active run. The descriptor is inherited by the
# harness after the governed PAPER environment has been loaded below; retry
# children close it and run under their parent's still-held lock.
if [[ "${CAERUS_PAPER_RETRY_CHILD:-0}" != "1" ]]; then
    mkdir -p "${REPO_ROOT}/outputs/workflow"
    exec 9>"${REPO_ROOT}/outputs/workflow/paper_execution_retry.lock"
    if ! flock -n 9; then
        echo "FATAL: paper execution retry harness is already running" >&2
        exit 75
    fi
fi

# Replace any same-date stale terminal pointer before the first fallible source,
# venv, or retry-wrapper step. This writer uses only the system Python standard
# library and an atomic rename; it cannot depend on project packages or .env.
BOOTSTRAP_REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
BOOTSTRAP_RUN_ID="${BOOTSTRAP_REPORT_DATE}T$(date +%Y%m%dT%H%M%S%z)_paper_bootstrap"
BOOTSTRAP_POINTER_CLAIMED=0
bootstrap_pointer() {
    local pointer_status="$1"
    local pointer_substatus="$2"
    local pointer_message="$3"
    BOOTSTRAP_REPORT_DATE="${BOOTSTRAP_REPORT_DATE}" \
    BOOTSTRAP_RUN_ID="${BOOTSTRAP_RUN_ID}" \
    BOOTSTRAP_STATUS="${pointer_status}" \
    BOOTSTRAP_SUBSTATUS="${pointer_substatus}" \
    BOOTSTRAP_MESSAGE="${pointer_message}" \
        python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

trade_date = os.environ["BOOTSTRAP_REPORT_DATE"]
root = Path("outputs/workflow") / trade_date
root.mkdir(parents=True, exist_ok=True)
payload = {
    "stage": "execution",
    "trade_date": trade_date,
    "mode": "PAPER",
    "run_id": os.environ["BOOTSTRAP_RUN_ID"],
    "run_root": f"outputs/paper_lane/runs/{os.environ['BOOTSTRAP_RUN_ID']}",
    "status": os.environ["BOOTSTRAP_STATUS"],
    "substatus": os.environ["BOOTSTRAP_SUBSTATUS"],
    "status_message": os.environ["BOOTSTRAP_MESSAGE"],
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}
with tempfile.NamedTemporaryFile("w", dir=root, delete=False) as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    temporary = Path(handle.name)
temporary.replace(root / "execution.json")
PY
}
bootstrap_pointer "running" "paper_execution_bootstrap" "paper execution invocation started"
BOOTSTRAP_POINTER_CLAIMED=1
bootstrap_failure_pointer() {
    local exit_code=$?
    if [[ ${exit_code} -ne 0 && ${BOOTSTRAP_POINTER_CLAIMED} -eq 1 ]]; then
        bootstrap_pointer "failed_unknown" "paper_execution_bootstrap_failed" \
            "paper execution failed before governed run initialization" || true
    fi
    return ${exit_code}
}
trap bootstrap_failure_pointer EXIT

# --- Load credentials and config (.env ONLY — never ~/.caerus/live_pilot.env) ---
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
else
    echo "FATAL: ${REPO_ROOT}/.env not found" >&2
    exit 1
fi

# PAPER's governed target already carries its cash reserve (5% for Orion).
# Use the complete current account as the independent submission ceiling so the
# same reserve is not applied a second time by the shared live-lane cap default.
export CAERUS_LIVE_PILOT_CAP_PCT="1.0"

# --- Shared lane behavior params (shared defaults; single source for both lanes) ---
# Sourced AFTER .env. lane_params.sh provides SHARED DEFAULTS with env-wins
# semantics for MIN_TRADE_USD, MAX_ORDERS, and the concentrated-alpha per-name
# ceiling (CONCENTRATED_MAX_WEIGHT — always-on, no flag).
# PAPER's CAP_PCT is deliberately lane-owned at 1.0 above because target cash is
# already governed by the sealed portfolio; the live lane retains its own value.
# CONCENTRATED_MAX_WEIGHT must match cron_precompute so RiskControls does not
# re-clip the book. All other operator-set values in .env are never silently
# overridden. Divergence between lanes appears in their parameter fingerprints.
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/lane_params.sh"

# --- Activate venv ---
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
if [[ ! -x "${PYTHON_BIN}" ]]; then
    echo "FATAL: python interpreter not found or not executable: ${PYTHON_BIN}" >&2
    exit 1
fi

# --- Mode / endpoint: PAPER, always ---
export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="paper"
export TRADING_MODE="paper"
export WORKFLOW_KIND="paper"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

# Hard endpoint assertion (mirror of the live lane's live-host assertion): the
# unified paper lane must never point anywhere but the Alpaca paper host.
if [[ "${ALPACA_BASE_URL}" != "https://paper-api.alpaca.markets" ]]; then
    echo "FATAL: ALPACA_BASE_URL must be https://paper-api.alpaca.markets for the paper lane" >&2
    exit 1
fi

# --- Paper-lane account scope ---
# PAPER is a full-current-account target lane.  Stale environment values must
# never shrink the broker account into a synthetic staging account.  The exact
# 09:35 authorizer reconstructs NAV from fresh broker cash, positions, and marks;
# the sealed target's cash weight supplies the reserve independently.
unset CAERUS_LIVE_PILOT_CAPITAL_CAP
unset CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP
# Transitional input hint for the current one-capital-sleeve configuration. The
# plan builder replaces it with ``caerus_paper_portfolio`` automatically when
# the sealed allocator package contains more than one capital sleeve.
export CAERUS_LIVE_PILOT_SLEEVE_ID="${CAERUS_LIVE_PILOT_SLEEVE_ID:-caerus_orion}"
# Empty by design. The governed PAPER lane consumes only the sealed allocator
# package; any explicitly supplied recovery policy is fail-closed as downstream
# target substitution.
export CAERUS_PAPER_RECOVERY_POLICY="${CAERUS_PAPER_RECOVERY_POLICY:-}"
# Sells run through the SAME fail-closed gates as live (master flag + whitelist);
# paper arms them with the wildcard so the full buy/sell/hold model is exercised.
export CAERUS_LIVE_PILOT_SELLS_ENABLED="1"
export CAERUS_LIVE_PILOT_SELL_WHITELIST="*"
# Orion PAPER uses broker-supported fractional quantities for target fidelity.
# Pin the runtime value so it must match the immutable plan; the executor fails
# closed on any plan/runtime disagreement. This is scoped to the paper endpoint
# asserted above and does not affect Lyra LIVE.
export CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL="1"
# Fractional exits remain explicitly enabled for legacy/off-target cleanup. The
# executor additionally requires paper_lane output ancestry, so these pins are
# inert on the real-money live-pilot lane.
export CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED="1"
export CAERUS_PAPER_FRACTIONAL_EXIT_MIN_NOTIONAL_USD="1.00"
# PAPER sell-first rotation contract: poll for up to two minutes for every sell
# to become terminal and for broker cash/buying power to reflect confirmed fills.
# Once clean, only the current run's confirmed proceeds may augment the buy cash
# ceiling. The shared executor additionally requires PAPER mode + paper endpoint,
# so none of these pins can weaken the real-money settled-cash/GFV guard.
export CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS="120"
export CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS="61"
export CAERUS_LIVE_PILOT_SETTLEMENT_BASE_DELAY_SECONDS="2"
export CAERUS_LIVE_PILOT_SETTLEMENT_MAX_DELAY_SECONDS="2"
export CAERUS_EXACT_FILL_REFRESH_ATTEMPTS="61"
export CAERUS_EXACT_FILL_REFRESH_DELAY_SECONDS="2"
export CAERUS_PAPER_REUSE_CONFIRMED_SELL_PROCEEDS="1"
# Approval-style gates: set inline =1 for paper. WHY: these flags exist to force
# a HUMAN arming step before real-money submission on the live lane. The paper
# lane submits only to the paper endpoint (asserted above; the gate stack and the
# broker layer both refuse paper-mode orders on any non-paper host), so there is
# no human-arming requirement — an unarmed paper lane would just silently stop
# exercising the engine. Setting them here (not in .env) keeps the arming
# semantics of ~/.caerus/live_pilot.env exclusively about live money.
export CAERUS_LIVE_PILOT_APPROVED="1"
export CAERUS_LIVE_PILOT_CRON_APPROVED="1"
export CAERUS_LIVE_PILOT_SCHEDULE_ENABLED="1"
export CAERUS_LIVE_PILOT_SUBMIT_APPROVED="1"
export CAERUS_REQUIRE_APPROVED_EXECUTION_PACKAGE="1"
export CAERUS_REQUIRE_EXACT_EXECUTION_PLAN="1"
export CAERUS_EXACT_MAX_PLAN_AGE_SECONDS="900"
# Deliberately NOT exported here: CAERUS_LIVE_PILOT_KILL_SWITCH. The kill switch
# is a LIVE-lane safety gate; the paper gate stack short-circuits on the paper
# broker before ever consulting it, so exporting =0 here would be inert for
# paper while creating a latent hazard — any child process inheriting this env
# against a non-paper broker would find the switch pre-disarmed. Never suppress
# a safety gate via environment inheritance.

# --- Lane-scoped directories (isolated from outputs/live_pilot) ---
PAPER_LANE_ROOT="outputs/paper_lane"
PAPER_PLANS_DIR="${PAPER_LANE_ROOT}/plans"
PAPER_STATE_DIR="${PAPER_LANE_ROOT}/state"

# A second options submitter would bypass the sealed v3 plan and invalidate its
# reconciled poststate. Options remain research-only until represented inside
# this same authority/WAL/economic contract.
export ALLOW_OPTIONS_EXECUTION="0"
export ALLOW_OPTIONS_SUBMISSION="0"

# --- Suppress emails during execution (Phase 3 handles email; unchanged) ---
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_PRETRADE=0
export EMAIL_TRADING_CONFIRMATION=0
export EMAIL_INTERNAL_DEBUG=0

# --- Lane-wide transient retry harness (PAPER only) ---
# Start after every PAPER override/pin so same-run fill recovery and late
# confirmation inherit exactly the same governed credentials, endpoint, caps,
# approval state, exact-plan policy, and email behavior as the child lane.
if [[ "${CAERUS_PAPER_RETRY_CHILD:-0}" != "1" ]]; then
    BOOTSTRAP_POINTER_CLAIMED=0
    exec "${PYTHON_BIN}" -m scripts.paper_execution_retry \
        --trade-date "${REPORT_DATE}" \
        --repo-root "${REPO_ROOT}" \
        --inherited-lock-fd 9
fi

export WORKFLOW_STARTED_AT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}" "${PAPER_PLANS_DIR}" "${PAPER_STATE_DIR}" "outputs/workflow/${REPORT_DATE}"
LOG_FILE="${LOG_DIR}/execute_${REPORT_DATE}.log"
WORKFLOW_DIR="${REPO_ROOT}/outputs/workflow/${REPORT_DATE}"
EXECUTION_SELF_HEAL_STATUS_PATH="${WORKFLOW_DIR}/execution_self_heal.json"
BUNDLE_VALIDATION_PATH="${WORKFLOW_DIR}/execution_bundle_validation.json"
EXECUTION_READINESS_CERTIFICATION_ENABLED="${EXECUTION_READINESS_CERTIFICATION_ENABLED:-1}"

exec > >(tee -a "${LOG_FILE}") 2>&1

echo "=== PHASE 2: ORDER EXECUTION (UNIFIED PAPER LANE) ==="
echo "started_at=${WORKFLOW_STARTED_AT_UTC}"
echo "report_date=${REPORT_DATE}"
echo "mode=${MODE} trading_mode=${TRADING_MODE} alpaca_paper=${ALPACA_PAPER}"
echo "alpaca_base_url=${ALPACA_BASE_URL}"
echo "engine=live_pilot_build_plan_from_precompute+live_pilot_execute"
echo "lane_params_fingerprint=${CAERUS_LANE_PARAMS_FINGERPRINT}"
echo "paper_account_scope=FULL_CURRENT_ACCOUNT"
echo "max_orders=${CAERUS_LIVE_PILOT_MAX_ORDERS}"
echo "min_trade_usd=${CAERUS_LIVE_PILOT_MIN_TRADE_USD}"
echo "paper_fractional_rebalancing=${CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL}"
echo "paper_fractional_exit_enabled=${CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED}"
_DEPLOY_SHA="$(python3 -c "import json,sys; d=json.load(open('outputs/deploy_state.json')) if __import__('pathlib').Path('outputs/deploy_state.json').exists() else {}; print(d.get('deployed_sha','unknown'))" 2>/dev/null || echo "unknown")"
echo "deployed_sha=${_DEPLOY_SHA}"

RUN_TS="$(date +%Y%m%dT%H%M%S%z)"
DRY_RUN_ID="${REPORT_DATE}T${RUN_TS}_paper_cron_dry"
SUBMIT_RUN_ID="${REPORT_DATE}T${RUN_TS}_paper_cron_submit"
if [[ -n "${CAERUS_PAPER_DRILL_EPOCH:-}" ]]; then
    # The epoch owns the immutable plan/WAL namespace; the invocation timestamp
    # owns append-only run and attempt artifacts.  A failed preauthorization
    # invocation must not reserve the filenames needed to record a later
    # idempotent recovery of the same epoch.
    DRY_RUN_ID="${REPORT_DATE}_${CAERUS_PAPER_DRILL_EPOCH}_${RUN_TS}_paper_drill_dry"
    SUBMIT_RUN_ID="${REPORT_DATE}_${CAERUS_PAPER_DRILL_EPOCH}_${RUN_TS}_paper_drill_submit"
fi

write_paper_pointer() {
    # write_paper_pointer <run_id> <run_root> <terminal_status> <reason_code>
    if "${PYTHON_BIN}" scripts/paper_lane_write_execution_pointer.py \
        --trade-date "${REPORT_DATE}" \
        --run-id "$1" \
        --run-root "$2" \
        --terminal-status "$3" \
        --reason-code "$4"; then
        return 0
    fi
    echo "ERROR: canonical pointer writer failed; emitting atomic emergency pointer" >&2
    POINTER_RUN_ID="$1" POINTER_RUN_ROOT="$2" POINTER_TERMINAL="$3" POINTER_REASON="$4" \
        "${PYTHON_BIN}" - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

root = Path("outputs/workflow") / os.environ["REPORT_DATE"]
root.mkdir(parents=True, exist_ok=True)
terminal = os.environ["POINTER_TERMINAL"]
status = "running" if terminal.lower() == "running" else "failed_unknown"
payload = {
    "stage": "execution",
    "trade_date": os.environ["REPORT_DATE"],
    "mode": "PAPER",
    "run_id": os.environ["POINTER_RUN_ID"],
    "run_root": os.environ["POINTER_RUN_ROOT"],
    "status": status,
    "substatus": "canonical_pointer_writer_failed",
    "status_message": os.environ["POINTER_REASON"] or "canonical_pointer_writer_failed",
    "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
}
target = root / "execution.json"
with tempfile.NamedTemporaryFile("w", dir=root, delete=False) as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    temporary = Path(handle.name)
temporary.replace(target)
PY
    # The emergency pointer is intentionally a failure artifact. Never let the
    # scheduler report a successful execution when canonical publication failed.
    return 1
}

# Claim the same-day execution pointer before any fallible bundle/cap/authority
# work so a rerun can never leave a stale success visible to confirmation.
write_paper_pointer \
    "${SUBMIT_RUN_ID}" \
    "${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}" \
    "running" \
    "paper_execution_initializing"
BOOTSTRAP_POINTER_CLAIMED=0

fail_lane() {
    # fail_lane <reason_code>: write a failed pointer, log, exit 1.
    FAIL_RUN_ROOT="${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}"
    mkdir -p "${FAIL_RUN_ROOT}"
    set +e
    FAIL_REASON="$1" FAIL_RUN_ROOT="${FAIL_RUN_ROOT}" PAPER_LANE_ROOT="${PAPER_LANE_ROOT}" REPORT_DATE="${REPORT_DATE}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from core.execution_attempt_registry import AttemptRecord, append_attempt, read_attempts, select_from_registry, write_selection_pointer
from core.failure_semantics import FailureClass, TerminalOutcome, build_system_failure, get_failure_policy

run_root = Path(os.environ["FAIL_RUN_ROOT"])
trade_date = os.environ["REPORT_DATE"]
reason = os.environ["FAIL_REASON"]
failure_class = FailureClass.PRECOMPUTE_FAILURE if "precompute" in reason else FailureClass.AUTHORIZATION_FAILURE
failure = build_system_failure(
    failure_class=failure_class,
    reason_code=reason,
    before_financial_mutation=True,
).to_dict()
policy = get_failure_policy(failure_class).to_dict()
terminal = {
    "run_id": run_root.name,
    "trade_date": trade_date,
    "mode": "PAPER",
    "terminal_status": "BLOCKED",
    "terminal_outcome": TerminalOutcome.SYSTEM_FAILURE.value,
    "reason_code": reason,
    "reason": reason,
    "halt_reason": reason,
    "failure_class": failure_class.value,
    "failure_semantics": failure,
    "failure_policy": policy,
    "submitted_count": 0,
    "run_root": str(run_root),
}
def write(name, payload):
    path = run_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
write("execution_results.json", {**terminal, "status": "BLOCKED"})
write("operator_summary.json", terminal)
write("live_pilot_operator_summary.json", terminal)
write("execution_payload.json", {**terminal, "schema_version": "caerus.execution_payload.v3", "orders": []})
write("execution_timeline.json", {**terminal, "schema_version": "caerus.execution_lifecycle_timeline.v2", "stages": [{"stage": "AUTHORIZE", "status": "FAILED"}]})
write("live_pilot_reconciliation.json", {**terminal, "status": "FAILED_PRE_SUBMIT", "state": "BLOCKED"})
write("live_pilot_broker_snapshot_pre.json", {"trade_date": trade_date, "status": "NOT_CAPTURED_BLOCKED_BEFORE_BROKER_SNAPSHOT", "account": {}, "positions": []})
write("live_pilot_broker_snapshot_post.json", {"trade_date": trade_date, "status": "NOT_CAPTURED_BLOCKED_BEFORE_BROKER_SNAPSHOT", "account": {}, "positions": []})
write("audit/execution_integrity.json", {**terminal, "schema_version": "caerus.execution_integrity.v2", "status": "FAIL", "findings": [reason]})
registry = Path(os.environ["PAPER_LANE_ROOT"]) / "execution_attempts"
prior = read_attempts(registry, trade_date=trade_date)
record = AttemptRecord(
    attempt_id=run_root.name,
    trade_date=trade_date,
    run_id=run_root.name,
    lane="paper",
    sequence=len(prior) + 1,
    terminal_outcome=TerminalOutcome.SYSTEM_FAILURE,
    recorded_at=datetime.now(timezone.utc).isoformat(),
    run_root=str(run_root),
    failure_class=failure_class,
    reason_code=reason,
    source_artifacts=(str(run_root / "execution_results.json"),),
)
append_attempt(registry, record)
write_selection_pointer(registry, select_from_registry(registry, trade_date=trade_date))
PY
    FAILURE_ARTIFACT_STATUS=$?
    set -e
    if [[ ${FAILURE_ARTIFACT_STATUS} -ne 0 ]]; then
        echo "ERROR: auxiliary failure-artifact persistence failed; canonical pointer still forced failed" >&2
    fi
    write_paper_pointer \
        "${SUBMIT_RUN_ID}" \
        "${FAIL_RUN_ROOT}" \
        "BLOCKED" \
        "$1"
    echo "FATAL: paper lane blocked: $1"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
}

summary_field() {
    # summary_field <captured_output> <field>
    # The captured output is stdout+stderr merged (2>&1), so it may contain
    # non-JSON noise (deprecation warnings, tracebacks) around the executor's
    # final json.dumps summary. Extract the LAST valid JSON object in the
    # stream instead of json.loads()-ing the whole blob, so a stray stderr
    # line can never blank out run_root/terminal_status/reason_code and turn
    # a real outcome into terminal_status=failed_unknown.
    local summary_json="$1"
    local field="$2"
    SUMMARY_JSON="${summary_json}" SUMMARY_FIELD="${field}" "${PYTHON_BIN}" - <<'PY'
import json
import os

raw = os.environ["SUMMARY_JSON"]
decoder = json.JSONDecoder()
payload = {}
idx = 0
while True:
    start = raw.find("{", idx)
    if start == -1:
        break
    try:
        candidate, end = decoder.raw_decode(raw, start)
    except json.JSONDecodeError:
        idx = start + 1
        continue
    if isinstance(candidate, dict):
        payload = candidate
    idx = end
print(payload.get(os.environ["SUMMARY_FIELD"], ""))
PY
}

# --- Verify precompute bundle integrity (with self-heal retry; unchanged) ---
BUNDLE_DIR="${REPO_ROOT}/outputs/precompute/${REPORT_DATE}"
RECOVERY_ATTEMPTED=0
RECOVERY_RESULT="not_attempted"
RECOVERY_STARTED_AT=""
RECOVERY_FINISHED_AT=""

if ! python3 -m core.precompute_bundle_validation \
    --bundle-dir "${BUNDLE_DIR}" \
    --trade-date "${REPORT_DATE}" \
    --require-sealed-paper-target \
    --json-output "${BUNDLE_VALIDATION_PATH}"; then
    echo "WARN: precompute bundle validation failed; details=${BUNDLE_VALIDATION_PATH}"
    echo "WARN: attempting self-heal by rebuilding today's precompute bundle before giving up."
    RECOVERY_ATTEMPTED=1
    RECOVERY_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if SELF_HEAL_PRECOMPUTE_ONLY=1 REPORT_DATE="${REPORT_DATE}" "${REPO_ROOT}/scripts/cron_precompute.sh"; then
        RECOVERY_RESULT="completed"
    else
        RECOVERY_RESULT="failed"
        echo "ERROR: self-heal precompute rebuild failed"
    fi
    RECOVERY_FINISHED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

    if ! python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --require-sealed-paper-target \
        --json-output "${BUNDLE_VALIDATION_PATH}"; then
        python3 -m core.precompute_bundle_validation \
            --bundle-dir "${BUNDLE_DIR}" \
            --trade-date "${REPORT_DATE}" \
            --require-sealed-paper-target \
            --json-output "${BUNDLE_VALIDATION_PATH}" \
            --recovery-status-output "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
            --previous-recovery-status "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
            --recovery-attempted \
            --recovery-result "${RECOVERY_RESULT}" \
            --execution-continued false \
            --recovery-started-at "${RECOVERY_STARTED_AT}" \
            --recovery-finished-at "${RECOVERY_FINISHED_AT}" || true
        echo "FATAL: precompute bundle validation failed after self-heal; details=${BUNDLE_VALIDATION_PATH}"
        echo "FATAL: execution halted to avoid degraded bundle execution."
        echo "self_heal_status=${EXECUTION_SELF_HEAL_STATUS_PATH}"
        fail_lane "precompute_bundle_validation_failed_after_self_heal"
    fi

    python3 -m core.precompute_bundle_validation \
        --bundle-dir "${BUNDLE_DIR}" \
        --trade-date "${REPORT_DATE}" \
        --require-sealed-paper-target \
        --json-output "${BUNDLE_VALIDATION_PATH}" \
        --recovery-status-output "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
        --previous-recovery-status "${EXECUTION_SELF_HEAL_STATUS_PATH}" \
        --recovery-attempted \
        --recovery-result "${RECOVERY_RESULT}" \
        --execution-continued true \
        --recovery-started-at "${RECOVERY_STARTED_AT}" \
        --recovery-finished-at "${RECOVERY_FINISHED_AT}"
    echo "self_heal_status=${EXECUTION_SELF_HEAL_STATUS_PATH}"
fi
echo "OK: precompute bundle validated at ${BUNDLE_DIR}"
echo "bundle_validation=${BUNDLE_VALIDATION_PATH}"

# The 07:00 bundle carries one immutable Decision target, never exact orders.
# Fresh broker and market state below remain mandatory before Risk and Trader
# can publish the exact broker-ready plan.
echo "precompute_authority=SEALED_DECISION_TARGET_ONLY"

# --- Resolve the capital cap from the current broker account ---
# This is a submission ceiling, not a planning-equity substitute. Exact target
# quantities are calculated later from the authorizer's fresh reconstructed NAV.
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
print("" if cap is None else f"{cap:.2f}", src, "" if pv is None else str(pv), sep="\t")
PY
)"
PLAN_CAP="$(printf '%s' "${CAP_RESOLVE}" | cut -f1)"
CAP_SOURCE="$(printf '%s' "${CAP_RESOLVE}" | cut -f2)"
BROKER_ACCOUNT_VALUE="$(printf '%s' "${CAP_RESOLVE}" | cut -f3)"
if [[ -z "${PLAN_CAP}" ]]; then
    echo "FATAL: could not resolve paper capital cap (source=${CAP_SOURCE})"
    CAP_FAILURE_REASON="$(
        CAP_ERROR="${CAP_SOURCE}" "${PYTHON_BIN}" - <<'PY'
import os
from core.broker_retry_policy import is_retryable_broker_read_error

error = os.environ.get("CAP_ERROR", "")
print(
    "paper_lane_capital_cap_transient_read_failed"
    if is_retryable_broker_read_error(error)
    else "paper_lane_capital_cap_unresolved"
)
PY
    )"
    fail_lane "${CAP_FAILURE_REASON}"
fi
echo "current_broker_account_value=${BROKER_ACCOUNT_VALUE}"
echo "resolved_submission_cap=${PLAN_CAP} (source=${CAP_SOURCE})"

# --- Mark execution running for the confirm flow ---
write_paper_pointer \
    "${DRY_RUN_ID}" \
    "${PAPER_LANE_ROOT}/runs/${DRY_RUN_ID}" \
    "running" \
    "paper_dry_run_started"

# --- Build the full-rebalance plan (same builder as live; paper-scoped dirs) ---
set +e
BUILD_ARGS=(
    --trade-date "${REPORT_DATE}"
    --lane paper
    --recovery-policy-config "${REPO_ROOT}/config/paper_recovery_policy.json"
    --approved-sleeve "${CAERUS_LIVE_PILOT_SLEEVE_ID}"
    --capital-cap "${PLAN_CAP}"
    --max-orders "${CAERUS_LIVE_PILOT_MAX_ORDERS}"
    --allow-fractional
    --output-dir "${PAPER_PLANS_DIR}"
    --state-dir "${PAPER_STATE_DIR}"
)
if [[ -n "${CAERUS_PAPER_RECOVERY_POLICY}" ]]; then
    BUILD_ARGS+=(--recovery-policy "${CAERUS_PAPER_RECOVERY_POLICY}")
fi
BUILD_OUTPUT="$(
    "${PYTHON_BIN}" scripts/live_pilot_build_plan_from_precompute.py "${BUILD_ARGS[@]}"
)"
BUILD_STATUS=$?
set -e
echo "${BUILD_OUTPUT}"
if [[ "${BUILD_STATUS}" -ne 0 ]]; then
    echo "paper_lane_plan_builder_exit_code=${BUILD_STATUS}"
    fail_lane "paper_lane_plan_blocked"
fi

set +e
PLAN_PATH="$(
    BUILD_OUTPUT="${BUILD_OUTPUT}" "${PYTHON_BIN}" - <<'PY'
import json
import os

payload = json.loads(os.environ["BUILD_OUTPUT"])
if payload.get("status") != "READY_FOR_MANUAL_APPROVAL":
    raise SystemExit(f"FATAL: paper lane plan blocked: {payload.get('status')}")
print(str(payload.get("json_path") or ""))
PY
)"
PLAN_STATUS=$?
set -e
if [[ "${PLAN_STATUS}" -ne 0 || -z "${PLAN_PATH}" ]]; then
    fail_lane "paper_lane_plan_missing_path"
fi
echo "plan_path=${PLAN_PATH}"

# --- One final Decision: bind current broker state and seal exact orders ---
AUTHORITY_RUN_ID="${REPORT_DATE}T${RUN_TS}_paper_authority"
DRILL_AUTH_ARGS=()
EXACT_PLAN_PATH="${PAPER_PLANS_DIR}/exact_execution_plan_${REPORT_DATE}.json"
if [[ -n "${CAERUS_PAPER_DRILL_EPOCH:-}" ]]; then
    AUTHORITY_RUN_ID="${REPORT_DATE}_${CAERUS_PAPER_DRILL_EPOCH}_paper_authority"
    EXACT_PLAN_PATH="${PAPER_PLANS_DIR}/exact_execution_plan_${CAERUS_PAPER_DRILL_EPOCH}.latest.json"
    DRILL_POLICY_PATH="${REPO_ROOT}/config/paper_intraday_drill_policy_${REPORT_DATE}.json"
    DRILL_AUTH_ARGS+=(
        --drill-epoch "${CAERUS_PAPER_DRILL_EPOCH}"
        --drill-policy-config "${DRILL_POLICY_PATH}"
    )
fi
set +e
AUTHORITY_OUTPUT="$("${PYTHON_BIN}" scripts/authorize_exact_execution_plan.py \
    --plan "${PLAN_PATH}" \
    --run-id "${AUTHORITY_RUN_ID}" \
    --output "${EXACT_PLAN_PATH}" \
    "${DRILL_AUTH_ARGS[@]}" 2>&1)"
AUTHORITY_STATUS=$?
set -e
echo "${AUTHORITY_OUTPUT}"
AUTHORITY_PLAN_PATH="$(summary_field "${AUTHORITY_OUTPUT}" json_path || true)"
if [[ "${AUTHORITY_STATUS}" -ne 0 || -z "${AUTHORITY_PLAN_PATH}" || ! -f "${AUTHORITY_PLAN_PATH}" ]]; then
    AUTHORITY_REASON="$(summary_field "${AUTHORITY_OUTPUT}" reason_code || true)"
    fail_lane "${AUTHORITY_REASON:-paper_exact_plan_authorization_nonretryable_failed}"
fi
PLAN_PATH="${AUTHORITY_PLAN_PATH}"
echo "exact_plan_path=${PLAN_PATH}"

# --- Pass 1: DRY RUN (no submission; isolated artifacts) ---
echo "=== PAPER LANE DRY RUN ==="
set +e
DRY_OUTPUT="$(CAERUS_LIVE_PILOT_DRY_RUN=1 "${PYTHON_BIN}" scripts/live_pilot_execute.py \
    --plan "${PLAN_PATH}" --run-id "${DRY_RUN_ID}" --output-root "${PAPER_LANE_ROOT}" 2>&1)"
DRY_STATUS=$?
set -e
echo "${DRY_OUTPUT}"
if [[ "${DRY_STATUS}" -ne 0 ]]; then
    DRY_REASON="$(summary_field "${DRY_OUTPUT}" reason_code || true)"
    DRY_RUN_ROOT="${PAPER_LANE_ROOT}/runs/${DRY_RUN_ID}"
    write_paper_pointer "${DRY_RUN_ID}" "${DRY_RUN_ROOT}" "BLOCKED" "${DRY_REASON:-paper_lane_dry_run_failed}"
    echo "FATAL: paper lane dry run failed (exit=${DRY_STATUS})"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=1"
    exit 1
fi

# --- Pass 2: PAPER SUBMISSION (real paper orders to paper-api) ---
echo "=== PAPER LANE SUBMISSION ==="
write_paper_pointer \
    "${SUBMIT_RUN_ID}" \
    "${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}" \
    "running" \
    "paper_submission_started"
set +e
SUBMIT_OUTPUT="$(CAERUS_LIVE_PILOT_DRY_RUN=0 "${PYTHON_BIN}" scripts/live_pilot_execute.py \
    --plan "${PLAN_PATH}" --run-id "${SUBMIT_RUN_ID}" --output-root "${PAPER_LANE_ROOT}" 2>&1)"
SUBMIT_STATUS=$?
set -e
echo "${SUBMIT_OUTPUT}"
EXPECTED_SUBMIT_RUN_ROOT="${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}"
REPORTED_SUBMIT_RUN_ROOT="$(summary_field "${SUBMIT_OUTPUT}" run_root || true)"
SUBMIT_RUN_ROOT="${EXPECTED_SUBMIT_RUN_ROOT}"
SUBMIT_TERMINAL="$(summary_field "${SUBMIT_OUTPUT}" terminal_status || true)"
SUBMIT_REASON="$(summary_field "${SUBMIT_OUTPUT}" reason_code || true)"
if [[ -z "${REPORTED_SUBMIT_RUN_ROOT}" || "${REPORTED_SUBMIT_RUN_ROOT}" != "${EXPECTED_SUBMIT_RUN_ROOT}" ]]; then
    echo "ERROR: executor did not return the expected nonempty run root" >&2
    SUBMIT_STATUS=1
    SUBMIT_TERMINAL="FAILED_RECONCILIATION"
    SUBMIT_REASON="exact_execution_run_root_identity_mismatch"
fi

EXIT_CODE=0
if [[ "${SUBMIT_STATUS}" -ne 0 ]] || [[ "${SUBMIT_TERMINAL}" != "SUBMITTED" && "${SUBMIT_TERMINAL}" != "AUTHORIZED_NO_TRADE" ]]; then
    EXIT_CODE=1
fi
FINAL_TERMINAL="${SUBMIT_TERMINAL:-failed_unknown}"
FINAL_REASON="${SUBMIT_REASON}"
if ! write_paper_pointer \
    "${SUBMIT_RUN_ID}" \
    "${SUBMIT_RUN_ROOT}" \
    "${FINAL_TERMINAL}" \
    "${FINAL_REASON}"; then
    EXIT_CODE=1
    echo "ERROR: canonical terminal execution pointer publication failed" >&2
fi

# Exact-plan equality, broker fills, quantities, cash, economics, and governed
# target attainment are terminal checks inside live_pilot_execute.py.  The daily
# shadow/analytics surface remains observable, but it is reporting health and
# cannot retroactively rewrite a reconciled broker execution.
SHADOW_REPORT_STATUS="SKIPPED"
OPERATIONAL_DRAG_STATUS="SKIPPED"
DAILY_HEALTH_COMMAND_STATUS="SKIPPED"
HEALTH_STATUS="MISSING"
if [[ ${EXIT_CODE} -eq 0 ]]; then
    SHADOW_REPORT_STATUS=0
    "${PYTHON_BIN}" -m scripts.live_vs_shadow_reconciliation \
        --trade-date "${REPORT_DATE}" \
        --broker-positions-path "${SUBMIT_RUN_ROOT}/live_pilot_broker_snapshot_post.json" \
        || SHADOW_REPORT_STATUS=$?
    OPERATIONAL_DRAG_STATUS=0
    "${PYTHON_BIN}" scripts/run_operational_drag_analysis.py \
        --date "${REPORT_DATE}" \
        --repo-root "${REPO_ROOT}" || OPERATIONAL_DRAG_STATUS=$?
    DAILY_HEALTH_COMMAND_STATUS=0
    "${PYTHON_BIN}" -m scripts.caerus_daily_health_check \
        --trade-date "${REPORT_DATE}" \
        --root "${REPO_ROOT}" || DAILY_HEALTH_COMMAND_STATUS=$?
    HEALTH_STATUS="$(
        HEALTH_PATH="${REPO_ROOT}/outputs/health/caerus_daily_health_check/${REPORT_DATE}/health_check.json" \
            "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["HEALTH_PATH"])
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
print(str(payload.get("overall_status") or "MISSING"))
PY
    )"
    if [[ "${SHADOW_REPORT_STATUS}" != "0" || "${OPERATIONAL_DRAG_STATUS}" != "0" || "${DAILY_HEALTH_COMMAND_STATUS}" != "0" || "${HEALTH_STATUS}" != "GREEN" ]]; then
        echo "WARNING: non-blocking posttrade reporting is degraded (shadow=${SHADOW_REPORT_STATUS}, drag=${OPERATIONAL_DRAG_STATUS}, health_command=${DAILY_HEALTH_COMMAND_STATUS}, health=${HEALTH_STATUS})"
    fi
fi
REPORTING_ARTIFACT="${WORKFLOW_DIR}/paper_posttrade_reporting.json"
REPORTING_ARTIFACT="${REPORTING_ARTIFACT}" \
SHADOW_REPORT_STATUS="${SHADOW_REPORT_STATUS}" \
OPERATIONAL_DRAG_STATUS="${OPERATIONAL_DRAG_STATUS}" \
DAILY_HEALTH_COMMAND_STATUS="${DAILY_HEALTH_COMMAND_STATUS}" \
HEALTH_STATUS="${HEALTH_STATUS}" \
FINAL_TERMINAL="${FINAL_TERMINAL}" \
FINAL_REASON="${FINAL_REASON}" \
    "${PYTHON_BIN}" - <<'PY' || echo "WARNING: could not persist non-blocking posttrade reporting artifact" >&2
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["REPORTING_ARTIFACT"])
component_statuses = {
    "live_vs_shadow_exit": os.environ["SHADOW_REPORT_STATUS"],
    "operational_drag_exit": os.environ["OPERATIONAL_DRAG_STATUS"],
    "daily_health_command_exit": os.environ["DAILY_HEALTH_COMMAND_STATUS"],
    "daily_health_status": os.environ["HEALTH_STATUS"],
}
healthy = all(value == "0" for key, value in component_statuses.items() if key.endswith("_exit")) and component_statuses["daily_health_status"] == "GREEN"
payload = {
    "schema_version": "caerus.paper_posttrade_reporting.v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": "OK" if healthy else "DEGRADED",
    "execution_terminal_status": os.environ["FINAL_TERMINAL"],
    "execution_reason_code": os.environ["FINAL_REASON"],
    "non_blocking": True,
    "components": component_statuses,
}
path.parent.mkdir(parents=True, exist_ok=True)
with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False) as handle:
    json.dump(payload, handle, indent=2, sort_keys=True)
    handle.write("\n")
    temporary = Path(handle.name)
temporary.replace(path)
PY
echo "posttrade_reporting=${REPORTING_ARTIFACT} (non_blocking=true)"

echo "options_execution_authority=DISABLED_PENDING_EXACT_PLAN_INTEGRATION"
if [[ ${EXIT_CODE} -ne 0 ]]; then
    echo "ERROR: paper lane submission terminal_status=${SUBMIT_TERMINAL:-unknown} reason=${SUBMIT_REASON:-unknown} (exit=${SUBMIT_STATUS})"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}"
exit ${EXIT_CODE}
