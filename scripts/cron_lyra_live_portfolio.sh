#!/usr/bin/env bash
set -euo pipefail

readonly REPO_ROOT="${CAERUS_REPO_ROOT:-/home/brettolson/quant-daily-report}"
readonly PYTHON_BIN="${CAERUS_PYTHON_BIN:-/home/brettolson/.venvs/quant-daily-report/bin/python}"
readonly LIVE_CREDENTIALS="${CAERUS_LYRA_LIVE_CREDENTIALS:-/home/brettolson/.caerus/live_pilot.env}"
readonly LIVE_CONFIG="${CAERUS_LYRA_LIVE_CONFIG:-/home/brettolson/.caerus/lyra_live.env}"

[[ $# -eq 1 && ( "$1" == "initialization" || "$1" == "recurring" ) ]] || exit 2
source "${LIVE_CREDENTIALS}"
source "${LIVE_CONFIG}"
cd "${REPO_ROOT}"

if [[ "$1" == "initialization" ]]; then
  readonly EXECUTION_SESSION="2026-08-20"
  readonly TARGET_SOURCE="${REPO_ROOT}/outputs/shadow_candidates/2026-08-17/caerus_lyra.json"
else
  readonly EXECUTION_SESSION="$(TZ=America/New_York date +%F)"
  readonly SIGNAL_SESSION="$(TZ=America/New_York date -d 'yesterday' +%F)"
  readonly TARGET_SOURCE="${REPO_ROOT}/outputs/shadow_candidates/${SIGNAL_SESSION}/caerus_lyra.json"
fi

exec "${PYTHON_BIN}" scripts/run_lyra_live_portfolio.py \
  --mode "$1" \
  --execution-session "${EXECUTION_SESSION}" \
  --target-source "${TARGET_SOURCE}" \
  --owner-decision "${CAERUS_LYRA_LIVE_OWNER_DECISION_PATH}" \
  --state-root "${CAERUS_LYRA_LIVE_STATE_ROOT}" \
  --submit
