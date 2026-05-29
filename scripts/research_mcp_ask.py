"""Operator-facing CLI bridge to the Caerus research-registry MCP.

Usage
-----
::

    python -m scripts.research_mcp_ask "Does timing matter more in high VIX regimes?"

The command routes the natural-language question through the existing
``research_registry.mcp_server.call_tool`` (no parallel MCP, no LLM call,
no external network). It then prints a concise human-readable answer
plus a Markdown summary, and writes deterministic artifacts to

* ``outputs/research_mcp/questions/<TIMESTAMP>/answer.json`` — full
  structured payload exactly as returned by the MCP tool.
* ``outputs/research_mcp/questions/<TIMESTAMP>/answer.md`` — the same
  content the operator sees on stdout, formatted as Markdown for archival
  or paste-into-PR use.

Exit codes
----------
* ``0`` — the MCP returned status ``OK``.
* ``2`` — the MCP returned a missing-artifact status
  (``NO_TIMING_DATA`` / ``NO_REGIME_DATA`` / ``BAD_REGIME_SCHEMA``). The
  printed output includes the exact missing path and the next command
  the operator should run.
* ``3`` — the question did not match any whitelisted intent
  (``UNSUPPORTED_INTENT``). The printed output lists the supported
  phrases.
* ``1`` — an unexpected error (usage problem, Python exception). Stderr
  carries the detail.

Read-only contract
------------------
This script never writes outside ``outputs/research_mcp/``, never calls
the broker or any execution-path module, and never imports cron,
reconciliation, or ``core/timing_policy``. It is purely a presentation
+ persistence layer over the existing MCP server.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from research_registry.mcp_server import ToolContext, call_tool


DEFAULT_OUTPUT_ROOT = Path("outputs/research_mcp/questions")
DEFAULT_TOOL = "answer_research_question"


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _now_stamp(now: dt.datetime | None = None) -> str:
    """Filesystem-safe ISO-ish timestamp (no colons): ``2026-05-29T16-35-12Z``."""
    moment = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H-%M-%SZ")


def _jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return _jsonable(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _fmt_money(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_bps(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def _fmt_float(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value):,.4f}"
    except (TypeError, ValueError):
        return "—"


def _offset_sort_key(label: str) -> int:
    """Sort `T+5m`-style labels numerically. Non-matching labels go last."""
    try:
        return int(label.replace("T+", "").rstrip("mM"))
    except (ValueError, AttributeError):
        return 1_000_000


def _inner_answer(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the underlying tool payload.

    ``answer_research_question`` wraps the routed tool's response in an
    ``answer`` field; calling the routed tool directly returns the same
    shape at the top level. This helper hides that difference.
    """
    answer = payload.get("answer")
    if isinstance(answer, dict):
        return answer
    return payload


