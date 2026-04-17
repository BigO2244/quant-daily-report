"""
quant_research_agent — main entry point.

Nightly pipeline:
  1. Load strategy context
  2. Ingest from all configured sources
  3. Deduplicate against seen-IDs store
  4. Score with Claude
  5. Format digest email
  6. Persist HTML + JSON to --output-dir (always runs, before send attempt)
  7. Send via Gmail/SMTP (non-blocking — failure logs but does not abort)
  8. Mark items as seen

Email transport: delivery/smtp_email.py — Gmail-compatible SMTP, same env
vars and secrets as the trading execution workflows (EMAIL_SENDER,
EMAIL_APP_PASSWORD, EMAIL_RECIPIENT).

Usage:
    python main.py                    # full run
    python main.py --dry-run          # ingest + score + print, no email sent
    python main.py --send-test        # send a minimal test email to verify SMTP creds
    python main.py --source arxiv     # single ingestor only
    python main.py --no-dedup         # skip dedup check (re-surfaces all items)
    python main.py --output-dir /path # save digest HTML + JSON to directory
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from agent.context_loader import load_context
from agent.analyzer import analyze_items
from agent.email_formatter import build_digest
from agent.models import DigestEmail
from delivery.smtp_email import send
from ingestors import arxiv_ingestor, earnings_ingestor, macro_ingestor, news_ingestor
from store.dedup_store import DedupStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")

_STORE_PATH = Path(__file__).parent / "store" / "seen_ids.json"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Quant Research Agent — nightly digest")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Ingest and score, print digest, but do not send email or update dedup store.",
    )
    p.add_argument(
        "--send-test",
        action="store_true",
        help="Send a minimal test email via Gmail/SMTP to verify credentials, then exit.",
    )
    p.add_argument(
        "--source",
        choices=["arxiv", "earnings", "macro", "news", "all"],
        default="all",
        help="Which ingestor(s) to run (default: all)",
    )
    p.add_argument(
        "--no-dedup",
        action="store_true",
        help="Skip deduplication — surfaces all items regardless of prior runs.",
    )
    p.add_argument(
        "--output-dir",
        metavar="PATH",
        default=str(Path(__file__).parent / "outputs"),
        help="Directory to save digest HTML and JSON (default: quant_research_agent/outputs/).",
    )
    return p.parse_args()


def _send_test_email() -> int:
    """Send a minimal test email to verify Gmail/SMTP credentials. Returns exit code."""
    now = datetime.now(tz=timezone.utc)
    digest = DigestEmail(
        subject=f"[TEST] Quant Research Digest — {now.date()}",
        html_body=(
            "<!DOCTYPE html><html><body style='font-family:sans-serif;padding:24px;'>"
            "<h2 style='color:#1d4ed8;'>Quant Research Agent — Test Email</h2>"
            "<p>Gmail/SMTP credentials verified successfully.</p>"
            f"<p style='color:#6b7280;font-size:12px;'>Sent at {now.strftime('%Y-%m-%d %H:%M UTC')}</p>"
            "</body></html>"
        ),
        plain_body=(
            "Quant Research Agent — Test Email\n\n"
            "Gmail/SMTP credentials verified successfully.\n"
            f"Sent at {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
        ),
        items=[],
        generated=now,
    )
    logger.info("Sending test email via Gmail/SMTP...")
    success = send(digest)
    if success:
        logger.info("Test email sent successfully.")
        return 0
    else:
        logger.error("Test email failed. Check EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECIPIENT.")
        return 1


def _persist_outputs(digest: DigestEmail, output_dir: Path) -> None:
    """Save digest HTML and a JSON summary to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = digest.generated.strftime("%Y-%m-%d")

    html_path = output_dir / f"digest_{date_str}.html"
    html_path.write_text(digest.html_body, encoding="utf-8")
    logger.info("Digest HTML saved: %s", html_path)

    summary = {
        "date": date_str,
        "generated_at": digest.generated.isoformat(),
        "subject": digest.subject,
        "item_count": len(digest.items),
        "sources": list({i.source for i in digest.items}),
        "items": [
            {
                "title": i.title,
                "source": i.source,
                "score": i.score,
                "sentiment": i.sentiment,
                "url": i.url,
            }
            for i in digest.items
        ],
    }
    json_path = output_dir / f"digest_{date_str}.json"
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    logger.info("Digest JSON saved: %s", json_path)


