"""
Smoke test for Gmail/SMTP email delivery.

Sends a minimal test email using the same credentials as the trading
execution workflows.  No ingestor or Claude calls — purely validates
the SMTP auth + send path end-to-end.

Env vars (same names used by core/quant_report.py and the repo workflows):
    EMAIL_SENDER        Gmail sender address
    EMAIL_APP_PASSWORD  Gmail app password
    EMAIL_RECIPIENT     Recipient address
    SMTP_HOST           Optional override (default: smtp.gmail.com)
    SMTP_PORT           Optional override (default: 587)

Usage (from quant_research_agent/):
    python scripts/smoke_test_email.py
    python scripts/smoke_test_email.py --dry-run
    python scripts/smoke_test_email.py --to you@example.com

Exit codes:
    0  success (or dry-run)
    1  credentials missing or send failed
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.models import DigestEmail
from delivery.smtp_email import send

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("smoke_test_email")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Smoke test: send a test email via Gmail/SMTP")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate credentials are present without actually sending.")
    p.add_argument("--to", metavar="EMAIL", default=None,
                   help="Override recipient address (default: EMAIL_RECIPIENT env var).")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    now = datetime.now(tz=timezone.utc)

    digest = DigestEmail(
        subject=f"[SMOKE TEST] Quant Research Digest — {now.strftime('%Y-%m-%d %H:%M UTC')}",
        html_body=(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:24px;'>"
            "<h2 style='color:#1d4ed8;'>Quant Research Agent — Smoke Test</h2>"
            "<p>Gmail/SMTP credentials are configured correctly.</p>"
            f"<p style='color:#6b7280;font-size:12px;'>Sent at {now.strftime('%Y-%m-%d %H:%M UTC')}</p>"
            "</body></html>"
        ),
        plain_body=(
            "Quant Research Agent — Smoke Test\n\n"
            "Gmail/SMTP credentials are configured correctly.\n"
            f"Sent at {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
        ),
        items=[],
        generated=now,
    )

    if args.dry_run:
        logger.info("[dry-run] Validating SMTP credentials are present...")
        success = send(digest, to_address=args.to, dry_run=True)
    else:
        logger.info("Sending smoke test email via Gmail/SMTP...")
        success = send(digest, to_address=args.to)

    if success:
        logger.info("Smoke test PASSED.")
        return 0
    else:
        logger.error("Smoke test FAILED. Review errors above.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
