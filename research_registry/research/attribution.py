"""Read-only performance attribution analysis.

Backs the ``attribution_analysis`` MCP tool. Reads the per-strategy
artifacts that the attribution pipeline already produces under
``outputs/attribution/<DATE>/`` and emits a structured panel of:

* per-strategy headline return + top contributors / detractors
  (from ``attribution_summary.json``)
* top drawdown contributors (from ``contribution_report.json``)
* factor exposures — market beta, momentum, volatility, sector
  concentration, plus availability flags for growth/value and
  quality/profitability tilts (from ``factor_exposure.json``)
* regime-stratified performance (from ``regime_performance_breakdown.json``)
* a deterministic, template-driven narrative summary

The tool is **read-only** and never invents numbers. Any field that is
``null`` or ``UNAVAILABLE`` in the source is surfaced verbatim in the
panel's ``unavailable_metrics`` list. The narrative template names only
fields that are present.

Question handling
-----------------
Strategy names are parsed from the question text using the same closed
set the shadow comparison tool uses (``polaris | orion | lyra | leda``).
If two strategy names appear, the tool adds a comparison block to the
output identifying the outperformer and the headline performance delta.
If no strategy name is mentioned, all available strategies are returned.

Fail-closed contract
--------------------
* ``status="NO_ATTRIBUTION_DATA"`` — no ``outputs/attribution/<DATE>/``
  directory on disk with an ``attribution_summary.json``.
* ``status="NEEDS_DATA"`` — a strategy named in the question is not
  present in the artifact (typo or strategy not yet running). The
  response carries ``missing_strategies``.
* ``status="OK"`` — at least one panel is populated. Per-strategy
  ``unavailable_metrics`` is populated when the underlying source
  reports null / UNAVAILABLE.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from research_registry.research.shadow_comparison import (
    BENCHMARK_SLUG,
    KNOWN_STRATEGY_NAMES,
    parse_strategy_names,
    strategy_slug,
)

DEFAULT_ATTRIBUTION_ROOT = Path("outputs/attribution")

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def select_latest_attribution_date(
    attribution_root: Path = DEFAULT_ATTRIBUTION_ROOT,
) -> Optional[Path]:
    """Return the lexicographically-latest ``<DATE>`` dir with a summary."""
    if not attribution_root.exists() or not attribution_root.is_dir():
        return None
    candidates = [
        p for p in attribution_root.iterdir()
        if p.is_dir() and _DATE_DIR_RE.match(p.name)
        and (p / "attribution_summary.json").exists()
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda p: p.name)[-1]


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-file extractors. Each takes the per-strategy entry from one
# artifact and returns a normalised slice of the panel, plus an
# ``unavailable_metrics`` list listing the fields the source did not
# populate. We never invent values.
# ---------------------------------------------------------------------------


def _extract_from_summary(entry: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    unavailable: list[str] = []
    portfolio_return = _coerce_float(entry.get("portfolio_21d_return_current_book"))
    if portfolio_return is None:
        unavailable.append("portfolio_21d_return_current_book")

    top_contributor = _normalise_contribution_row(entry.get("primary_21d_return_source"))
    top_detractor = _normalise_contribution_row(entry.get("primary_21d_detractor"))
    if top_contributor is None:
        unavailable.append("primary_21d_return_source")
    if top_detractor is None:
        unavailable.append("primary_21d_detractor")

    return (
        {
            "strategy_name": entry.get("strategy_name"),
            "portfolio_return_21d": portfolio_return,
            "market_beta": _coerce_float(entry.get("market_beta")),
            "max_sector_weight": _coerce_float(entry.get("max_sector_weight")),
            "top3_contribution_share_21d": _coerce_float(entry.get("top3_contribution_share_21d")),
            "best_risk_regime": entry.get("best_risk_regime"),
            "worst_risk_regime": entry.get("worst_risk_regime"),
            "hidden_factor_flags": list(entry.get("hidden_factor_flags") or []),
            "decision_attribution_status": entry.get("decision_attribution_status"),
            "top_contributor": top_contributor,
            "top_detractor": top_detractor,
        },
        unavailable,
    )


def _normalise_contribution_row(row: Any) -> Optional[dict[str, Any]]:
    if not isinstance(row, Mapping):
        return None
    return {
        "ticker": row.get("ticker"),
        "sector": row.get("sector"),
        "weight": _coerce_float(row.get("weight")),
        "return": _coerce_float(row.get("return")),
        "contribution": _coerce_float(row.get("contribution")),
        "contribution_pct_of_portfolio_return": _coerce_float(
            row.get("contribution_pct_of_portfolio_return")
        ),
    }


def _extract_drawdown_contributors(
    entry: Mapping[str, Any] | None,
    *,
    limit: int = 5,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(entry, Mapping):
        return [], ["drawdown_contribution"]
    dd = entry.get("drawdown_contribution")
    if not isinstance(dd, Mapping):
        return [], ["drawdown_contribution"]
    rows = dd.get("top_drawdown_contributors") or []
    out: list[dict[str, Any]] = []
    for row in rows[:limit]:
        if not isinstance(row, Mapping):
            continue
        out.append(
            {
                "ticker": row.get("ticker"),
                "sector": row.get("sector"),
                "contribution_to_drawdown": _coerce_float(row.get("contribution_to_drawdown")),
            }
        )
    return out, [] if out else ["drawdown_contribution"]


def _extract_factor_exposures(
    entry: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(entry, Mapping):
        return {}, ["factor_exposure"]
    unavailable: list[str] = []

    momentum_block = entry.get("momentum_exposure") if isinstance(entry.get("momentum_exposure"), Mapping) else {}
    weighted_momentum = _coerce_float(momentum_block.get("weighted_12_1_momentum"))
    if weighted_momentum is None:
        unavailable.append("weighted_12_1_momentum")

    vol_block = entry.get("volatility_exposure") if isinstance(entry.get("volatility_exposure"), Mapping) else {}
    weighted_vol = _coerce_float(vol_block.get("weighted_20d_ann_vol"))
    if weighted_vol is None:
        unavailable.append("weighted_20d_ann_vol")

    # Growth/value, quality/profitability, and market-cap proxy may be
    # explicitly UNAVAILABLE / LIQUIDITY_PROXY_ONLY — we surface whatever
    # status string the source carries.
    growth_value = entry.get("growth_value_tilt") if isinstance(entry.get("growth_value_tilt"), Mapping) else {}
    quality = entry.get("quality_profitability_tilt") if isinstance(entry.get("quality_profitability_tilt"), Mapping) else {}
    market_cap = entry.get("market_cap_tilt_proxy") if isinstance(entry.get("market_cap_tilt_proxy"), Mapping) else {}

    out = {
        "market_beta": _coerce_float(entry.get("market_beta")),
        "market_correlation": _coerce_float(entry.get("market_correlation")),
        "realized_volatility_ann_current_book": _coerce_float(entry.get("realized_volatility_ann_current_book")),
        "weighted_12_1_momentum": weighted_momentum,
        "weighted_20d_ann_vol": weighted_vol,
        "growth_value_tilt_status": growth_value.get("status"),
        "quality_profitability_tilt_status": quality.get("status"),
        "market_cap_tilt_status": market_cap.get("status"),
        "selection_alpha_interpretation": entry.get("selection_alpha_interpretation"),
    }
    for key in ("market_beta", "market_correlation", "realized_volatility_ann_current_book"):
        if out[key] is None:
            unavailable.append(key)
    return out, unavailable


def _extract_sector_exposure(
    factor_entry: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(factor_entry, Mapping):
        return {}, ["sector_exposure"]
    sector_block = factor_entry.get("sector_exposure") if isinstance(factor_entry.get("sector_exposure"), Mapping) else {}
    weights_raw = sector_block.get("weights") or {}
    weights = {
        str(sector): _coerce_float(weight)
        for sector, weight in weights_raw.items()
        if _coerce_float(weight) is not None
    }
    out = {
        "weights": dict(sorted(weights.items(), key=lambda kv: -(kv[1] or 0.0))),
        "max_sector_weight": _coerce_float(sector_block.get("max_sector_weight")),
        "sector_hhi": _coerce_float(sector_block.get("sector_hhi")),
    }
    return out, [] if weights else ["sector_exposure_weights"]


def _extract_regime_performance(
    regime_entry: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    if not isinstance(regime_entry, Mapping):
        return {}, ["regime_performance"]
    perf = regime_entry.get("performance_by_regime") if isinstance(regime_entry.get("performance_by_regime"), Mapping) else {}
    interpretation = regime_entry.get("interpretation") if isinstance(regime_entry.get("interpretation"), Mapping) else {}
    if not perf:
        return {}, ["regime_performance"]
    # Pass through the four standard regime axes if present; ignore anything else.
    out: dict[str, Any] = {
        "best_risk_regime": interpretation.get("best_risk_regime"),
        "worst_risk_regime": interpretation.get("worst_risk_regime"),
    }
    for axis in ("risk_regime", "breadth_regime", "trend_regime", "volatility_regime"):
        block = perf.get(axis) if isinstance(perf.get(axis), Mapping) else None
        if not block:
            continue
        out[axis] = {
            label: {
                "avg_daily_return": _coerce_float(metrics.get("avg_daily_return")),
                "cumulative_return": _coerce_float(metrics.get("cumulative_return")),
                "excess_vs_spy": _coerce_float(metrics.get("excess_vs_spy")),
                "hit_rate": _coerce_float(metrics.get("hit_rate")),
                "valid_days": metrics.get("valid_days"),
            }
            for label, metrics in block.items()
            if isinstance(metrics, Mapping)
        }
    return out, []


# ---------------------------------------------------------------------------
# Per-strategy panel assembly
# ---------------------------------------------------------------------------


def _strategy_panel(
    *,
    slug: str,
    summary_entry: Mapping[str, Any],
    contribution_entry: Mapping[str, Any] | None,
    factor_entry: Mapping[str, Any] | None,
    regime_entry: Mapping[str, Any] | None,
) -> dict[str, Any]:
    summary_block, summary_unavailable = _extract_from_summary(summary_entry)
    drawdown_contribs, drawdown_unavailable = _extract_drawdown_contributors(contribution_entry)
    factor_block, factor_unavailable = _extract_factor_exposures(factor_entry)
    sector_block, sector_unavailable = _extract_sector_exposure(factor_entry)
    regime_block, regime_unavailable = _extract_regime_performance(regime_entry)

    panel: dict[str, Any] = {"strategy_slug": slug}
    panel.update(summary_block)
    panel["top_drawdown_contributors"] = drawdown_contribs
    panel["factor_exposures"] = factor_block
    panel["sector_exposure"] = sector_block
    panel["regime_performance"] = regime_block

    unavailable: list[str] = []
    unavailable.extend(summary_unavailable)
    unavailable.extend(drawdown_unavailable)
    unavailable.extend(factor_unavailable)
    unavailable.extend(sector_unavailable)
    unavailable.extend(regime_unavailable)
    panel["unavailable_metrics"] = unavailable
    return panel


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AttributionAnswer:
    status: str
    trade_date: Optional[str]
    attribution_root: str
    source_paths: list[str]
    available_strategies: tuple[str, ...]
    requested_strategies: tuple[str, ...]
    panels: dict[str, dict[str, Any]]
    comparison: Optional[dict[str, Any]]
    leader_by_return: Optional[str]
    narrative: str
    missing_strategies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def attribution_summary_to_dict(answer: AttributionAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "trade_date": answer.trade_date,
        "attribution_root": answer.attribution_root,
        "source_paths": list(answer.source_paths),
        "available_strategies": list(answer.available_strategies),
        "requested_strategies": list(answer.requested_strategies),
        "panels": answer.panels,
        "comparison": answer.comparison,
        "leader_by_return": answer.leader_by_return,
        "narrative": answer.narrative,
        "missing_strategies": list(answer.missing_strategies),
        "warnings": list(answer.warnings),
    }


def analyse_attribution(
    *,
    attribution_root: Path = DEFAULT_ATTRIBUTION_ROOT,
    question: str | None = None,
    strategies: Iterable[str] | None = None,
) -> AttributionAnswer:
    latest = select_latest_attribution_date(attribution_root)
    if latest is None:
        return AttributionAnswer(
            status="NO_ATTRIBUTION_DATA",
            trade_date=None,
            attribution_root=str(attribution_root),
            source_paths=[],
            available_strategies=(),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            panels={},
            comparison=None,
            leader_by_return=None,
            narrative=(
                f"No attribution artifacts on disk under {attribution_root}. "
                "Run the attribution pipeline first."
            ),
            warnings=[f"no attribution_summary.json found under {attribution_root}"],
        )

    summary_path = latest / "attribution_summary.json"
    contribution_path = latest / "contribution_report.json"
    factor_path = latest / "factor_exposure.json"
    regime_path = latest / "regime_performance_breakdown.json"

    summary_payload = _safe_json(summary_path) or {}
    contribution_payload = _safe_json(contribution_path) or {}
    factor_payload = _safe_json(factor_path) or {}
    regime_payload = _safe_json(regime_path) or {}

    summary_strategies = summary_payload.get("strategies") or {}
    available = tuple(sorted(summary_strategies.keys()))
    if not available:
        return AttributionAnswer(
            status="NEEDS_DATA",
            trade_date=str(summary_payload.get("trade_date") or ""),
            attribution_root=str(attribution_root),
            source_paths=[str(summary_path)],
            available_strategies=(),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            panels={},
            comparison=None,
            leader_by_return=None,
            narrative="attribution_summary.json has no strategies block.",
            warnings=["attribution_summary.json has no strategies"],
        )

    requested_names = list(strategies) if strategies else parse_strategy_names(question or "")
    if requested_names:
        requested_slugs = [strategy_slug(name) for name in requested_names]
        missing_slugs = [slug for slug in requested_slugs if slug not in summary_strategies]
        selected_slugs = [slug for slug in requested_slugs if slug in summary_strategies]
        if not selected_slugs and missing_slugs:
            return AttributionAnswer(
                status="NEEDS_DATA",
                trade_date=str(summary_payload.get("trade_date") or ""),
                attribution_root=str(attribution_root),
                source_paths=[str(summary_path)],
                available_strategies=available,
                requested_strategies=tuple(requested_names),
                panels={},
                comparison=None,
                leader_by_return=None,
                narrative=(
                    f"None of the requested strategies ({requested_names}) are "
                    f"present in attribution_summary.json. Available: {list(available)}."
                ),
                missing_strategies=missing_slugs,
                warnings=[f"missing strategy slug: {slug}" for slug in missing_slugs],
            )
    else:
        requested_slugs = list(available)
        missing_slugs = []
        selected_slugs = list(available)

    panels: dict[str, dict[str, Any]] = {}
    for slug in selected_slugs:
        if slug == BENCHMARK_SLUG:
            continue
        panel = _strategy_panel(
            slug=slug,
            summary_entry=summary_strategies[slug],
            contribution_entry=(contribution_payload.get("strategies") or {}).get(slug),
            factor_entry=(factor_payload.get("strategies") or {}).get(slug),
            regime_entry=(regime_payload.get("strategies") or {}).get(slug),
        )
        panels[slug] = panel

    leader = _pick_leader_by_return(panels)
    comparison = _build_comparison(panels, requested_names) if len(panels) >= 2 else None
    narrative = _render_narrative(
        trade_date=str(summary_payload.get("trade_date") or latest.name),
        panels=panels,
        comparison=comparison,
        question=question or "",
    )

    warnings: list[str] = []
    if missing_slugs:
        warnings.extend(f"missing strategy slug: {slug}" for slug in missing_slugs)
    for slug, panel in panels.items():
        if panel.get("unavailable_metrics"):
            warnings.append(f"{slug}: unavailable metrics = {panel['unavailable_metrics']}")

    return AttributionAnswer(
        status="OK",
        trade_date=str(summary_payload.get("trade_date") or latest.name),
        attribution_root=str(attribution_root),
        source_paths=[
            str(summary_path),
            str(contribution_path) if contribution_path.exists() else f"{contribution_path} (missing)",
            str(factor_path) if factor_path.exists() else f"{factor_path} (missing)",
            str(regime_path) if regime_path.exists() else f"{regime_path} (missing)",
        ],
        available_strategies=available,
        requested_strategies=tuple(requested_names) if requested_names else available,
        panels=panels,
        comparison=comparison,
        leader_by_return=leader,
        narrative=narrative,
        missing_strategies=missing_slugs,
        warnings=warnings,
    )


def _pick_leader_by_return(panels: Mapping[str, Mapping[str, Any]]) -> Optional[str]:
    best_slug: Optional[str] = None
    best_value: Optional[float] = None
    for slug, panel in panels.items():
        value = panel.get("portfolio_return_21d")
        if value is None:
            continue
        if best_value is None or value > best_value:
            best_value = value
            best_slug = slug
    return best_slug


def _build_comparison(
    panels: Mapping[str, Mapping[str, Any]],
    requested_names: list[str],
) -> Optional[dict[str, Any]]:
    """If a comparison was requested (two strategies named), summarise it.

    With more than two strategies present in ``panels``, we still pick the
    best vs the worst by 21d return so the ``leader_by_return`` field has
    a paired runner-up. The comparison block is intentionally minimal:
    headline returns + the delta + each side's top contributor.
    """
    by_return: list[tuple[str, float]] = sorted(
        (
            (slug, panel.get("portfolio_return_21d"))
            for slug, panel in panels.items()
            if panel.get("portfolio_return_21d") is not None
        ),
        key=lambda kv: -(kv[1] or 0.0),
    )
    if len(by_return) < 2:
        return None
    leader_slug, leader_ret = by_return[0]
    trailer_slug, trailer_ret = by_return[-1]
    if leader_slug == trailer_slug:
        return None
    leader_panel = panels[leader_slug]
    trailer_panel = panels[trailer_slug]
    return {
        "outperformer": leader_slug,
        "underperformer": trailer_slug,
        "outperformer_return_21d": leader_ret,
        "underperformer_return_21d": trailer_ret,
        "outperformance": (leader_ret or 0.0) - (trailer_ret or 0.0),
        "outperformer_top_contributor": leader_panel.get("top_contributor"),
        "underperformer_top_contributor": trailer_panel.get("top_contributor"),
        "outperformer_top_detractor": leader_panel.get("top_detractor"),
        "underperformer_top_detractor": trailer_panel.get("top_detractor"),
        "explicitly_requested": len(requested_names) >= 2,
    }


# ---------------------------------------------------------------------------
# Deterministic narrative (template, no LLM)
# ---------------------------------------------------------------------------


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_signed_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _render_narrative(
    *,
    trade_date: str,
    panels: Mapping[str, Mapping[str, Any]],
    comparison: Mapping[str, Any] | None,
    question: str,
) -> str:
    """Template-driven; no randomness, no LLM. The same inputs always
    produce the same output string."""
    if not panels:
        return "No attribution panels were populated."

    lines: list[str] = []
    lines.append(
        f"Performance attribution for trade date {trade_date} (current-book "
        f"trailing exposure, ~21 trading days)."
    )
    for slug in sorted(panels.keys()):
        panel = panels[slug]
        name = panel.get("strategy_name") or slug
        ret = panel.get("portfolio_return_21d")
        top = panel.get("top_contributor") or {}
        bot = panel.get("top_detractor") or {}
        line = (
            f"  • {name}: 21d return {_fmt_signed_pct(ret)}. "
            f"Top contributor: {top.get('ticker') or 'n/a'} ({top.get('sector') or '—'}, "
            f"contribution {_fmt_signed_pct(top.get('contribution'))}, "
            f"weight {_fmt_pct(top.get('weight'))}). "
            f"Top detractor: {bot.get('ticker') or 'n/a'} ({bot.get('sector') or '—'}, "
            f"contribution {_fmt_signed_pct(bot.get('contribution'))})."
        )
        lines.append(line)
        flags = panel.get("hidden_factor_flags") or []
        if flags:
            beta = panel.get("market_beta")
            beta_text = f" (β={beta:.2f})" if isinstance(beta, (int, float)) else ""
            lines.append(f"      Hidden factor flags: {list(flags)}{beta_text}.")
        sector_block = panel.get("sector_exposure") or {}
        max_sector = sector_block.get("max_sector_weight")
        sector_weights = sector_block.get("weights") or {}
        if max_sector is not None and sector_weights:
            top_sector = next(iter(sector_weights))
            lines.append(
                f"      Sector concentration: max {top_sector} {_fmt_pct(max_sector)}."
            )

    if comparison:
        lines.append("")
        outperformer_panel = panels.get(comparison["outperformer"]) or {}
        underperformer_panel = panels.get(comparison["underperformer"]) or {}
        outperformer_name = outperformer_panel.get("strategy_name") or comparison["outperformer"]
        underperformer_name = underperformer_panel.get("strategy_name") or comparison["underperformer"]
        gap_text = _fmt_signed_pct(comparison.get("outperformance"))
        lines.append(
            f"Comparison: {outperformer_name} ({_fmt_signed_pct(comparison['outperformer_return_21d'])}) "
            f"outperformed {underperformer_name} "
            f"({_fmt_signed_pct(comparison['underperformer_return_21d'])}) by {gap_text}."
        )
        top_out = comparison.get("outperformer_top_contributor") or {}
        top_under = comparison.get("underperformer_top_contributor") or {}
        if top_out.get("ticker") or top_under.get("ticker"):
            lines.append(
                f"  Leader's largest contributor: {top_out.get('ticker') or 'n/a'} "
                f"({_fmt_signed_pct(top_out.get('contribution'))}). "
                f"Trailer's largest contributor: {top_under.get('ticker') or 'n/a'} "
                f"({_fmt_signed_pct(top_under.get('contribution'))})."
            )

    lines.append("")
    lines.append(
        "Note: attribution is current-book trailing exposure, not historical realised "
        "positions. Selection alpha vs factors is partial; treat as descriptive, not causal."
    )
    return "\n".join(lines)
