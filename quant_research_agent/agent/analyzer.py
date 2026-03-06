"""
Claude-powered analyzer.

Takes a list of ResearchItems and returns ScoredItems.
Each item is sent to Claude with the strategy context as the system prompt.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

from agent.models import ResearchItem, ScoredItem
from agent.context_loader import load_context, build_system_prompt

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 256


def _parse_response(text: str) -> dict[str, Any]:
    """Parse Claude's JSON response, stripping any accidental markdown fencing."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    return json.loads(text)


def analyze_items(
    items: list[ResearchItem],
    context_path: str | None = None,
    dry_run: bool = False,
) -> list[ScoredItem]:
    """
    Score a list of ResearchItems against the current strategy context.

    Args:
        items:        Raw research items from ingestors.
        context_path: Optional path override for strategy_context.yaml.
        dry_run:      If True, skip Claude calls and return placeholder scores.

    Returns:
        List of ScoredItems, sorted by score descending.
    """
    if not items:
        return []

    context = load_context(context_path)
    system_prompt = build_system_prompt(context)
    min_threshold = context.get("scoring", {}).get("min_score_threshold", 0.40)

    if dry_run:
        logger.info("[dry-run] Skipping Claude calls, returning stub scores.")
        return [
            ScoredItem(
                score=0.5,
                rationale="Dry-run stub — no Claude call made.",
                themes_matched=[],
                tickers_mentioned=[],
                sentiment="neutral",
                raw_item=item,
            )
            for item in items
        ]

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EnvironmentError("ANTHROPIC_API_KEY is not set.")

    client = anthropic.Anthropic(api_key=api_key)
    scored: list[ScoredItem] = []

    for item in items:
        user_message = (
            f"Source: {item.source}\n"
            f"Title: {item.title}\n"
            f"Published: {item.published.isoformat()}\n"
            f"URL: {item.url}\n\n"
            f"{item.summary}"
        )

        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}],
            )
            parsed = _parse_response(response.content[0].text)

            score = float(parsed.get("score", 0.0))
            if score < min_threshold:
                logger.debug(
                    "Skipping %s (score %.2f below threshold %.2f)",
                    item.item_id,
                    score,
                    min_threshold,
                )
                continue

            scored.append(
                ScoredItem(
                    score=score,
                    rationale=parsed.get("rationale", ""),
                    themes_matched=parsed.get("themes_matched", []),
                    tickers_mentioned=parsed.get("tickers_mentioned", []),
                    sentiment=parsed.get("sentiment", "neutral"),
                    raw_item=item,
                )
            )

        except json.JSONDecodeError as exc:
            logger.warning("Failed to parse Claude response for %s: %s", item.item_id, exc)
        except anthropic.APIError as exc:
            logger.error("Claude API error for %s: %s", item.item_id, exc)

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored
