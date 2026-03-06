"""
arXiv ingestor.

Fetches recent papers matching configured investment themes using the arxiv library.
Returns a list of ResearchItems. No production imports.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import arxiv

from agent.models import ResearchItem

logger = logging.getLogger(__name__)

# Default queries per theme — override via context if needed
QUERIES = [
    "cat:q-fin.PM",          # Portfolio management
    "cat:q-fin.TR",          # Trading and market microstructure
    "cat:q-fin.ST",          # Statistical finance
    "cat:q-fin.GN momentum factor equity",   # General quant finance
]

MAX_RESULTS_PER_QUERY = 8


def fetch(
    context: dict[str, Any] | None = None,
    max_results_per_query: int = MAX_RESULTS_PER_QUERY,
) -> list[ResearchItem]:
    """
    Fetch recent arXiv papers relevant to the configured investment themes.

    Args:
        context:              Strategy context dict from context_loader.
        max_results_per_query: Max papers to fetch per search query.

    Returns:
        Deduplicated list of ResearchItems (source='arxiv').
    """
    queries = _build_queries(context)
    seen_ids: set[str] = set()
    items: list[ResearchItem] = []

    client = arxiv.Client()

    for query in queries:
        search = arxiv.Search(
            query=query,
            max_results=max_results_per_query,
            sort_by=arxiv.SortCriterion.SubmittedDate,
        )
        try:
            results = list(client.results(search))
        except Exception as exc:
            logger.warning("arXiv query failed (%r): %s", query, exc)
            continue

        for paper in results:
            paper_id = paper.entry_id.split("/")[-1]  # e.g. "2403.12345v1"
            if paper_id in seen_ids:
                continue
            seen_ids.add(paper_id)

            published = paper.published
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)

            items.append(
                ResearchItem(
                    source="arxiv",
                    item_id=f"arxiv:{paper_id}",
                    title=paper.title.strip(),
                    summary=paper.summary.strip(),
                    url=paper.entry_id,
                    published=published,
                    metadata={
                        "authors": [str(a) for a in paper.authors],
                        "categories": paper.categories,
                        "query": query,
                    },
                )
            )

    logger.info("arXiv: fetched %d papers across %d queries", len(items), len(queries))
    return items


def _build_queries(context: dict[str, Any] | None) -> list[str]:
    """Build search queries from theme keywords in context, falling back to defaults."""
    if not context:
        return QUERIES

    themes = context.get("themes", [])
    if not themes:
        return QUERIES

    queries = []
    for theme in themes:
        keywords = theme.get("keywords", [])
        if keywords:
            # Take first 3 keywords and combine into one query
            kw_str = " OR ".join(f'"{k}"' for k in keywords[:3])
            queries.append(f"({kw_str}) AND (finance OR investing OR trading)")

    return queries if queries else QUERIES
