"""
Manual test run script.

Runs a small dry-run end-to-end: ingest a small sample from each source,
score with Claude (or stub if --no-claude), and print the digest to stdout.
Does NOT send email. Does NOT write to the dedup store.
Writes an HTML preview to outputs/digest_preview.html (disable with --no-preview).

Usage:
    python scripts/test_run.py
    python scripts/test_run.py --no-claude     # skip real API call, use stubs
    python scripts/test_run.py --source arxiv  # single ingestor only
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Allow running from scripts/ or from project root
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

from agent.context_loader import load_context
from agent.analyzer import analyze_items
from agent.email_formatter import build_digest
from ingestors import arxiv_ingestor, earnings_ingestor, macro_ingestor, news_ingestor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("test_run")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Dry-run test for quant_research_agent")
    p.add_argument(
        "--no-claude",
        action="store_true",
        help="Skip Claude API calls, use stub scores (0.5, neutral)",
    )
    p.add_argument(
        "--no-preview",
        action="store_true",
        help="Do not write outputs/digest_preview.html",
    )
    p.add_argument(
        "--source",
        choices=["arxiv", "earnings", "macro", "news", "all"],
        default="all",
        help="Which ingestor to run (default: all)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    dry_run = args.no_claude or not os.environ.get("ANTHROPIC_API_KEY")

    if dry_run and not args.no_claude:
        logger.warning("ANTHROPIC_API_KEY not set — running in stub mode.")

    logger.info("Loading strategy context...")
    context = load_context()

    # --- Ingest ---
    raw_items = []

    if args.source in ("arxiv", "all"):
        logger.info("Fetching arXiv papers...")
        raw_items += arxiv_ingestor.fetch(context=context, max_results_per_query=3)

    if args.source in ("earnings", "all"):
        logger.info("Fetching earnings data...")
        # Limit to first 3 tickers for a test run
        watchlist = context.get("portfolio", {}).get("watchlist", [])[:3]
        raw_items += earnings_ingestor.fetch(context=context, tickers=watchlist)

    if args.source in ("macro", "all"):
        logger.info("Fetching macro data...")
        raw_items += macro_ingestor.fetch(context=context)

    if args.source in ("news", "all"):
        logger.info("Fetching news feeds...")
        raw_items += news_ingestor.fetch(context=context, max_per_feed=5)

    logger.info("Total raw items: %d", len(raw_items))

    if not raw_items:
        logger.warning("No items fetched. Check API keys and network access.")
        return

    # --- Analyze ---
    logger.info("Scoring items with Claude (dry_run=%s)...", dry_run)
    scored = analyze_items(raw_items, dry_run=dry_run)
    logger.info("Items above threshold: %d", len(scored))

    if not scored:
        logger.info("Nothing passed the score threshold. Done.")
        return

    # --- Format ---
    digest = build_digest(scored, context=context)

    # --- Preview ---
    if not args.no_preview:
        preview_dir = _ROOT / "outputs"
        preview_dir.mkdir(parents=True, exist_ok=True)
        preview_path = preview_dir / "digest_preview.html"
        preview_path.write_text(digest.html_body, encoding="utf-8")
        logger.info("Wrote HTML preview: %s", preview_path)

    # --- Print ---
    print("\n" + "=" * 70)
    print(f"SUBJECT: {digest.subject}")
    print("=" * 70)
    print(digest.plain_body)
    print("=" * 70)
    print(f"\nHTML body: {len(digest.html_body)} chars")
    print(f"Items in digest: {len(digest.items)}")
    print("\nDone. (No email sent, no dedup store written.)")


if __name__ == "__main__":
    main()
