"""
Renders a list of ScoredItems into a DigestEmail (HTML + plain text).
No external dependencies — pure Python string formatting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from agent.models import ScoredItem, DigestEmail

_SENTIMENT_EMOJI = {
    "bullish": "[+]",
    "bearish": "[-]",
    "neutral": "[~]",
}

_SENTIMENT_COLOR = {
    "bullish": "#1a7a4a",
    "bearish": "#b91c1c",
    "neutral": "#374151",
}

_SOURCE_LABEL = {
    "arxiv": "arXiv",
    "earnings": "Earnings",
    "macro": "Macro",
    "news": "News",
}


def _score_bar(score: float, width: int = 10) -> str:
    filled = round(score * width)
    return "█" * filled + "░" * (width - filled)


def _group_by_source(items: list[ScoredItem]) -> dict[str, list[ScoredItem]]:
    groups: dict[str, list[ScoredItem]] = {}
    for item in items:
        groups.setdefault(item.source, []).append(item)
    return groups


def _render_plain_item(item: ScoredItem, index: int) -> str:
    sentiment = _SENTIMENT_EMOJI.get(item.sentiment, "[~]")
    bar = _score_bar(item.score)
    themes = ", ".join(item.themes_matched) if item.themes_matched else "—"
    tickers = ", ".join(item.tickers_mentioned) if item.tickers_mentioned else "—"
    date_str = item.published.strftime("%Y-%m-%d %H:%M UTC")

    return (
        f"{index}. {sentiment} {item.title}\n"
        f"   Score: {item.score:.2f} {bar}\n"
        f"   {item.rationale}\n"
        f"   Themes: {themes} | Tickers: {tickers}\n"
        f"   Published: {date_str}\n"
        f"   {item.url}\n"
    )


def _render_html_item(item: ScoredItem) -> str:
    color = _SENTIMENT_COLOR.get(item.sentiment, "#374151")
    bar_filled = round(item.score * 10)
    bar_empty = 10 - bar_filled
    themes = ", ".join(item.themes_matched) if item.themes_matched else "—"
    tickers = ", ".join(f"<strong>{t}</strong>" for t in item.tickers_mentioned) if item.tickers_mentioned else "—"
    date_str = item.published.strftime("%Y-%m-%d %H:%M UTC")
    sentiment_label = item.sentiment.capitalize()

    return f"""
    <div style="border-left:4px solid {color};padding:12px 16px;margin-bottom:16px;background:#f9fafb;border-radius:0 4px 4px 0;">
      <div style="font-size:15px;font-weight:600;color:#111827;margin-bottom:4px;">
        <a href="{item.url}" style="color:#1d4ed8;text-decoration:none;">{item.title}</a>
      </div>
      <div style="font-size:13px;color:{color};margin-bottom:6px;">
        {sentiment_label} &nbsp;|&nbsp; Score: {item.score:.2f} &nbsp;
        <span style="font-family:monospace;">{"█"*bar_filled}{"░"*bar_empty}</span>
      </div>
      <div style="font-size:13px;color:#374151;margin-bottom:6px;">{item.rationale}</div>
      <div style="font-size:12px;color:#6b7280;">
        Themes: {themes} &nbsp;|&nbsp; Tickers: {tickers} &nbsp;|&nbsp; {date_str}
      </div>
    </div>"""


def build_digest(
    items: list[ScoredItem],
    context: dict[str, Any] | None = None,
    max_items: int = 20,
) -> DigestEmail:
    """
    Render a list of ScoredItems into a DigestEmail.

    Args:
        items:     Scored and sorted items from analyzer.
        context:   Strategy context dict (used for subject line metadata).
        max_items: Hard cap on total items included.

    Returns:
        DigestEmail with subject, html_body, plain_body, and items list.
    """
    now = datetime.now(tz=timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    items = items[:max_items]
    groups = _group_by_source(items)

    # --- Subject ---
    subject = f"Quant Research Digest — {date_str} ({len(items)} items)"

    # --- Plain text ---
    plain_sections = [
        f"QUANT RESEARCH DIGEST — {date_str}",
        f"{len(items)} items above threshold\n",
        "=" * 60,
    ]
    for source, source_items in groups.items():
        label = _SOURCE_LABEL.get(source, source.upper())
        plain_sections.append(f"\n{label.upper()} ({len(source_items)})\n" + "-" * 40)
        for i, item in enumerate(source_items, 1):
            plain_sections.append(_render_plain_item(item, i))

    plain_body = "\n".join(plain_sections)

    # --- HTML ---
    html_sections_body = ""
    for source, source_items in groups.items():
        label = _SOURCE_LABEL.get(source, source.upper())
        items_html = "".join(_render_html_item(it) for it in source_items)
        html_sections_body += f"""
    <h2 style="font-size:16px;font-weight:700;color:#1f2937;margin:24px 0 12px;
               border-bottom:2px solid #e5e7eb;padding-bottom:6px;">
      {label} <span style="font-weight:400;color:#6b7280;font-size:13px;">({len(source_items)})</span>
    </h2>
    {items_html}"""

    html_body = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:680px;
             margin:0 auto;padding:24px;color:#111827;background:#ffffff;">
  <div style="background:#1d4ed8;color:#fff;padding:20px 24px;border-radius:8px;margin-bottom:24px;">
    <div style="font-size:20px;font-weight:700;">Quant Research Digest</div>
    <div style="font-size:13px;opacity:0.85;margin-top:4px;">{date_str} &nbsp;|&nbsp; {len(items)} items</div>
  </div>
  {html_sections_body}
  <div style="margin-top:32px;font-size:11px;color:#9ca3af;border-top:1px solid #e5e7eb;padding-top:12px;">
    Generated {now.strftime("%Y-%m-%d %H:%M UTC")} by quant_research_agent.
    Scored by Claude. For informational purposes only.
  </div>
</body>
</html>"""

    return DigestEmail(
        subject=subject,
        html_body=html_body,
        plain_body=plain_body,
        items=items,
        generated=now,
    )
