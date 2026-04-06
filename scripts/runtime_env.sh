#!/usr/bin/env bash
# Shared runtime environment helpers for cron wrappers.

resolve_runtime_venv_dir() {
    local repo_root="${1:?repo_root required}"
    local -a candidates=()

    if [[ -n "${CAERUS_VENV_DIR:-}" ]]; then
        candidates+=("${CAERUS_VENV_DIR}")
    fi
    candidates+=(
        "${HOME}/.venvs/quant-daily-report"
        "${repo_root}/venv"
        "${repo_root}/.venv"
    )

    local candidate
    for candidate in "${candidates[@]}"; do
        if [[ -n "${candidate}" && -f "${candidate}/bin/activate" ]]; then
            printf '%s\n' "${candidate}"
            return 0
        fi
    done

    echo "FATAL: runtime virtualenv not found. checked: ${candidates[*]}" >&2
    return 1
}


activate_runtime_venv() {
    local repo_root="${1:?repo_root required}"
    local venv_dir
    venv_dir="$(resolve_runtime_venv_dir "${repo_root}")" || return 1

    # shellcheck disable=SC1090
    source "${venv_dir}/bin/activate"
    export CAERUS_ACTIVE_VENV_DIR="${venv_dir}"
}
