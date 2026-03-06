"""
Tests for agent/analyzer.py

Uses dry_run=True to avoid real Claude API calls.
Tests JSON parse logic with a mock client.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from agent.models import ResearchItem, ScoredItem
from agent import analyzer


def _make_item(item_id: str = "test:001", source: str = "arxiv") -> ResearchItem:
    return ResearchItem(
        source=source,
        item_id=item_id,
        title="Test Research Item",
        summary="Summary of the research item for testing.",
        url="https://example.com/item",
        published=datetime(2026, 3, 1, tzinfo=timezone.utc),
    )


class TestAnalyzeDryRun:
    def test_dry_run_returns_stub_scores(self):
        items = [_make_item("a"), _make_item("b"), _make_item("c")]
        results = analyzer.analyze_items(items, dry_run=True)
        assert len(results) == 3
        for r in results:
            assert isinstance(r, ScoredItem)
            assert r.score == 0.5
            assert r.sentiment == "neutral"
            assert "dry-run" in r.rationale

    def test_dry_run_empty_input(self):
        results = analyzer.analyze_items([], dry_run=True)
        assert results == []

    def test_dry_run_preserves_raw_item(self):
        item = _make_item("test:xyz", source="earnings")
        results = analyzer.analyze_items([item], dry_run=True)
        assert results[0].raw_item is item


class TestParseResponse:
    def test_clean_json(self):
        text = '{"score": 0.8, "rationale": "Relevant.", "themes_matched": ["ai_infrastructure"], "tickers_mentioned": ["NVDA"], "sentiment": "bullish"}'
        parsed = analyzer._parse_response(text)
        assert parsed["score"] == 0.8
        assert parsed["sentiment"] == "bullish"

    def test_strips_markdown_fencing(self):
        text = '```json\n{"score": 0.6, "rationale": "OK.", "themes_matched": [], "tickers_mentioned": [], "sentiment": "neutral"}\n```'
        parsed = analyzer._parse_response(text)
        assert parsed["score"] == 0.6

    def test_strips_plain_code_block(self):
        text = '```\n{"score": 0.4, "rationale": "Low.", "themes_matched": [], "tickers_mentioned": [], "sentiment": "bearish"}\n```'
        parsed = analyzer._parse_response(text)
        assert parsed["score"] == 0.4

    def test_raises_on_invalid_json(self):
        with pytest.raises(Exception):
            analyzer._parse_response("not valid json at all")


class TestAnalyzeWithMockClient:
    def _mock_response(self, score: float, sentiment: str = "neutral") -> MagicMock:
        import json
        content_block = MagicMock()
        content_block.text = json.dumps({
            "score": score,
            "rationale": "Mock rationale.",
            "themes_matched": [],
            "tickers_mentioned": [],
            "sentiment": sentiment,
        })
        response = MagicMock()
        response.content = [content_block]
        return response

    def test_filters_below_threshold(self):
        items = [_make_item("low:001")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(score=0.10)

        with patch("agent.analyzer.anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            results = analyzer.analyze_items(items, dry_run=False)

        # Score 0.10 is below default threshold of 0.40
        assert results == []

    def test_includes_above_threshold(self):
        items = [_make_item("high:001")]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = self._mock_response(score=0.85, sentiment="bullish")

        with patch("agent.analyzer.anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            results = analyzer.analyze_items(items, dry_run=False)

        assert len(results) == 1
        assert results[0].score == 0.85
        assert results[0].sentiment == "bullish"

    def test_sorted_by_score_descending(self):
        items = [_make_item("a"), _make_item("b"), _make_item("c")]
        scores = [0.9, 0.6, 0.75]
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            self._mock_response(s) for s in scores
        ]

        with patch("agent.analyzer.anthropic.Anthropic", return_value=mock_client), \
             patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            results = analyzer.analyze_items(items, dry_run=False)

        result_scores = [r.score for r in results]
        assert result_scores == sorted(result_scores, reverse=True)

    def test_missing_api_key_raises(self):
        items = [_make_item()]
        with patch.dict("os.environ", {}, clear=True):
            # Ensure ANTHROPIC_API_KEY is not set
            import os
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with pytest.raises(EnvironmentError, match="ANTHROPIC_API_KEY"):
                analyzer.analyze_items(items, dry_run=False)
