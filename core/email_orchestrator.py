"""Email orchestrator for consolidated PRE/POST/ALERT email delivery.

This module consolidates email outputs into three distinct email types:
1. PRE email (06:30 ET): "PROPOSED TRADES — YYYY-MM-DD (MODE)" - Planned trades before market open
2. POST email (09:35 ET): "EXECUTION REPORT — YYYY-MM-DD (MODE)" - Execution results + reconciliation
3. ALERT email (conditional): "ALERT — Quant Daily pipeline failed — YYYY-MM-DD (STEP)" - Failures only

Idempotency: Tracks sent emails per trade_date|mode|email_type|run_id to prevent duplicates.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import traceback
from pathlib import Path
from typing import Any, Literal

from core.quant_report import send_email
from paper.build_execution_email import build_execution_email_html, build_execution_email_text

logger = logging.getLogger(__name__)

EmailType = Literal["PRE", "POST", "ALERT"]


def _get_idempotency_key(
    trade_date: str,
    mode: str,
    email_type: EmailType,
    run_id: str | None,
) -> str:
    """Generate a deterministic idempotency key for email deduplication."""
    return f"{trade_date}|{mode.upper()}|{email_type}|{run_id or 'default'}"


def _get_email_marker_path(run_root: Path | str, email_type: EmailType) -> Path:
    """Get the path to the idempotency marker for a given email type."""
    run_root_path = Path(run_root) if isinstance(run_root, str) else run_root
    marker_dir = run_root_path / "email_sent"
    marker_dir.mkdir(parents=True, exist_ok=True)
    return marker_dir / f"{email_type.lower()}.json"


def _check_email_sent(run_root: Path | str, email_type: EmailType) -> bool:
    """Check if an email of the given type has already been sent for this run."""
    marker_path = _get_email_marker_path(run_root, email_type)
    return marker_path.exists()


def _mark_email_sent(
    run_root: Path | str,
    email_type: EmailType,
    trade_date: str,
    mode: str,
    run_id: str | None,
    subject: str,
) -> None:
    """Mark that an email of the given type has been sent for this run."""
    marker_path = _get_email_marker_path(run_root, email_type)
    payload = {
        "email_type": email_type,
        "trade_date": trade_date,
        "mode": mode,
        "run_id": run_id,
        "subject": subject,
        "sent_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "idempotency_key": _get_idempotency_key(trade_date, mode, email_type, run_id),
    }
    marker_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "[EMAIL_ORCHESTRATOR] Marked email sent: type=%s path=%s",
        email_type,
        marker_path,
    )


def _redact_secrets(text: str) -> str:
    """Redact any potential secrets from error text."""
    # Basic redaction - look for common secret patterns
    redacted = text
    for env_var in ["API_KEY", "SECRET", "TOKEN", "PASSWORD", "ALPACA"]:
        if env_var in redacted:
            # Replace any word containing these terms with [REDACTED]
            import re
            pattern = r'\b\S*' + env_var + r'\S*\b'
            redacted = re.sub(pattern, '[REDACTED]', redacted, flags=re.IGNORECASE)
    return redacted


def send_pre_email(
    execution_payload: dict[str, Any],
    run_root: Path | str,
    trade_date: str,
    mode: str,
    run_id: str | None = None,
    force: bool = False,
) -> bool:
    """Send PRE email (proposed trades before market open).
    
    Args:
        execution_payload: The execution email payload with planned trades
        run_root: Path to the run archive directory
        trade_date: Trade date string (YYYY-MM-DD)
        mode: Trading mode (e.g., SHADOW, ALPACA, PAPER)
        run_id: Optional run identifier
        force: If True, bypass idempotency check
        
    Returns:
        True if email was sent, False if skipped (already sent)
    """
    email_type = "PRE"
    
    # Check idempotency
    if not force and _check_email_sent(run_root, email_type):
        logger.info(
            "[EMAIL_ORCHESTRATOR] PRE email already sent for trade_date=%s mode=%s run_id=%s",
            trade_date,
            mode,
            run_id,
        )
        return False
    
    # Build subject and body
    exec_subject, exec_body = build_execution_email_text(execution_payload)
    _, exec_body_html = build_execution_email_html(execution_payload)
    
    # Override subject to PRE format
    subject = f"PROPOSED TRADES — {trade_date} ({mode.upper()})"
    
    # Send email
    try:
        send_email(subject=subject, body_html=exec_body_html, body_text=exec_body)
        logger.info("[EMAIL_ORCHESTRATOR] Sent PRE email: %s", subject)
        
        # Mark as sent
        _mark_email_sent(run_root, email_type, trade_date, mode, run_id, subject)
        return True
    except Exception as e:
        logger.error("[EMAIL_ORCHESTRATOR] Failed to send PRE email: %s", e)
        raise


def send_post_email(
    execution_payload: dict[str, Any],
    paper_summary: dict[str, Any] | None,
    run_root: Path | str,
    trade_date: str,
    mode: str,
    run_id: str | None = None,
    force: bool = False,
) -> bool:
    """Send POST email (execution results + reconciliation).
    
    Args:
        execution_payload: The execution email payload with actual trades
        paper_summary: Paper trading summary with fills, reconciliation, etc.
        run_root: Path to the run archive directory
        trade_date: Trade date string (YYYY-MM-DD)
        mode: Trading mode (e.g., SHADOW, ALPACA, PAPER)
        run_id: Optional run identifier
        force: If True, bypass idempotency check
        
    Returns:
        True if email was sent, False if skipped (already sent)
        
    Raises:
        ValueError: If required artifacts are missing for POST email
    """
    email_type = "POST"
    
    # Check idempotency
    if not force and _check_email_sent(run_root, email_type):
        logger.info(
            "[EMAIL_ORCHESTRATOR] POST email already sent for trade_date=%s mode=%s run_id=%s",
            trade_date,
            mode,
            run_id,
        )
        return False
    
    # Validate required inputs for POST email
    if paper_summary is None:
        raise ValueError("POST email requires paper_summary (execution results missing)")
    
    # Check if execution summary is available
    if not paper_summary.get("fills") and not paper_summary.get("execution_trades"):
        # This might be okay if no trades were planned, but log it
        logger.warning("[EMAIL_ORCHESTRATOR] POST email: no fills or execution_trades found")
    
    # Build execution report content
    # TODO: Create a dedicated POST email builder that includes reconciliation
    # For now, use the execution email builder but with POST context
    exec_subject, exec_body = build_execution_email_text(execution_payload)
    _, exec_body_html = build_execution_email_html(execution_payload)
    
    # Override subject to POST format
    subject = f"EXECUTION REPORT — {trade_date} ({mode.upper()})"
    
    # Enhance body with reconciliation info if available
    recon = paper_summary.get("reconciliation", {})
    if recon:
        recon_text = "\n\n=== RECONCILIATION ===\n"
        recon_text += f"Broker vs Ledger Match: {recon.get('status', 'UNKNOWN')}\n"
        recon_text += f"Target Cash %: {recon.get('target_cash_pct', 'n/a')}\n"
        recon_text += f"Achieved Cash %: {recon.get('achieved_cash_pct', 'n/a')}\n"
        recon_text += f"Invariant Failures: {recon.get('invariant_failures', 'n/a')}\n"
        exec_body = exec_body + recon_text
    
    # Send email
    try:
        send_email(subject=subject, body_html=exec_body_html, body_text=exec_body)
        logger.info("[EMAIL_ORCHESTRATOR] Sent POST email: %s", subject)
        
        # Mark as sent
        _mark_email_sent(run_root, email_type, trade_date, mode, run_id, subject)
        return True
    except Exception as e:
        logger.error("[EMAIL_ORCHESTRATOR] Failed to send POST email: %s", e)
        raise


def send_alert_email(
    error_summary: str,
    trade_date: str,
    mode: str,
    step: Literal["PRE", "POST"],
    run_root: Path | str,
    run_id: str | None = None,
    workflow_url: str | None = None,
    force: bool = False,
) -> bool:
    """Send ALERT email when pipeline fails or required artifacts are missing.
    
    Args:
        error_summary: Concise error summary (will be redacted of secrets)
        trade_date: Trade date string (YYYY-MM-DD)
        mode: Trading mode (e.g., SHADOW, ALPACA, PAPER)
        step: Which step failed (PRE or POST)
        run_root: Path to the run archive directory
        run_id: Optional run identifier
        workflow_url: Optional workflow run URL for debugging
        force: If True, bypass idempotency check
        
    Returns:
        True if email was sent, False if skipped (already sent)
    """
    email_type = "ALERT"
    
    # Check idempotency
    if not force and _check_email_sent(run_root, email_type):
        logger.info(
            "[EMAIL_ORCHESTRATOR] ALERT email already sent for trade_date=%s mode=%s run_id=%s",
            trade_date,
            mode,
            run_id,
        )
        return False
    
    subject = f"ALERT — Quant Daily pipeline failed — {trade_date} ({step})"
    
    # Redact secrets from error summary
    safe_error = _redact_secrets(error_summary)
    
    # Build alert body
    body_lines = [
        f"ALERT: Quant Daily pipeline failure detected",
        f"",
        f"Trade Date: {trade_date}",
        f"Mode: {mode.upper()}",
        f"Step Failed: {step}",
        f"Run ID: {run_id or 'n/a'}",
        f"",
        f"ERROR SUMMARY:",
        f"{safe_error[:1000]}",  # Limit to first 1000 chars
        f"",
        f"INVESTIGATION:",
    ]
    
    if run_root:
        body_lines.append(f"  - Run Archive: {run_root}")
    
    if workflow_url:
        body_lines.append(f"  - Workflow URL: {workflow_url}")
    else:
        body_lines.append(f"  - Workflow URL: Check GitHub Actions for latest run")
    
    body_lines.append(f"")
    
    if step == "PRE":
        body_lines.append(f"IMPACT: No trades were planned/sent (PRE step failed)")
    elif step == "POST":
        body_lines.append(f"IMPACT: Trades may have executed; verify broker now")
    
    body_text = "\n".join(body_lines)
    
    # Send email
    try:
        send_email(subject=subject, body_html=None, body_text=body_text)
        logger.info("[EMAIL_ORCHESTRATOR] Sent ALERT email: %s", subject)
        
        # Mark as sent
        _mark_email_sent(run_root, email_type, trade_date, mode, run_id, subject)
        return True
    except Exception as e:
        logger.error("[EMAIL_ORCHESTRATOR] Failed to send ALERT email: %s", e)
        # Don't re-raise - we don't want alert email failures to cascade
        return False


def orchestrate_email(
    workflow_step: Literal["PRE", "POST"],
    execution_payload: dict[str, Any],
    paper_summary: dict[str, Any] | None,
    run_root: Path | str,
    trade_date: str,
    mode: str,
    run_id: str | None = None,
    workflow_url: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Main orchestration entry point for email sending.
    
    Determines which email to send based on workflow_step and handles errors gracefully.
    
    Args:
        workflow_step: Which workflow is running (PRE or POST)
        execution_payload: The execution email payload
        paper_summary: Paper trading summary (required for POST)
        run_root: Path to the run archive directory
        trade_date: Trade date string (YYYY-MM-DD)
        mode: Trading mode (e.g., SHADOW, ALPACA, PAPER)
        run_id: Optional run identifier
        workflow_url: Optional workflow run URL
        force: If True, bypass idempotency checks
        
    Returns:
        Dict with status and metadata: {
            "sent": bool,
            "email_type": EmailType,
            "skipped": bool (if already sent),
            "error": str | None,
        }
    """
    result: dict[str, Any] = {
        "sent": False,
        "email_type": workflow_step,
        "skipped": False,
        "error": None,
    }
    
    try:
        if workflow_step == "PRE":
            # PRE workflow: send proposed trades email
            sent = send_pre_email(
                execution_payload=execution_payload,
                run_root=run_root,
                trade_date=trade_date,
                mode=mode,
                run_id=run_id,
                force=force,
            )
            result["sent"] = sent
            result["skipped"] = not sent
            
        elif workflow_step == "POST":
            # POST workflow: send execution report email
            try:
                sent = send_post_email(
                    execution_payload=execution_payload,
                    paper_summary=paper_summary,
                    run_root=run_root,
                    trade_date=trade_date,
                    mode=mode,
                    run_id=run_id,
                    force=force,
                )
                result["sent"] = sent
                result["skipped"] = not sent
            except ValueError as e:
                # Missing required artifacts for POST - send ALERT
                logger.error("[EMAIL_ORCHESTRATOR] POST email missing artifacts: %s", e)
                send_alert_email(
                    error_summary=f"POST email generation failed: {e}",
                    trade_date=trade_date,
                    mode=mode,
                    step="POST",
                    run_root=run_root,
                    run_id=run_id,
                    workflow_url=workflow_url,
                    force=force,
                )
                result["email_type"] = "ALERT"
                result["error"] = str(e)
        
    except Exception as e:
        # Unexpected error - send ALERT
        logger.error(
            "[EMAIL_ORCHESTRATOR] Email orchestration failed: %s\n%s",
            e,
            traceback.format_exc(),
        )
        send_alert_email(
            error_summary=f"Unexpected error in email orchestration: {e}\n{traceback.format_exc()[:500]}",
            trade_date=trade_date,
            mode=mode,
            step=workflow_step,
            run_root=run_root,
            run_id=run_id,
            workflow_url=workflow_url,
            force=force,
        )
        result["email_type"] = "ALERT"
        result["error"] = str(e)
    
    return result
