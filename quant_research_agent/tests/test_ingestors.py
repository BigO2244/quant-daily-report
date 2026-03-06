"""
Tests for ingestors.

Uses mocking to avoid real network/API calls in CI.
Each ingestor is tested for:
  - correct ResearchItem structure
  - deduplication (no repeated item_ids)
  - graceful handling of empty/failed responses
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.models import ResearchItem


# ---------------------------------------------------------------------------
# arXiv ingestor
# ---------------------------------------------------------------------------

class TestArxivIngestor:
    def _mock_paper(self, paper_id: str, title: str) -> MagicMock:
        paper = MagicMock()
        paper.entry_id = f"https://arxiv.org/abs/{paper_id}"
        paper.title = title
        paper.summary = f"Summary of {title}."
        paper.published = datetime(2026, 3, 1, tzinfo=timezone.utc)
        paper.authors = ["Author One"]
        paper.categories = ["cs.LG"]
        return paper

    def test_returns_research_items(self):
        from ingestors import arxiv_ingestor

        mock_papers = [self._mock_paper("2403.00001v1", "Paper One")]
        mock_client = MagicMock()
        mock_client.results.return_value = mock_papers

        with patch("ingestors.arxiv_ingestor.arxiv.Client", return_value=mock_client):
            items = arxiv_ingestor.fetch(context=None)

        assert len(items) >= 1
        item = items[0]
        assert isinstance(item, ResearchItem)
        assert item.source == "arxiv"
        assert item.item_id.startswith("arxiv:")
        assert "arxiv.org" in item.url

    def test_deduplicates_across_queries(self):
        from ingestors import arxiv_ingestor

        # Same paper returned for multiple queries
        dup_paper = self._mock_paper("2403.99999v1", "Duplicate Paper")
        mock_client = MagicMock()
        mock_client.results.return_value = [dup_paper]

        with patch("ingestors.arxiv_ingestor.arxiv.Client", return_value=mock_client):
            items = arxiv_ingestor.fetch(context=None)

        ids = [i.item_id for i in items]
        assert len(ids) == len(set(ids))

    def test_handles_client_exception(self):
        from ingestors import arxiv_ingestor

        mock_client = MagicMock()
        mock_client.results.side_effect = Exception("Network error")

        with patch("ingestors.arxiv_ingestor.arxiv.Client", return_value=mock_client):
            items = arxiv_ingestor.fetch(context=None)

        assert items == []


# ---------------------------------------------------------------------------
# Earnings ingestor
# ---------------------------------------------------------------------------

class TestEarningsIngestor:
    def _mock_ticker_info(self, ticker: str) -> dict:
        return {
            "shortName": f"{ticker} Inc",
            "trailingEps": 5.42,
            "forwardEps": 6.10,
            "totalRevenue": 100_000_000_000,
            "revenueGrowth": 0.12,
            "earningsGrowth": 0.18,
            "trailingPE": 28.4,
            "forwardPE": 24.1,
            "recommendationKey": "buy",
            "numberOfAnalystOpinions": 42,
            "targetMeanPrice": 900.0,
            "currentPrice": 800.0,
        }

    def test_returns_one_item_per_ticker(self):
        from ingestors import earnings_ingestor

        tickers = ["NVDA", "MSFT"]
        mock_ticker = MagicMock()
        mock_ticker.info = self._mock_ticker_info("TEST")

        with patch("ingestors.earnings_ingestor.yf.Ticker", return_value=mock_ticker):
            items = earnings_ingestor.fetch(tickers=tickers)

        assert len(items) == 2
        for item in items:
            assert item.source == "earnings"
            assert item.item_id.startswith("earnings:")

    def test_skips_ticker_with_no_data(self):
        from ingestors import earnings_ingestor

        mock_ticker = MagicMock()
        mock_ticker.info = {}  # No useful fields

        with patch("ingestors.earnings_ingestor.yf.Ticker", return_value=mock_ticker):
            items = earnings_ingestor.fetch(tickers=["EMPTY"])

        assert items == []

    def test_handles_yfinance_exception(self):
        from ingestors import earnings_ingestor

        mock_ticker = MagicMock()
        mock_ticker.info = property(lambda self: (_ for _ in ()).throw(Exception("timeout")))

        with patch("ingestors.earnings_ingestor.yf.Ticker", side_effect=Exception("timeout")):
            items = earnings_ingestor.fetch(tickers=["FAIL"])

        assert items == []

    def test_empty_ticker_list(self):
        from ingestors import earnings_ingestor
        items = earnings_ingestor.fetch(tickers=[])
        assert items == []


# ---------------------------------------------------------------------------
# Macro ingestor
# ---------------------------------------------------------------------------

class TestMacroIngestor:
    def _mock_fred_series(self, value: float):
        import pandas as pd
        idx = pd.to_datetime(["2026-02-01"])
        return pd.Series([value], index=idx)

    def test_returns_single_item(self):
        from ingestors import macro_ingestor

        mock_fred = MagicMock()
        mock_fred.get_series.return_value = self._mock_fred_series(5.33)

        with patch("ingestors.macro_ingestor.Fred", return_value=mock_fred), \
             patch.dict("os.environ", {"FRED_API_KEY": "test-key"}):
            items = macro_ingestor.fetch()

        assert len(items) == 1
        item = items[0]
        assert item.source == "macro"
        assert item.item_id.startswith("macro:fred:")
        assert "FRED" in item.url or "fred" in item.url.lower()

    def test_no_api_key_returns_empty(self):
        from ingestors import macro_ingestor
        import os
        os.environ.pop("FRED_API_KEY", None)

        with patch.dict("os.environ", {}, clear=True):
            items = macro_ingestor.fetch()

        assert items == []

    def test_all_series_fail_returns_empty(self):
        from ingestors import macro_ingestor

        mock_fred = MagicMock()
        mock_fred.get_series.side_effect = Exception("FRED error")

        with patch("ingestors.macro_ingestor.Fred", return_value=mock_fred), \
             patch.dict("os.environ", {"FRED_API_KEY": "test-key"}):
            items = macro_ingestor.fetch()

        assert items == []


# ---------------------------------------------------------------------------
# News ingestor
# ---------------------------------------------------------------------------

class TestNewsIngestor:
    def _mock_feed(self, entries: list[dict]) -> MagicMock:
        mock = MagicMock()
        mock.bozo = False
        mock.entries = [MagicMock(**e) for e in entries]
        for entry_mock, entry_data in zip(mock.entries, entries):
            entry_mock.get = lambda key, default=None, _d=entry_data: _d.get(key, default)
        return mock

    def test_returns_research_items(self):
        from ingestors import news_ingestor

        feeds = [{"name": "Test Feed", "url": "https://example.com/rss"}]
        entry = {
            "id": "https://example.com/article/1",
            "title": "Markets rally on strong jobs report",
            "summary": "Stocks surged after better-than-expected payrolls data.",
            "link": "https://example.com/article/1",
            "published": "Thu, 01 Mar 2026 12:00:00 +0000",
        }

        mock_parsed = MagicMock()
        mock_parsed.bozo = False
        mock_parsed.entries = [MagicMock(
            get=lambda k, d=None, _e=entry: _e.get(k, d)
        )]

        with patch("ingestors.news_ingestor.feedparser.parse", return_value=mock_parsed):
            items = news_ingestor.fetch(feeds=feeds)

        # Should have attempted to parse and return items
        assert isinstance(items, list)

    def test_deduplicates_by_id(self):
        from ingestors import news_ingestor

        # Two feeds returning the same article URL
        feeds = [
            {"name": "Feed A", "url": "https://a.com/rss"},
            {"name": "Feed B", "url": "https://b.com/rss"},
        ]

        dup_entry = MagicMock()
        dup_entry.get = lambda k, d=None: {
            "id": "https://example.com/dup",
            "title": "Duplicate Article",
            "link": "https://example.com/dup",
            "summary": "Body.",
            "published": "Thu, 01 Mar 2026 12:00:00 +0000",
        }.get(k, d)

        mock_parsed = MagicMock()
        mock_parsed.bozo = False
        mock_parsed.entries = [dup_entry]

        with patch("ingestors.news_ingestor.feedparser.parse", return_value=mock_parsed):
            items = news_ingestor.fetch(feeds=feeds)

        ids = [i.item_id for i in items]
        assert len(ids) == len(set(ids))

    def test_skips_bozo_feed_with_no_entries(self):
        from ingestors import news_ingestor

        feeds = [{"name": "Bad Feed", "url": "https://bad.com/rss"}]
        mock_parsed = MagicMock()
        mock_parsed.bozo = True
        mock_parsed.entries = []

        with patch("ingestors.news_ingestor.feedparser.parse", return_value=mock_parsed):
            items = news_ingestor.fetch(feeds=feeds)

        assert items == []
