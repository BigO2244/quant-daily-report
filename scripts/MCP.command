#!/usr/bin/env bash
set -euo pipefail

# MCP.command source launcher.
#
# Purpose:
#   Build a disposable read-only MCP-lite registry on the VM and print the
#   daily operator brief plus supporting integrity/governance views. This is
#   manual operator tooling only.
#
# Assumptions:
#   - VM source lives in ~/quant-daily-report.
#   - SSH access uses the existing Caerus VM identity.
#   - The VM production Python environment is:
#     ~/.venvs/quant-daily-report/bin/python3
#
# Rollback:
#   Revert this script or remove the copied MCP.command launcher. No cron,
#   broker, execution, dashboard, or strategy behavior depends on this launcher.

VM="${MCP_VM:-${CAERUS_VM:-brettolson@alpha-stack-scheduler}}"
REMOTE_REPO="${MCP_REMOTE_REPO:-~/quant-daily-report}"
PY="${MCP_REMOTE_PY:-}"
DB_PATH="${MCP_DB_PATH:-/tmp/caerus-mcp-operator.db}"
LIMIT="${MCP_LIMIT:-10}"
DISPLAY_PY="${PY:-~/.venvs/quant-daily-report/bin/python3}"

echo "[MCP] Read-only operator launcher"
echo "[MCP] VM: ${VM}"
echo "[MCP] Remote repo: ${REMOTE_REPO}"
echo "[MCP] Python: ${DISPLAY_PY}"
echo "[MCP] Disposable DB: ${DB_PATH}"
echo "[MCP] Limit: ${LIMIT}"
echo

ssh "${VM}" "cd ${REMOTE_REPO} && MCP_REMOTE_PY='${PY}' MCP_DB_PATH='${DB_PATH}' MCP_LIMIT='${LIMIT}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

PY="${MCP_REMOTE_PY:-${HOME}/.venvs/quant-daily-report/bin/python3}"
DB_PATH="${MCP_DB_PATH}"
LIMIT="${MCP_LIMIT}"

if [[ "${DB_PATH}" != /tmp/* ]]; then
    echo "[MCP][ERROR] Refusing to write registry DB outside /tmp: ${DB_PATH}" >&2
    exit 10
fi

if [[ ! -x "${PY}" ]]; then
    echo "[MCP][ERROR] Missing VM production Python: ${PY}" >&2
    exit 11
fi

echo "[MCP] Remote HEAD: $(git rev-parse --short HEAD 2>/dev/null || printf 'unknown')"
echo "[MCP] Building disposable registry..."
"${PY}" scripts/research_registry_cli.py build-caerus-registry \
    --db "${DB_PATH}" \
    --runs-root outputs/runs \
    --packets-root outputs/research_packets \
    --docs-root docs/governance \
    --limit "${LIMIT}"

echo
echo "[MCP] Daily operator brief"
"${PY}" scripts/research_registry_cli.py daily-operator-brief --db "${DB_PATH}"

echo
echo "[MCP] Execution integrity findings"
"${PY}" scripts/research_registry_cli.py integrity-findings --db "${DB_PATH}"

echo
echo "[MCP] Governance open items"
"${PY}" scripts/research_registry_cli.py governance-open --db "${DB_PATH}"

echo
echo "[MCP] Complete. Source artifacts were read only; disposable DB remains at ${DB_PATH}."
REMOTE_SCRIPT

echo
echo "[MCP] Launcher complete."
