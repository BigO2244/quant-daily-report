"""
Data models for the quant research agent.

All dataclasses here are self-contained — no production imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class ResearchItem:
    """
    A single piece of raw research from any ingestor.

    source:     one of 'arxiv', 'earnings', 'macro', 'news'
    item_id:    stable unique ID used for deduplication (e.g. arXiv ID, ticker+date)
    title:      short title or headline
    summary:    full text body to pass to Claude
    url:        canonical URL for the item
    published:  publication timestamp (UTC)
    metadata:   ingestor-specific extra fields (ticker, author, etc.)
    """

    source: str
    item_id: str
    title: str
    summary: str
    url: str
    published: datetime
    metadata: dict = field(default_factory=dict)


@dataclass
class ScoredItem:
    """
    A ResearchItem after Claude analysis.

    score:          0.0–1.0 composite relevance score
    rationale:      one-sentence Claude explanation
    themes_matched: list of strategy themes this item hits
    tickers_mentioned: watchlist tickers explicitly mentioned
    sentiment:      'bullish' | 'bearish' | 'neutral'
    raw_item:       the original ResearchItem
    """

    score: float
    rationale: str
    themes_matched: list[str]
    tickers_mentioned: list[str]
    sentiment: str
    raw_item: ResearchItem

    @property
    def source(self) -> str:
        return self.raw_item.source

    @property
    def title(self) -> str:
        return self.raw_item.title

    @property
    def url(self) -> str:
        return self.raw_item.url

    @property
    def published(self) -> datetime:
        return self.raw_item.published


@dataclass
class DigestEmail:
    """
    The fully assembled email ready for delivery.

    subject:    email subject line
    html_body:  rendered HTML
    plain_body: plain-text fallback
    items:      ordered list of ScoredItems included in the digest
    generated:  timestamp when the digest was built
    """

    subject: str
    html_body: str
    plain_body: str
    items: list[ScoredItem]
    generated: datetime = field(default_factory=datetime.utcnow)
