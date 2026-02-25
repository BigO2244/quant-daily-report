#!/usr/bin/env python3
import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path


def _req_env(name: str) -> str:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        raise RuntimeError(f"Missing required env var: {name}")
    return str(v).strip()


def _env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or str(v).strip() == "":
        return default
    try:
        return int(str(v).strip())
    except ValueError as e:
        raise RuntimeError(f"Invalid int for env var {name}: {v!r}") from e


def _smtp_session() -> smtplib.SMTP:
    """
    Create an SMTP session that is explicitly connected before starttls().
    This avoids 'SMTPServerDisconnected: please run connect() first' in CI.
    """
    host = _req_env("SMTP_HOST")
    port = _env_int("SMTP_PORT", 587)

    s = smtplib.SMTP(timeout=30)
    s.connect(host, port)  # <-- critical: explicit connect
    s.ehlo()
    s.starttls()
    s.ehlo()
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report-dir", default="outputs/alpha_report")
    ap.add_argument("--subject-prefix", default="[ALPHA]")
    args = ap.parse_args()

    smtp_user = _req_env("SMTP_USER")
    smtp_pass = _req_env("SMTP_PASSWORD")
    email_to = _req_env("REPORT_TO_EMAIL")

    report_dir = Path(args.report_dir)
    html_path = report_dir / "alpha_report.html"
    if not html_path.exists():
        raise SystemExit(f"Missing report HTML: {html_path}")

    html = html_path.read_text(encoding="utf-8")

    msg = EmailMessage()
    msg["From"] = smtp_user
    msg["To"] = email_to
    msg["Subject"] = f"{args.subject_prefix} Alpha Engine Report"
    msg.set_content("Alpha Engine Report (HTML). If you cannot view HTML, open the attached file.")
    msg.add_alternative(html, subtype="html")

    # Attach PNGs if present
    for fname in ("equity_curve.png", "drawdown.png", "breaker_timeline.png"):
        p = report_dir / fname
        if p.exists():
            msg.add_attachment(p.read_bytes(), maintype="image", subtype="png", filename=fname)

    # Attach the HTML as a file too
    msg.add_attachment(html.encode("utf-8"), maintype="text", subtype="html", filename="alpha_report.html")

    try:
        with _smtp_session() as s:
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
    except Exception as e:
        raise SystemExit(f"Email send failed: {type(e).__name__}: {e}") from e

    print("[EMAIL] Sent alpha report.")


if __name__ == "__main__":
    main()