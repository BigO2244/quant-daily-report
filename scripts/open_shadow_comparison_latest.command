#!/usr/bin/env bash
set -euo pipefail

VM="${SHADOW_VM:-brettolson@34.61.147.38}"
REMOTE_DIR="${SHADOW_REMOTE_DIR:-~/quant-daily-report/outputs/shadow_candidates/latest}"
LOCAL_DIR="${SHADOW_LOCAL_DIR:-$HOME/Documents/Caerus/quant-daily-report-main/outputs/shadow_candidates/latest}"
LOCAL_MD="${LOCAL_DIR}/comparison.md"

mkdir -p "${LOCAL_DIR}"

echo "[SHADOW] Pulling latest comparison artifacts from VM source of truth..."
echo "[SHADOW] VM: ${VM}:${REMOTE_DIR}/"

for artifact in comparison.md comparison.json delta.json shadow_evaluation.json; do
    scp "${VM}:${REMOTE_DIR}/${artifact}" "${LOCAL_DIR}/${artifact}"
done

echo "[SHADOW] Updated local copy: ${LOCAL_MD}"
if command -v grep >/dev/null 2>&1; then
    grep -m 1 -A 1 "^## Trade Date" "${LOCAL_MD}" || true
fi

if [[ "${SHADOW_SKIP_OPEN:-0}" != "1" ]]; then
    if command -v open >/dev/null 2>&1; then
        open -a TextEdit "${LOCAL_MD}" || open "${LOCAL_DIR}"
    else
        echo "[SHADOW] Open manually: ${LOCAL_MD}"
    fi
fi
