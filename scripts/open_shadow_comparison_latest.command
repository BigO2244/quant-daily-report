#!/usr/bin/env bash
set -euo pipefail

# Orion.command source launcher.
#
# Purpose:
#   Build the FR-030 Daily Research Interpretation Packet on the VM, retrieve
#   the generated packet bundle locally, and open the primary operator review
#   surface. This is manual operator tooling only.
#
# Assumptions:
#   - VM source lives in ~/quant-daily-report.
#   - SSH/scp access uses the existing Caerus VM identity.
#   - The repo Python environment is available as venv/bin/python, .venv/bin/python,
#     or python3.
#
# Rollback:
#   Revert this script or copy an older version back to Orion.command. No cron,
#   broker, execution, dashboard, or promotion behavior depends on this launcher.

VM="${ORION_VM:-${SHADOW_VM:-brettolson@34.61.147.38}}"
REMOTE_REPO="${ORION_REMOTE_REPO:-~/quant-daily-report}"
LOCAL_DIR="${ORION_LOCAL_DIR:-$HOME/Downloads/caerus_research_packets/latest}"
REQUESTED_TRADE_DATE="${ORION_TRADE_DATE:-}"
SKIP_OPEN="${ORION_SKIP_OPEN:-${SHADOW_SKIP_OPEN:-0}}"
ALLOW_INCOMPLETE_PACKET="${ORION_ALLOW_INCOMPLETE_PACKET:-0}"
REFRESH_BEFORE_PACKET="${ORION_REFRESH_BEFORE_PACKET:-0}"

LOCAL_PACKET_MD="${LOCAL_DIR}/packet.md"
LOCAL_PACKET_HTML="${LOCAL_DIR}/packet.html"
LOCAL_SUMMARY="${LOCAL_DIR}/summary.json"
LOCAL_SUPPORTING_DIR="${LOCAL_DIR}/supporting_shadow"

mkdir -p "${LOCAL_DIR}" "${LOCAL_SUPPORTING_DIR}"

echo "[FR-030] Daily Research Review launcher"
echo "[FR-030] VM: ${VM}"
echo "[FR-030] Remote repo: ${REMOTE_REPO}"
echo "[FR-030] Local packet dir: ${LOCAL_DIR}"

