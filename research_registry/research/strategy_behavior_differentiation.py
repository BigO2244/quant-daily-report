"""Return-stream behavioral differentiation across challenger strategies.

Backs the ``strategy_behavior_differentiation`` MCP tool. Reads the
existing wide-format NAV series at
``outputs/shadow_candidates/performance/shadow_nav_series.csv`` and
computes pairwise return correlation, rolling 20D/60D correlation
stability, shared-negative-day counts, and shared / common drawdown
windows. **Never invents missing returns** — when the NAV series is
absent the tool fails closed with a structured response that
inventories candidate artifact locations and proposes the artifact
contract a future producer would need to satisfy.

Behavioral similarity tiers (deterministic, no LLM):

    highly_similar_behavior    : full-window |corr| >= 0.85
    partially_similar_behavior : 0.50 <= |corr| < 0.85
    behaviorally_differentiated: |corr| < 0.50
    insufficient_evidence      : n_observations < 30

All-strategies diversification verdict comes from the average pairwise
correlation (low / moderate / high). The full-window correlation is
the primary signal; rolling-window stability is reported alongside but
does not alter the tier directly.

The module assumes pandas is available (already a project dependency,
used by sibling research modules).
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

import pandas as pd

from research_registry.research.shadow_comparison import (
    BENCHMARK_SLUG,
    KNOWN_STRATEGY_NAMES,
    parse_strategy_names,
    strategy_slug,
)

DEFAULT_NAV_SERIES_PATH = Path("outputs/shadow_candidates/performance/shadow_nav_series.csv")

# Tier thresholds (deterministic).
HIGHLY_SIMILAR_CORR = 0.85
PARTIALLY_SIMILAR_CORR = 0.50

# Diversification verdict thresholds on average pairwise correlation.
LOW_DIVERSIFICATION_AVG_CORR = 0.70
HIGH_DIVERSIFICATION_AVG_CORR = 0.40

# Statistical guardrails.
MIN_OBSERVATIONS = 30
MIN_DOWNSIDE_OBS = 20
ROLLING_20D = 20
ROLLING_60D = 60
SHARED_DRAWDOWN_FLOOR = -0.05  # both strategies must be below this in drawdown


# ---------------------------------------------------------------------------
# Discovery (used by fail-closed branch)
# ---------------------------------------------------------------------------


_CANDIDATE_ARTIFACT_PATHS: tuple[Path, ...] = (
    Path("outputs/shadow_candidates/performance/shadow_nav_series.csv"),
    Path("outputs/shadow_candidates/performance/shadow_returns.csv"),
    Path("outputs/portfolio_history"),
    Path("outputs/research_clarity"),
    Path("outputs/research_packets"),
    Path("outputs/attribution/rolling_exposure_history.csv"),
    Path("outputs/research/sleeve1_alpha_variant_timeseries.csv"),
    Path("outputs/research/sleeve1_backtest_2009_2025_timeseries.csv"),
)


def _candidate_inventory(root: Path = Path(".")) -> dict[str, Any]:
    """Probe filesystem for candidate per-strategy time-series artifacts.

    Returns a structured inventory the fail-closed branch can surface so
    the operator sees exactly what was searched and what was found.
    """
    found: list[dict[str, Any]] = []
    missing: list[str] = []
    for relative in _CANDIDATE_ARTIFACT_PATHS:
        path = root / relative
        if path.exists():
            entry: dict[str, Any] = {"path": str(relative), "exists": True}
            if path.is_file():
                try:
                    entry["size_bytes"] = path.stat().st_size
                    if path.suffix == ".csv":
                        with path.open(encoding="utf-8") as fh:
                            reader = csv.reader(fh)
                            header = next(reader, None)
                            entry["header"] = list(header or [])
                            entry["row_count_estimate"] = sum(1 for _ in reader)
                except OSError:
                    entry["read_error"] = True
            elif path.is_dir():
                entry["dir"] = True
                try:
                    entry["child_count"] = sum(1 for _ in path.iterdir())
                except OSError:
                    entry["read_error"] = True
            found.append(entry)
        else:
            missing.append(str(relative))
    return {"candidates_found": found, "candidates_missing": missing}


# ---------------------------------------------------------------------------
# NAV → returns transform
# ---------------------------------------------------------------------------


def _load_nav_series(nav_path: Path) -> tuple[Optional[pd.DataFrame], list[str]]:
    """Load the wide-format NAV CSV. Returns ``(df, warnings)``.

    ``df`` is indexed by date and contains the ``caerus_*`` columns plus
    the benchmark (if present). Returns ``(None, [...])`` if the file is
    missing or malformed.
    """
    if not nav_path.exists():
        return None, [f"nav_series_missing: {nav_path}"]
    try:
        df = pd.read_csv(nav_path, parse_dates=["date"])
    except (OSError, ValueError, pd.errors.ParserError) as exc:
        return None, [f"nav_series_unreadable: {exc}"]
    if "date" not in df.columns:
        return None, ["nav_series_missing_date_column"]
    df = df.set_index("date").sort_index()
    return df, []


def _strategy_inception(nav_col: pd.Series) -> Optional[pd.Timestamp]:
    """First index where NAV departs from the starting (typically 1.0) value."""
    if nav_col.empty:
        return None
    start_val = nav_col.iloc[0]
    moves = (nav_col != start_val)
    if not moves.any():
        return None
    return nav_col.index[moves.values.argmax()]


def _pair_active_returns(
    returns: pd.DataFrame,
    a: str,
    b: str,
) -> pd.DataFrame:
    """Return the slice of ``returns`` from the later of A/B inception."""
    a_nz = returns[a] != 0
    b_nz = returns[b] != 0
    if not a_nz.any() or not b_nz.any():
        return returns.iloc[0:0]
    a_start = returns.index[a_nz.values.argmax()]
    b_start = returns.index[b_nz.values.argmax()]
    start = max(a_start, b_start)
    out = returns.loc[start:, [a, b]].dropna()
    # Drop rows where both returns are exactly zero (likely calendar gaps).
    keep = ~((out[a] == 0) & (out[b] == 0))
    return out.loc[keep]


# ---------------------------------------------------------------------------
# Per-pair analysis
# ---------------------------------------------------------------------------


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _summary_stats(series: pd.Series) -> dict[str, Optional[float]]:
    cleaned = series.dropna()
    if cleaned.empty:
        return {"n": 0, "mean": None, "p10": None, "p90": None, "median": None}
    return {
        "n": int(cleaned.size),
        "mean": round(float(cleaned.mean()), 6),
        "median": round(float(cleaned.median()), 6),
        "p10": round(float(cleaned.quantile(0.10)), 6),
        "p90": round(float(cleaned.quantile(0.90)), 6),
    }


def _behavioral_tier(corr: Optional[float], n_obs: int) -> str:
    if corr is None or n_obs < MIN_OBSERVATIONS:
        return "insufficient_evidence"
    mag = abs(corr)
    if mag >= HIGHLY_SIMILAR_CORR:
        return "highly_similar_behavior"
    if mag >= PARTIALLY_SIMILAR_CORR:
        return "partially_similar_behavior"
    return "behaviorally_differentiated"


def _worst_shared_drawdown(
    nav_pair: pd.DataFrame,
    a: str,
    b: str,
) -> Optional[dict[str, Any]]:
    """Return the date + per-strategy drawdown of the worst joint drawdown day.

    A joint drawdown day = both strategies below ``SHARED_DRAWDOWN_FLOOR``.
    """
    if nav_pair.empty:
        return None
    cumA = nav_pair[a] / nav_pair[a].cummax() - 1.0
    cumB = nav_pair[b] / nav_pair[b].cummax() - 1.0
    joint = pd.DataFrame({"a": cumA, "b": cumB})
    both_below = (joint["a"] < SHARED_DRAWDOWN_FLOOR) & (joint["b"] < SHARED_DRAWDOWN_FLOOR)
    if not both_below.any():
        return None
    # Worst joint = where the *less negative* of the two is minimised; equivalently min(max(a,b)).
    joint_worst_axis = joint.where(both_below).max(axis=1)
    worst_idx = joint_worst_axis.idxmin()
    return {
        "date": str(pd.Timestamp(worst_idx).date()) if pd.notna(worst_idx) else None,
        "drawdown_left": round(float(joint.loc[worst_idx, "a"]), 6),
        "drawdown_right": round(float(joint.loc[worst_idx, "b"]), 6),
        "shared_drawdown_days": int(both_below.sum()),
    }


def _build_pair_record(
    *,
    left_slug: str,
    right_slug: str,
    returns: pd.DataFrame,
    nav: pd.DataFrame,
) -> dict[str, Any]:
    pair_returns = _pair_active_returns(returns, left_slug, right_slug)
    n_obs = int(pair_returns.shape[0])
    caveats: list[str] = []

    if n_obs < MIN_OBSERVATIONS:
        return {
            "left_slug": left_slug,
            "right_slug": right_slug,
            "n_observations": n_obs,
            "active_window": _active_window(pair_returns),
            "return_correlation": None,
            "rolling_20d_correlation": _summary_stats(pd.Series(dtype=float)),
            "rolling_60d_correlation": _summary_stats(pd.Series(dtype=float)),
            "correlation_stability_iqr": None,
            "shared_negative_days": 0,
            "shared_negative_pct": None,
            "downside_correlation": None,
            "worst_shared_drawdown": None,
            "behavioral_similarity_tier": "insufficient_evidence",
            "confidence": "LOW",
            "caveats": [
                f"insufficient_observations:{n_obs}/{MIN_OBSERVATIONS}",
            ],
        }

    left = pair_returns[left_slug]
    right = pair_returns[right_slug]

    full_corr = _coerce_float(left.corr(right))

    # Rolling stability (only computed when sample supports the window).
    rolling_20 = pd.Series(dtype=float)
    rolling_60 = pd.Series(dtype=float)
    if n_obs >= ROLLING_20D + 5:
        rolling_20 = left.rolling(ROLLING_20D).corr(right).dropna()
    else:
        caveats.append(f"insufficient_rolling_20d:{n_obs}")
    if n_obs >= ROLLING_60D + 5:
        rolling_60 = left.rolling(ROLLING_60D).corr(right).dropna()
    else:
        caveats.append(f"insufficient_rolling_60d:{n_obs}")
    stats_20 = _summary_stats(rolling_20)
    stats_60 = _summary_stats(rolling_60)
    stability_iqr = (
        round(stats_20["p90"] - stats_20["p10"], 6)
        if stats_20["p90"] is not None and stats_20["p10"] is not None
        else None
    )

    both_negative = (left < 0) & (right < 0)
    shared_negative_days = int(both_negative.sum())
    shared_negative_pct = round(shared_negative_days / n_obs, 6) if n_obs else None

    # Downside correlation — at least one side negative.
    downside_mask = (left < 0) | (right < 0)
    downside_corr = None
    if int(downside_mask.sum()) >= MIN_DOWNSIDE_OBS:
        downside_corr = _coerce_float(left[downside_mask].corr(right[downside_mask]))
    else:
        caveats.append(f"insufficient_downside_obs:{int(downside_mask.sum())}/{MIN_DOWNSIDE_OBS}")

    # Worst shared drawdown — use NAV pair sliced to the active window.
    nav_pair = nav.loc[pair_returns.index.min():, [left_slug, right_slug]]
    worst_shared = _worst_shared_drawdown(nav_pair, left_slug, right_slug)

    tier = _behavioral_tier(full_corr, n_obs)
    confidence = "MODERATE" if n_obs >= 250 else ("LOW" if n_obs >= MIN_OBSERVATIONS else "LOW")
    # Stability flag adds caveat if rolling correlation is unstable.
    if stability_iqr is not None and stability_iqr > 0.40:
        caveats.append("rolling_correlation_unstable_iqr>0.40")

    return {
        "left_slug": left_slug,
        "right_slug": right_slug,
        "n_observations": n_obs,
        "active_window": _active_window(pair_returns),
        "return_correlation": round(full_corr, 6) if full_corr is not None else None,
        "rolling_20d_correlation": stats_20,
        "rolling_60d_correlation": stats_60,
        "correlation_stability_iqr": stability_iqr,
        "shared_negative_days": shared_negative_days,
        "shared_negative_pct": shared_negative_pct,
        "downside_correlation": round(downside_corr, 6) if downside_corr is not None else None,
        "worst_shared_drawdown": worst_shared,
        "behavioral_similarity_tier": tier,
        "confidence": confidence,
        "caveats": caveats,
    }


def _active_window(returns_slice: pd.DataFrame) -> Optional[dict[str, Any]]:
    if returns_slice.empty:
        return None
    return {
        "start_date": str(returns_slice.index.min().date()),
        "end_date": str(returns_slice.index.max().date()),
    }


# ---------------------------------------------------------------------------
# All-strategies rollup
# ---------------------------------------------------------------------------


def _common_negative_days(returns_subset: pd.DataFrame) -> int:
    """Count rows where every column is strictly negative."""
    if returns_subset.empty:
        return 0
    mask = (returns_subset < 0).all(axis=1)
    return int(mask.sum())


def _diversification_verdict(avg_corr: Optional[float], n_pairs: int) -> tuple[str, str]:
    if avg_corr is None or n_pairs == 0:
        return "insufficient_evidence", "No pairwise correlation could be computed."
    if avg_corr >= LOW_DIVERSIFICATION_AVG_CORR:
        return (
            "low_behavioral_diversification",
            f"Average pairwise correlation {avg_corr:.3f} ≥ {LOW_DIVERSIFICATION_AVG_CORR}; "
            "strategies behave alike through time.",
        )
    if avg_corr <= HIGH_DIVERSIFICATION_AVG_CORR:
        return (
            "high_behavioral_diversification",
            f"Average pairwise correlation {avg_corr:.3f} ≤ {HIGH_DIVERSIFICATION_AVG_CORR}; "
            "strategies have meaningfully independent return streams.",
        )
    return (
        "moderate_behavioral_diversification",
        f"Average pairwise correlation {avg_corr:.3f} in the moderate band.",
    )


def _extreme_pair(pairs: list[dict[str, Any]], *, want_max: bool) -> Optional[dict[str, Any]]:
    scored = [p for p in pairs if p.get("return_correlation") is not None]
    if not scored:
        return None
    return sorted(
        scored,
        key=lambda p: (-p["return_correlation"] if want_max else p["return_correlation"]),
    )[0]


# ---------------------------------------------------------------------------
# Narrative (deterministic template)
# ---------------------------------------------------------------------------


def _fmt_corr(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):+.3f}"
    except (TypeError, ValueError):
        return "n/a"


def _fmt_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "n/a"


def _render_narrative(
    *,
    date_range: tuple[Optional[str], Optional[str]],
    pairs: list[dict[str, Any]],
    most_similar: Optional[dict[str, Any]],
    most_diff: Optional[dict[str, Any]],
    avg_corr: Optional[float],
    common_negative_days: int,
    diversification_verdict: str,
    diversification_rationale: str,
) -> str:
    start, end = date_range
    lines: list[str] = []
    range_text = f" {start} → {end}" if start and end else ""
    lines.append(
        f"Behavioral differentiation from realized NAV series{range_text} ({len(pairs)} pair"
        f"{'s' if len(pairs) != 1 else ''})."
    )
    for pair in pairs:
        lines.append(
            f"  • {pair['left_slug']} ↔ {pair['right_slug']}: "
            f"tier={pair['behavioral_similarity_tier']}, "
            f"corr={_fmt_corr(pair.get('return_correlation'))} "
            f"over n={pair.get('n_observations', 0)} days; "
            f"shared negative days={pair.get('shared_negative_days', 0)} "
            f"({_fmt_pct(pair.get('shared_negative_pct'))}); "
            f"downside corr={_fmt_corr(pair.get('downside_correlation'))}."
        )
        worst = pair.get("worst_shared_drawdown") or {}
        if worst.get("date"):
            lines.append(
                f"      Worst shared drawdown day: {worst.get('date')} "
                f"(left dd={_fmt_pct(worst.get('drawdown_left'))}, "
                f"right dd={_fmt_pct(worst.get('drawdown_right'))}, "
                f"{worst.get('shared_drawdown_days')} total shared-DD days)."
            )
    if most_similar and most_diff and most_similar is not most_diff:
        lines.append("")
        lines.append(
            f"Most behaviorally similar: {most_similar['left_slug']} ↔ {most_similar['right_slug']} "
            f"(corr {_fmt_corr(most_similar.get('return_correlation'))})."
        )
        lines.append(
            f"Most behaviorally differentiated: {most_diff['left_slug']} ↔ {most_diff['right_slug']} "
            f"(corr {_fmt_corr(most_diff.get('return_correlation'))})."
        )
    if avg_corr is not None:
        lines.append("")
        lines.append(f"Average pairwise correlation: {_fmt_corr(avg_corr)}")
    if common_negative_days:
        lines.append(
            f"Days where ALL selected strategies were negative: {common_negative_days}."
        )
    lines.append("")
    lines.append(f"Diversification: {diversification_verdict}. {diversification_rationale}")
    lines.append("")
    lines.append(
        "Note: correlation is a descriptive measure of historical co-movement; "
        "it is not a forward-looking guarantee of future independence."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Top-level orchestrator + dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BehaviorDifferentiationAnswer:
    status: str
    nav_series_path: str
    date_range_start: Optional[str]
    date_range_end: Optional[str]
    available_strategies: tuple[str, ...]
    requested_strategies: tuple[str, ...]
    behavior_pairs: list[dict[str, Any]]
    most_behaviorally_similar_pair: Optional[dict[str, Any]]
    most_behaviorally_differentiated_pair: Optional[dict[str, Any]]
    average_pairwise_correlation: Optional[float]
    common_negative_days_count: int
    behavioral_diversification_verdict: str
    behavioral_diversification_rationale: str
    candidate_artifact_inventory: Optional[dict[str, Any]]
    proposed_artifact_contract: Optional[str]
    narrative: str
    missing_strategies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_paths: list[str] = field(default_factory=list)


def behavior_differentiation_to_dict(answer: BehaviorDifferentiationAnswer) -> dict[str, Any]:
    return {
        "status": answer.status,
        "nav_series_path": answer.nav_series_path,
        "date_range_start": answer.date_range_start,
        "date_range_end": answer.date_range_end,
        "available_strategies": list(answer.available_strategies),
        "requested_strategies": list(answer.requested_strategies),
        "behavior_pairs": answer.behavior_pairs,
        "most_behaviorally_similar_pair": answer.most_behaviorally_similar_pair,
        "most_behaviorally_differentiated_pair": answer.most_behaviorally_differentiated_pair,
        "average_pairwise_correlation": answer.average_pairwise_correlation,
        "common_negative_days_count": answer.common_negative_days_count,
        "behavioral_diversification_verdict": answer.behavioral_diversification_verdict,
        "behavioral_diversification_rationale": answer.behavioral_diversification_rationale,
        "candidate_artifact_inventory": answer.candidate_artifact_inventory,
        "proposed_artifact_contract": answer.proposed_artifact_contract,
        "narrative": answer.narrative,
        "missing_strategies": list(answer.missing_strategies),
        "warnings": list(answer.warnings),
        "source_paths": list(answer.source_paths),
    }


_PROPOSED_ARTIFACT_CONTRACT = (
    "Required artifact for behavioral differentiation:\n"
    "  path: outputs/shadow_candidates/performance/shadow_nav_series.csv\n"
    "  schema: wide CSV with columns:\n"
    "    date              ISO trading-day date (YYYY-MM-DD)\n"
    "    caerus_polaris    daily NAV (float, inception NAV = 1.0)\n"
    "    caerus_orion      daily NAV (float, inception NAV = 1.0)\n"
    "    caerus_lyra       daily NAV (float, inception NAV = 1.0)\n"
    "    caerus_leda       (optional) daily NAV (float)\n"
    "    spy_benchmark     daily NAV (float) — for reference only\n"
    "  requirements:\n"
    "    - at least 30 active days per strategy (NAV != 1.0)\n"
    "    - deterministic, replay-safe (no point-in-time leakage)\n"
    "    - non-trading days omitted (no carry-forward NAV without a marker)\n"
    "  producer:\n"
    "    write once per shadow_evaluation refresh under the existing\n"
    "    outputs/shadow_candidates/performance/ directory; treat as\n"
    "    append-only to preserve historical replay."
)


def analyse_behavior_differentiation(
    *,
    nav_series_path: Path = DEFAULT_NAV_SERIES_PATH,
    question: Optional[str] = None,
    strategies: Optional[Iterable[str]] = None,
    repo_root: Path = Path("."),
) -> BehaviorDifferentiationAnswer:
    """Compute behavioral differentiation from the NAV series."""
    df, load_warnings = _load_nav_series(nav_series_path)
    requested_names: list[str] = (
        list(strategies) if strategies else parse_strategy_names(question or "")
    )

    if df is None:
        inventory = _candidate_inventory(repo_root)
        return BehaviorDifferentiationAnswer(
            status="NO_RETURN_STREAM",
            nav_series_path=str(nav_series_path),
            date_range_start=None,
            date_range_end=None,
            available_strategies=(),
            requested_strategies=tuple(requested_names),
            behavior_pairs=[],
            most_behaviorally_similar_pair=None,
            most_behaviorally_differentiated_pair=None,
            average_pairwise_correlation=None,
            common_negative_days_count=0,
            behavioral_diversification_verdict="insufficient_evidence",
            behavioral_diversification_rationale=(
                f"NAV series not found at {nav_series_path}. Behavioral differentiation "
                "requires a per-strategy daily NAV time series; see proposed_artifact_contract."
            ),
            candidate_artifact_inventory=inventory,
            proposed_artifact_contract=_PROPOSED_ARTIFACT_CONTRACT,
            narrative=(
                f"No NAV time series available at {nav_series_path}. Cannot compute "
                "behavioral correlation, downside co-movement, or shared drawdown. "
                "See candidate_artifact_inventory + proposed_artifact_contract for the "
                "exact gap and the artifact a future producer must satisfy."
            ),
            warnings=load_warnings,
            source_paths=[],
        )

    strategy_cols: tuple[str, ...] = tuple(
        col for col in df.columns
        if col.startswith("caerus_")
    )
    if not strategy_cols:
        return BehaviorDifferentiationAnswer(
            status="NO_RETURN_STREAM",
            nav_series_path=str(nav_series_path),
            date_range_start=None,
            date_range_end=None,
            available_strategies=(),
            requested_strategies=tuple(requested_names),
            behavior_pairs=[],
            most_behaviorally_similar_pair=None,
            most_behaviorally_differentiated_pair=None,
            average_pairwise_correlation=None,
            common_negative_days_count=0,
            behavioral_diversification_verdict="insufficient_evidence",
            behavioral_diversification_rationale=(
                f"NAV series exists at {nav_series_path} but contains no caerus_* columns."
            ),
            candidate_artifact_inventory=_candidate_inventory(repo_root),
            proposed_artifact_contract=_PROPOSED_ARTIFACT_CONTRACT,
            narrative=(
                f"NAV series at {nav_series_path} has no caerus_* strategy columns; "
                "cannot derive behavioral differentiation."
            ),
            warnings=["nav_series_missing_caerus_columns"],
            source_paths=[str(nav_series_path)],
        )

    # Resolve selected strategies.
    missing: list[str] = []
    selected: list[str]
    if requested_names:
        requested_slugs = [strategy_slug(n) for n in requested_names]
        missing = [s for s in requested_slugs if s not in strategy_cols]
        selected = [s for s in requested_slugs if s in strategy_cols]
        if not selected and missing:
            return BehaviorDifferentiationAnswer(
                status="NEEDS_DATA",
                nav_series_path=str(nav_series_path),
                date_range_start=str(df.index.min().date()),
                date_range_end=str(df.index.max().date()),
                available_strategies=strategy_cols,
                requested_strategies=tuple(requested_names),
                behavior_pairs=[],
                most_behaviorally_similar_pair=None,
                most_behaviorally_differentiated_pair=None,
                average_pairwise_correlation=None,
                common_negative_days_count=0,
                behavioral_diversification_verdict="insufficient_evidence",
                behavioral_diversification_rationale=(
                    f"None of the requested strategies ({requested_names}) are present "
                    f"in the NAV series. Available: {list(strategy_cols)}."
                ),
                candidate_artifact_inventory=None,
                proposed_artifact_contract=None,
                narrative=(
                    f"None of the requested strategies ({requested_names}) are in the "
                    f"NAV series. Available: {list(strategy_cols)}."
                ),
                missing_strategies=missing,
                warnings=[f"missing strategy slug: {slug}" for slug in missing],
                source_paths=[str(nav_series_path)],
            )
        # Single-strategy question → include all so the operator sees its pair relationships.
        if len(selected) == 1 and len(strategy_cols) >= 2:
            selected = list(strategy_cols)
    else:
        selected = list(strategy_cols)

    selected_sorted = sorted(selected)

    # Returns frame.
    returns = df[list(strategy_cols)].pct_change().dropna(how="all")

    # Pairwise.
    pairs: list[dict[str, Any]] = []
    correlations: list[float] = []
    for i, left in enumerate(selected_sorted):
        for right in selected_sorted[i + 1:]:
            pair = _build_pair_record(
                left_slug=left,
                right_slug=right,
                returns=returns,
                nav=df,
            )
            pairs.append(pair)
            if pair["return_correlation"] is not None:
                correlations.append(pair["return_correlation"])

    avg_corr = round(sum(correlations) / len(correlations), 6) if correlations else None
    most_similar = _extreme_pair(pairs, want_max=True)
    most_diff = _extreme_pair(pairs, want_max=False)

    # Common negative days over the intersection of all selected strategies' active windows.
    active_returns = returns[selected_sorted].dropna()
    # Drop pre-inception zero rows.
    keep = ~(active_returns == 0).all(axis=1)
    active_returns = active_returns.loc[keep]
    common_negative = _common_negative_days(active_returns)

    div_verdict, div_rationale = _diversification_verdict(avg_corr, len(correlations))

    date_range = (
        str(df.index.min().date()) if not df.empty else None,
        str(df.index.max().date()) if not df.empty else None,
    )

    narrative = _render_narrative(
        date_range=date_range,
        pairs=pairs,
        most_similar=most_similar,
        most_diff=most_diff,
        avg_corr=avg_corr,
        common_negative_days=common_negative,
        diversification_verdict=div_verdict,
        diversification_rationale=div_rationale,
    )

    warnings: list[str] = list(load_warnings)
    if missing:
        warnings.extend(f"missing strategy slug: {slug}" for slug in missing)
    insufficient_pairs = [p for p in pairs if p["behavioral_similarity_tier"] == "insufficient_evidence"]
    if insufficient_pairs:
        warnings.append(
            f"{len(insufficient_pairs)} pair(s) have insufficient observations for a tier verdict"
        )

    return BehaviorDifferentiationAnswer(
        status="OK",
        nav_series_path=str(nav_series_path),
        date_range_start=date_range[0],
        date_range_end=date_range[1],
        available_strategies=strategy_cols,
        requested_strategies=tuple(requested_names) if requested_names else strategy_cols,
        behavior_pairs=pairs,
        most_behaviorally_similar_pair=most_similar,
        most_behaviorally_differentiated_pair=most_diff,
        average_pairwise_correlation=avg_corr,
        common_negative_days_count=common_negative,
        behavioral_diversification_verdict=div_verdict,
        behavioral_diversification_rationale=div_rationale,
        candidate_artifact_inventory=None,
        proposed_artifact_contract=None,
        narrative=narrative,
        missing_strategies=missing,
        warnings=warnings,
        source_paths=[str(nav_series_path)],
    )
