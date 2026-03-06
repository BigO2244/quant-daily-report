"""
News ingestor.

Fetches recent headlines via RSS/Atom feeds using feedparser.
Configured feed list targets financial news sources.
No API key required. No production imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

from agent.models import ResearchItem

logger = logging.getLogger(__name__)

# Default RSS feeds — financial news sources
RSS_FEEDS = [
    ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("Investopedia", "https://www.investopedia.com/feedbuilder/feed/getfeed/?feedName=rss_headline"),
    ("CNBC Markets", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
]

_MAX_ITEMS_PER_FEED = 10


def fetch(
    context: dict[str, Any] | None = None,
    feeds: list[tuple[str, str]] | None = None,
    max_per_feed: int = _MAX_ITEMS_PER_FEED,
) -> list[ResearchItem]:
    """
    Fetch recent news headlines from RSS feeds.

    Args:
        context:      Strategy context (reserved for future keyword filtering).
        feeds:        Optional list of (name, url) tuples to override defaults.
        max_per_feed: Max articles to pull per feed.

    Returns:
        Deduplicated list of ResearchItems (source='news').
    """
    feed_list = feeds or RSS_FEEDS
    seen_ids: set[str] = set()
    items: list[ResearchItem] = []

    for feed_name, feed_url in feed_list:

        try:
            parsed = feedparser.parse(feed_url)
        except Exception as exc:
            logger.warning("Feed parse failed (%s): %s", feed_name, exc)
            continue

        if parsed.bozo and not parsed.entries:
            logger.warning("Feed malformed or empty (%s)", feed_name)
            continue

        count = 0
        for entry in parsed.entries:
            if count >= max_per_feed:
                break

            item_id = entry.get("id") or entry.get("link", "")
            if not item_id or item_id in seen_ids:
                continue
            seen_ids.add(item_id)

            title = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            link = entry.get("link", "")
            published = _parse_date(entry)

            if not title or not link:
                continue

            items.append(
                ResearchItem(
                    source="news",
                    item_id=f"news:{item_id}",
                    title=title,
                    summary=summary or title,
                    url=link,
                    published=published,
                    metadata={"feed": feed_name},
                )
            )
            count += 1

    logger.info("News: fetched %d items from %d feeds", len(items), len(feed_list))
    return items


def _parse_date(entry: Any) -> datetime:
    """Parse a feed entry's published date, falling back to now."""
    for field in ("published", "updated", "created"):
        raw = entry.get(field)
        if raw:
            try:
                return parsedate_to_datetime(raw).astimezone(timezone.utc)
            except Exception:
                pass

    published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if published_parsed:
        try:
            import calendar
            ts = calendar.timegm(published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            pass

    return datetime.now(tz=timezone.utc)