echo "[FR-030] Building latest research packet on VM..."
if ! REMOTE_OUTPUT=$(ssh "${VM}" "cd ${REMOTE_REPO} && ORION_TRADE_DATE='${REQUESTED_TRADE_DATE}' ORION_ALLOW_INCOMPLETE_PACKET='${ALLOW_INCOMPLETE_PACKET}' ORION_REFRESH_BEFORE_PACKET='${REFRESH_BEFORE_PACKET}' bash -s" <<'REMOTE_SCRIPT'
set -euo pipefail

if [[ -x venv/bin/python ]]; then
    PY="venv/bin/python"
elif [[ -x .venv/bin/python ]]; then
    PY=".venv/bin/python"
else
    PY="python3"
fi

TRADE_DATE="${ORION_TRADE_DATE:-}"
if [[ -z "${TRADE_DATE}" ]]; then
    TRADE_DATE="$(ls -1 outputs/shadow_candidates 2>/dev/null | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}$' | sort | tail -1)"
fi

if [[ -z "${TRADE_DATE}" ]]; then
    echo "[FR-030][ERROR] No dated shadow candidate directory found." >&2
    exit 20
fi

if [[ ! -d "outputs/shadow_candidates/${TRADE_DATE}" ]]; then
    echo "[FR-030][ERROR] Missing shadow candidate source: outputs/shadow_candidates/${TRADE_DATE}" >&2
    exit 21
fi

if [[ "${ORION_REFRESH_BEFORE_PACKET:-0}" == "1" ]]; then
    echo "[FR-030][ERROR] Refresh-before-packet is not implemented yet; run the approved hydration workflow separately." >&2
    exit 23
fi

PREFLIGHT_JSON="$("${PY}" - "${TRADE_DATE}" <<'PY'
import json
import sys
from pathlib import Path

trade_date = sys.argv[1]
shadow_dir = Path("outputs/shadow_candidates") / trade_date
performance_path = shadow_dir / "shadow_performance.json"
comparison_path = shadow_dir / "comparison.json"
hydration_path = Path("outputs/price_hydration") / trade_date / "status.json"

def read_json(path):
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_read_error": str(exc)}
    return payload if isinstance(payload, dict) else {"_read_error": "not_json_object"}

performance = read_json(performance_path)
comparison = read_json(comparison_path)
hydration = read_json(hydration_path)
strategies = comparison.get("strategies") if isinstance(comparison.get("strategies"), dict) else {}
shadow_data_status = performance.get("data_status")
shadow_data_reason = performance.get("data_reason")
comparison_status = comparison.get("status", "OK" if strategies else "UNKNOWN")
strategy_count = len(strategies)
price_hydration_status = hydration.get("status") if hydration else "MISSING"
max_cache_date = hydration.get("max_cache_date") or hydration.get("as_of_date")
hydration_covers_trade_date = bool(max_cache_date and str(max_cache_date) >= trade_date)

failures = []
if not performance_path.exists():
    failures.append("missing shadow_performance.json")
if shadow_data_status != "OK":
    failures.append("shadow_performance.data_status is not OK")
if shadow_data_reason not in (None, "", "OK"):
    failures.append("shadow_performance.data_reason is present")
if not comparison_path.exists():
    failures.append("missing comparison.json")
if comparison_status != "OK":
    failures.append("comparison.status is not OK")
if strategy_count == 0:
    failures.append("comparison.strategies is empty")
if not hydration_path.exists():
    failures.append("missing price hydration status")
elif price_hydration_status != "OK":
    failures.append("price hydration status is not OK")
elif not hydration_covers_trade_date:
    failures.append("price hydration max cache date does not cover trade date")

print(json.dumps({
    "source_readiness": "READY" if not failures else "INCOMPLETE",
    "failures": failures,
    "shadow_data_status": shadow_data_status,
    "shadow_data_reason": shadow_data_reason,
    "comparison_status": comparison_status,
    "strategy_count": strategy_count,
    "hydration_status_path": str(hydration_path),
    "price_hydration_status": price_hydration_status,
    "price_hydration_max_cache_date": max_cache_date,
    "hydration_covers_trade_date": hydration_covers_trade_date,
}, sort_keys=True))
PY
)"

echo "__FR030_PREFLIGHT__:${PREFLIGHT_JSON}"
SOURCE_READINESS="$(printf '%s\n' "${PREFLIGHT_JSON}" | "${PY}" -c 'import json,sys; print(json.load(sys.stdin)["source_readiness"])')"
if [[ "${SOURCE_READINESS}" != "READY" ]]; then
    echo "[FR-030][WARN] Post-close research source is not ready; packet would be incomplete." >&2
    printf '%s\n' "${PREFLIGHT_JSON}" | "${PY}" -c 'import json,sys; payload=json.load(sys.stdin); [print(f"[FR-030][WARN] {key}: {payload.get(key)}") for key in ("shadow_data_status", "shadow_data_reason", "comparison_status", "strategy_count", "hydration_status_path", "price_hydration_status", "price_hydration_max_cache_date")]; [print(f"[FR-030][WARN] failure: {failure}") for failure in payload.get("failures", [])]' >&2
    if [[ "${ORION_ALLOW_INCOMPLETE_PACKET:-0}" != "1" ]]; then
        echo "[FR-030][ERROR] Source readiness failed. Set ORION_ALLOW_INCOMPLETE_PACKET=1 to build an explicitly incomplete advisory packet." >&2
        exit 24
    fi
    echo "[FR-030][WARN] ORION_ALLOW_INCOMPLETE_PACKET=1 set; building packet with INCOMPLETE source readiness." >&2
fi

"${PY}" -m scripts.research.build_research_clarity_wave \
    --trade-date "${TRADE_DATE}" \
    --source-dir "outputs/shadow_candidates/${TRADE_DATE}" \
    --output-dir "outputs/research_clarity/${TRADE_DATE}" >/tmp/caerus_fr030_clarity_build.log

"${PY}" -m scripts.research.build_daily_research_packet \
    --trade-date "${TRADE_DATE}" \
    --shadow-dir "outputs/shadow_candidates/${TRADE_DATE}" \
    --clarity-dir "outputs/research_clarity/${TRADE_DATE}" \
    --output-dir "outputs/research_packets/${TRADE_DATE}" >/tmp/caerus_fr030_packet_build.log

