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


def _fmt_pct(value: Any) -> str:
    """Format a fractional value as a percent (e.g. 0.293 → '29.30%')."""
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def _fmt_signed_pct(value: Any) -> str:
    if value is None:
        return "—"
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "—"


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

    # OK path for attribution_analysis — render per-strategy panels + narrative.
    # Detect via fields that are unique to the attribution payload (panels with
    # `top_contributor` keys; a `narrative` string; a `leader_by_return` field).
    attribution_panels = inner.get("panels") or {}
    attribution_narrative = inner.get("narrative")
    is_attribution = (
        status == "OK"
        and isinstance(attribution_narrative, str)
        and attribution_panels
        and any(
            isinstance(p, dict) and "top_contributor" in p
            for p in attribution_panels.values()
        )
    )
    if is_attribution:
        trade_date = inner.get("trade_date")
        leader = inner.get("leader_by_return")
        lines.append("")
        if trade_date:
            lines.append(f"Trade date: {trade_date}   Leader by 21d return: {leader or 'n/a'}")
        lines.append("")
        header = ["strategy", "21d_return", "top_contributor", "top_detractor", "β", "max_sector"]
        col_widths = [max(len(h), 18) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append(f"## Attribution panels — trade date `{trade_date or '?'}`")
        if leader:
            md.append("")
            md.append(f"**Leader by 21d return:** `{leader}`")
        md.append("")
        md.append("| strategy | 21d_return | top_contributor | top_detractor | β | max_sector |")
        md.append("| --- | ---: | --- | --- | ---: | --- |")
        for slug, panel in sorted(attribution_panels.items()):
            ret = panel.get("portfolio_return_21d")
            top = panel.get("top_contributor") or {}
            bot = panel.get("top_detractor") or {}
            beta = panel.get("market_beta")
            max_sector_block = (panel.get("sector_exposure") or {})
            max_sector_weight = max_sector_block.get("max_sector_weight")
            # Identify the sector with the max weight for a more useful label.
            sector_weights = max_sector_block.get("weights") or {}
            top_sector = next(iter(sector_weights), None) if isinstance(sector_weights, dict) else None
            sector_cell = (
                f"{top_sector} {_fmt_pct(max_sector_weight)}"
                if top_sector and max_sector_weight is not None
                else (_fmt_pct(max_sector_weight) if max_sector_weight is not None else "—")
            )
            top_cell = (
                f"{top.get('ticker', '?')} {_fmt_signed_pct(top.get('contribution'))}"
                if top.get("ticker") else "—"
            )
            bot_cell = (
                f"{bot.get('ticker', '?')} {_fmt_signed_pct(bot.get('contribution'))}"
                if bot.get("ticker") else "—"
            )
            row = [
                slug,
                _fmt_signed_pct(ret),
                top_cell,
                bot_cell,
                f"{beta:.2f}" if isinstance(beta, (int, float)) else "—",
                sector_cell,
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")

        # Per-strategy hidden factor flags + top drawdown contributors.
        for slug in sorted(attribution_panels.keys()):
            panel = attribution_panels[slug]
            flags = panel.get("hidden_factor_flags") or []
            drawdowns = panel.get("top_drawdown_contributors") or []
            if flags or drawdowns:
                lines.append("")
                lines.append(f"  {slug}:")
                if flags:
                    lines.append(f"    hidden_factor_flags: {list(flags)}")
                if drawdowns:
                    dd_text = ", ".join(
                        f"{r.get('ticker', '?')} ({_fmt_signed_pct(r.get('contribution_to_drawdown'))})"
                        for r in drawdowns[:3]
                    )
                    lines.append(f"    top drawdown contributors: {dd_text}")

        comparison = inner.get("comparison")
        if isinstance(comparison, dict):
            outperformer = comparison.get("outperformer")
            underperformer = comparison.get("underperformer")
            gap = comparison.get("outperformance")
            if outperformer and underperformer:
                lines.append("")
                lines.append(
                    f"Comparison: {outperformer} outperformed {underperformer} "
                    f"by {_fmt_signed_pct(gap)}"
                    + (" (explicitly requested)" if comparison.get("explicitly_requested") else "")
                )
                md.append("")
                md.append(
                    f"**Comparison:** `{outperformer}` outperformed `{underperformer}` "
                    f"by {_fmt_signed_pct(gap)}"
                    + (" (explicitly requested)" if comparison.get("explicitly_requested") else "")
                )

        if attribution_narrative:
            lines.append("")
            lines.append("Narrative:")
            for narrative_line in attribution_narrative.splitlines():
                lines.append(f"  {narrative_line}")
            md.append("")
            md.append("### Narrative")
            md.append("")
            md.append("```")
            md.append(attribution_narrative)
            md.append("```")

    # OK / NO_RETURN_STREAM path for strategy_behavior_differentiation.
    # Detect via the unique `behavior_pairs` key (sibling tools use
    # `pairwise_overlap` / `pairwise_differentiation`).
    behavior_pairs = inner.get("behavior_pairs")
    is_behavior = isinstance(behavior_pairs, list) and (
        bool(behavior_pairs) or status == "NO_RETURN_STREAM"
    ) and (
        bool(behavior_pairs)
        or any(k in inner for k in ("nav_series_path", "candidate_artifact_inventory"))
    )
    if is_behavior and behavior_pairs:
        date_start = inner.get("date_range_start")
        date_end = inner.get("date_range_end")
        div_verdict = inner.get("behavioral_diversification_verdict")
        div_rationale = inner.get("behavioral_diversification_rationale")
        avg_corr = inner.get("average_pairwise_correlation")
        common_neg = inner.get("common_negative_days_count")
        most_similar = inner.get("most_behaviorally_similar_pair") or {}
        most_diff = inner.get("most_behaviorally_differentiated_pair") or {}

        lines.append("")
        if date_start and date_end:
            lines.append(
                f"NAV window: {date_start} → {date_end}   "
                f"Diversification: {div_verdict}"
            )
        if div_rationale:
            lines.append(f"  ({div_rationale})")
        if avg_corr is not None:
            lines.append(f"Average pairwise correlation: {_fmt_float(avg_corr)}")
        if common_neg:
            lines.append(f"Days where ALL selected strategies were negative: {common_neg}")
        if most_similar.get("left_slug"):
            lines.append(
                f"Most behaviorally similar:        {most_similar['left_slug']} ↔ "
                f"{most_similar['right_slug']}  corr={_fmt_float(most_similar.get('return_correlation'))}"
            )
        if most_diff.get("left_slug") and most_diff is not most_similar:
            lines.append(
                f"Most behaviorally differentiated: {most_diff['left_slug']} ↔ "
                f"{most_diff['right_slug']}  corr={_fmt_float(most_diff.get('return_correlation'))}"
            )
        lines.append("")
        header = ["pair", "tier", "corr", "downside_corr", "shared_neg_days", "n_obs"]
        col_widths = [max(len(h), 22) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))

        md.append("")
        md.append(f"## Behavioral differentiation — NAV window `{date_start} → {date_end}`")
        if div_verdict:
            md.append("")
            md.append(f"**Diversification verdict:** `{div_verdict}` — {div_rationale or ''}")
        if avg_corr is not None:
            md.append(f"**Average pairwise correlation:** `{_fmt_float(avg_corr)}`")
        md.append("")
        md.append("| pair | tier | correlation | downside_corr | shared_neg_days | n_obs |")
        md.append("| --- | --- | ---: | ---: | ---: | ---: |")
        for pair in behavior_pairs:
            pair_label = f"{pair.get('left_slug', '?')}↔{pair.get('right_slug', '?')}"
            shared_pct = pair.get("shared_negative_pct")
            shared_text = f"{pair.get('shared_negative_days', 0)} ({_fmt_pct(shared_pct)})"
            row = [
                pair_label,
                str(pair.get("behavioral_similarity_tier") or "?"),
                _fmt_float(pair.get("return_correlation")),
                _fmt_float(pair.get("downside_correlation")),
                shared_text,
                str(pair.get("n_observations") or 0),
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        # Per-pair worst shared drawdown + stability + caveats.
        for pair in behavior_pairs:
            extras: list[str] = []
            worst = pair.get("worst_shared_drawdown") or {}
            if worst.get("date"):
                extras.append(
                    f"worst shared drawdown day: {worst.get('date')} "
                    f"(left={_fmt_pct(worst.get('drawdown_left'))}, "
                    f"right={_fmt_pct(worst.get('drawdown_right'))}, "
                    f"{worst.get('shared_drawdown_days')} total shared-DD days)"
                )
            stab = pair.get("correlation_stability_iqr")
            if stab is not None:
                rolling_20 = pair.get("rolling_20d_correlation") or {}
                extras.append(
                    f"rolling 20D corr: mean={_fmt_float(rolling_20.get('mean'))}, "
                    f"p10={_fmt_float(rolling_20.get('p10'))}, "
                    f"p90={_fmt_float(rolling_20.get('p90'))}, IQR={_fmt_float(stab)}"
                )
            caveats = pair.get("caveats") or []
            if caveats:
                extras.append(f"caveats: {caveats}")
            if extras:
                lines.append("")
                lines.append(f"  {pair.get('left_slug', '?')}↔{pair.get('right_slug', '?')}:")
                for extra in extras:
                    lines.append(f"    {extra}")
        narrative_text = inner.get("narrative")
        if narrative_text:
            lines.append("")
            lines.append("Narrative:")
            for narrative_line in narrative_text.splitlines():
                lines.append(f"  {narrative_line}")
            md.append("")
            md.append("### Narrative")
            md.append("")
            md.append("```")
            md.append(narrative_text)
            md.append("```")

    elif status == "NO_RETURN_STREAM" and (
        inner.get("nav_series_path") or inner.get("candidate_artifact_inventory")
    ):
        lines.append("")
        lines.append("Required artifact missing:")
        lines.append(f"  {inner.get('nav_series_path', 'outputs/shadow_candidates/performance/shadow_nav_series.csv')}")
        inventory = inner.get("candidate_artifact_inventory") or {}
        found = inventory.get("candidates_found") or []
        missing_paths = inventory.get("candidates_missing") or []
        if found:
            lines.append("")
            lines.append("Candidate artifacts found:")
            for entry in found:
                if entry.get("dir"):
                    lines.append(f"  - {entry['path']} (dir, {entry.get('child_count', '?')} children)")
                else:
                    header_str = (
                        f" header={entry['header']}"
                        if entry.get("header") else ""
                    )
                    lines.append(
                        f"  - {entry['path']} ({entry.get('size_bytes', '?')} bytes,"
                        f" rows={entry.get('row_count_estimate', '?')}{header_str})"
                    )
        if missing_paths:
            lines.append("")
            lines.append("Candidate artifacts missing:")
            for path in missing_paths:
                lines.append(f"  - {path}")
        contract = inner.get("proposed_artifact_contract")
        if contract:
            lines.append("")
            lines.append("Proposed artifact contract:")
            for c_line in contract.splitlines():
                lines.append(f"  {c_line}")
        md.append("")
        md.append("## Behavioral differentiation — `NO_RETURN_STREAM`")
        md.append("")
        md.append(f"**Missing NAV series:** `{inner.get('nav_series_path', '?')}`")
        if contract:
            md.append("")
            md.append("### Proposed artifact contract")
            md.append("")
            md.append("```")
            md.append(contract)
            md.append("```")

    # OK path for strategy_differentiation — pairwise verdict table +
    # common factor flags + diversification verdict + narrative.
    # Detected via the unique `pairwise_differentiation` field name (the
    # sibling `shadow_comparison` tool uses `pairwise_overlap`).
    diff_pairs = inner.get("pairwise_differentiation")
    if status == "OK" and isinstance(diff_pairs, list) and diff_pairs:
        trade_date = inner.get("trade_date")
        div_verdict = inner.get("diversification_verdict")
        div_rationale = inner.get("diversification_rationale")
        common_flags = inner.get("common_factor_flags") or []
        most_similar = inner.get("most_similar_pair") or {}
        most_diff = inner.get("most_differentiated_pair") or {}
        lines.append("")
        if trade_date:
            lines.append(f"Trade date: {trade_date}   Diversification: {div_verdict}")
        if div_rationale:
            lines.append(f"  ({div_rationale})")
        if common_flags:
            lines.append(f"Common factor flags across all strategies: {common_flags}")
        if most_similar.get("left_slug"):
            lines.append(
                f"Most similar: {most_similar['left_slug']} ↔ {most_similar['right_slug']} "
                f"(similarity={_fmt_float(most_similar.get('similarity_score'))})"
            )
        if most_diff.get("left_slug") and most_diff is not most_similar:
            lines.append(
                f"Most differentiated: {most_diff['left_slug']} ↔ {most_diff['right_slug']} "
                f"(similarity={_fmt_float(most_diff.get('similarity_score'))})"
            )
        lines.append("")
        header = ["pair", "verdict", "similarity", "holdings_overlap", "sector_overlap", "factor_prox", "shared_top"]
        col_widths = [max(len(h), 20) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append(f"## Strategy differentiation — trade date `{trade_date or '?'}`")
        md.append("")
        if div_verdict:
            md.append(f"**Diversification verdict:** `{div_verdict}` — {div_rationale or ''}")
        if common_flags:
            md.append(f"**Common factor flags (all strategies):** `{common_flags}`")
        md.append("")
        md.append("| pair | verdict | similarity | holdings_overlap | sector_overlap | factor_proximity | shared_top_contributor |")
        md.append("| --- | --- | ---: | ---: | ---: | ---: | --- |")
        for pair in diff_pairs:
            pair_label = f"{pair.get('left_slug', '?')}↔{pair.get('right_slug', '?')}"
            row = [
                pair_label,
                str(pair.get("verdict") or "?"),
                _fmt_float(pair.get("similarity_score")),
                _fmt_pct(pair.get("holdings_overlap_pct")),
                _fmt_float(pair.get("sector_overlap_score")),
                _fmt_float(pair.get("factor_proximity_score")),
                str(pair.get("shared_top_contributor") or "—"),
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        # Per-pair extras: shared drawdown contributors + caveats.
        for pair in diff_pairs:
            extras: list[str] = []
            shared_dd = pair.get("shared_drawdown_contributors") or []
            if shared_dd:
                extras.append(f"shared drawdown contributors: {shared_dd[:3]}")
            caveats = pair.get("caveats") or []
            if caveats:
                extras.append(f"caveats: {caveats}")
            if extras:
                lines.append("")
                lines.append(f"  {pair.get('left_slug', '?')}↔{pair.get('right_slug', '?')}:")
                for extra in extras:
                    lines.append(f"    {extra}")
        narrative_text = inner.get("narrative")
        if narrative_text:
            lines.append("")
            lines.append("Narrative:")
            for narrative_line in narrative_text.splitlines():
                lines.append(f"  {narrative_line}")
            md.append("")
            md.append("### Narrative")
            md.append("")
            md.append("```")
            md.append(narrative_text)
            md.append("```")

    # OK path for promotion_readiness (strategy-aware) — render per-strategy
    # panel + recommendation + blockers + explanation. Detect via the
    # combination of `strategy_panels` (also used by shadow / attribution)
    # PLUS a `recommendation` field at the strategy-panel level, which is
    # unique to promotion_readiness.
    promo_panels = inner.get("strategy_panels") if isinstance(inner.get("strategy_panels"), dict) else None
    is_promotion_readiness = (
        status == "OK"
        and promo_panels
        and any(
            isinstance(p, dict) and "recommendation" in p and "blockers" in p
            for p in promo_panels.values()
        )
    )
    if is_promotion_readiness:
        trade_date = inner.get("strategy_trade_date") or inner.get("trade_date")
        closest = inner.get("closest_to_promotion")
        has_phase_c = inner.get("has_phase_c_sidecar")
        lines.append("")
        if trade_date:
            lines.append(f"Trade date: {trade_date}   Closest to promotion: {closest or 'n/a'}")
        if has_phase_c is False:
            lines.append("Phase C sidecar: missing (recommendation derived from shadow_evaluation + stability_analysis only)")
        elif has_phase_c is True:
            lines.append("Phase C sidecar: present (authoritative readiness_state used where available)")
        lines.append("")
        header = ["strategy", "recommendation", "conf", "excess_vs_spy", "max_dd", "vol", "valid_obs"]
        col_widths = [max(len(h), 18) for h in header]
        lines.append(_render_row(header, col_widths))
        lines.append(_render_row(["-" * w for w in col_widths], col_widths))
        md.append("")
        md.append(f"## Promotion readiness — trade date `{trade_date or '?'}`")
        md.append("")
        if closest:
            md.append(f"**Closest to promotion:** `{closest}`  ")
        md.append(f"**Phase C sidecar:** `{'present' if has_phase_c else 'missing'}`  ")
        md.append("")
        md.append("| strategy | recommendation | confidence | excess_vs_spy | max_drawdown | realized_vol | valid_obs_windows |")
        md.append("| --- | --- | --- | ---: | ---: | ---: | ---: |")
        ranked = inner.get("ranking_by_recommendation") or sorted(promo_panels.keys())
        for slug in ranked:
            panel = promo_panels.get(slug) or {}
            metrics = panel.get("metrics") or {}
            row = [
                slug,
                str(panel.get("recommendation") or "?"),
                str(panel.get("confidence") or "?"),
                _fmt_signed_pct(metrics.get("excess_return_vs_spy")),
                _fmt_signed_pct(metrics.get("max_drawdown")),
                _fmt_float(metrics.get("realized_volatility_ann")),
                str(panel.get("valid_observation_windows") or 0),
            ]
            lines.append(_render_row(row, col_widths))
            md.append("| " + " | ".join(row) + " |")
        # Per-strategy blockers + explanation.
        for slug in ranked:
            panel = promo_panels.get(slug) or {}
            blockers = panel.get("blockers") or []
            explanation = panel.get("explanation") or ""
            if blockers or explanation:
                lines.append("")
                lines.append(f"  {slug}:")
                if blockers:
                    lines.append(f"    blockers: {list(blockers)}")
                if explanation:
                    lines.append(f"    explanation: {explanation}")
        # Strategy-level warnings (separate from the top-level warnings list).
        strat_warnings = inner.get("strategy_warnings") or []
        if strat_warnings:
            lines.append("")
            lines.append("Strategy warnings:")
            for warning in strat_warnings:
                lines.append(f"  - {warning}")
            md.append("")
            md.append("### Strategy warnings")
            md.append("")
            for warning in strat_warnings:
                md.append(f"- {warning}")

    # OK path for stable_window_evaluation — render per-policy dispersion +
    # promotion-validity blocks + caveats.
    policy_panels = inner.get("policy_panels")
    promotion_validity = inner.get("promotion_validity")
    is_stable_window = (
        status == "OK"
        and isinstance(policy_panels, list)
        and any(isinstance(p, dict) and "dispersion" in p for p in policy_panels or [])
    )
    if is_stable_window:
        lines.append("")
        md.append("")
        md.append("## Stable-window / random-window evaluation")
        for panel in policy_panels:
            policy = panel.get("policy")
            years = panel.get("years")
            n = panel.get("n_windows")
            dispersion = panel.get("dispersion") or {}
            consistency = panel.get("consistency") or {}
            sensitivity = (panel.get("start_date_sensitivity") or {}).get("interpretation")
            insufficient = panel.get("insufficient_sample")
            tag = " *insufficient_sample*" if insufficient else ""
            lines.append(f"Policy {policy} ({years}-year, n={n}){tag}")
            lines.append(
                f"  CAGR:        p10={_fmt_signed_pct(dispersion.get('cagr', {}).get('p10'))}  "
                f"median={_fmt_signed_pct(dispersion.get('cagr', {}).get('median'))}  "
                f"p90={_fmt_signed_pct(dispersion.get('cagr', {}).get('p90'))}"
            )
            lines.append(
                f"  Drawdown:    p10={_fmt_signed_pct(dispersion.get('max_drawdown', {}).get('p10'))}  "
                f"median={_fmt_signed_pct(dispersion.get('max_drawdown', {}).get('median'))}  "
                f"p90={_fmt_signed_pct(dispersion.get('max_drawdown', {}).get('p90'))}"
            )
            sh = dispersion.get("sharpe", {})
            lines.append(
                f"  Sharpe:      p10={_fmt_float(sh.get('p10'))}  "
                f"median={_fmt_float(sh.get('median'))}  "
                f"p90={_fmt_float(sh.get('p90'))}"
            )
            lines.append(
                f"  Consistency: {_fmt_pct(consistency.get('fraction_positive_return'))} "
                f"of {n} windows positive    Start-date sensitivity: {sensitivity}"
            )
            worst_dd = panel.get("worst_window_by_drawdown") or {}
            best_cagr = panel.get("best_window_by_cagr") or {}
            if worst_dd.get("start_date"):
                lines.append(
                    f"  Worst drawdown: {worst_dd.get('start_date')} → "
                    f"{worst_dd.get('end_date', '?')}   max_dd={_fmt_signed_pct(worst_dd.get('max_drawdown'))}  "
                    f"CAGR={_fmt_signed_pct(worst_dd.get('cagr'))}"
                )
            if best_cagr.get("start_date"):
                lines.append(
                    f"  Best CAGR:      {best_cagr.get('start_date')} → "
                    f"{best_cagr.get('end_date', '?')}   CAGR={_fmt_signed_pct(best_cagr.get('cagr'))}  "
                    f"max_dd={_fmt_signed_pct(best_cagr.get('max_drawdown'))}"
                )
            lines.append("")
            # Markdown table for this policy
            md.append("")
            md.append(f"### Policy `{policy}` ({years}-year, n={n}){tag}")
            md.append("")
            md.append("| metric | p10 | median | p90 |")
            md.append("| --- | ---: | ---: | ---: |")
            for metric_name in ("cagr", "max_drawdown", "sharpe", "ulcer_index"):
                d = dispersion.get(metric_name) or {}
                md.append(
                    f"| {metric_name} | {_fmt_float(d.get('p10'))} | "
                    f"{_fmt_float(d.get('median'))} | {_fmt_float(d.get('p90'))} |"
                )
        if isinstance(promotion_validity, dict) and promotion_validity:
            lines.append("Promotion validity:")
            md.append("")
            md.append("### Promotion validity")
            md.append("")
            for mode, block in promotion_validity.items():
                if not isinstance(block, dict):
                    continue
                valid = block.get("valid_days_since_inception")
                shadow_only = block.get("shadow_only_days_since_inception")
                excluded = block.get("diagnostic_excluded_count")
                lines.append(
                    f"  {mode}: valid_days={valid}  shadow_only={shadow_only}  excluded={excluded}"
                )
                md.append(f"- `{mode}`: valid_days={valid}, shadow_only={shadow_only}, excluded={excluded}")
        caveats = inner.get("confidence_caveats") or []
        if caveats:
            lines.append("")
            lines.append(f"Confidence caveats: {caveats}")
            md.append("")
            md.append(f"**Confidence caveats:** `{caveats}`")

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
