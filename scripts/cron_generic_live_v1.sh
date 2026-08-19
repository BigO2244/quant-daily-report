#!/usr/bin/env bash
set -euo pipefail

# Thin generic-v4-only wrapper. It never imports or calls the legacy executor.
REPO_ROOT="${HOME}/quant-daily-report"
GENERIC_ENV="${HOME}/.caerus/generic_live_v1.env"
CREDENTIAL_ENV="${HOME}/.caerus/live_pilot.env"
PYTHON_BIN="/home/brettolson/.venvs/quant-daily-report/bin/python"

cd "${REPO_ROOT}"
[[ -f "${CREDENTIAL_ENV}" ]] && source "${CREDENTIAL_ENV}"
[[ -f "${GENERIC_ENV}" ]] || exit 0
source "${GENERIC_ENV}"

if [[ "${CAERUS_GENERIC_LIVE_SCHEDULE_ENABLED:-0}" != "1" ]]; then
    exit 0
fi

PREFLIGHT_PATH="${CAERUS_GENERIC_LIVE_PREFLIGHT_PATH:?missing preflight path}"
PLAN_PATH="${CAERUS_GENERIC_LIVE_PLAN_PATH:?missing exact plan path}"
SESSION_GATE_PATH="${CAERUS_GENERIC_LIVE_SESSION_GATE_PATH:?missing session gate path}"
WAL_DIRECTORY="${CAERUS_GENERIC_LIVE_WAL_DIRECTORY:?missing WAL directory}"
RESULT_PATH="${CAERUS_GENERIC_LIVE_RESULT_PATH:?missing result path}"
EXECUTED_AT="$(${PYTHON_BIN} -c 'import datetime; print(datetime.datetime.now(datetime.timezone.utc).isoformat())')"

exec "${PYTHON_BIN}" scripts/run_generic_live_v1_session.py \
    --preflight "${PREFLIGHT_PATH}" \
    --exact-plan "${PLAN_PATH}" \
    --executed-at "${EXECUTED_AT}" \
    --wal-directory "${WAL_DIRECTORY}" \
    --session-gate-path "${SESSION_GATE_PATH}" \
    --result-path "${RESULT_PATH}" \
    --submit-exact-session
