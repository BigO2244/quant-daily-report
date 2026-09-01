#!/bin/bash
# Mac Studio advisory-research entry point. This installed runtime is isolated
# from the iCloud-synced repository and has no broker or execution authority.
set -euo pipefail

umask 077
export TZ="America/New_York"
export PYTHONUNBUFFERED="1"

readonly RUNTIME_ROOT="${CAERUS_MAC_RESEARCH_RUNTIME_ROOT:-/Users/brettolson/.caerus/research-runtime}"
readonly SOURCE_ROOT="${RUNTIME_ROOT}/source/quant_research_agent"
readonly PYTHON_BIN="${CAERUS_MAC_RESEARCH_PYTHON:-/Users/brettolson/.caerus/venvs/quant-research/bin/python}"
readonly OUTPUT_ROOT="${RUNTIME_ROOT}/outputs"
readonly LOCK_DIR="${RUNTIME_ROOT}/run.lock.d"

if ! mkdir "${LOCK_DIR}" 2>/dev/null; then
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
        "[MAC_RESEARCH] already running; duplicate launch suppressed"
    exit 0
fi
cleanup() {
    rmdir "${LOCK_DIR}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

[[ -x "${PYTHON_BIN}" ]] || {
    echo "[MAC_RESEARCH] runtime missing: ${PYTHON_BIN}" >&2
    exit 2
}
[[ -r "${SOURCE_ROOT}/.env" ]] || {
    echo "[MAC_RESEARCH] research credentials missing" >&2
    exit 2
}

mkdir -p "${OUTPUT_ROOT}"
cd "${SOURCE_ROOT}"
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "[MAC_RESEARCH] start source_sha=$(tr -d '\n' < "${RUNTIME_ROOT}/SOURCE_SHA")"
"${PYTHON_BIN}" main.py --output-dir "${OUTPUT_ROOT}" "$@"
printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    "[MAC_RESEARCH] complete"
