#!/usr/bin/env bash
# Phase 3: Confirmation — 10:00 AM ET weekdays
# Verifies execution results and sends confirmation email.
set -euo pipefail

# --- Resolve repo root ---
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# --- Timezone ---
export TZ="America/New_York"

# --- Load credentials and config ---
if [[ -f "${REPO_ROOT}/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/.env"
    set +a
else
    echo "FATAL: ${REPO_ROOT}/.env not found" >&2
    exit 1
fi

# --- Activate venv ---
# shellcheck disable=SC1091
source "${REPO_ROOT}/scripts/runtime_env.sh"
activate_runtime_venv "${REPO_ROOT}" || exit 1

# --- Compute report date ---
export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"
export MODE="paper"
export TRADING_MODE="paper"
export ALPACA_PAPER="1"
export ALPACA_BASE_URL="https://paper-api.alpaca.markets"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/confirm_${REPORT_DATE}.log"

send_failure_email() {
    local subject="$1"
    local body="$2"
    FAILURE_EMAIL_SUBJECT="${subject}" FAILURE_EMAIL_BODY="${body}" python3 - <<'PYEOF'
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

subject = os.environ["FAILURE_EMAIL_SUBJECT"]
body = os.environ["FAILURE_EMAIL_BODY"]

msg = MIMEMultipart()
msg["Subject"] = subject
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = os.environ["REPORT_TO_EMAIL"]
msg.attach(MIMEText(body, "plain"))

with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
    smtp.starttls()
    smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
    smtp.sendmail(os.environ["SMTP_USER"], [os.environ["REPORT_TO_EMAIL"]], msg.as_string())
PYEOF
}

send_pointer_alert() {
    local subject="$1"
    local body="$2"
    send_failure_email "${subject}" "${body}" >> "${LOG_FILE}" 2>&1 || {
        echo "WARN: failure alert email send failed (non-blocking)" | tee -a "${LOG_FILE}"
    }
}

# --- Enable email sending for confirmation phase ---
export EMAIL_PRETRADE=1
export EMAIL_TRADING_CONFIRMATION=1
export EMAIL_INLINE_REPORTS=0
export EMAIL_MARKET_CONDITIONS=0
export EMAIL_INTERNAL_DEBUG=0

echo "=== PHASE 3: CONFIRMATION ===" | tee -a "${LOG_FILE}"
echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "${LOG_FILE}"
echo "report_date=${REPORT_DATE}" | tee -a "${LOG_FILE}"
echo "mode=${MODE} trading_mode=${TRADING_MODE} alpaca_paper=${ALPACA_PAPER}" | tee -a "${LOG_FILE}"

resolve_execution_pointer_field() {
    local field="$1"
    EXECUTION_POINTER_FIELD="${field}" python3 - <<'PYEOF'
import json
import os
import sys
from core.run_pointer import read_trade_stage_pointer

trade_date = str(os.getenv("REPORT_DATE", "")).strip()
field = str(os.getenv("EXECUTION_POINTER_FIELD", "")).strip()
try:
    pointer = read_trade_stage_pointer(trade_date, "execution")
except json.JSONDecodeError as exc:
    print(f"[CONFIRM] malformed execution workflow pointer for {trade_date}: {exc}", file=sys.stderr)
    raise
print((pointer or {}).get(field, ""))
PYEOF
}

# --- Check that Phase 2 produced execution-stage results ---
EXECUTION_RUN_ROOT="$(resolve_execution_pointer_field run_root 2>>"${LOG_FILE}" || true)"
EXECUTION_POINTER_STATUS="$(resolve_execution_pointer_field status 2>>"${LOG_FILE}" || true)"
POINTER_CONFIRMABLE=1
if [[ -z "${EXECUTION_RUN_ROOT}" || -z "${EXECUTION_POINTER_STATUS}" ]]; then
    POINTER_CONFIRMABLE=0
    echo "WARN: execution workflow pointer missing for ${REPORT_DATE} — Phase 2 may not have completed" | tee -a "${LOG_FILE}"
    send_pointer_alert \
        "❌ [Alpha Stack] Execution pointer missing — ${REPORT_DATE}" \
        "Phase 3 confirmation skipped because no execution workflow pointer was found for ${REPORT_DATE}.

Repo root: ${REPO_ROOT}
Log file: ${LOG_FILE}

Check:
- outputs/workflow/${REPORT_DATE}/execution.json
- logs/execute_${REPORT_DATE}.log
- outputs/latest_run.json"
fi
if [[ -n "${EXECUTION_POINTER_STATUS}" && "${EXECUTION_POINTER_STATUS,,}" != "success" && "${EXECUTION_POINTER_STATUS,,}" != "no_action" ]]; then
    POINTER_CONFIRMABLE=0
    echo "WARN: execution workflow pointer is not a confirmable success/no_action state for ${REPORT_DATE}" | tee -a "${LOG_FILE}"
    send_pointer_alert \
        "⚠️ [Alpha Stack] Execution not confirmable — ${REPORT_DATE}" \
        "Phase 3 confirmation skipped because the execution workflow pointer for ${REPORT_DATE} is not a terminal success/no_action state.

run_root: ${EXECUTION_RUN_ROOT:-unknown}
status: ${EXECUTION_POINTER_STATUS}
log file: ${LOG_FILE}

Inspect:
- outputs/workflow/${REPORT_DATE}/execution.json
- logs/execute_${REPORT_DATE}.log
- outputs/latest_run.json"
fi
CONFIRM_EXIT=0
if [[ "${POINTER_CONFIRMABLE}" != "1" ]]; then
    CONFIRM_EXIT=1
fi
if [[ "${POINTER_CONFIRMABLE}" == "1" ]]; then
    if ! REPORT_DATE="${REPORT_DATE}" "${PYTHON_BIN:-python3}" - <<'PYEOF' >> "${LOG_FILE}" 2>&1
import json
import os
from pathlib import Path
from core.run_pointer import read_trade_stage_pointer

trade_date = os.environ["REPORT_DATE"]
pointer = read_trade_stage_pointer(trade_date, "execution") or {}
run_root = Path(str(pointer.get("run_root") or ""))
if str(pointer.get("trade_date") or "") != trade_date:
    raise SystemExit("pointer trade_date mismatch")
if str(pointer.get("mode") or "").upper() != "PAPER":
    raise SystemExit("pointer mode mismatch")
if not run_root.is_dir():
    raise SystemExit("pointer run_root missing")
operator = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
integrity = json.loads((run_root / "audit" / "execution_integrity.json").read_text(encoding="utf-8"))
pointer_status = str(pointer.get("status") or "").lower()
terminal = str(operator.get("terminal_status") or "").upper()
outcome = str(operator.get("terminal_outcome") or "").upper()
recon = str(operator.get("reconciliation_status") or "").upper()
if pointer_status == "success":
    if terminal != "SUBMITTED" or outcome != "RECONCILED_SUCCESS" or recon not in {"CLEAN", "RECONCILED"}:
        raise SystemExit("pointer success contradicts operator terminal truth")
elif pointer_status == "no_action":
    if terminal != "AUTHORIZED_NO_TRADE" or outcome != "AUTHORIZED_NO_TRADE":
        raise SystemExit("pointer no_action contradicts operator terminal truth")
else:
    raise SystemExit("pointer status is not confirmable")
if str(integrity.get("status") or "").upper() != "OK":
    raise SystemExit("execution integrity is not OK")
PYEOF
    then
        POINTER_CONFIRMABLE=0
        CONFIRM_EXIT=1
        echo "WARN: canonical pointer and run-local terminal evidence disagree — suppressing confirmation" | tee -a "${LOG_FILE}"
    fi
fi

# --- Step 1: Send pre-trade execution status email ---
if [[ "${POINTER_CONFIRMABLE}" == "1" ]]; then
    echo "[CONFIRM] Sending execution status email..." | tee -a "${LOG_FILE}"
    python3 daily_trade_execution_email.py >> "${LOG_FILE}" 2>&1 || {
        echo "WARN: execution status email failed (non-blocking)" | tee -a "${LOG_FILE}"
    }
else
    echo "WARN: execution status email suppressed because terminal evidence is not confirmable" | tee -a "${LOG_FILE}"
fi

# --- Step 2: Send trading confirmation email ---
echo "[CONFIRM] Sending trading confirmation email..." | tee -a "${LOG_FILE}"
if [[ "${POINTER_CONFIRMABLE}" != "1" ]]; then
    CONFIRM_EXIT=1
    echo "WARN: execution pointer is not confirmable — skipping normal confirmation email" | tee -a "${LOG_FILE}"
else
    python3 -m scripts.send_trading_confirmation_email >> "${LOG_FILE}" 2>&1 || {
        echo "WARN: trading confirmation email failed (non-blocking)" | tee -a "${LOG_FILE}"
        CONFIRM_EXIT=1
        TAIL="$(tail -20 "${LOG_FILE}")"
        send_failure_email \
            "❌ [Alpha Stack] Trade confirmation FAILED — ${REPORT_DATE}" \
            "Phase 3 confirmation FAILED at $(date).

Last 20 lines of log:
${TAIL}" >> "${LOG_FILE}" 2>&1 || {
                echo "WARN: failure alert email send failed (non-blocking)" | tee -a "${LOG_FILE}"
            }
    }
fi

# --- Step 3: Verify execution status from execution-stage operator summary ---
echo "[CONFIRM] Checking execution status..." | tee -a "${LOG_FILE}"
python3 - >> "${LOG_FILE}" 2>&1 <<'PYEOF' || CONFIRM_EXIT=1
import json
import os
from pathlib import Path
from core.run_pointer import read_trade_stage_pointer

trade_date = str(os.getenv("REPORT_DATE", "")).strip()
pointer = read_trade_stage_pointer(trade_date, "execution")
if not pointer:
    print("[CONFIRM] no execution workflow pointer — cannot verify")
    raise SystemExit(1)

run_root = pointer.get("run_root", "")
status = pointer.get("status", "unknown")
if str(pointer.get("trade_date") or "") != trade_date:
    print("[CONFIRM] execution pointer trade_date mismatch")
    raise SystemExit(1)
if str(pointer.get("mode") or "").upper() != "PAPER":
    print("[CONFIRM] execution pointer mode is not PAPER")
    raise SystemExit(1)

print(f"[CONFIRM] run_id={pointer.get('run_id', 'unknown')}")
print(f"[CONFIRM] trade_date={pointer.get('trade_date', 'unknown')}")
print(f"[CONFIRM] terminal_status={status}")

op_path = Path(run_root) / "operator_summary.json" if run_root else None
if op_path and op_path.exists():
    op = json.loads(op_path.read_text(encoding="utf-8"))
    print(f"[CONFIRM] submitted_count={op.get('orders_submitted_count', op.get('submitted_count', 0))}")
    print(f"[CONFIRM] operator_status={op.get('operator_execution_status', 'unknown')}")
    print(f"[CONFIRM] broker_reject_status={op.get('broker_reject_status', 'none')}")
else:
    print("[CONFIRM] operator_summary.json not found")

if str(status).lower() not in {"success", "no_action"}:
    print(f"[CONFIRM] WARNING: run is not confirmable status={status}")
    raise SystemExit(1)
PYEOF

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${CONFIRM_EXIT}" | tee -a "${LOG_FILE}"
exit ${CONFIRM_EXIT}