def main() -> int:
    args = parse_args()

    if args.send_test:
        return _send_test_email()

    logger.info("quant_research_agent starting (dry_run=%s, source=%s)", args.dry_run, args.source)

    # --- Load context ---
    try:
        context = load_context()
    except FileNotFoundError as exc:
        logger.error("Strategy context not found: %s", exc)
        return 1

    digest_cfg = context.get("digest", {})
    max_items = digest_cfg.get("max_total_items", 20)

    # --- Ingest ---
    raw_items = []

    if args.source in ("arxiv", "all") and digest_cfg.get("include_arxiv", True):
        logger.info("Ingesting arXiv...")
        raw_items += arxiv_ingestor.fetch(context=context)

    if args.source in ("earnings", "all") and digest_cfg.get("include_earnings", True):
        logger.info("Ingesting earnings...")
        raw_items += earnings_ingestor.fetch(context=context)

    if args.source in ("macro", "all") and digest_cfg.get("include_macro", True):
        logger.info("Ingesting macro...")
        raw_items += macro_ingestor.fetch(context=context)

    if args.source in ("news", "all") and digest_cfg.get("include_news", True):
        logger.info("Ingesting news...")
        raw_items += news_ingestor.fetch(context=context)

    logger.info("Total raw items ingested: %d", len(raw_items))

    if not raw_items:
        logger.warning("No items ingested. Exiting.")
        return 0

    # --- Dedup ---
    store = DedupStore(_STORE_PATH)

    if not args.no_dedup:
        before = len(raw_items)
        raw_items = [i for i in raw_items if not store.is_seen(i.item_id)]
        logger.info("After dedup: %d items (%d filtered)", len(raw_items), before - len(raw_items))

    if not raw_items:
        logger.info("All items already seen. Nothing new to digest.")
        return 0

    # --- Analyze ---
    logger.info("Scoring %d items with Claude...", len(raw_items))
    scored = analyze_items(raw_items, dry_run=args.dry_run)
    logger.info("%d items passed threshold.", len(scored))

    if not scored:
        logger.info("No items above score threshold. Nothing to send.")
        return 0

    # --- Format digest ---
    digest = build_digest(scored, context=context, max_items=max_items)
    logger.info("Digest built: %d items, subject: %s", len(digest.items), digest.subject)

    # --- Persist outputs (always, before send, so artifacts exist even if email fails) ---
    _persist_outputs(digest, Path(args.output_dir))

    # --- Deliver ---
    if args.dry_run:
        print("\n" + "=" * 70)
        print(f"[DRY RUN] SUBJECT: {digest.subject}")
        print("=" * 70)
        print(digest.plain_body)
        print("=" * 70)
        logger.info("[dry-run] Email not sent. Dedup store not updated.")
    else:
        success = send(digest)
        if not success:
            # Non-blocking: log the failure but do not abort the run.
            # Artifacts are already saved; operator can resend manually.
            logger.warning(
                "Email delivery failed — digest artifacts still saved. "
                "Check EMAIL_SENDER / EMAIL_APP_PASSWORD / EMAIL_RECIPIENT and retry with --send-test."
            )
        else:
            # Mark all scored items as seen only after successful delivery
            store.mark_seen_bulk([i.raw_item.item_id for i in scored])
            logger.info("Marked %d items as seen in dedup store.", len(scored))

    logger.info("quant_research_agent completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