def render_human_and_markdown(question: str, payload: dict[str, Any]) -> tuple[str, str]:
    """Format the MCP response for stdout + answer.md.

    Returns a ``(human_text, markdown_text)`` pair. Pure function: no I/O,
    no side effects.
    """
    status = str(payload.get("status") or "UNKNOWN")
    intent = payload.get("intent")
    routed_to = payload.get("routed_to")
    inner = _inner_answer(payload)

    lines: list[str] = []
    md: list[str] = []

    lines.append(f"Question: {question}")
    lines.append(f"Status:   {status}")
    md.append("# Research MCP Answer")
    md.append("")
    md.append(f"**Question:** {question}  ")
    md.append(f"**Status:** `{status}`  ")
    if intent:
        suffix = f" → {routed_to}" if routed_to else ""
        lines.append(f"Intent:   {intent}{suffix}")
        md.append(f"**Intent:** `{intent}`{(' → `' + routed_to + '`') if routed_to else ''}  ")

    run_date = inner.get("run_date")
    baseline = inner.get("baseline_offset")
    cache_version = inner.get("cache_key_version")
    if run_date or baseline:
        lines.append(
            "Run:      "
            + " | ".join(
                bit
                for bit in (
                    f"run_date={run_date}" if run_date else "",
                    f"baseline={baseline}" if baseline else "",
                    f"cache={cache_version}" if cache_version else "",
                )
                if bit
            )
        )
        md.append("")
        if run_date:
            md.append(f"**Run date:** `{run_date}`  ")
        if baseline:
            md.append(f"**Baseline:** `{baseline}`  ")
        if cache_version:
            md.append(f"**Cache:** `{cache_version}`  ")

    # OK path for shadow_comparison — render the per-strategy panel.
    panels = inner.get("panels") or {}
    if status == "OK" and panels and (inner.get("leader_summary") or inner.get("leader_by_cumulative_return") is not None):
        lines.append("")
        if inner.get("trade_date"):
            lines.append(f"Trade date: {inner['trade_date']}  (benchmark: {inner.get('benchmark_symbol', 'SPY')})")
        if inner.get("leader_summary"):
            lines.append(f"Leader: {inner['leader_summary']}")
        lines.append("")
        header = ["strategy", "nav", "cum_return", "excess_vs_spy", "avg_turnover", "max_drawdown"]
        col_widths = [max(len(h), 14) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append(f"## Shadow comparison — trade date `{inner.get('trade_date', '?')}`")
        if inner.get("leader_summary"):
            md.append("")
            md.append(f"**Leader:** {inner['leader_summary']}")
        md.append("")
        md.append("| " + " | ".join(header) + " |")
        md.append("| " + " | ".join(["---"] * len(header)) + " |")
        for slug, panel in panels.items():
            row = [
                slug,
                _fmt_float(panel.get("nav")),
                _fmt_float(panel.get("cumulative_return")),
                _fmt_float(panel.get("excess_return_vs_spy")),
                _fmt_float(panel.get("avg_turnover")),
                _fmt_float(panel.get("max_drawdown")),
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        pairwise = inner.get("pairwise_overlap") or []
        if pairwise:
            lines.append("")
            lines.append("Pairwise overlap:")
            md.append("")
            md.append("### Pairwise overlap")
            md.append("")
            for entry in pairwise:
                line = (
                    f"  {entry.get('left_slug', '?')} ↔ {entry.get('right_slug', '?')}: "
                    f"overlap_weight={_fmt_float(entry.get('overlap_weight_pct'))}, "
                    f"shared={entry.get('shared_names') or []}"
                )
                lines.append(line)
                md.append(f"- `{entry.get('left_slug', '?')}` ↔ `{entry.get('right_slug', '?')}` — overlap "
                          f"{_fmt_float(entry.get('overlap_weight_pct'))}; shared: {entry.get('shared_names') or []}")

    # OK path for execution_timing_summary — render the per-offset table + recommendation.
    by_offset = inner.get("by_offset") or {}
    recommendation = inner.get("recommendation")
    if status == "OK" and by_offset and recommendation:
        baseline = inner.get("baseline_offset")
        highlighted = set(inner.get("highlighted_offsets") or [])
        lines.append("")
        lines.append(f"Days replayed: {inner.get('days_replayed')}  |  Baseline: {baseline}")
        lines.append(f"Best non-baseline offset: {inner.get('best_offset')}")
        lines.append(f"Recommendation: {recommendation}")
        if inner.get("recommendation_reason"):
            lines.append(f"  Reason: {inner['recommendation_reason']}")
        lines.append("")
        header = ["offset", "mean_usd", "median_usd", "mean_bps", "n_days"]
        col_widths = [max(len(h), 12) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append(f"## Timing summary — `{inner.get('run_date', '?')}` (baseline `{baseline}`)")
        md.append("")
        md.append(f"**Days replayed:** {inner.get('days_replayed')}  ")
        md.append(f"**Best non-baseline offset:** `{inner.get('best_offset')}`  ")
        md.append(f"**Recommendation:** `{recommendation}` — {inner.get('recommendation_reason', '')}")
        md.append("")
        md.append("| " + " | ".join(header) + " |")
        md.append("| " + " | ".join(["---"] * len(header)) + " |")
        for label, metrics in sorted(by_offset.items(), key=lambda kv: _offset_sort_key(kv[0])):
            decoration = ""
            if label == baseline:
                decoration = " (baseline)"
            elif label in highlighted:
                decoration = " *"
            row = [
                label + decoration,
                _fmt_money(metrics.get("mean_opportunity_usd")),
                _fmt_money(metrics.get("median_opportunity_usd")),
                _fmt_bps(metrics.get("mean_opportunity_bps")),
                str(metrics.get("n_days_with_opportunity") or 0),
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        if highlighted:
            lines.append("")
            lines.append("  * = offset highlighted from the question text")

    # OK path — render the regime aggregates table when present.
    aggregates = inner.get("regime_aggregates") or []
    offsets = inner.get("offsets") or []
    if status == "OK" and aggregates and offsets:
        non_baseline = [o for o in offsets if o != baseline]
        lines.append("")
        lines.append("Per-regime opportunity vs baseline (positive = offset cheaper than baseline)")
        lines.append("")
        header = ["regime", "n_days"] + [f"{o} mean_usd" for o in non_baseline]
        col_widths = [max(len(h), 12) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append("## Per-regime opportunity (vs baseline)")
        md.append("")
        md.append("Positive = offset is *cheaper* than baseline (less cash out for same order set).")
        md.append("")
        md.append("| " + " | ".join(header) + " |")
        md.append("| " + " | ".join(["---"] * len(header)) + " |")
        for agg in aggregates:
            regime_label = str(agg.get("regime", "?"))
            if agg.get("insufficient_sample"):
                regime_label += " *"
            row = [regime_label, str(agg.get("n_days", 0))]
            for off in non_baseline:
                entry = (agg.get("by_offset") or {}).get(off) or {}
                row.append(_fmt_money(entry.get("mean_opportunity_usd")))
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        if any(a.get("insufficient_sample") for a in aggregates):
            lines.append("")
            lines.append(
                "  * = insufficient sample size (< "
                + str(inner.get("coverage", {}).get("insufficient_sample_threshold", 5))
                + " days); do not draw significance from those buckets."
            )
            md.append("")
            md.append(
                "`*` = insufficient sample (< "
                + str(inner.get("coverage", {}).get("insufficient_sample_threshold", 5))
                + " days). Do not draw significance claims from those buckets."
            )

    # Missing-artifact paths — print the exact next command.
    if status == "NO_TIMING_DATA":
        lines.append("")
        lines.append("Required artifact missing:")
        lines.append("  outputs/research/execution_timing/<RUN_DATE>/timing_summary.json")
        lines.append("")
        lines.append("Next command:")
        lines.append("  python -m scripts.research.execution_timing_replay --run-date $(date -u +%F)")
        md.append("")
        md.append("## Required artifact missing")
        md.append("")
        md.append("- `outputs/research/execution_timing/<RUN_DATE>/timing_summary.json`")
        md.append("")
        md.append("**Next command:** `python -m scripts.research.execution_timing_replay --run-date $(date -u +%F)`")

    if status == "NO_REGIME_DATA":
        regime_path = inner.get("regime_history") or "outputs/vix_regime/regime_history.csv"
        lines.append("")
        lines.append("Required artifact missing or empty:")
        lines.append(f"  {regime_path}")
        lines.append("")
        lines.append("Next: confirm the VIX classifier has written rows to that CSV.")
        md.append("")
        md.append("## Required artifact missing or empty")
        md.append("")
        md.append(f"- `{regime_path}`")

    if status == "BAD_REGIME_SCHEMA":
        missing_cols = inner.get("missing_columns") or []
        lines.append("")
        lines.append("Regime CSV has unrecognised columns:")
        lines.append(f"  missing: {missing_cols}")
        lines.append("")
        lines.append(
            "The loader accepts one of {date, as_of, execution_date} for the date "
            "column, plus `regime` and `vix`. Fix the CSV header or extend "
            "research_registry.research.timing_regime._REGIME_DATE_COLUMN_CANDIDATES."
        )
        md.append("")
        md.append("## Bad regime CSV schema")
        md.append("")
        md.append(f"Missing columns: `{missing_cols}`")

    if status == "NEEDS_DATA":
        missing = payload.get("missing_artifacts") or []
        matched_cap = payload.get("matched_capability") or {}
        lines.append("")
        lines.append(f"Capability matched: {matched_cap.get('name', '?')}")
        lines.append("Required artifacts missing:")
        for path in missing:
            lines.append(f"  - {path}")
        lines.append("")
        lines.append("Next: produce the missing artifact(s), then re-ask the same question.")
        md.append("")
        md.append(f"## Required artifacts missing for `{matched_cap.get('name', '?')}`")
        md.append("")
        for path in missing:
            md.append(f"- `{path}`")

    if status == "NEEDS_CAPABILITY":
        matched_cap = payload.get("matched_capability") or {}
        suggested = payload.get("suggested_next_build")
        lines.append("")
        lines.append(
            f"Capability matched: {matched_cap.get('name', '?')} "
            "(recognised but not yet implemented)"
        )
        if matched_cap.get("description"):
            lines.append("")
            lines.append(f"  {matched_cap['description']}")
        if suggested:
            lines.append("")
            lines.append("Suggested next build:")
            lines.append(f"  {suggested}")
        md.append("")
        md.append(f"## Capability `{matched_cap.get('name', '?')}` — not yet implemented")
        md.append("")
        if matched_cap.get("description"):
            md.append(matched_cap["description"])
            md.append("")
        if suggested:
            md.append("### Suggested next build")
            md.append("")
            md.append(suggested)

    if status == "UNSUPPORTED_INTENT":
        closest = payload.get("closest_capabilities") or []
        lines.append("")
        lines.append("This gateway is deliberately regex-driven (no LLM).")
        if closest:
            lines.append("")
            lines.append("Closest capabilities (by token overlap):")
            for entry in closest:
                lines.append(
                    f"  - {entry.get('name', '?')}: "
                    f"{entry.get('description', '')[:80]}"
                )
        lines.append("")
        lines.append("Supported phrasings:")
        md.append("")
        md.append("## Closest capabilities")
        md.append("")
        for entry in closest:
            md.append(f"- **{entry.get('name', '?')}** — {entry.get('description', '')}")
        md.append("")
        md.append("## Supported phrasings")
        md.append("")
        for intent_def in payload.get("available_intents") or []:
            for phrase in intent_def.get("matches") or []:
                lines.append(f"  - {phrase}")
                md.append(f"- {phrase}")
        first = (payload.get("available_intents") or [{}])[0]
        example = first.get("example_question")
        if example:
            lines.append("")
            lines.append(f'Example: "{example}"')
            md.append("")
            md.append(f"**Example:** `\"{example}\"`")

    # Warnings appear at the end of the human view.
    warnings = payload.get("warnings") or inner.get("warnings") or []
    if warnings:
        lines.append("")
        lines.append("Warnings:")
        for warning in warnings:
            lines.append(f"  - {warning}")
        md.append("")
        md.append("## Warnings")
        md.append("")
        for warning in warnings:
            md.append(f"- {warning}")

    return "\n".join(lines) + "\n", "\n".join(md) + "\n"


def _render_row(cells: list[str], widths: list[int]) -> str:
    return "  ".join(cell.ljust(width) for cell, width in zip(cells, widths)).rstrip()


def status_to_exit_code(status: str) -> int:
    if status == "OK":
        return 0
    if status in {
        "NO_TIMING_DATA",
        "NO_REGIME_DATA",
        "BAD_REGIME_SCHEMA",
        "NEEDS_DATA",
    }:
        return 2
    if status in {"UNSUPPORTED_INTENT", "NEEDS_CAPABILITY"}:
        return 3
    # Unknown statuses still exit clean — the answer was returned cleanly,
    # the operator just needs to interpret it.
    return 0


# ---------------------------------------------------------------------------
# Argparse + main
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_mcp_ask",
        description=(
            "Ask the Caerus research-registry MCP a natural-language question. "
            "Routes through answer_research_question, writes deterministic "
            "artifacts under outputs/research_mcp/questions/<TIMESTAMP>/."
        ),
    )
    parser.add_argument("question", help="Natural-language question to send to the MCP.")
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help=f"Where to write answer.json/answer.md (default: {DEFAULT_OUTPUT_ROOT}).",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="Skip writing answer.json/answer.md to disk; print to stdout only.",
    )
    parser.add_argument(
        "--raw-json",
        action="store_true",
        help="Print raw JSON to stdout instead of the formatted human view (artifacts still written).",
    )
    parser.add_argument(
        "--timestamp",
        default=None,
        help="Override the artifact directory timestamp (used by tests for deterministic output paths).",
    )
    parser.add_argument(
        "--tool",
        default=DEFAULT_TOOL,
        help=(
            "MCP tool to invoke. Default is answer_research_question. Override "
            "only if you know the target tool accepts a `question` argument."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    question = (args.question or "").strip()
    if not question:
        print("error: question must not be empty", file=sys.stderr)
        return 1

    try:
        result = call_tool(args.tool, {"question": question}, context=ToolContext())
    except TypeError as exc:
        print(f"error: tool {args.tool!r} did not accept 'question': {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: MCP call failed: {exc}", file=sys.stderr)
        return 1

    payload = _jsonable(result)
    human, markdown = render_human_and_markdown(question, payload)

    output_dir: Path | None = None
    if not args.no_write:
        output_dir = Path(args.output_root) / (args.timestamp or _now_stamp())
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "answer.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (output_dir / "answer.md").write_text(markdown, encoding="utf-8")

    if args.raw_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(human)
        if output_dir is not None:
            print("Artifacts:")
            print(f"  - {output_dir / 'answer.json'}")
            print(f"  - {output_dir / 'answer.md'}")

    return status_to_exit_code(str(payload.get("status") or ""))


if __name__ == "__main__":
    raise SystemExit(main())
