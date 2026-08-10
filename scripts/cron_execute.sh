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

# --- Shared lane behavior params (shared defaults; single source for both lanes) ---
# Sourced AFTER .env. lane_params.sh provides SHARED DEFAULTS with env-wins
# semantics for the execution knobs (MIN_TRADE_USD, MAX_ORDERS, CAP_PCT) and
# the concentrated-alpha per-name ceiling (CONCENTRATED_MAX_WEIGHT — always-on,
# no flag; must match cron_precompute so RiskControls does not re-clip the book):
# an operator-set value in .env is never silently overridden. Divergence between
# lanes shows up as different lane_params_fingerprint values in the two cron logs.
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

# --- Lane-wide transient retry harness (PAPER only) ---
# The outer invocation owns one retry budget across cap resolution, dry run, and
# submission pre-snapshot checks. Child attempts re-run this script with every
# validation/gate refreshed. Live execution uses a different cron and never
# enters this wrapper.
if [[ "${CAERUS_PAPER_RETRY_CHILD:-0}" != "1" ]]; then
    export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
    exec "${PYTHON_BIN}" -m scripts.paper_execution_retry \
        --trade-date "${REPORT_DATE}" \
        --repo-root "${REPO_ROOT}"
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

# --- Paper-lane pins ---
# Staging scale: pin the plan/exec capital cap to $10k so paper exercises the
# engine at a fixed, comparable scale regardless of the paper account's drifting
# portfolio value (resolve_dynamic_cap only ever TIGHTENS to this value).
export CAERUS_LIVE_PILOT_CAPITAL_CAP="10000"
# The cap alone is NOT enough: the executor sizes target shares against the
# broker account's REAL equity (weight * total_equity / price), so a paper
# account far above $10k would compute needs that exceed the approved cap and
# hard-block with live_pilot_total_notional_exceeds_cap before the dry pass.
# PLANNING_EQUITY_CAP clamps the executor's planning equity to the same $10k
# staging scale (it only ever TIGHTENS; unset on the live lane -> live sizes
# against real equity, unchanged).
export CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP}"
export CAERUS_LIVE_PILOT_SLEEVE_ID="${CAERUS_LIVE_PILOT_SLEEVE_ID:-caerus_orion}"
# Empty by design. The governed PAPER lane consumes only the current or
# immediately preceding XNYS Orion snapshot; any explicitly supplied recovery
# policy is fail-closed by the plan builder as downstream target substitution.
export CAERUS_PAPER_RECOVERY_POLICY="${CAERUS_PAPER_RECOVERY_POLICY:-}"
# Sells run through the SAME fail-closed gates as live (master flag + whitelist);
# paper arms them with the wildcard so the full buy/sell/hold model is exercised.
export CAERUS_LIVE_PILOT_SELLS_ENABLED="1"
export CAERUS_LIVE_PILOT_SELL_WHITELIST="*"
# Paper-only target-fidelity cleanup. Fractional entries remain disabled; this
# permits exact exits of legacy fractional holdings that are no longer in the
# target portfolio. The executor additionally requires the paper_lane output
# ancestry, so this flag is inert on the real-money live-pilot lane.
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

# --- Options overlay execution (default enabled for paper trading; unchanged) ---
export ALLOW_OPTIONS_EXECUTION="${ALLOW_OPTIONS_EXECUTION:-1}"
export ALLOW_OPTIONS_SUBMISSION="${ALLOW_OPTIONS_SUBMISSION:-1}"

# --- Suppress emails during execution (Phase 3 handles email; unchanged) ---
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_PRETRADE=0
export EMAIL_TRADING_CONFIRMATION=0
export EMAIL_INTERNAL_DEBUG=0

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
echo "capital_cap_pin=${CAERUS_LIVE_PILOT_CAPITAL_CAP}"
echo "max_orders=${CAERUS_LIVE_PILOT_MAX_ORDERS}"
echo "min_trade_usd=${CAERUS_LIVE_PILOT_MIN_TRADE_USD}"
echo "paper_fractional_exit_enabled=${CAERUS_PAPER_FRACTIONAL_EXIT_ENABLED}"
_DEPLOY_SHA="$(python3 -c "import json,sys; d=json.load(open('outputs/deploy_state.json')) if __import__('pathlib').Path('outputs/deploy_state.json').exists() else {}; print(d.get('deployed_sha','unknown'))" 2>/dev/null || echo "unknown")"
echo "deployed_sha=${_DEPLOY_SHA}"

