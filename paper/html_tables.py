from __future__ import annotations

from html import escape
from typing import Iterable, Sequence


def render_html_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    numeric_cols: set[int] | None = None,
    empty_message: str = "(none)",
) -> str:
    numeric_cols = numeric_cols or set()
    rows = list(rows)
    if not rows:
        return f"<p><em>{escape(empty_message)}</em></p>"

    head = "".join(f"<th>{escape(str(h))}</th>" for h in headers)
    body_parts: list[str] = []
    for row in rows:
        cells: list[str] = []
        for idx, cell in enumerate(row):
            css = " class='num'" if idx in numeric_cols else ""
            cells.append(f"<td{css}>{escape(str(cell))}</td>")
        body_parts.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table class='tbl'><thead><tr>{head}</tr></thead><tbody>{''.join(body_parts)}</tbody></table>"


def render_card(title: str, inner_html: str, subtitle: str | None = None) -> str:
    sub = f"<p class='muted'>{escape(subtitle)}</p>" if subtitle else ""
    return f"<div class='card'><h3>{escape(title)}</h3>{sub}{inner_html}</div>"
