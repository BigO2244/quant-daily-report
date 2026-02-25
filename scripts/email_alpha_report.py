#!/usr/bin/env python3
import argparse
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

__EMAIL_SCRIPT_VERSION__ = "2026-02-25-b351e2a"
_REQUIRED_EMAIL_ENV_VARS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "REPORT_TO_EMAIL",
)


def _required_email_env() -> dict[str, str]:
    values: dict[str, str] = {}
    missing: list[str] = []
    for name in _REQUIRED_EMAIL_ENV_VARS:
        v = os.getenv(name)
        if v is None or str(v).strip() == "":
            missing.append(name)
            continue
        values[name] = str(v).strip()
    if missing:
        raise RuntimeError(f"Missing required env vars: {', '.join(missing)}")
    return values


def _parse_port(raw: str) -> int:
    try:
        return int(str(raw).strip())
    except ValueError as e:
        raise RuntimeError(f"Invalid SMTP_PORT value: {raw!r}") from e


def _smtp_session(host: str, port: int) -> smtplib.SMTP:
    """
    Create an SMTP session that is explicitly connected before starttls().
    This avoids 'SMTPServerDisconnected: please run connect() first' in CI.
    """
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

    env = _required_email_env()
    smtp_host = env["SMTP_HOST"]
    smtp_port = _parse_port(env["SMTP_PORT"])
    smtp_user = env["SMTP_USER"]
    smtp_pass = env["SMTP_PASSWORD"]
    email_to = env["REPORT_TO_EMAIL"]
    dry_run = os.getenv("EMAIL_DRY_RUN", "").strip().lower() in {"1", "true", "yes", "y", "on"}

    print(
        f"[EMAIL] script_version={__EMAIL_SCRIPT_VERSION__} "
        f"host={smtp_host} port={smtp_port}"
    )

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
        with _smtp_session(smtp_host, smtp_port) as s:
            if dry_run:
                print("[EMAIL] DRY_RUN complete.")
                return
            s.login(smtp_user, smtp_pass)
            s.send_message(msg)
    except Exception as e:
        raise SystemExit(f"Email send failed: {type(e).__name__}: {e}") from e

    print("[EMAIL] Sent alpha report.")


if __name__ == "__main__":
    main()
