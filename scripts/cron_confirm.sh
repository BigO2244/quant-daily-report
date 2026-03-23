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
if [[ -f "${REPO_ROOT}/venv/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "${REPO_ROOT}/venv/bin/activate"
else
    echo "FATAL: ${REPO_ROOT}/venv/bin/activate not found" >&2
    exit 1
fi

# --- Compute report date ---
export REPORT_DATE="${REPORT_DATE:-$(date +%F)}"

# --- Log setup ---
LOG_DIR="${REPO_ROOT}/logs"
mkdir -p "${LOG_DIR}"
LOG_FILE="${LOG_DIR}/confirm_${REPORT_DATE}.log"

send_failure_email() {
    local subject="$1"
    local body="$2"
    python3 - <<PYEOF
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

msg = MIMEMultipart()
msg["Subject"] = """${subject}"""
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = os.environ["REPORT_TO_EMAIL"]
msg.attach(MIMEText("""${body}""", "plain"))

with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
    smtp.starttls()
    smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
    smtp.sendmail(os.environ["SMTP_USER"], [os.environ["REPORT_TO_EMAIL"]], msg.as_string())
PYEOF
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

# --- Check that Phase 2 produced results ---
LATEST_RUN="${REPO_ROOT}/outputs/latest_run.json"
if [[ ! -f "${LATEST_RUN}" ]]; then
    echo "WARN: ${LATEST_RUN} not found — Phase 2 may not have run" | tee -a "${LOG_FILE}"
fi

CONFIRM_EXIT=0

# --- Step 1: Send pre-trade execution status email ---
echo "[CONFIRM] Sending execution status email..." | tee -a "${LOG_FILE}"
python3 daily_trade_execution_email.py >> "${LOG_FILE}" 2>&1 || {
    echo "WARN: execution status email failed (non-blocking)" | tee -a "${LOG_FILE}"
}

# --- Step 2: Send trading confirmation email ---
echo "[CONFIRM] Sending trading confirmation email..." | tee -a "${LOG_FILE}"
if [[ -f "${LATEST_RUN}" ]]; then
    RUN_ROOT="$(python3 -c "import json, pathlib; p=pathlib.Path('${LATEST_RUN}'); print((json.loads(p.read_text(encoding='utf-8')).get('run_root','') if p.exists() else ''))" 2>/dev/null || true)"
    if [[ -n "${RUN_ROOT}" ]] && [[ -f "${RUN_ROOT}/execution_results.json" ]]; then
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
    else
        echo "WARN: execution_results.json not found — skipping confirmation email" | tee -a "${LOG_FILE}"
    fi
else
    echo "WARN: latest_run.json missing — skipping confirmation email" | tee -a "${LOG_FILE}"
fi

# --- Step 3: Verify execution status from operator summary ---
echo "[CONFIRM] Checking execution status..." | tee -a "${LOG_FILE}"
python3 - >> "${LOG_FILE}" 2>&1 <<'PYEOF'
import json
from pathlib import Path

latest_path = Path("outputs/latest_run.json")
if not latest_path.exists():
    print("[CONFIRM] no latest_run.json — cannot verify")
    raise SystemExit(0)

latest = json.loads(latest_path.read_text(encoding="utf-8"))
run_root = latest.get("run_root", "")
status = latest.get("status", "unknown")

print(f"[CONFIRM] run_id={latest.get('run_id', 'unknown')}")
print(f"[CONFIRM] trade_date={latest.get('trade_date', 'unknown')}")
print(f"[CONFIRM] terminal_status={status}")

op_path = Path(run_root) / "operator_summary.json" if run_root else None
if op_path and op_path.exists():
    op = json.loads(op_path.read_text(encoding="utf-8"))
    print(f"[CONFIRM] submitted_count={op.get('orders_submitted_count', op.get('submitted_count', 0))}")
    print(f"[CONFIRM] operator_status={op.get('operator_execution_status', 'unknown')}")
    print(f"[CONFIRM] broker_reject_status={op.get('broker_reject_status', 'none')}")
else:
    print("[CONFIRM] operator_summary.json not found")

if status.startswith("failed"):
    print(f"[CONFIRM] WARNING: run ended with status={status}")
PYEOF

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ) exit_code=${CONFIRM_EXIT}" | tee -a "${LOG_FILE}"
exit ${CONFIRM_EXIT}
