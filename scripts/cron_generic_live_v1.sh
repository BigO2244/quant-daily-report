#!/usr/bin/env bash
set -euo pipefail
umask 077

# Date-bound generic-v4-only launcher. This file is inert unless its one
# explicit session, protected config, exact runtime pins, and three approvals
# all agree. It never imports or calls the legacy executor.
readonly REPO_ROOT="/home/brettolson/quant-daily-report"
readonly GENERIC_ENV="/home/brettolson/.caerus/generic_live_v1.env"
readonly CREDENTIAL_ENV="/home/brettolson/.caerus/live_pilot.env"
readonly PYTHON_BIN="/home/brettolson/.venvs/quant-daily-report/bin/python"
readonly INPUT_ROOT="/home/brettolson/.caerus/generic_live_v1_inputs"
readonly STATE_ROOT="/home/brettolson/.caerus/generic_live_v1_state"
readonly FIXED_SESSION_GATE="${STATE_ROOT}/session_gate.json"
readonly BOOTSTRAP_GUARD="${REPO_ROOT}/scripts/generic_live_v1_bootstrap_guard.sh"

if [[ "${CAERUS_GENERIC_LIVE_BOOTSTRAP_GUARD:-0}" != "1" ]]; then
    exec "${BOOTSTRAP_GUARD}" "$@"
fi

