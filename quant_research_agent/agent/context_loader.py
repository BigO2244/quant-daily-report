"""
Loads strategy_context.yaml and exposes it as a structured object.

Keeps file I/O and config parsing out of analyzer.py.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "strategy_context.yaml"


def load_context(path: str | Path | None = None) -> dict[str, Any]:
    """
    Load and return the strategy context as a plain dict.

    Args:
        path: Optional path override. Defaults to config/strategy_context.yaml
              relative to the project root.

    Returns:
        Parsed YAML as a dict.

    Raises:
        FileNotFoundError: if the config file does not exist.
        yaml.YAMLError: if the file is malformed.
    """
    config_path = Path(path) if path else _DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Strategy context not found: {config_path}")

    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def build_system_prompt(context: dict[str, Any]) -> str:
    """
    Render the strategy context into a Claude system prompt string.

    Args:
        context: dict from load_context()

    Returns:
        System prompt string to pass as the 'system' parameter to Claude.
    """
    portfolio = context.get("portfolio", {})
    watchlist = portfolio.get("watchlist", [])
    themes = context.get("themes", [])
    scoring = context.get("scoring", {})

    theme_lines = "\n".join(
        f"  - {t['name']}: {t['description']}"
        for t in themes
    )
    watchlist_str = ", ".join(watchlist) if watchlist else "(none)"
    threshold = scoring.get("min_score_threshold", 0.40)

    return f"""You are a quantitative research analyst assistant.

Your job is to evaluate research items (arXiv papers, earnings summaries, macro data, news)
against the following live strategy context and return a structured JSON assessment.

## Portfolio context
- Style: {portfolio.get('style', 'unspecified')}
- Holding horizon: {portfolio.get('horizon', 'unspecified')}
- Universe: {portfolio.get('universe', 'unspecified')}

## Watchlist tickers
{watchlist_str}

## Active investment themes
{theme_lines}

## Scoring guidance
- Score each item from 0.0 to 1.0 for relevance to this strategy.
- Items scoring below {threshold} should be scored honestly — do not inflate.
- Sentiment must be one of: bullish, bearish, neutral (relative to the strategy).
- themes_matched must only contain names from the theme list above.
- tickers_mentioned must only contain tickers from the watchlist above.

## Scoring examples (calibrate against these)

EXAMPLE 1 — Static earnings snapshot, no new catalyst today:
Item: "NVDA earnings snapshot — 95% YoY growth, Strong Buy consensus"
Correct score: 0.78 (strong fundamental backdrop, but this data is not new
today — it reflects last quarter. No imminent catalyst within 5 trading days.)
Wrong score: 0.95 (would require guidance raised TODAY or beat announced TODAY)

EXAMPLE 2 — News with direct watchlist catalyst:
Item: "NVDA raises Q2 guidance above consensus after market close"
Correct score: 0.93 (imminent, specific, time-bound catalyst on watchlist ticker)

EXAMPLE 3 — Academic paper, relevant methodology:
Item: "arXiv: NLP model predicts earnings surprise from press releases"
Correct score: 0.60 (relevant to strategy, actionable over weeks not days,
no specific ticker signal today)

EXAMPLE 4 — Macro news, broad market impact:
Item: "Oil shock fears driving broad equity selloff, yields rising"
Correct score: 0.68 (relevant regime signal, affects watchlist, but indirect)

EXAMPLE 5 — Indirect readthrough:
Item: "Anthropic showing astronomical growth — Mag 7 stock to benefit"
Correct score: 0.70 (valid readthrough to AMZN/GOOGL, but indirect and
requires confirmation)

KEY RULE: A score above 0.88 requires an event that happened TODAY or
is scheduled within 3 trading days for a specific watchlist ticker.
Fundamental quality alone never justifies above 0.85.

## Output format (strict JSON, no markdown wrapper)
{{
  "score": <float 0.0-1.0>,
  "rationale": "<one sentence>",
  "themes_matched": ["<theme_name>", ...],
  "tickers_mentioned": ["<TICKER>", ...],
  "sentiment": "bullish" | "bearish" | "neutral"
}}"""
