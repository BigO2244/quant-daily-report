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
source "${CREDENTIAL_ENV}"
source "${GENERIC_ENV}"

if [[ "${CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED:-0}" != "1" ]]; then
    exit 0
fi
[[ "${CAERUS_GENERIC_LIVE_EFFECTIVE_SESSION:-}" == "${REQUESTED_SESSION}" ]] || exit 78
[[ "$(TZ=America/New_York date +%F)" == "${REQUESTED_SESSION}" ]] || exit 0
[[ "${CAERUS_GENERIC_LIVE_REPO_ROOT:-}" == "${REPO_ROOT}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_PYTHON_BIN:-}" == "${PYTHON_BIN}" ]] || exit 78
[[ "${CAERUS_GENERIC_LIVE_POSTTRADE_OBSERVATION_ENABLED:-0}" == "0" ]] || exit 78

readonly PREFLIGHT_PATH="${CAERUS_GENERIC_LIVE_PREFLIGHT_PATH:?missing preflight path}"
readonly PLAN_PATH="${CAERUS_GENERIC_LIVE_PLAN_PATH:?missing exact plan path}"
readonly SESSION_GATE_PATH="${CAERUS_GENERIC_LIVE_SESSION_GATE_PATH:?missing session gate path}"
readonly WAL_DIRECTORY="${CAERUS_GENERIC_LIVE_WAL_DIRECTORY:?missing WAL directory}"
readonly RESULT_PATH="${CAERUS_GENERIC_LIVE_RESULT_PATH:?missing result path}"
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
    --exact-plan "${PLAN_PATH}" \
    --executed-at "${EXECUTED_AT}" \
    --wal-directory "${WAL_DIRECTORY}" \
    --session-gate-path "${SESSION_GATE_PATH}" \
    --result-path "${RESULT_PATH}" \
    --submit-exact-session
trap - ERR INT TERM HUP