if [[ "${1:-}" != "--effective-session" || ! "${2:-}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ || -n "${3:-}" ]]; then
    echo "usage: $0 --effective-session YYYY-MM-DD" >&2
    exit 64
fi
readonly REQUESTED_SESSION="$2"

for protected_file in "${GENERIC_ENV}" "${CREDENTIAL_ENV}"; do
    [[ -f "${protected_file}" && ! -L "${protected_file}" ]] || exit 78
    [[ "$(readlink -f "${protected_file}")" == "${protected_file}" ]] || exit 78
    [[ "$(stat -c '%a' "${protected_file}")" == "600" ]] || exit 78
done
[[ -d "${REPO_ROOT}" && ! -L "${REPO_ROOT}" ]] || exit 78
[[ "$(readlink -f "${REPO_ROOT}")" == "${REPO_ROOT}" ]] || exit 78
[[ -x "${PYTHON_BIN}" ]] || exit 78

# These files are owner-only and paths are fixed above; sourcing does not
# accept caller-selected locations.
source "${CREDENTIAL_ENV}" >/dev/null 2>&1 || exit 78
source "${GENERIC_ENV}" >/dev/null 2>&1 || exit 78

if [[ "${CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED:-0}" != "1" ]]; then
    exit 0
fi
[[ "${CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION:-}" == "${REQUESTED_SESSION}" ]] || exit 78
[[ "$(TZ=America/New_York date +%F)" == "${REQUESTED_SESSION}" ]] || exit 0
[[ "${CAERUS_GENERIC_LIVE_REPO_ROOT:-}" == "${REPO_ROOT}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_PYTHON_BIN:-}" == "${PYTHON_BIN}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_INPUT_ROOT:-}" == "${INPUT_ROOT}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_STATE_ROOT:-}" == "${STATE_ROOT}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED:-0}" == "1" ]] || exit 78

readonly PREFLIGHT_PATH="${CAERUS_GENERIC_LIVE_PREFLIGHT_PATH:?missing preflight path}"
readonly OWNER_DECISION_PATH="${CAERUS_GENERIC_LIVE_OWNER_DECISION_PATH:?missing owner decision path}"
readonly ACCOUNT_OBSERVATION_PATH="${CAERUS_GENERIC_LIVE_ACCOUNT_OBSERVATION_PATH:?missing account observation path}"
readonly LYRA_DECISION_PATH="${CAERUS_GENERIC_LIVE_LYRA_DECISION_PATH:?missing Lyra decision path}"
readonly LYRA_CAPTURE_PATH="${CAERUS_GENERIC_LIVE_LYRA_CAPTURE_PATH:?missing Lyra capture path}"
readonly OPERATIONAL_PROOFS_PATH="${CAERUS_GENERIC_LIVE_OPERATIONAL_PROOFS_PATH:?missing operational proofs path}"
readonly PLAN_PATH="${CAERUS_GENERIC_LIVE_PLAN_PATH:?missing exact plan path}"
readonly SESSION_GATE_PATH="${CAERUS_GENERIC_LIVE_SESSION_GATE_PATH:?missing session gate path}"
[[ "${SESSION_GATE_PATH}" == "${FIXED_SESSION_GATE}" ]] || exit 78
readonly WAL_DIRECTORY="${CAERUS_GENERIC_LIVE_WAL_DIRECTORY:?missing WAL directory}"
readonly RESULT_PATH="${CAERUS_GENERIC_LIVE_RESULT_PATH:?missing result path}"
readonly EXISTING_JOURNAL_PATH="${CAERUS_GENERIC_LIVE_EXISTING_JOURNAL_PATH:?missing journal path}"
readonly PRIOR_VALUATIONS_PATH="${CAERUS_GENERIC_LIVE_PRIOR_VALUATIONS_PATH:?missing valuations path}"
readonly DEPLOYMENT_POLICY_PATH="${CAERUS_GENERIC_LIVE_DEPLOYMENT_POLICY_PATH:?missing deployment policy path}"
readonly KNOWN_SLEEVE_IDS_PATH="${CAERUS_GENERIC_LIVE_KNOWN_SLEEVE_IDS_PATH:?missing sleeve ids path}"
readonly DEPLOYMENT_STATE_PATH="${CAERUS_GENERIC_LIVE_DEPLOYMENT_STATE_PATH:?missing deployment state path}"
readonly CAPITAL_PATH="${CAERUS_GENERIC_LIVE_CAPITAL_PATH:?missing capital path}"
readonly OTHER_LANE_AUDITS_PATH="${CAERUS_GENERIC_LIVE_OTHER_LANE_AUDITS_PATH:?missing other lane audits path}"
readonly BROKER_EVIDENCE_DIRECTORY="${STATE_ROOT}/broker_evidence/${REQUESTED_SESSION}"
readonly REPORTING_ARTIFACT_DIRECTORY="${STATE_ROOT}/reporting/${REQUESTED_SESSION}"
readonly ROLLBACK_EVIDENCE_DIRECTORY="${STATE_ROOT}/rollback_evidence"
readonly BASE_POSTTRADE_RESULT_PATH="${STATE_ROOT}/posttrade-base-${REQUESTED_SESSION}.json"
readonly CLOSURE_RESULT_PATH="${STATE_ROOT}/posttrade-closure-${REQUESTED_SESSION}.json"
readonly PUBLISHED_POINTER_PATH="${STATE_ROOT}/posttrade-published-${REQUESTED_SESSION}.json"
readonly EXECUTED_AT="$(${PYTHON_BIN} -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')"

rearm_on_wrapper_failure() {
    local exit_code=$?
    trap - ERR INT TERM HUP
    "${PYTHON_BIN}" "${REPO_ROOT}/scripts/rearm_generic_live_v1.py" \
        --state-path "${SESSION_GATE_PATH}" \
        --state-root "${CAERUS_GENERIC_LIVE_STATE_ROOT:-}" \
        --preflight-hash "${CAERUS_GENERIC_LIVE_PREFLIGHT_HASH:-}" \
        --plan-hash "${CAERUS_GENERIC_LIVE_PLAN_HASH:-}" \
        --trigger PREFLIGHT_BREAK >/dev/null 2>&1 || true
    exit "${exit_code}"
}
trap rearm_on_wrapper_failure ERR INT TERM HUP

cd "${REPO_ROOT}"
"${PYTHON_BIN}" scripts/run_generic_live_v1_session.py \
    --preflight "${PREFLIGHT_PATH}" \
    --owner-decision "${OWNER_DECISION_PATH}" \
    --account-observation "${ACCOUNT_OBSERVATION_PATH}" \
    --lyra-decision "${LYRA_DECISION_PATH}" \
    --lyra-capture-result "${LYRA_CAPTURE_PATH}" \
    --operational-proofs "${OPERATIONAL_PROOFS_PATH}" \
    --exact-plan "${PLAN_PATH}" \
    --executed-at "${EXECUTED_AT}" \
    --wal-directory "${WAL_DIRECTORY}" \
    --session-gate-path "${SESSION_GATE_PATH}" \
    --result-path "${RESULT_PATH}" \
    --submit-exact-session

install -d -m 700 \
    "${BROKER_EVIDENCE_DIRECTORY}" \
    "${REPORTING_ARTIFACT_DIRECTORY}" \
    "${ROLLBACK_EVIDENCE_DIRECTORY}"
readonly FINALIZED_AT="$(${PYTHON_BIN} -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')"
readonly CRON_DAY="$((10#${REQUESTED_SESSION:8:2}))"
readonly CRON_MONTH="$((10#${REQUESTED_SESSION:5:2}))"
readonly EXACT_CRON_LINE="36 9 ${CRON_DAY} ${CRON_MONTH} * ${BOOTSTRAP_GUARD} --effective-session ${REQUESTED_SESSION} >> ${REPO_ROOT}/logs/cron_generic_live_v1.log 2>&1 # CAERUS_GENERIC_LIVE_V1_SESSION=${REQUESTED_SESSION}"
"${PYTHON_BIN}" scripts/finalize_generic_live_v1_posttrade.py \
    --input-root "${INPUT_ROOT}" \
    --state-root "${STATE_ROOT}" \
    --submission-result "${RESULT_PATH}" \
    --exact-plan "${PLAN_PATH}" \
    --collect-from-broker \
    --broker-evidence-directory "${BROKER_EVIDENCE_DIRECTORY}" \
    --published-pointer-path "${PUBLISHED_POINTER_PATH}" \
    --existing-journal "${EXISTING_JOURNAL_PATH}" \
    --prior-valuations "${PRIOR_VALUATIONS_PATH}" \
    --deployment-policy "${DEPLOYMENT_POLICY_PATH}" \
    --known-sleeve-ids "${KNOWN_SLEEVE_IDS_PATH}" \
    --deployment-state "${DEPLOYMENT_STATE_PATH}" \
    --capital "${CAPITAL_PATH}" \
    --other-lane-audits "${OTHER_LANE_AUDITS_PATH}" \
    --session-gate-path "${SESSION_GATE_PATH}" \
    --base-result-path "${BASE_POSTTRADE_RESULT_PATH}" \
    --closure-result-path "${CLOSURE_RESULT_PATH}" \
    --reporting-artifact-directory "${REPORTING_ARTIFACT_DIRECTORY}" \
    --exact-cron-line "${EXACT_CRON_LINE}" \
    --active-config-path "${GENERIC_ENV}" \
    --backup-config-path "/home/brettolson/.caerus/generic_live_v1.env.rollback" \
    --paper-root "${REPO_ROOT}" \
    --paper-path "${REPO_ROOT}/scripts/cron_precompute.sh" \
    --paper-path "${REPO_ROOT}/scripts/cron_execute.sh" \
    --paper-path "${REPO_ROOT}/scripts/crontab.txt" \
    --rollback-evidence-directory "${ROLLBACK_EVIDENCE_DIRECTORY}" \
    --reconciled-at "${FINALIZED_AT}" \
    --valuation-date "${REQUESTED_SESSION}" \
    --finalized-at "${FINALIZED_AT}"
trap - ERR INT TERM HUP
