"""
Tests for agent/models.py

No mocking needed — these are pure dataclass tests.
"""

from datetime import datetime, timezone

import pytest

from agent.models import ResearchItem, ScoredItem, DigestEmail


def _make_item(**kwargs) -> ResearchItem:
    defaults = dict(
        source="arxiv",
        item_id="arxiv:2403.00001v1",
        title="Test Paper",
        summary="A test summary.",
        url="https://arxiv.org/abs/2403.00001",
        published=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
    )
    return ResearchItem(**{**defaults, **kwargs})


def _make_scored(item: ResearchItem, **kwargs) -> ScoredItem:
    defaults = dict(
        score=0.75,
        rationale="Highly relevant to AI infrastructure theme.",
        themes_matched=["ai_infrastructure"],
        tickers_mentioned=["NVDA"],
        sentiment="bullish",
        raw_item=item,
    )
    return ScoredItem(**{**defaults, **kwargs})


class TestResearchItem:
    def test_basic_construction(self):
        item = _make_item()
        assert item.source == "arxiv"
        assert item.item_id == "arxiv:2403.00001v1"
        assert item.metadata == {}

    def test_metadata_default_is_independent(self):
        a = _make_item()
        b = _make_item()
        a.metadata["key"] = "value"
        assert "key" not in b.metadata

    def test_custom_metadata(self):
        item = _make_item(metadata={"authors": ["Alice", "Bob"], "categories": ["cs.LG"]})
        assert item.metadata["authors"] == ["Alice", "Bob"]

    def test_all_sources_accepted(self):
        for source in ("arxiv", "earnings", "macro", "news"):
            item = _make_item(source=source)
            assert item.source == source


class TestScoredItem:
    def test_proxy_properties(self):
        item = _make_item()
        scored = _make_scored(item)
        assert scored.source == item.source
        assert scored.title == item.title
        assert scored.url == item.url
        assert scored.published == item.published

    def test_score_range(self):
        item = _make_item()
        for score in (0.0, 0.5, 1.0):
            scored = _make_scored(item, score=score)
            assert 0.0 <= scored.score <= 1.0

    def test_empty_themes_and_tickers(self):
        item = _make_item()
        scored = _make_scored(item, themes_matched=[], tickers_mentioned=[])
        assert scored.themes_matched == []
        assert scored.tickers_mentioned == []

    def test_sentiment_values(self):
        item = _make_item()
        for sentiment in ("bullish", "bearish", "neutral"):
            scored = _make_scored(item, sentiment=sentiment)
            assert scored.sentiment == sentiment


class TestDigestEmail:
    def test_construction(self):
        item = _make_item()
        scored = _make_scored(item)
        digest = DigestEmail(
            subject="Test Digest",
            html_body="<html></html>",
            plain_body="plain",
            items=[scored],
        )
        assert digest.subject == "Test Digest"
        assert len(digest.items) == 1
        assert isinstance(digest.generated, datetime)

    def test_generated_defaults_to_now(self):
        before = datetime.utcnow()
        digest = DigestEmail(
            subject="s", html_body="h", plain_body="p", items=[]
        )
        after = datetime.utcnow()
        assert before <= digest.generated <= after
