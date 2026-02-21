from __future__ import annotations

import os
from typing import Mapping


def _first_non_empty(env: Mapping[str, str], *keys: str) -> str:
    for key in keys:
        value = str(env.get(key, "")).strip()
        if value:
            return value
    return ""


def resolve_email_env(env: Mapping[str, str] | None = None) -> dict[str, object]:
    """
    Resolve canonical email credentials + recipient from modern and legacy aliases.

    Precedence:
      sender: EMAIL_SENDER > SMTP_USER > REPORT_EMAIL_FROM
      password: EMAIL_APP_PASSWORD > SMTP_PASSWORD
      recipient: EMAIL_RECIPIENT > REPORT_TO_EMAIL > REPORT_EMAIL_TO
    """
    source = os.environ if env is None else env

    sender = _first_non_empty(source, "EMAIL_SENDER", "SMTP_USER", "REPORT_EMAIL_FROM")
    password = _first_non_empty(source, "EMAIL_APP_PASSWORD", "SMTP_PASSWORD")
    recipient = _first_non_empty(source, "EMAIL_RECIPIENT", "REPORT_TO_EMAIL", "REPORT_EMAIL_TO")
    smtp_host = _first_non_empty(source, "SMTP_HOST") or "smtp.gmail.com"
    smtp_port = _first_non_empty(source, "SMTP_PORT") or "587"

    missing: list[str] = []
    if not sender:
        missing.append("sender")
    if not password:
        missing.append("password")
    if not recipient:
        missing.append("recipient")

    return {
        "sender": sender,
        "password": password,
        "recipient": recipient,
        "missing": missing,
        "smtp_host": smtp_host,
        "smtp_port": smtp_port,
        # Backward-compatible env view for downstream SMTP callers.
        "legacy_env": {
            "SMTP_HOST": smtp_host,
            "SMTP_PORT": smtp_port,
            "SMTP_USER": sender,
            "SMTP_PASSWORD": password,
            "REPORT_TO_EMAIL": recipient,
        },
    }
