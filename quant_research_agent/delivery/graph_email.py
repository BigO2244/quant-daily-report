"""
DEPRECATED — Microsoft Graph API email delivery.

This module is NO LONGER THE ACTIVE DELIVERY PATH for the research digest.
Active transport: delivery/smtp_email.py (Gmail/SMTP, same credentials as
the trading workflows).

Kept for reference only. Do not import from main.py or any active code path.
"""
# fmt: off  # noqa: E402

from __future__ import annotations

import email.mime.multipart
import email.mime.text
import logging
import os

import requests

from agent.models import DigestEmail

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_SCOPE = "https://graph.microsoft.com/.default"


def _get_access_token(client_id: str, client_secret: str, tenant_id: str) -> str:
    """Obtain a bearer token via client credentials flow."""
    url = _TOKEN_URL.format(tenant_id=tenant_id)
    resp = requests.post(
        url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": _SCOPE,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _build_mime(
    from_addr: str,
    to_addrs: list[str],
    subject: str,
    plain_body: str,
    html_body: str,
) -> bytes:
    """
    Build a multipart/alternative MIME message.
    HTML is listed last so HTML-capable clients prefer it;
    plain-text clients get the first (fallback) part.
    """
    msg = email.mime.multipart.MIMEMultipart("alternative")
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.attach(email.mime.text.MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(email.mime.text.MIMEText(html_body, "html", "utf-8"))
    return msg.as_bytes()


def send(
    digest: DigestEmail,
    to_addresses: list[str] | None = None,
    dry_run: bool = False,
) -> bool:
    """
    Send a DigestEmail via Microsoft Graph.

    Reads credentials from environment:
        MS_GRAPH_CLIENT_ID
        MS_GRAPH_CLIENT_SECRET
        MS_GRAPH_TENANT_ID
        MS_GRAPH_USER_EMAIL   (the mailbox to send from/to)

    Sends as multipart/alternative MIME — HTML body with plain-text fallback.

    Args:
        digest:       The assembled DigestEmail.
        to_addresses: Override recipient list. Defaults to MS_GRAPH_USER_EMAIL.
        dry_run:      If True, log the payload but do not send.

    Returns:
        True on success, False on failure.
    """
    client_id = os.environ.get("MS_GRAPH_CLIENT_ID", "")
    client_secret = os.environ.get("MS_GRAPH_CLIENT_SECRET", "")
    tenant_id = os.environ.get("MS_GRAPH_TENANT_ID", "")
    user_email = os.environ.get("MS_GRAPH_USER_EMAIL", "")

    if not all([client_id, client_secret, tenant_id, user_email]):
        logger.error(
            "MS Graph credentials incomplete. Set MS_GRAPH_CLIENT_ID, "
            "MS_GRAPH_CLIENT_SECRET, MS_GRAPH_TENANT_ID, MS_GRAPH_USER_EMAIL."
        )
        return False

    recipients = to_addresses or [user_email]

    if dry_run:
        logger.info("[dry-run] Would send to %s — subject: %s", recipients, digest.subject)
        logger.debug("[dry-run] HTML: %d chars | plain: %d chars",
                     len(digest.html_body), len(digest.plain_body))
        return True

    try:
        token = _get_access_token(client_id, client_secret, tenant_id)
    except requests.HTTPError as exc:
        logger.error("Failed to obtain MS Graph token: %s — %s",
                     exc, exc.response.text if exc.response is not None else "")
        return False
    except requests.RequestException as exc:
        logger.error("Network error obtaining MS Graph token: %s", exc)
        return False

    # POST raw MIME to /sendMail — Graph accepts multipart/alternative this way,
    # giving email clients both a plain-text fallback and an HTML render.
    mime_bytes = _build_mime(
        from_addr=user_email,
        to_addrs=recipients,
        subject=digest.subject,
        plain_body=digest.plain_body,
        html_body=digest.html_body,
    )
    endpoint = f"{_GRAPH_BASE}/users/{user_email}/sendMail"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "text/plain",  # signals raw MIME body to Graph
    }

    try:
        resp = requests.post(endpoint, data=mime_bytes, headers=headers, timeout=30)
        resp.raise_for_status()
        logger.info("Email sent to %s (subject: %s)", recipients, digest.subject)
        return True
    except requests.HTTPError as exc:
        logger.error(
            "Graph /sendMail failed [%s]: %s",
            exc.response.status_code if exc.response is not None else "?",
            exc.response.text if exc.response is not None else str(exc),
        )
        return False
    except requests.RequestException as exc:
        logger.error("Network error sending email: %s", exc)
        return False