for artifact in packet.md packet.html packet.json summary.json; do
    if [[ ! -s "outputs/research_packets/${TRADE_DATE}/${artifact}" ]]; then
        echo "[FR-030][ERROR] Missing generated packet artifact: ${artifact}" >&2
        exit 22
    fi
done

echo "__FR030_TRADE_DATE__:${TRADE_DATE}"
echo "__FR030_PACKET_DIR__:outputs/research_packets/${TRADE_DATE}"
echo "__FR030_SHADOW_DIR__:outputs/shadow_candidates/${TRADE_DATE}"
REMOTE_SCRIPT
); then
    echo "[FR-030][ERROR] VM packet generation failed." >&2
    exit 1
fi

echo "${REMOTE_OUTPUT}"

TRADE_DATE="$(printf '%s\n' "${REMOTE_OUTPUT}" | awk -F: '/^__FR030_TRADE_DATE__:/ {print $2}' | tail -1)"
REMOTE_PACKET_DIR="$(printf '%s\n' "${REMOTE_OUTPUT}" | awk -F: '/^__FR030_PACKET_DIR__:/ {print $2}' | tail -1)"
REMOTE_SHADOW_DIR="$(printf '%s\n' "${REMOTE_OUTPUT}" | awk -F: '/^__FR030_SHADOW_DIR__:/ {print $2}' | tail -1)"

if [[ -z "${TRADE_DATE}" || -z "${REMOTE_PACKET_DIR}" ]]; then
    echo "[FR-030][ERROR] Could not determine generated packet directory from VM output." >&2
    exit 2
fi

echo "[FR-030] Retrieving packet bundle for ${TRADE_DATE}..."
for artifact in packet.md packet.html packet.json summary.json; do
    if ! scp "${VM}:${REMOTE_REPO}/${REMOTE_PACKET_DIR}/${artifact}" "${LOCAL_DIR}/${artifact}"; then
        echo "[FR-030][ERROR] Failed to retrieve ${artifact}." >&2
        exit 3
    fi
done

printf '%s\n' "${TRADE_DATE}" > "${LOCAL_DIR}/trade_date.txt"

echo "[FR-030] Retrieving supporting shadow comparison artifacts when available..."
for artifact in comparison.md comparison.json; do
    scp "${VM}:${REMOTE_REPO}/${REMOTE_SHADOW_DIR}/${artifact}" "${LOCAL_SUPPORTING_DIR}/${artifact}" >/dev/null 2>&1 || true
done

echo "[FR-030] Packet copied to: ${LOCAL_DIR}"

if [[ -s "${LOCAL_SUMMARY}" ]] && command -v python3 >/dev/null 2>&1; then
    python3 - "${LOCAL_SUMMARY}" <<'PY'
import json
import sys
from pathlib import Path

summary = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
print(f"[FR-030] Packet Status: {summary.get('status', 'UNKNOWN')}")
print(f"[FR-030] Confidence Floor: {summary.get('confidence_floor', 'UNKNOWN')}")
print(f"[FR-030] Fragility Indicators: {summary.get('fragility_indicator_count', 'UNKNOWN')}")
print(f"[FR-030] Risk Flags: {summary.get('risk_flag_count', 'UNKNOWN')}")
leader = summary.get("leader") or {}
if leader:
    print(f"[FR-030] Daily Leader: {leader.get('strategy_name', leader.get('strategy_id', 'UNKNOWN'))}")
PY
else
    echo "[FR-030] Summary unavailable or python3 missing; open packet for details."
fi

if [[ "${SKIP_OPEN}" != "1" ]]; then
    if command -v open >/dev/null 2>&1; then
        if [[ -s "${LOCAL_PACKET_HTML}" ]]; then
            open "${LOCAL_PACKET_HTML}"
        elif [[ -s "${LOCAL_PACKET_MD}" ]]; then
            open -a TextEdit "${LOCAL_PACKET_MD}" || open "${LOCAL_DIR}"
        else
            echo "[FR-030][ERROR] No local packet review surface found." >&2
            exit 4
        fi
    else
        echo "[FR-030] Open manually: ${LOCAL_PACKET_HTML}"
    fi
else
    echo "[FR-030] Open skipped by ORION_SKIP_OPEN/SHADOW_SKIP_OPEN."
fi
