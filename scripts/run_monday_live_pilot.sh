#!/usr/bin/env bash
# Manual LIVE_PILOT bridge runner. This script is intentionally not referenced
# by cron and must be run by an operator from an interactive shell.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

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

export MODE="live_pilot"
export TRADING_MODE="live_pilot"
export ALPACA_PAPER="0"
export ALPACA_BASE_URL="https://api.alpaca.markets"
export CAERUS_LIVE_PILOT_CAPITAL_CAP="${CAERUS_LIVE_PILOT_CAPITAL_CAP:-100}"
export CAERUS_LIVE_PILOT_MAX_ORDERS="${CAERUS_LIVE_PILOT_MAX_ORDERS:-1}"
export CAERUS_LIVE_PILOT_SLEEVE_ID="${CAERUS_LIVE_PILOT_SLEEVE_ID:-orion}"
export CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH="${CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH:-cfdc5d0aa0e3fdc38adadc78f1ebc30cbc83df187a4223c22597e787cd8a7c85}"
export CAERUS_LIVE_PILOT_APPROVED="${CAERUS_LIVE_PILOT_APPROVED:-1}"

require_eq() {
    local name="$1"
    local expected="$2"
    local actual="${!name:-}"
    if [[ "${actual}" != "${expected}" ]]; then
        echo "FATAL: ${name} must be ${expected}; got '${actual:-<unset>}'" >&2
        exit 1
    fi
}

require_present() {
    local name="$1"
    local actual="${!name:-}"
    if [[ -z "${actual}" ]]; then
        echo "FATAL: ${name} is required" >&2
        exit 1
    fi
}

require_eq TRADING_MODE live_pilot
require_eq ALPACA_PAPER 0
require_eq CAERUS_LIVE_PILOT_APPROVED 1
require_eq CAERUS_LIVE_PILOT_MAX_ORDERS 1
require_present CAERUS_LIVE_PILOT_SLEEVE_ID
require_present CAERUS_LIVE_PILOT_ACCOUNT_ID_HASH

if [[ "${ALPACA_BASE_URL:-}" != "https://api.alpaca.markets" && "${ALPACA_BASE_URL:-}" != "https://api.alpaca.markets/" ]]; then
    echo "FATAL: ALPACA_BASE_URL must be https://api.alpaca.markets for LIVE_PILOT" >&2
    exit 1
fi

if [[ "${CAERUS_LIVE_PILOT_KILL_SWITCH:-0}" == "1" ]]; then
    echo "FATAL: CAERUS_LIVE_PILOT_KILL_SWITCH=1" >&2
    exit 1
fi

"${PYTHON_BIN}" - <<'PY'
import os
import sys

try:
    cap = float(os.environ.get("CAERUS_LIVE_PILOT_CAPITAL_CAP", ""))
except ValueError:
    print("FATAL: CAERUS_LIVE_PILOT_CAPITAL_CAP must be numeric", file=sys.stderr)
    raise SystemExit(1)
if cap <= 0 or cap > 100:
    print("FATAL: CAERUS_LIVE_PILOT_CAPITAL_CAP must be > 0 and <= 100", file=sys.stderr)
    raise SystemExit(1)
PY

mkdir -p outputs/live_pilot/logs outputs/live_pilot/plans
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_PATH="outputs/live_pilot/logs/monday_live_pilot_${RUN_TS}.log"

exec > >(tee -a "${LOG_PATH}") 2>&1

echo "=== MONDAY LIVE_PILOT RUNNER ==="
echo "started_at=${RUN_TS}"
echo "repo_root=${REPO_ROOT}"
echo "log_path=${LOG_PATH}"
echo "trading_mode=${TRADING_MODE}"
echo "alpaca_paper=${ALPACA_PAPER}"
echo "alpaca_base_url=${ALPACA_BASE_URL}"
echo "approved_sleeve=${CAERUS_LIVE_PILOT_SLEEVE_ID}"
echo "capital_cap=${CAERUS_LIVE_PILOT_CAPITAL_CAP}"
echo "max_orders=${CAERUS_LIVE_PILOT_MAX_ORDERS}"

set +e
BUILD_OUTPUT="$(
    "${PYTHON_BIN}" scripts/live_pilot_build_plan_from_precompute.py \
        --approved-sleeve "${CAERUS_LIVE_PILOT_SLEEVE_ID}" \
        --capital-cap "${CAERUS_LIVE_PILOT_CAPITAL_CAP}" \
        --max-orders "${CAERUS_LIVE_PILOT_MAX_ORDERS}" \
        --output-dir outputs/live_pilot/plans
)"
BUILD_STATUS=$?
set -e
echo "${BUILD_OUTPUT}"
if [[ "${BUILD_STATUS}" -ne 0 ]]; then
    echo "live_pilot_plan_builder_exit_code=${BUILD_STATUS}"
fi

PLAN_PATH="$(
    BUILD_OUTPUT="${BUILD_OUTPUT}" "${PYTHON_BIN}" - <<'PY'
import json
import os
import sys

payload = json.loads(os.environ["BUILD_OUTPUT"])
if payload.get("status") != "READY_FOR_MANUAL_APPROVAL":
    print(f"FATAL: live pilot plan blocked: {payload.get('status')}", file=sys.stderr)
    raise SystemExit(1)
path = str(payload.get("json_path") or "")
if not path:
    print("FATAL: live pilot plan did not report json_path", file=sys.stderr)
    raise SystemExit(1)
print(path)
PY
)"

echo "plan_path=${PLAN_PATH}"

echo "=== LIVE_PILOT DRY RUN ==="
export CAERUS_LIVE_PILOT_DRY_RUN=1
"${PYTHON_BIN}" scripts/live_pilot_execute.py --plan "${PLAN_PATH}"

echo "dry_run_success=true"
if [[ "${CAERUS_LIVE_PILOT_SUBMIT_APPROVED:-0}" != "1" ]]; then
    echo "LIVE_PILOT submission paused: set CAERUS_LIVE_PILOT_SUBMIT_APPROVED=1 and rerun this script after Brett approval."
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 0
fi

require_eq CAERUS_LIVE_PILOT_SUBMIT_APPROVED 1

echo "=== LIVE_PILOT SUBMISSION ==="
export CAERUS_LIVE_PILOT_DRY_RUN=0
"${PYTHON_BIN}" scripts/live_pilot_execute.py --plan "${PLAN_PATH}"

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
