"""Strategy differentiation / common factor analysis.

Backs the ``strategy_differentiation`` MCP tool. Answers whether the
challenger strategies (polaris, orion, lyra, leda) are genuinely
different bets or mostly the same factor / sector / holding exposure.

Reads existing artifacts only — never creates them:

* ``outputs/shadow_candidates/<DATE>/comparison.json`` — pairwise
  holdings overlap (overlap_weight_pct, shared_names, unique names).
* ``outputs/shadow_candidates/<DATE>/shadow_evaluation.json`` —
  per-strategy turnover and concentration (for context).
* ``outputs/attribution/<DATE>/factor_exposure.json`` — per-strategy
  market_beta, momentum, volatility, sector_exposure.
* ``outputs/attribution/<DATE>/attribution_summary.json`` — per-strategy
  primary_21d_return_source / primary_21d_detractor +
  hidden_factor_flags.
* ``outputs/attribution/<DATE>/contribution_report.json`` — per-strategy
  drawdown_contribution.top_drawdown_contributors.

Per-pair similarity score (deterministic):
    avg of the available components, each normalized to [0,1]:
      * holdings_overlap_pct                          (from comparison)
      * sector_overlap_score                          (1 if same top sector
                                                       above 50%; partial
                                                       otherwise)
      * factor_proximity                              (1 - clipped |beta|
                                                       / |momentum| / |vol|
                                                       differences)

Pair verdict tiers (deterministic, no LLM):
    highly_overlapping       : similarity >= 0.7 OR overlap >= 0.7
    differentiated           : similarity <= 0.3 AND overlap <= 0.3
    partially_differentiated : otherwise
    insufficient_evidence    : < 2 score components available

Top-level verdict + recommendation come from the distribution of pair
verdicts. We never invent metrics; missing inputs become explicit
``warnings`` and the ``insufficient_evidence`` tier.
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

DEFAULT_OUTPUTS_ROOT = Path("outputs")
DEFAULT_SHADOW_ROOT = Path("outputs/shadow_candidates")
DEFAULT_ATTRIBUTION_ROOT = Path("outputs/attribution")

# Clipping bounds when normalising factor distance to [0,1].
# Beyond these the strategies are treated as "fully different" on
# that axis. Chosen conservatively so two strategies need a meaningful
# spread to register as factor-differentiated.
_BETA_CLIP = 1.0      # |beta_a - beta_b| >= 1.0 → score 0 on this axis
_MOMENTUM_CLIP = 2.0  # |momentum_a - momentum_b| >= 2.0 → score 0
_VOL_CLIP = 0.30      # |vol_a - vol_b| >= 0.30 → score 0

HIGHLY_OVERLAPPING_THRESHOLD = 0.70
DIFFERENTIATED_THRESHOLD = 0.30

_DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Discovery + IO
# ---------------------------------------------------------------------------


def _safe_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _select_latest_dated_dir(root: Path) -> Optional[Path]:
    """Return the lex-max ``YYYY-MM-DD`` directory or the ``latest`` alias."""
    if not root.exists() or not root.is_dir():
        return None
    alias = root / "latest"
    if alias.exists() and alias.is_dir():
        return alias
    dated = [p for p in root.iterdir() if p.is_dir() and _DATE_DIR_RE.match(p.name)]
    if not dated:
        return None
    return sorted(dated, key=lambda p: p.name)[-1]


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Per-strategy snapshot
# ---------------------------------------------------------------------------


def _strategy_snapshot(
    *,
    slug: str,
    eval_entry: Optional[Mapping[str, Any]],
    factor_entry: Optional[Mapping[str, Any]],
    attribution_summary_entry: Optional[Mapping[str, Any]],
    contribution_entry: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    """Collect every datum we read about one strategy into a flat snapshot."""
    sector_block = (
        factor_entry.get("sector_exposure")
        if isinstance(factor_entry, Mapping) and isinstance(factor_entry.get("sector_exposure"), Mapping)
        else {}
    )
    weights = sector_block.get("weights") or {}
    sorted_sectors = sorted(
        ((k, _coerce_float(v)) for k, v in weights.items() if _coerce_float(v) is not None),
        key=lambda kv: -(kv[1] or 0.0),
    )
    top_sector = sorted_sectors[0][0] if sorted_sectors else None
    top_sector_weight = sorted_sectors[0][1] if sorted_sectors else None

    momentum_block = (
        factor_entry.get("momentum_exposure")
        if isinstance(factor_entry, Mapping) and isinstance(factor_entry.get("momentum_exposure"), Mapping)
        else {}
    )
    vol_block = (
        factor_entry.get("volatility_exposure")
        if isinstance(factor_entry, Mapping) and isinstance(factor_entry.get("volatility_exposure"), Mapping)
        else {}
    )

    top_contributor = (
        attribution_summary_entry.get("primary_21d_return_source")
        if isinstance(attribution_summary_entry, Mapping) else None
    )
    top_detractor = (
        attribution_summary_entry.get("primary_21d_detractor")
        if isinstance(attribution_summary_entry, Mapping) else None
    )
    hidden_flags = list(
        attribution_summary_entry.get("hidden_factor_flags")
        if isinstance(attribution_summary_entry, Mapping)
        and isinstance(attribution_summary_entry.get("hidden_factor_flags"), list)
        else []
    )

    drawdown_rows: list[dict[str, Any]] = []
    if isinstance(contribution_entry, Mapping):
        dd = contribution_entry.get("drawdown_contribution")
        if isinstance(dd, Mapping):
            for row in (dd.get("top_drawdown_contributors") or [])[:5]:
                if isinstance(row, Mapping):
                    drawdown_rows.append(
                        {
                            "ticker": row.get("ticker"),
                            "sector": row.get("sector"),
                            "contribution_to_drawdown": _coerce_float(row.get("contribution_to_drawdown")),
                        }
                    )

    eval_metrics: dict[str, Any] = {}
    if isinstance(eval_entry, Mapping):
        eval_metrics = {
            "avg_turnover": _coerce_float(eval_entry.get("avg_turnover")),
            "avg_top_3_concentration": _coerce_float(eval_entry.get("avg_top_3_concentration")),
            "max_drawdown": _coerce_float(eval_entry.get("max_drawdown")),
            "realized_volatility_ann": _coerce_float(eval_entry.get("realized_volatility_ann")),
        }

    return {
        "strategy_slug": slug,
        "top_sector": top_sector,
        "top_sector_weight": top_sector_weight,
        "sector_weights": {k: _coerce_float(v) for k, v in weights.items() if _coerce_float(v) is not None},
        "market_beta": _coerce_float(
            (factor_entry or {}).get("market_beta") if isinstance(factor_entry, Mapping) else None
        ),
        "weighted_12_1_momentum": _coerce_float(momentum_block.get("weighted_12_1_momentum")),
        "weighted_20d_ann_vol": _coerce_float(vol_block.get("weighted_20d_ann_vol")),
        "hidden_factor_flags": hidden_flags,
        "top_contributor": (
            {
                "ticker": top_contributor.get("ticker"),
                "sector": top_contributor.get("sector"),
                "contribution": _coerce_float(top_contributor.get("contribution")),
            }
            if isinstance(top_contributor, Mapping) and top_contributor.get("ticker")
            else None
        ),
        "top_detractor": (
            {
                "ticker": top_detractor.get("ticker"),
                "sector": top_detractor.get("sector"),
                "contribution": _coerce_float(top_detractor.get("contribution")),
            }
            if isinstance(top_detractor, Mapping) and top_detractor.get("ticker")
            else None
        ),
        "top_drawdown_contributors": drawdown_rows,
        "evaluation_metrics": eval_metrics,
    }


# ---------------------------------------------------------------------------
# Per-pair similarity
# ---------------------------------------------------------------------------


def _clipped_proximity(diff: Optional[float], clip: float) -> Optional[float]:
    """Return ``1 - |diff|/clip`` clamped to [0,1], or None if diff is None."""
    if diff is None or clip <= 0:
        return None
    score = 1.0 - min(abs(diff) / clip, 1.0)
    return round(score, 6)


def _factor_proximity(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[Optional[float], dict[str, Any]]:
    """Average of available factor-axis proximities + the underlying deltas."""
    axes: list[tuple[str, float, str]] = [
        ("market_beta", _BETA_CLIP, "beta_difference"),
        ("weighted_12_1_momentum", _MOMENTUM_CLIP, "momentum_difference"),
        ("weighted_20d_ann_vol", _VOL_CLIP, "vol_difference"),
    ]
    detail: dict[str, Any] = {}
    proximities: list[float] = []
    for field_name, clip, detail_key in axes:
        a = left.get(field_name)
        b = right.get(field_name)
        if a is None or b is None:
            detail[detail_key] = None
            continue
        diff = float(a) - float(b)
        detail[detail_key] = round(diff, 6)
        prox = _clipped_proximity(diff, clip)
        if prox is not None:
            proximities.append(prox)
    if not proximities:
        return None, detail
    return round(sum(proximities) / len(proximities), 6), detail


def _sector_overlap_score(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[Optional[float], Optional[str]]:
    """Return ``(score, shared_top_sector)``.

    score = sum of min(weight_left, weight_right) across all sectors
    present in both. Returns ``(None, None)`` when neither side has
    sector weights.
    """
    left_weights = left.get("sector_weights") or {}
    right_weights = right.get("sector_weights") or {}
    if not left_weights or not right_weights:
        return None, None
    shared = 0.0
    for sector, lw in left_weights.items():
        rw = right_weights.get(sector)
        if rw is None:
            continue
        shared += min(float(lw), float(rw))
    shared_top: Optional[str] = None
    if left.get("top_sector") and left.get("top_sector") == right.get("top_sector"):
        shared_top = left.get("top_sector")
    return round(min(shared, 1.0), 6), shared_top


def _shared_named_rows(
    left_rows: list[dict[str, Any]],
    right_rows: list[dict[str, Any]],
) -> list[str]:
    """Return tickers present in both rowsets, preserving left order."""
    right_tickers = {r.get("ticker") for r in right_rows if r.get("ticker")}
    out: list[str] = []
    for row in left_rows:
        ticker = row.get("ticker")
        if ticker and ticker in right_tickers and ticker not in out:
            out.append(ticker)
    return out


def _pair_verdict(similarity: Optional[float], overlap_pct: Optional[float]) -> str:
    if similarity is None and overlap_pct is None:
        return "insufficient_evidence"
    # Allow verdict purely from holdings overlap when factor data is sparse.
    s = similarity if similarity is not None else 0.0
    o = overlap_pct if overlap_pct is not None else s
    high_signal = max(s, o)
    low_signal = min(s, o) if (similarity is not None and overlap_pct is not None) else high_signal
    if high_signal >= HIGHLY_OVERLAPPING_THRESHOLD:
        return "highly_overlapping"
    if low_signal <= DIFFERENTIATED_THRESHOLD and high_signal <= DIFFERENTIATED_THRESHOLD + 0.1:
        return "differentiated"
    return "partially_differentiated"


def _pair_record(
    *,
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    overlap_entry: Optional[Mapping[str, Any]],
) -> dict[str, Any]:
    overlap_pct = (
        _coerce_float(overlap_entry.get("overlap_weight_pct"))
        if isinstance(overlap_entry, Mapping) else None
    )
    shared_names = list(overlap_entry.get("shared_names") or []) if isinstance(overlap_entry, Mapping) else []
    factor_score, factor_detail = _factor_proximity(left, right)
    sector_score, shared_top_sector = _sector_overlap_score(left, right)

    components: list[tuple[str, float]] = []
    if overlap_pct is not None:
        components.append(("holdings_overlap", overlap_pct))
    if factor_score is not None:
        components.append(("factor_proximity", factor_score))
    if sector_score is not None:
        components.append(("sector_overlap", sector_score))

    similarity: Optional[float]
    if len(components) >= 2:
        similarity = round(sum(score for _, score in components) / len(components), 6)
    elif len(components) == 1:
        # Only one signal available — still report it, but flag insufficient
        # for verdict purposes by leaving similarity None.
        similarity = None
    else:
        similarity = None

    # Shared signals across attribution.
    shared_top_contributor = None
    left_tc = left.get("top_contributor") or {}
    right_tc = right.get("top_contributor") or {}
    if left_tc.get("ticker") and left_tc.get("ticker") == right_tc.get("ticker"):
        shared_top_contributor = left_tc.get("ticker")
    shared_top_detractor = None
    left_td = left.get("top_detractor") or {}
    right_td = right.get("top_detractor") or {}
    if left_td.get("ticker") and left_td.get("ticker") == right_td.get("ticker"):
        shared_top_detractor = left_td.get("ticker")
    shared_drawdown = _shared_named_rows(
        left.get("top_drawdown_contributors") or [],
        right.get("top_drawdown_contributors") or [],
    )

    verdict = _pair_verdict(similarity, overlap_pct)

    caveats: list[str] = []
    if overlap_pct is None:
        caveats.append("no_pairwise_overlap_data")
    if factor_score is None:
        caveats.append("no_factor_proximity_data")
    if sector_score is None:
        caveats.append("no_sector_overlap_data")

    return {
        "left_slug": left.get("strategy_slug"),
        "right_slug": right.get("strategy_slug"),
        "holdings_overlap_pct": overlap_pct,
        "shared_names_count": len(shared_names),
        "shared_names": shared_names,
        "factor_proximity_score": factor_score,
        "factor_deltas": factor_detail,
        "sector_overlap_score": sector_score,
        "shared_top_sector": shared_top_sector,
        "shared_top_contributor": shared_top_contributor,
        "shared_top_detractor": shared_top_detractor,
        "shared_drawdown_contributors": shared_drawdown,
        "similarity_score": similarity,
        "similarity_components": [c[0] for c in components],
        "verdict": verdict,
        "caveats": caveats,
    }


# ---------------------------------------------------------------------------
# All-strategies rollup
# ---------------------------------------------------------------------------


def _common_factor_flags(snapshots: dict[str, dict[str, Any]]) -> list[str]:
    """Hidden factor flags that ALL snapshots share."""
    flag_sets = [
        set(snap.get("hidden_factor_flags") or [])
        for snap in snapshots.values()
    ]
    if not flag_sets:
        return []
    common: set[str] = set(flag_sets[0])
    for s in flag_sets[1:]:
        common &= s
    return sorted(common)


def _diversification_verdict(pairs: list[dict[str, Any]]) -> tuple[str, str]:
    """Return ``(verdict, rationale)`` summarising overall diversification."""
    if not pairs:
        return "insufficient_evidence", "No pairs evaluated."
    by_verdict: dict[str, int] = {}
    for p in pairs:
        by_verdict[p["verdict"]] = by_verdict.get(p["verdict"], 0) + 1
    n = len(pairs)
    if by_verdict.get("highly_overlapping", 0) >= max(1, n // 2 + 1):
        return (
            "low_diversification",
            f"{by_verdict.get('highly_overlapping', 0)}/{n} pairs are highly_overlapping.",
        )
    if by_verdict.get("differentiated", 0) >= max(1, n // 2 + 1):
        return (
            "high_diversification",
            f"{by_verdict.get('differentiated', 0)}/{n} pairs are differentiated.",
        )
    if by_verdict.get("insufficient_evidence", 0) == n:
        return (
            "insufficient_evidence",
            f"All {n} pairs have insufficient data for a verdict.",
        )
    return (
        "moderate_diversification",
        "; ".join(f"{v}={count}" for v, count in sorted(by_verdict.items())) + f" across {n} pairs.",
    )


def _extreme_pair(pairs: list[dict[str, Any]], *, want_max: bool) -> Optional[dict[str, Any]]:
    scored = [p for p in pairs if p.get("similarity_score") is not None]
    if not scored:
        # Fall back to holdings overlap if similarity is unavailable.
        scored = [p for p in pairs if p.get("holdings_overlap_pct") is not None]
        key = "holdings_overlap_pct"
    else:
        key = "similarity_score"
    if not scored:
        return None
    return sorted(scored, key=lambda p: -(p[key] or 0.0) if want_max else (p[key] or 0.0))[0]


# ---------------------------------------------------------------------------
# Narrative
# ---------------------------------------------------------------------------


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_score(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _render_narrative(
    *,
    trade_date: str,
    pairs: list[dict[str, Any]],
    most_similar: Optional[dict[str, Any]],
    most_differentiated: Optional[dict[str, Any]],
    common_flags: list[str],
    diversification_verdict: str,
    diversification_rationale: str,
) -> str:
    lines: list[str] = []
    lines.append(
        f"Strategy differentiation for trade date {trade_date} "
        f"({len(pairs)} pair{'s' if len(pairs) != 1 else ''})."
    )
    for pair in pairs:
        lines.append(
            f"  • {pair['left_slug']} ↔ {pair['right_slug']}: "
            f"verdict={pair['verdict']}, similarity={_fmt_score(pair['similarity_score'])}, "
            f"holdings overlap={_fmt_pct(pair['holdings_overlap_pct'])}, "
            f"sector overlap={_fmt_score(pair['sector_overlap_score'])}, "
            f"factor proximity={_fmt_score(pair['factor_proximity_score'])}."
        )
        extras: list[str] = []
        if pair.get("shared_top_sector"):
            extras.append(f"shared top sector={pair['shared_top_sector']}")
        if pair.get("shared_top_contributor"):
            extras.append(f"shared top contributor={pair['shared_top_contributor']}")
        if pair.get("shared_drawdown_contributors"):
            extras.append(
                f"shared drawdown contributors={pair['shared_drawdown_contributors'][:3]}"
            )
        if extras:
            lines.append("      " + "; ".join(extras) + ".")
    if most_similar and most_differentiated and most_similar is not most_differentiated:
        lines.append("")
        lines.append(
            f"Most similar: {most_similar['left_slug']} ↔ {most_similar['right_slug']} "
            f"(similarity={_fmt_score(most_similar.get('similarity_score'))}, "
            f"holdings overlap={_fmt_pct(most_similar.get('holdings_overlap_pct'))})."
        )
        lines.append(
            f"Most differentiated: {most_differentiated['left_slug']} ↔ {most_differentiated['right_slug']} "
            f"(similarity={_fmt_score(most_differentiated.get('similarity_score'))})."
        )
    if common_flags:
        lines.append("")
        lines.append(f"Common factor flags across all strategies: {common_flags}.")
    lines.append("")
    lines.append(f"Diversification: {diversification_verdict}. {diversification_rationale}")
    lines.append("")
    lines.append(
        "Note: similarity is a composite of holdings overlap, factor proximity, "
        "and sector overlap; treat as descriptive, not as a formal alpha test."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StrategyDifferentiationAnswer:
    status: str
    trade_date: Optional[str]
    shadow_root: str
    attribution_root: str
    requested_strategies: tuple[str, ...]
    available_strategies: tuple[str, ...]
    strategy_snapshots: dict[str, dict[str, Any]]
    pairwise_differentiation: list[dict[str, Any]]
    most_similar_pair: Optional[dict[str, Any]]
    most_differentiated_pair: Optional[dict[str, Any]]
    common_factor_flags: list[str]
    diversification_verdict: str
    diversification_rationale: str
    narrative: str
    missing_strategies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)


def differentiation_to_dict(answer: StrategyDifferentiationAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "trade_date": answer.trade_date,
        "shadow_root": answer.shadow_root,
        "attribution_root": answer.attribution_root,
        "requested_strategies": list(answer.requested_strategies),
        "available_strategies": list(answer.available_strategies),
        "strategy_snapshots": answer.strategy_snapshots,
        "pairwise_differentiation": answer.pairwise_differentiation,
        "most_similar_pair": answer.most_similar_pair,
        "most_differentiated_pair": answer.most_differentiated_pair,
        "common_factor_flags": list(answer.common_factor_flags),
        "diversification_verdict": answer.diversification_verdict,
        "diversification_rationale": answer.diversification_rationale,
        "narrative": answer.narrative,
        "missing_strategies": list(answer.missing_strategies),
        "warnings": list(answer.warnings),
        "source_paths": list(answer.source_paths),
    }


def analyse_strategy_differentiation(
    *,
    outputs_root: Path = DEFAULT_OUTPUTS_ROOT,
    shadow_root: Optional[Path] = None,
    attribution_root: Optional[Path] = None,
    question: Optional[str] = None,
    strategies: Optional[Iterable[str]] = None,
) -> StrategyDifferentiationAnswer:
    """Per-pair + cross-strategy differentiation summary.

    Fail-closed: ``NO_SHADOW_DATA`` when no shadow_candidates directory
    exists; ``NEEDS_DATA`` when requested strategies are not present in
    the artifacts; ``OK`` otherwise (with explicit per-pair ``caveats``
    and top-level ``warnings`` for any missing inputs).
    """
    resolved_shadow_root = (
        shadow_root if shadow_root is not None else outputs_root / "shadow_candidates"
    )
    resolved_attribution_root = (
        attribution_root if attribution_root is not None else outputs_root / "attribution"
    )

    shadow_dir = _select_latest_dated_dir(resolved_shadow_root)
    if shadow_dir is None:
        return StrategyDifferentiationAnswer(
            status="NO_SHADOW_DATA",
            trade_date=None,
            shadow_root=str(resolved_shadow_root),
            attribution_root=str(resolved_attribution_root),
            requested_strategies=tuple(strategies or parse_strategy_names(question or "")),
            available_strategies=(),
            strategy_snapshots={},
            pairwise_differentiation=[],
            most_similar_pair=None,
            most_differentiated_pair=None,
            common_factor_flags=[],
            diversification_verdict="insufficient_evidence",
            diversification_rationale=(
                f"No shadow_candidates directory found under {resolved_shadow_root}."
            ),
            narrative=(
                f"No shadow_candidates directory found under {resolved_shadow_root}; "
                "cannot compute strategy differentiation."
            ),
            warnings=[f"no shadow_candidates directory under {resolved_shadow_root}"],
        )

    comparison_path = shadow_dir / "comparison.json"
    eval_path = shadow_dir / "shadow_evaluation.json"
    comparison_payload = _safe_json(comparison_path) or {}
    shadow_eval = _safe_json(eval_path) or {}
    trade_date = (
        comparison_payload.get("trade_date")
        or shadow_eval.get("trade_date")
        or shadow_dir.name
    )

    attribution_dir = _select_latest_dated_dir(resolved_attribution_root)
    factor_payload: dict[str, Any] = {}
    summary_payload: dict[str, Any] = {}
    contribution_payload: dict[str, Any] = {}
    attribution_warnings: list[str] = []
    if attribution_dir is not None:
        factor_payload = _safe_json(attribution_dir / "factor_exposure.json") or {}
        summary_payload = _safe_json(attribution_dir / "attribution_summary.json") or {}
        contribution_payload = _safe_json(attribution_dir / "contribution_report.json") or {}
    else:
        attribution_warnings.append(
            f"no attribution directory under {resolved_attribution_root}; "
            "factor_proximity and shared-contributor signals will be unavailable"
        )

    # Strategy inventory comes from the shadow eval (authoritative for
    # "which slugs are alive"); benchmark filtered out.
    raw_strategies = shadow_eval.get("strategies") or {}
    available = tuple(
        sorted(s for s in raw_strategies.keys() if s != BENCHMARK_SLUG)
    )

    requested_names: list[str] = (
        list(strategies) if strategies else parse_strategy_names(question or "")
    )
    if requested_names:
        requested_slugs = [strategy_slug(n) for n in requested_names]
        missing = [s for s in requested_slugs if s not in raw_strategies]
        selected = [s for s in requested_slugs if s in raw_strategies]
        # NEEDS_DATA when ALL requested strategies are absent (e.g. operator
        # asked about "leda" and no leda artifact exists).
        if not selected and missing:
            return StrategyDifferentiationAnswer(
                status="NEEDS_DATA",
                trade_date=str(trade_date) if trade_date else None,
                shadow_root=str(resolved_shadow_root),
                attribution_root=str(resolved_attribution_root),
                requested_strategies=tuple(requested_names),
                available_strategies=available,
                strategy_snapshots={},
                pairwise_differentiation=[],
                most_similar_pair=None,
                most_differentiated_pair=None,
                common_factor_flags=[],
                diversification_verdict="insufficient_evidence",
                diversification_rationale=(
                    f"None of the requested strategies ({requested_names}) are "
                    f"present in shadow_evaluation.json. Available: {list(available)}."
                ),
                narrative=(
                    f"None of the requested strategies ({requested_names}) are "
                    f"present on disk."
                ),
                missing_strategies=missing,
                warnings=[f"missing strategy slug: {slug}" for slug in missing],
                source_paths=[str(eval_path)],
            )
        # Single strategy named (and present) → expand to include the
        # rest of the available set so the operator sees its pair-
        # relationships. Two or more named → focus on exactly those.
        if len(selected) == 1 and len(available) >= 2:
            selected = list(available)
    else:
        requested_slugs = list(available)
        missing = []
        selected = list(available)

    # Build per-strategy snapshots.
    factor_strategies = factor_payload.get("strategies") or {}
    summary_strategies = summary_payload.get("strategies") or {}
    contribution_strategies = contribution_payload.get("strategies") or {}
    snapshots: dict[str, dict[str, Any]] = {}
    for slug in selected:
        snapshots[slug] = _strategy_snapshot(
            slug=slug,
            eval_entry=raw_strategies.get(slug),
            factor_entry=factor_strategies.get(slug),
            attribution_summary_entry=summary_strategies.get(slug),
            contribution_entry=contribution_strategies.get(slug),
        )

    # Build pairwise records.
    pairwise_raw = comparison_payload.get("pairwise_overlap") or []
    overlap_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    for entry in pairwise_raw:
        if not isinstance(entry, Mapping):
            continue
        key_a = str(entry.get("left_slug") or "")
        key_b = str(entry.get("right_slug") or "")
        if key_a and key_b:
            overlap_by_pair[(key_a, key_b)] = entry
            overlap_by_pair[(key_b, key_a)] = entry

    pairs: list[dict[str, Any]] = []
    selected_sorted = sorted(selected)
    for i, left_slug in enumerate(selected_sorted):
        for right_slug in selected_sorted[i + 1:]:
            overlap_entry = overlap_by_pair.get((left_slug, right_slug))
            pairs.append(
                _pair_record(
                    left=snapshots[left_slug],
                    right=snapshots[right_slug],
                    overlap_entry=overlap_entry,
                )
            )

    most_similar = _extreme_pair(pairs, want_max=True)
    most_differentiated = _extreme_pair(pairs, want_max=False)
    common_flags = _common_factor_flags(snapshots)
    div_verdict, div_rationale = _diversification_verdict(pairs)

    narrative = _render_narrative(
        trade_date=str(trade_date),
        pairs=pairs,
        most_similar=most_similar,
        most_differentiated=most_differentiated,
        common_flags=common_flags,
        diversification_verdict=div_verdict,
        diversification_rationale=div_rationale,
    )

    warnings: list[str] = list(attribution_warnings)
    if missing:
        warnings.extend(f"missing strategy slug: {slug}" for slug in missing)
    if any(p["verdict"] == "insufficient_evidence" for p in pairs):
        warnings.append("at_least_one_pair_has_insufficient_evidence")

    source_paths = [
        str(eval_path),
        str(comparison_path) if comparison_path.exists() else f"{comparison_path} (missing)",
    ]
    if attribution_dir is not None:
        for name in ("factor_exposure.json", "attribution_summary.json", "contribution_report.json"):
            p = attribution_dir / name
            source_paths.append(str(p) if p.exists() else f"{p} (missing)")

    return StrategyDifferentiationAnswer(
        status="OK",
        trade_date=str(trade_date),
        shadow_root=str(resolved_shadow_root),
        attribution_root=str(resolved_attribution_root),
        requested_strategies=tuple(requested_names) if requested_names else available,
        available_strategies=available,
        strategy_snapshots=snapshots,
        pairwise_differentiation=pairs,
        most_similar_pair=most_similar,
        most_differentiated_pair=most_differentiated,
        common_factor_flags=common_flags,
        diversification_verdict=div_verdict,
        diversification_rationale=div_rationale,
        narrative=narrative,
        missing_strategies=missing,
        warnings=warnings,
        source_paths=source_paths,
    )