RUN_TS="$(date +%Y%m%dT%H%M%S%z)"
DRY_RUN_ID="${REPORT_DATE}T${RUN_TS}_paper_cron_dry"
SUBMIT_RUN_ID="${REPORT_DATE}T${RUN_TS}_paper_cron_submit"

write_paper_pointer() {
    # write_paper_pointer <run_id> <run_root> <terminal_status> <reason_code>
    "${PYTHON_BIN}" scripts/paper_lane_write_execution_pointer.py \
        --trade-date "${REPORT_DATE}" \
        --run-id "$1" \
        --run-root "$2" \
        --terminal-status "$3" \
        --reason-code "$4" || true
}

fail_lane() {
    # fail_lane <reason_code>: write a failed pointer, log, exit 1.
    FAIL_RUN_ROOT="${PAPER_LANE_ROOT}/runs/${SUBMIT_RUN_ID}"
    mkdir -p "${FAIL_RUN_ROOT}"
    FAIL_REASON="$1" FAIL_RUN_ROOT="${FAIL_RUN_ROOT}" "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

run_root = Path(os.environ["FAIL_RUN_ROOT"])
payload = {
    "run_id": run_root.name,
    "terminal_status": "BLOCKED",
    "reason": os.environ["FAIL_REASON"],
    "halt_reason": os.environ["FAIL_REASON"],
    "submitted_count": 0,
    "run_root": str(run_root),
}
(run_root / "execution_results.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
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
        --json-output "${BUNDLE_VALIDATION_PATH}"; then
        python3 -m core.precompute_bundle_validation \
            --bundle-dir "${BUNDLE_DIR}" \
            --trade-date "${REPORT_DATE}" \
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

# --- Certify execution readiness without submitting orders (unchanged) ---
if [[ ! "${EXECUTION_READINESS_CERTIFICATION_ENABLED}" =~ ^(0|false|FALSE|no|NO|n|N|off|OFF)$ ]]; then
    CERTIFICATION_PATH="${BUNDLE_DIR}/execution_readiness_certification.json"
    if ! python3 -m scripts.certify_execution_readiness \
        --trade-date "${REPORT_DATE}" \
        --mode paper \
        --no-submit \
        --output-path "${CERTIFICATION_PATH}"; then
        echo "FATAL: execution readiness certification failed; details=${CERTIFICATION_PATH}"
        fail_lane "execution_readiness_certification_failed"
    fi
    echo "OK: execution readiness certification written to ${CERTIFICATION_PATH}"
fi

# --- Resolve the capital cap (same resolver as the live lane) ---
# For paper the PAPER account's portfolio value is tightened by the $10k staging
# pin above, so plan sizing and execution agree at the pinned scale.
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
echo "resolved_capital_cap=${PLAN_CAP} (source=${CAP_SOURCE})"

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
    DRY_RUN_ROOT="$(summary_field "${DRY_OUTPUT}" run_root || true)"
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
SUBMIT_RUN_ROOT="$(summary_field "${SUBMIT_OUTPUT}" run_root || true)"
SUBMIT_TERMINAL="$(summary_field "${SUBMIT_OUTPUT}" terminal_status || true)"
SUBMIT_REASON="$(summary_field "${SUBMIT_OUTPUT}" reason_code || true)"

# --- Confirm-flow pointer: execution.json + paper_lane_execution.json ---
POINTER_OUTPUT="$(
    "${PYTHON_BIN}" scripts/paper_lane_write_execution_pointer.py \
        --trade-date "${REPORT_DATE}" \
        --run-id "${SUBMIT_RUN_ID}" \
        --run-root "${SUBMIT_RUN_ROOT}" \
        --terminal-status "${SUBMIT_TERMINAL:-failed_unknown}" \
        --reason-code "${SUBMIT_REASON}"
)"
echo "${POINTER_OUTPUT}"
LANE_OK="$(summary_field "${POINTER_OUTPUT}" lane_exit_ok || true)"

EXIT_CODE=1
if [[ "${LANE_OK}" == "True" || "${LANE_OK}" == "true" ]]; then
    EXIT_CODE=0
fi

# Reconcile the exact approved package against broker truth, then require the
# complete daily health surface to be green. This runs after the terminal
# pointer is published so reconciliation resolves this PAPER run rather than a
# stale legacy latest-run artifact.
if [[ ${EXIT_CODE} -eq 0 ]]; then
    "${PYTHON_BIN}" -m scripts.live_vs_shadow_reconciliation \
        --trade-date "${REPORT_DATE}" \
        --broker-positions-path "${SUBMIT_RUN_ROOT}/live_pilot_broker_snapshot_post.json" || EXIT_CODE=$?
fi
if [[ ${EXIT_CODE} -eq 0 ]]; then
    "${PYTHON_BIN}" -m scripts.caerus_daily_health_check \
        --trade-date "${REPORT_DATE}" \
        --root "${REPO_ROOT}" || EXIT_CODE=$?
fi
if [[ ${EXIT_CODE} -eq 0 ]]; then
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
    if [[ "${HEALTH_STATUS}" != "GREEN" ]]; then
        echo "ERROR: universal health gate is ${HEALTH_STATUS}, expected GREEN"
        EXIT_CODE=1
    fi
fi

# --- Options overlay execution (unchanged from the legacy paper lane) ---
if [[ ${EXIT_CODE} -eq 0 ]]; then
    OPTIONS_SUBMISSION_ENABLED="$(printf '%s' "${ALLOW_OPTIONS_EXECUTION:-${ALLOW_OPTIONS_SUBMISSION:-0}}" | tr '[:upper:]' '[:lower:]')"
    if [[ "${OPTIONS_SUBMISSION_ENABLED}" == "1" || "${OPTIONS_SUBMISSION_ENABLED}" == "true" || "${OPTIONS_SUBMISSION_ENABLED}" == "yes" || "${OPTIONS_SUBMISSION_ENABLED}" == "y" || "${OPTIONS_SUBMISSION_ENABLED}" == "on" ]]; then
        PAPER_REVIEW_PATH="${REPO_ROOT}/outputs/options_overlay_paper/options_overlay_paper_review_${REPORT_DATE}.json"
        if [[ ! -f "${PAPER_REVIEW_PATH}" ]]; then
            echo "ERROR: options submission requested but paper review is missing at ${PAPER_REVIEW_PATH}"
            EXIT_CODE=1
        else
            echo "=== PHASE 2: OPTIONS OVERLAY EXECUTION ==="
            "${PYTHON_BIN}" scripts/execute_options_overlay.py \
                --run-root "${REPO_ROOT}/outputs/options_execution/${REPORT_DATE}" \
                --output-dir "${REPO_ROOT}/outputs/options_execution" \
                --paper-review "${PAPER_REVIEW_PATH}" \
                --trade-date "${REPORT_DATE}" \
                --submit || EXIT_CODE=$?
            if [[ ${EXIT_CODE} -eq 0 ]]; then
                echo "OK: options overlay execution review completed"
            else
                echo "ERROR: options overlay execution failed with exit code ${EXIT_CODE}"
            fi
        fi
    fi
else
    echo "ERROR: paper lane submission terminal_status=${SUBMIT_TERMINAL:-unknown} reason=${SUBMIT_REASON:-unknown} (exit=${SUBMIT_STATUS})"
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${EXIT_CODE}"
exit ${EXIT_CODE}
