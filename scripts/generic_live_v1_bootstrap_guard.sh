#!/usr/bin/env bash
# External guard for failures that occur before the Python runner or its trap.
set -uo pipefail
umask 077

readonly DEFAULT_HOME="/home/brettolson"
OPS_HOME="${DEFAULT_HOME}/.caerus"
REPO_ROOT="${DEFAULT_HOME}/quant-daily-report"
PYTHON_BIN="${DEFAULT_HOME}/.venvs/quant-daily-report/bin/python"
if [[ -n "${CAERUS_GENERIC_LIVE_GUARD_TEST_ROOT:-}" ]]; then
    [[ "${CAERUS_GENERIC_LIVE_GUARD_TEST_MODE:-0}" == "1" ]] || exit 78
    OPS_HOME="${CAERUS_GENERIC_LIVE_GUARD_TEST_ROOT}/.caerus"
    REPO_ROOT="${CAERUS_GENERIC_LIVE_GUARD_TEST_ROOT}/quant-daily-report"
    PYTHON_BIN="${CAERUS_GENERIC_LIVE_GUARD_TEST_PYTHON:-/missing/python}"
fi
readonly OPS_HOME REPO_ROOT PYTHON_BIN
readonly STATE_ROOT="${OPS_HOME}/generic_live_v1_state"
readonly STATE_PATH="${STATE_ROOT}/session_gate.json"
readonly ACTIVE_CONFIG="${OPS_HOME}/generic_live_v1.env"
readonly BACKUP_CONFIG="${OPS_HOME}/generic_live_v1.env.rollback"
readonly EVIDENCE_ROOT="${STATE_ROOT}/rollback_evidence"
readonly CRON_LOG="${REPO_ROOT}/logs/cron_generic_live_v1.log"
readonly GUARD_PATH="${REPO_ROOT}/scripts/generic_live_v1_bootstrap_guard.sh"
REQUESTED_SESSION="${2:-UNKNOWN}"
ROLLBACK_STARTED=0
PAPER_BEFORE=""

hash_file() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    else
        shasum -a 256 "$1" | awk '{print $1}'
    fi
}

capture_paper_hashes() {
    local captured="" paper
    for paper in "${REPO_ROOT}/scripts/cron_precompute.sh" "${REPO_ROOT}/scripts/cron_execute.sh" "${REPO_ROOT}/scripts/crontab.txt"; do
        [[ -f "${paper}" ]] && captured+="${paper}:$(hash_file "${paper}");"
    done
    printf '%s' "${captured}"
}

