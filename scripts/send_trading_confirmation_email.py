from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from core.email_governance import EmailEvent
from core.execution_payload import STATUS_EXECUTED, STATUS_HALTED, STATUS_SKIPPED_DUPLICATE
from core.operator_summary import write_operator_summary, load_operator_summary, format_operator_summary_log
from core.quant_report import send_email
from core.run_pointer import read_latest_run_pointer

logger = logging.getLogger(__name__)


def _load_results() -> tuple[dict, Path]:
    latest = read_latest_run_pointer()
    if not latest or not isinstance(latest, dict):
        raise RuntimeError("Missing outputs/latest_run.json; cannot resolve execution_results.json")
    run_root = Path(str(latest.get("run_root") or "").strip())
    if not run_root:
        raise RuntimeError("latest_run.json missing run_root")
    results_path = run_root / "execution_results.json"
    if not results_path.exists():
        raise RuntimeError(f"Missing execution results artifact: {results_path}")
    with results_path.open("r", encoding="utf-8") as f:
        return json.load(f), results_path


def _build_confirmation_email(results: dict, results_path: Path) -> tuple[str, str, str]:
    """
    Build confirmation email from execution results.
    
    Clearly distinguishes:
    - EXECUTED: orders submitted and processed
    - HALTED: execution could not proceed
    - SKIPPED_DUPLICATE: run already executed
    """
    trade_date = str(results.get("trade_date") or "")
    run_id = str(results.get("run_id") or "")
    status = str(results.get("status") or "UNKNOWN")
    mode = str(results.get("mode") or "UNKNOWN")
    submitted = int(results.get("submitted_count") or 0)
    accepted = int(results.get("accepted_count") or 0)
    rejected = int(results.get("rejected_count") or 0)
    halt_reason = results.get("halt_reason")
    
    # Determine status display
    if status == STATUS_SKIPPED_DUPLICATE or (submitted > 0 and halt_reason and "duplicate" in halt_reason.lower()):
        status_display = "SKIPPED_DUPLICATE"
        status_emoji = "⏭️"
    elif status == STATUS_HALTED or halt_reason:
        status_display = "HALTED"
        status_emoji = "🛑"
    elif submitted > 0:
        status_display = "EXECUTED"
        status_emoji = "✅"
    else:
        status_display = "NO_ACTION"
        status_emoji = "—"

    subject = f"Trading Confirmation {trade_date} [{status_display}]"
    
    # Build reason line
    if status_display == "SKIPPED_DUPLICATE":
        reason_line = f"Skip reason: Duplicate execution detected for run_id {run_id}"
    elif halt_reason:
        reason_line = f"Halt reason: {halt_reason}"
    else:
        reason_line = "Halt reason: none"
    
    # Build artifact reference
    artifact_ref = f"Results artifact: {results_path}"
    
    body_text = (
        f"Run ID: {run_id}\n"
        f"Trade date: {trade_date}\n"
        f"Mode: {mode}\n"
        f"Status: {status_display}\n"
        f"\n"
        f"Submitted: {submitted}\n"
        f"Accepted: {accepted}\n"
        f"Rejected: {rejected}\n"
        f"\n"
        f"{reason_line}\n"
        f"{artifact_ref}\n"
    )
    body_html = (
        "<html><body>"
        f"<h3>{status_emoji} Trading Confirmation {trade_date}</h3>"
        f"<p><b>Run ID:</b> {run_id}</p>"
        f"<p><b>Trade Date:</b> {trade_date}</p>"
        f"<p><b>Mode:</b> {mode}</p>"
        f"<p><b>Status:</b> {status_display}</p>"
        f"<hr>"
        f"<p><b>Submitted:</b> {submitted} | <b>Accepted:</b> {accepted} | <b>Rejected:</b> {rejected}</p>"
        f"<p><b>Halt/skip reason:</b> {halt_reason or 'none'}</p>"
        f"<hr>"
        f"<p style='font-size: 0.9em; color: #666;'>{artifact_ref}</p>"
        "</body></html>"
    )
    return subject, body_text, body_html


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    results, results_path = _load_results()
    
    # Load run context for operator summary updates
    latest = read_latest_run_pointer()
    run_root = Path(str(latest.get("run_root") or "").strip()) if latest else None

    event = EmailEvent(
        event_type="trading_confirmation",
        subject="",
        body_text="",
        payload=results,
    )
    if not event.should_send():
        logger.info("[TRADING_CONFIRMATION] governance suppressed")
        if run_root:
            write_operator_summary(
                run_root,
                run_id=str(results.get("run_id") or ""),
                trade_date=str(results.get("trade_date") or ""),
                mode=str(results.get("mode") or ""),
                confirmation_email_sent=False,
            )
        return

    if str(os.getenv("EMAIL_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "y", "on"}:
        logger.info("[TRADING_CONFIRMATION] dry-run enabled; skipping send")
        if run_root:
            write_operator_summary(
                run_root,
                run_id=str(results.get("run_id") or ""),
                trade_date=str(results.get("trade_date") or ""),
                mode=str(results.get("mode") or ""),
                confirmation_email_sent=False,
            )
        return

    subject, body_text, body_html = _build_confirmation_email(results, results_path)
    send_email(subject=subject, body_text=body_text, body_html=body_html)
    logger.info("[TRADING_CONFIRMATION] sent from execution results: %s", results_path)
    
    # Update operator summary with confirmation email sent
    if run_root:
        write_operator_summary(
            run_root,
            run_id=str(results.get("run_id") or ""),
            trade_date=str(results.get("trade_date") or ""),
            mode=str(results.get("mode") or ""),
            confirmation_email_sent=True,
        )
        
        # Log final operator summary
        op_summary = load_operator_summary(run_root)
        if op_summary:
            print(format_operator_summary_log(op_summary))


if __name__ == "__main__":
    main()
