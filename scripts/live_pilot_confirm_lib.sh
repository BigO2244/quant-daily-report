#!/usr/bin/env bash
# Shared LIVE_PILOT confirmation sweep + fail-loud alert.
#
# Sourced by BOTH the scheduled confirm cron (backstop sweep) and the execute
# cron (execute-completion hook). It confirms EVERY terminal run for the trade
# date exactly once (dedupe via scripts/live_pilot_confirm_discover.py's JSONL
# ledger) and — unlike the old silent lane — raises a LOUD email alert when a
# scheduled sweep finds no run at all or when a confirmation email fails.
#
# Callers must have already:
#   * set REPORT_DATE, REPO_ROOT, PYTHON_BIN
#   * sourced .env (for SMTP_* + REPORT_TO_EMAIL) and activated the venv
#   * set MODE/TRADING_MODE=live_pilot and the live Alpaca endpoint
#
# Functions:
#   live_pilot_confirm_alert <subject> <body>
#   live_pilot_confirm_sweep [runs_root] [ledger_path]   -> exit 0 clean, 1 problem

live_pilot_confirm_alert() {
    local subject="$1"
    local body="$2"
    if [[ -z "${SMTP_HOST:-}" || -z "${SMTP_USER:-}" || -z "${REPORT_TO_EMAIL:-}" ]]; then
        echo "ERROR: cannot send confirm alert — SMTP env not configured (SMTP_HOST/SMTP_USER/REPORT_TO_EMAIL)" >&2
        echo "ALERT (unsent): ${subject}" >&2
        return 1
    fi
    LIVE_PILOT_ALERT_SUBJECT="${subject}" LIVE_PILOT_ALERT_BODY="${body}" "${PYTHON_BIN}" - <<'PYEOF'
import os
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

msg = MIMEMultipart()
msg["Subject"] = os.environ["LIVE_PILOT_ALERT_SUBJECT"]
msg["From"] = os.environ["SMTP_USER"]
msg["To"] = os.environ["REPORT_TO_EMAIL"]
msg.attach(MIMEText(os.environ["LIVE_PILOT_ALERT_BODY"], "plain"))

try:
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
        smtp.sendmail(os.environ["SMTP_USER"], [os.environ["REPORT_TO_EMAIL"]], msg.as_string())
except Exception as exc:  # noqa: BLE001 - alerting must not raise
    print(f"WARN: confirm alert email send failed: {exc}", file=sys.stderr)
    sys.exit(1)
PYEOF
}

live_pilot_confirm_sweep() {
    local runs_root="${1:-outputs/live_pilot/runs}"
    local ledger="${2:-outputs/live_pilot/state/confirm_sent_ledger.jsonl}"
    local discover_mod="scripts.live_pilot_confirm_discover"

    local summary
    summary="$(
        "${PYTHON_BIN}" -m "${discover_mod}" discover \
            --trade-date "${REPORT_DATE}" \
            --runs-root "${runs_root}" \
            --ledger "${ledger}" \
            --emit-summary
    )" || {
        echo "ERROR: confirm discovery failed for ${REPORT_DATE}"
        live_pilot_confirm_alert \
            "❌ [LIVE_PILOT] Confirm discovery FAILED — ${REPORT_DATE}" \
            "scripts.live_pilot_confirm_discover exited non-zero for ${REPORT_DATE}. Confirmation may be missing. Inspect ${runs_root} and logs." || true
        return 1
    }

    local has_any pending_count terminal_count
    has_any="$(printf '%s\n' "${summary}" | sed -n 's/^has_any_run=//p')"
    terminal_count="$(printf '%s\n' "${summary}" | sed -n 's/^terminal_count=//p')"
    pending_count="$(printf '%s\n' "${summary}" | sed -n 's/^pending_count=//p')"
    echo "confirm_sweep: has_any_run=${has_any} terminal_count=${terminal_count} pending_count=${pending_count}"

    if [[ "${has_any}" != "1" ]]; then
        # FAIL LOUD: a scheduled sweep found NO run for a trade date. This is the
        # exact condition the old live lane exited 0 on with only a log WARN.
        echo "ALERT: no LIVE_PILOT run found for ${REPORT_DATE}; nothing to confirm."
        live_pilot_confirm_alert \
            "❌ [LIVE_PILOT] No execution run to confirm — ${REPORT_DATE}" \
            "The LIVE_PILOT confirmation sweep found NO run under ${runs_root} for ${REPORT_DATE}.

This means the execute lane may have died before writing artifacts, or did not run.
An armed run that produced no confirmable artifact must be investigated immediately.

Check:
- ${runs_root}
- logs/live_pilot_execute_${REPORT_DATE}.log
- outputs/workflow/${REPORT_DATE}/live_pilot_execution.json" || true
        return 1
    fi

    if [[ "${pending_count}" == "0" ]]; then
        echo "confirm_sweep: all ${terminal_count} terminal run(s) already confirmed; nothing to send."
        return 0
    fi

    local pending_lines
    pending_lines="$(
        "${PYTHON_BIN}" -m "${discover_mod}" discover \
            --trade-date "${REPORT_DATE}" \
            --runs-root "${runs_root}" \
            --ledger "${ledger}" \
            --emit-pending
    )"

    local sweep_rc=0
    local run_id run_root results_path status
    while IFS=$'\t' read -r run_id run_root results_path status; do
        [[ -z "${run_id}" ]] && continue
        echo "confirm_sweep: confirming run_id=${run_id} status=${status}"

        export EMAIL_PRETRADE=0
        export EMAIL_TRADING_CONFIRMATION=1
        export EMAIL_INLINE_REPORTS=0
        export EMAIL_MARKET_CONDITIONS=0
        export EMAIL_INTERNAL_DEBUG=0
        export TRADING_CONFIRMATION_RESULTS_PATH="${results_path}"
        export TRADING_CONFIRMATION_RUN_ROOT="${run_root}"

        if "${PYTHON_BIN}" -m scripts.send_trading_confirmation_email; then
            "${PYTHON_BIN}" -m "${discover_mod}" mark-sent \
                --run-id "${run_id}" \
                --run-root "${run_root}" \
                --trade-date "${REPORT_DATE}" \
                --status "${status}" \
                --ledger "${ledger}" || {
                    echo "WARN: failed to record ${run_id} in dedupe ledger (may re-send next sweep)"
                }
        else
            sweep_rc=1
            echo "ERROR: confirmation email failed for run_id=${run_id}"
            live_pilot_confirm_alert \
                "❌ [LIVE_PILOT] Confirmation email FAILED — ${REPORT_DATE}" \
                "The LIVE_PILOT confirmation email failed to send for run ${run_id} (status=${status}).

results_path: ${results_path}
run_root: ${run_root}

This run is NOT recorded as confirmed and will be retried on the next sweep. Investigate the send failure." || true
        fi
    done <<< "${pending_lines}"

    return ${sweep_rc}
}