python_rollback() {
    local trigger="$1" day month exact evidence
    [[ -x "${PYTHON_BIN}" && -f "${REPO_ROOT}/scripts/rollback_generic_live_v1.py" ]] || return 1
    [[ "${REQUESTED_SESSION}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || return 1
    day="${REQUESTED_SESSION##*-}"
    month="${REQUESTED_SESSION:5:2}"
    day="$((10#${day}))"
    month="$((10#${month}))"
    exact="36 9 ${day} ${month} * ${GUARD_PATH} --effective-session ${REQUESTED_SESSION} >> ${CRON_LOG} 2>&1 # CAERUS_GENERIC_LIVE_V1_SESSION=${REQUESTED_SESSION}"
    evidence="${EVIDENCE_ROOT}/rollback-${REQUESTED_SESSION}-$(date -u +%Y%m%dT%H%M%SZ)-$$.json"
    "${PYTHON_BIN}" "${REPO_ROOT}/scripts/rollback_generic_live_v1.py" \
        --trigger "${trigger}" --state-path "${STATE_PATH}" \
        --preflight-hash "" --plan-hash "" --exact-cron-line "${exact}" \
        --active-config-path "${ACTIVE_CONFIG}" --backup-config-path "${BACKUP_CONFIG}" \
        --paper-path "${REPO_ROOT}/scripts/cron_precompute.sh" \
        --paper-path "${REPO_ROOT}/scripts/cron_execute.sh" \
        --paper-path "${REPO_ROOT}/scripts/crontab.txt" \
        --evidence-path "${evidence}" --allowed-root "${OPS_HOME}" \
        --allowed-root "${REPO_ROOT}" >/dev/null 2>&1
}

fallback_rollback() {
    local trigger="${1:-PREFLIGHT_BREAK}"
    [[ "${ROLLBACK_STARTED}" == "0" ]] || return 0
    ROLLBACK_STARTED=1
    [[ -L "${STATE_ROOT}" ]] && rm -f "${STATE_ROOT}" 2>/dev/null || true
    [[ -L "${EVIDENCE_ROOT}" ]] && rm -f "${EVIDENCE_ROOT}" 2>/dev/null || true
    mkdir -p "${STATE_ROOT}" "${EVIDENCE_ROOT}" 2>/dev/null || true
    chmod 700 "${STATE_ROOT}" "${EVIDENCE_ROOT}" 2>/dev/null || true

    if python_rollback "${trigger}"; then
        return 0
    fi

    local now body digest temporary
    now="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo 1970-01-01T00:00:00Z)"
    body="{\"generic_submission_enabled\":false,\"legacy_executor_enabled\":false,\"paper_cutover_enabled\":false,\"plan_hash\":\"$(printf '0%.0s' {1..64})\",\"preflight_hash\":\"$(printf '0%.0s' {1..64})\",\"rearmed_at\":\"${now}\",\"schema_version\":\"caerus.generic_live_v1_rearm.v1\",\"status\":\"ARMED\",\"trigger\":\"${trigger}\"}"
    digest="$(printf '%s' "${body}" | (sha256sum 2>/dev/null || shasum -a 256) | awk '{print $1}')"
    temporary="${STATE_ROOT}/.session_gate.json.$$"
    printf '%s\n' "${body%\}} ,\"content_hash\":\"${digest}\"}" | sed 's/} ,/},/' >"${temporary}" 2>/dev/null || true
    chmod 600 "${temporary}" 2>/dev/null || true
    mv -f "${temporary}" "${STATE_PATH}" 2>/dev/null || true

    local config_action="ALREADY_ABSENT"
    if [[ -f "${BACKUP_CONFIG}" && ! -L "${BACKUP_CONFIG}" ]]; then
        temporary="${OPS_HOME}/.generic_live_v1.env.$$"
        if cp "${BACKUP_CONFIG}" "${temporary}" 2>/dev/null \
            && chmod 600 "${temporary}" 2>/dev/null \
            && mv -f "${temporary}" "${ACTIVE_CONFIG}" 2>/dev/null; then
            config_action="RESTORED_BACKUP"
        else
            rm -f "${temporary}" 2>/dev/null || true
            config_action="RESTORE_FAILED"
        fi
    elif [[ -e "${ACTIVE_CONFIG}" || -L "${ACTIVE_CONFIG}" ]]; then
        rm -f "${ACTIVE_CONFIG}" 2>/dev/null || true
        config_action="REMOVED_NO_PRIOR_CONFIG"
    else
        config_action="ALREADY_ABSENT"
    fi

    local cron_exact_line_removed=true
    if command -v crontab >/dev/null 2>&1 \
        && [[ "${REQUESTED_SESSION}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
        local current filtered exact day month
        day="${REQUESTED_SESSION##*-}"
        month="${REQUESTED_SESSION:5:2}"
        day="$((10#${day}))"
        month="$((10#${month}))"
        exact="36 9 ${day} ${month} * ${GUARD_PATH} --effective-session ${REQUESTED_SESSION} >> ${CRON_LOG} 2>&1 # CAERUS_GENERIC_LIVE_V1_SESSION=${REQUESTED_SESSION}"
        current="$(crontab -l 2>/dev/null || true)"
        filtered="$(printf '%s\n' "${current}" | awk -v exact="${exact}" '$0 != exact')"
        printf '%s\n' "${filtered}" | crontab - 2>/dev/null || cron_exact_line_removed=false
        [[ "$(crontab -l 2>/dev/null || true)" != *"${exact}"* ]] || cron_exact_line_removed=false
    fi

    local paper_before paper_after paper_unchanged evidence
    paper_before="${PAPER_BEFORE}"
    paper_after="$(capture_paper_hashes)"
    paper_unchanged=false
    [[ -n "${paper_before}" && "${paper_before}" == "${paper_after}" ]] && paper_unchanged=true
    evidence="${EVIDENCE_ROOT}/rollback-${REQUESTED_SESSION//[^0-9-]/_}-$$.txt"
    printf 'status=ROLLED_BACK_ARMED\ntrigger=%s\nconfig_action=%s\ncron_exact_line_removed=%s\npaper_hashes_before=%s\npaper_hashes_after=%s\npaper_bytes_unchanged=%s\n' \
        "${trigger}" "${config_action}" "${cron_exact_line_removed}" "${paper_before}" "${paper_after}" "${paper_unchanged}" >"${evidence}" 2>/dev/null || true
    chmod 600 "${evidence}" 2>/dev/null || true
}

on_exit() {
    local status=$?
    trap - EXIT INT TERM HUP
    if [[ "${status}" -ne 0 ]]; then
        local trigger="PREFLIGHT_BREAK"
        if [[ -f "${STATE_PATH}" ]]; then
            local observed
            observed="$(sed -n 's/.*"trigger"[[:space:]]*:[[:space:]]*"\([A-Z_]*\)".*/\1/p' "${STATE_PATH}" 2>/dev/null | head -1)"
            case "${observed}" in
                PREFLIGHT_BREAK|SUBMISSION_BREAK|ORDER_BREAK|RECONCILIATION_BREAK|ACCOUNTING_BREAK|REPORTING_BREAK) trigger="${observed}" ;;
            esac
        fi
        fallback_rollback "${trigger}"
    fi
    exit "${status}"
}
trap on_exit EXIT INT TERM HUP

PAPER_BEFORE="$(capture_paper_hashes)"

if [[ "${1:-}" != "--effective-session" || ! "${REQUESTED_SESSION}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ || -n "${3:-}" ]]; then
    exit 64
fi

CAERUS_GENERIC_LIVE_BOOTSTRAP_GUARD=1 \
    "${REPO_ROOT}/scripts/cron_generic_live_v1.sh" --effective-session "${REQUESTED_SESSION}" >/dev/null 2>&1
status=$?
if [[ "${status}" -ne 0 ]]; then
    exit "${status}"
fi
trap - EXIT INT TERM HUP
exit 0
