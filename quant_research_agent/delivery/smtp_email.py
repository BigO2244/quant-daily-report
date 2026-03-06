"""
Gmail-compatible SMTP email delivery for the research digest.

Reuses the same env-var names and SMTP pattern as core/quant_report.py and
scripts/email_alpha_report.py.  No new secrets or auth model are introduced:
any mailbox already configured for the trading workflows works here unchanged.

Runtime env vars (must be injected by the caller / workflow):
    EMAIL_SENDER        sender / from address
    EMAIL_APP_PASSWORD  Gmail app password
    EMAIL_RECIPIENT     recipient address
    ENABLE_EMAIL        if "0" or "false", send() returns True without connecting
    SMTP_HOST           optional override  (default: smtp.gmail.com)
    SMTP_PORT           optional override  (default: 587)

Mirrors the credential-resolution logic in core/email_env.py without importing
it, so this module stays standalone within quant_research_agent/.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from agent.models import DigestEmail

logger = logging.getLogger(__name__)


def _resolve_creds() -> dict[str, str]:
    """Resolve SMTP credentials from canonical env var names."""
    def _get(key: str) -> str:
        return os.environ.get(key, "").strip()

    return {
        "sender":    _get("EMAIL_SENDER"),
        "password":  _get("EMAIL_APP_PASSWORD"),
        "recipient": _get("EMAIL_RECIPIENT"),
        "host":      _get("SMTP_HOST") or "smtp.gmail.com",
        "port":      _get("SMTP_PORT") or "587",
    }


def _smtp_connect(host: str, port: int) -> smtplib.SMTP:
    """
    Explicit connect + STARTTLS (matches scripts/email_alpha_report.py).
    The explicit .connect() call avoids 'SMTPServerDisconnected: please run
    connect() first' errors that occur in CI with smtplib.SMTP(host, port).
    """
    s = smtplib.SMTP(timeout=30)
    s.connect(host, port)
    s.ehlo()
    s.starttls()
    s.ehlo()
    return s


def send(
    digest: DigestEmail,
    to_address: str | None = None,
    dry_run: bool = False,
) -> bool:
    """
    Send a DigestEmail via Gmail-compatible SMTP.

    Builds a multipart/alternative message (plain-text + HTML) matching the
    structure used by core/quant_report.py::send_email().

    Args:
        digest:     The assembled DigestEmail.
        to_address: Override recipient. Defaults to EMAIL_RECIPIENT env var.
        dry_run:    If True, log intent but do not connect or send.

    Returns:
        True on success, False on any failure (never raises).
    """
    # Honour ENABLE_EMAIL gate — "0" or "false" means skip silently (not a failure).
    enable = os.environ.get("ENABLE_EMAIL", "").strip().lower()
    if enable in ("0", "false", "no", "off"):
        logger.info("Email skipped (ENABLE_EMAIL=%r). Digest artifacts still saved.", enable)
        return True

    creds = _resolve_creds()
    missing = [k for k in ("sender", "password", "recipient") if not creds[k]]
    if missing:
        logger.error(
            "SMTP credentials incomplete — missing: %s. "
            "Set EMAIL_SENDER, EMAIL_APP_PASSWORD, EMAIL_RECIPIENT.",
            missing,
        )
        return False

    recipient = to_address or creds["recipient"]

    if dry_run:
        logger.info("[dry-run] Would send to %s — subject: %s", recipient, digest.subject)
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = digest.subject
    msg["From"] = creds["sender"]
    msg["To"] = recipient
    msg.attach(MIMEText(digest.plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(digest.html_body, "html", "utf-8"))

    host = creds["host"]
    port = int(creds["port"])

    try:
        with _smtp_connect(host, port) as server:
            server.login(creds["sender"], creds["password"])
            refused = server.sendmail(creds["sender"], [recipient], msg.as_string())
        if refused:
            logger.error("SMTP refused recipients: %s", refused)
            return False
        logger.info("Email sent to %s (subject: %s)", recipient, digest.subject)
        return True
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("SMTP authentication failed — check EMAIL_SENDER / EMAIL_APP_PASSWORD: %s", exc)
        return False
    except smtplib.SMTPException as exc:
        logger.error("SMTP error: %s", exc)
        return False
    except OSError as exc:
        logger.error("Network error sending email: %s", exc)
        return False
