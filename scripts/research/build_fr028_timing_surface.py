#!/usr/bin/env python3
"""FR-028 Phase A timing-semantics comparison surface.

This is a research-only, additive comparison tool. It does not modify existing
Shadow NAV chains, dashboards, promotion logic, broker artifacts, or execution
behavior.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
MODEL_SLUGS = (*STRATEGY_SLUGS, "spy_benchmark")
DISPLAY_NAMES = {
    "caerus_polaris": "Polaris",
    "caerus_orion": "Orion",
    "caerus_lyra": "Lyra",
    "spy_benchmark": "SPY",
}
PROVENANCE_CURRENT = {
    "nav_surface_type": "CURRENT_OPERATIONAL_SHADOW",
    "timing_semantics": "same_day_target_weights_x_same_day_close_to_close_returns",
    "confidence_classification": "LOW_CONFIDENCE_TIMING_REVIEW_REQUIRED",
    "execution_realism": "MODEL_PORTFOLIO_SYNTHETIC_SAME_CLOSE",
    "provenance_status": "LEGACY_CURRENT_SEMANTICS_REFERENCE_ONLY",
    "comparison_scope": "FR-028_PHASE_A_RESEARCH_COMPARISON_ONLY",
}
PROVENANCE_PROPOSED = {
    "nav_surface_type": "PROPOSED_TIMING_CORRECTED_RESEARCH",
    "timing_semantics": "signal_date_weights_x_next_session_close_to_close_returns",
    "confidence_classification": "RESEARCH_ONLY_NOT_GOVERNANCE_APPROVED",
    "execution_realism": "MODEL_PORTFOLIO_PRIOR_SIGNAL_NEXT_CLOSE_SYNTHETIC",
    "provenance_status": "PARALLEL_RESEARCH_SURFACE_NOT_AUTHORITATIVE",
    "comparison_scope": "FR-028_PHASE_A_RESEARCH_COMPARISON_ONLY",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build FR-028 Phase A timing-corrected research surface.")
    parser.add_argument("--repo-root", default=str(_REPO_ROOT))
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--shadow-root", default="outputs/shadow_candidates")
    parser.add_argument("--output-root", default="outputs/fr028_research_surface")
    parser.add_argument("--through-date", default=None)
    return parser


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 10) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _date_dirs(root: Path, *, through_date: str | None = None) -> list[str]:
    dates = []
    for child in root.iterdir() if root.exists() else []:
        if not child.is_dir():
            continue
        try:
            pd.Timestamp(child.name)
        except Exception:
            continue
        if through_date is None or child.name <= through_date:
            dates.append(child.name)
    return sorted(dates)


def _load_snapshots(shadow_root: Path, *, through_date: str | None = None) -> dict[str, dict[str, dict[str, Any]]]:
    snapshots: dict[str, dict[str, dict[str, Any]]] = {}
    for date in _date_dirs(shadow_root, through_date=through_date):
        day_payloads = {}
        for slug in STRATEGY_SLUGS:
            payload = _read_json(shadow_root / date / f"{slug}.json")
            if payload:
                day_payloads[slug] = payload
        if set(day_payloads) == set(STRATEGY_SLUGS):
            snapshots[date] = day_payloads
    return snapshots


def _load_returns(path: Path, *, through_date: str | None) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    panel = pd.read_parquet(path)
    if panel.empty:
        return pd.DataFrame()
    panel = panel.copy()
    panel["date"] = pd.to_datetime(panel["date"])
    panel["ticker"] = panel["ticker"].astype(str).str.upper()
    if through_date:
        panel = panel[panel["date"] <= pd.Timestamp(through_date)]
    prices = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    return prices.pct_change()


def _weights(snapshot: dict[str, Any]) -> pd.Series:
    series = pd.Series(snapshot.get("target_weights") or {}, dtype=float)
    series.index = series.index.astype(str).str.upper()
    return series


def _weighted_return(returns: pd.DataFrame, date: pd.Timestamp, weights: pd.Series) -> float | None:
    if returns.empty or date not in returns.index:
        return None
    cols = [ticker for ticker in weights.index if ticker in returns.columns]
    if not cols:
        return None
    row = returns.loc[date, cols].fillna(0.0)
    return float(row.mul(weights.reindex(cols).fillna(0.0)).sum())


def _next_session(returns: pd.DataFrame, date: str) -> str | None:
    if returns.empty:
        return None
    dates = pd.DatetimeIndex(returns.index).sort_values()
    later = dates[dates > pd.Timestamp(date)]
    if len(later) == 0:
        return None
    return pd.Timestamp(later[0]).strftime("%Y-%m-%d")


def _metrics(points: list[dict[str, Any]]) -> dict[str, Any]:
    returns = pd.Series([row["daily_return"] for row in points if row.get("daily_return") is not None], dtype=float)
    nav = pd.Series([row["nav"] for row in points if row.get("nav") is not None], dtype=float)
    if returns.empty or nav.empty:
        return {
            "valid_days": 0,
            "cumulative_return": None,
            "max_drawdown": None,
            "volatility": None,
            "sharpe_proxy": None,
        }
    drawdown = nav / nav.cummax() - 1.0
    vol = float(returns.std(ddof=1) * (252.0 ** 0.5)) if len(returns) >= 2 else None
    sharpe = float((returns.mean() * 252.0) / vol) if vol and abs(vol) > 1e-12 else None
    return {
        "valid_days": int(len(returns)),
        "cumulative_return": _round(float(nav.iloc[-1] - 1.0)),
        "max_drawdown": _round(float(drawdown.min())),
        "volatility": _round(vol),
        "sharpe_proxy": _round(sharpe),
        "start_date": points[0].get("date") if points else None,
        "end_date": points[-1].get("date") if points else None,
    }


def build_semantics_chains(
    *,
    snapshots: dict[str, dict[str, dict[str, Any]]],
    returns: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any]]:
    current_points: dict[str, list[dict[str, Any]]] = {slug: [] for slug in MODEL_SLUGS}
    proposed_points: dict[str, list[dict[str, Any]]] = {slug: [] for slug in MODEL_SLUGS}
    current_nav = {slug: 1.0 for slug in MODEL_SLUGS}
    proposed_nav = {slug: 1.0 for slug in MODEL_SLUGS}
    for signal_date, day in sorted(snapshots.items()):
        current_date = pd.Timestamp(signal_date)
        next_date = _next_session(returns, signal_date)
        for slug in STRATEGY_SLUGS:
            weights = _weights(day[slug])
            current_ret = _weighted_return(returns, current_date, weights)
            if current_ret is not None:
                current_nav[slug] *= 1.0 + current_ret
                current_points[slug].append(
                    {
                        "date": signal_date,
                        "source_signal_date": signal_date,
                        "daily_return": _round(current_ret),
                        "nav": _round(current_nav[slug]),
                        "weights_count": int(len(weights)),
                    }
                )
            if next_date:
                proposed_ret = _weighted_return(returns, pd.Timestamp(next_date), weights)
                if proposed_ret is not None:
                    proposed_nav[slug] *= 1.0 + proposed_ret
                    proposed_points[slug].append(
                        {
                            "date": next_date,
                            "source_signal_date": signal_date,
                            "daily_return": _round(proposed_ret),
                            "nav": _round(proposed_nav[slug]),
                            "weights_count": int(len(weights)),
                        }
                    )
        current_spy = _as_float(returns.loc[current_date, "SPY"]) if "SPY" in returns.columns and current_date in returns.index else None
        if current_spy is not None:
            current_nav["spy_benchmark"] *= 1.0 + current_spy
            current_points["spy_benchmark"].append(
                {"date": signal_date, "source_signal_date": signal_date, "daily_return": _round(current_spy), "nav": _round(current_nav["spy_benchmark"]), "weights_count": 1}
            )
        if next_date and "SPY" in returns.columns and pd.Timestamp(next_date) in returns.index:
            proposed_spy = _as_float(returns.loc[pd.Timestamp(next_date), "SPY"])
            if proposed_spy is not None:
                proposed_nav["spy_benchmark"] *= 1.0 + proposed_spy
                proposed_points["spy_benchmark"].append(
                    {"date": next_date, "source_signal_date": signal_date, "daily_return": _round(proposed_spy), "nav": _round(proposed_nav["spy_benchmark"]), "weights_count": 1}
                )
    current = {
        "schema_version": "fr028_semantics_nav_v1",
        **PROVENANCE_CURRENT,
        "strategies": {
            slug: {
                "strategy_name": DISPLAY_NAMES[slug],
                "points": current_points[slug],
                "metrics": _metrics(current_points[slug]),
            }
            for slug in MODEL_SLUGS
        },
    }
    proposed = {
        "schema_version": "fr028_semantics_nav_v1",
        **PROVENANCE_PROPOSED,
        "strategies": {
            slug: {
                "strategy_name": DISPLAY_NAMES[slug],
                "points": proposed_points[slug],
                "metrics": _metrics(proposed_points[slug]),
            }
            for slug in MODEL_SLUGS
        },
    }
    return current, proposed


def _metric_delta(current: dict[str, Any], proposed: dict[str, Any], field: str) -> float | None:
    left = _as_float(((current.get("metrics") or {}).get(field)))
    right = _as_float(((proposed.get("metrics") or {}).get(field)))
    return _round(right - left) if left is not None and right is not None else None


def _common_source_points(current_strategy: dict[str, Any], proposed_strategy: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current_by_source = {row["source_signal_date"]: row for row in current_strategy.get("points") or []}
    proposed_by_source = {row["source_signal_date"]: row for row in proposed_strategy.get("points") or []}
    common = sorted(set(current_by_source) & set(proposed_by_source))
    return [current_by_source[date] for date in common], [proposed_by_source[date] for date in common]


def build_divergence_analysis(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    strategies: dict[str, Any] = {}
    for slug in MODEL_SLUGS:
        current_strategy = current["strategies"][slug]
        proposed_strategy = proposed["strategies"][slug]
        current_common_points, proposed_common_points = _common_source_points(current_strategy, proposed_strategy)
        current_metrics = _metrics(current_common_points)
        proposed_metrics = _metrics(proposed_common_points)
        current_comparable = {"metrics": current_metrics}
        proposed_comparable = {"metrics": proposed_metrics}
        current_excess = (
            _as_float(current_metrics.get("cumulative_return"))
            - _as_float(_metrics(_common_source_points(current["strategies"]["spy_benchmark"], proposed["strategies"]["spy_benchmark"])[0]).get("cumulative_return"))
            if _as_float(current_metrics.get("cumulative_return")) is not None
            and _as_float(_metrics(_common_source_points(current["strategies"]["spy_benchmark"], proposed["strategies"]["spy_benchmark"])[0]).get("cumulative_return")) is not None
            and slug != "spy_benchmark"
            else 0.0
            if slug == "spy_benchmark"
            else None
        )
        proposed_excess = (
            _as_float(proposed_metrics.get("cumulative_return"))
            - _as_float(_metrics(_common_source_points(current["strategies"]["spy_benchmark"], proposed["strategies"]["spy_benchmark"])[1]).get("cumulative_return"))
            if _as_float(proposed_metrics.get("cumulative_return")) is not None
            and _as_float(_metrics(_common_source_points(current["strategies"]["spy_benchmark"], proposed["strategies"]["spy_benchmark"])[1]).get("cumulative_return")) is not None
            and slug != "spy_benchmark"
            else 0.0
            if slug == "spy_benchmark"
            else None
        )
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "current_metrics": current_metrics,
            "proposed_metrics": proposed_metrics,
            "deltas": {
                "cumulative_return_delta": _metric_delta(current_comparable, proposed_comparable, "cumulative_return"),
                "excess_return_vs_spy_delta": _round(proposed_excess - current_excess) if proposed_excess is not None and current_excess is not None else None,
                "max_drawdown_delta": _metric_delta(current_comparable, proposed_comparable, "max_drawdown"),
                "volatility_delta": _metric_delta(current_comparable, proposed_comparable, "volatility"),
                "sharpe_proxy_delta": _metric_delta(current_comparable, proposed_comparable, "sharpe_proxy"),
            },
            "comparison_observation_count": len(current_common_points),
            "excluded_current_only_source_dates": sorted(
                set(row["source_signal_date"] for row in current_strategy.get("points") or [])
                - set(row["source_signal_date"] for row in proposed_strategy.get("points") or [])
            ),
            "timing_sensitivity_abs": _round(abs(_metric_delta(current_comparable, proposed_comparable, "cumulative_return") or 0.0)),
        }
    ranked_sensitivity = sorted(
        [
            {"strategy": slug, "strategy_name": DISPLAY_NAMES[slug], "timing_sensitivity_abs": payload["timing_sensitivity_abs"]}
            for slug, payload in strategies.items()
            if slug != "spy_benchmark"
        ],
        key=lambda item: item["timing_sensitivity_abs"] or 0.0,
        reverse=True,
    )
    return {
        "schema_version": "fr028_nav_divergence_analysis_v1",
        **PROVENANCE_PROPOSED,
        "current_surface": PROVENANCE_CURRENT,
        "proposed_surface": PROVENANCE_PROPOSED,
        "strategies": strategies,
        "ranked_timing_sensitivity": ranked_sensitivity,
    }


def _rank_by_metric(surface: dict[str, Any], field: str, other_surface: dict[str, Any] | None = None) -> list[str]:
    def metric(slug: str) -> float | None:
        if other_surface is not None:
            left, right = _common_source_points(surface["strategies"][slug], other_surface["strategies"][slug])
            points = left if surface.get("nav_surface_type") == PROVENANCE_CURRENT["nav_surface_type"] else right
            return _as_float(_metrics(points).get(field))
        return _as_float((surface["strategies"][slug].get("metrics") or {}).get(field))

    return [
        slug
        for slug, _ in sorted(
            [
                (slug, metric(slug))
                for slug in STRATEGY_SLUGS
            ],
            key=lambda item: item[1] if item[1] is not None else float("-inf"),
            reverse=True,
        )
    ]


def build_ranking_delta(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    current_rank = _rank_by_metric(current, "cumulative_return", proposed)
    proposed_rank = _rank_by_metric(proposed, "cumulative_return", current)
    return {
        "schema_version": "fr028_strategy_ranking_delta_v1",
        **PROVENANCE_PROPOSED,
        "ranking_metric": "cumulative_return",
        "current_ranking": current_rank,
        "proposed_ranking": proposed_rank,
        "rank_changes": {
            slug: {
                "current_rank": current_rank.index(slug) + 1 if slug in current_rank else None,
                "proposed_rank": proposed_rank.index(slug) + 1 if slug in proposed_rank else None,
                "delta": (proposed_rank.index(slug) - current_rank.index(slug)) if slug in current_rank and slug in proposed_rank else None,
            }
            for slug in STRATEGY_SLUGS
        },
    }


def build_drawdown_delta(current: dict[str, Any], proposed: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "fr028_drawdown_delta_analysis_v1",
        **PROVENANCE_PROPOSED,
        "strategies": {
            slug: {
                "strategy_name": DISPLAY_NAMES[slug],
                "current_max_drawdown": current["strategies"][slug]["metrics"].get("max_drawdown"),
                "proposed_max_drawdown": proposed["strategies"][slug]["metrics"].get("max_drawdown"),
                "drawdown_delta": _metric_delta(current["strategies"][slug], proposed["strategies"][slug], "max_drawdown"),
            }
            for slug in MODEL_SLUGS
        },
    }


def _load_optional_json(path: Path) -> dict[str, Any]:
    return _read_json(path) or {}


def build_attribution_delta(divergence: dict[str, Any], attribution_root: Path, as_of_date: str) -> dict[str, Any]:
    contribution = _load_optional_json(attribution_root / as_of_date / "concentration_analysis.json")
    exposure = _load_optional_json(attribution_root / as_of_date / "factor_exposure.json")
    strategies = {}
    for slug in STRATEGY_SLUGS:
        top3_share = (((contribution.get("strategies") or {}).get(slug) or {}).get("top3_contribution_share_21d"))
        beta = (((exposure.get("strategies") or {}).get(slug) or {}).get("market_beta"))
        timing = (((divergence.get("strategies") or {}).get(slug) or {}).get("timing_sensitivity_abs"))
        strategies[slug] = {
            "strategy_name": DISPLAY_NAMES[slug],
            "timing_sensitivity_abs": timing,
            "top3_contribution_share_21d": top3_share,
            "market_beta": beta,
            "concentration_amplifies_timing_risk": bool(top3_share is not None and timing is not None and top3_share >= 0.75 and timing > 0.01),
            "beta_amplification_changes_materially": bool(beta is not None and timing is not None and beta >= 1.5 and timing > 0.01),
            "status": "COMPARISON_PROXY_REQUIRES_ATTRIBUTION_HISTORY",
        }
    return {"schema_version": "fr028_attribution_delta_analysis_v1", **PROVENANCE_PROPOSED, "strategies": strategies}


def _simple_regime_for_date(returns: pd.DataFrame, date: str) -> str:
    if returns.empty or "SPY" not in returns.columns:
        return "unknown"
    series = returns.loc[returns.index <= pd.Timestamp(date), "SPY"].dropna()
    if len(series) < 20:
        return "unknown"
    trailing = float(series.tail(20).sum())
    vol = float(series.tail(20).std(ddof=1))
    if trailing < 0:
        return "risk_off"
    if vol > float(series.rolling(20).std().dropna().tail(252).median() or 0.0):
        return "high_vol_risk_on"
    return "risk_on"


def build_regime_delta(current: dict[str, Any], proposed: dict[str, Any], returns: pd.DataFrame) -> dict[str, Any]:
    strategies = {}
    for slug in STRATEGY_SLUGS:
        rows = []
        proposed_by_signal = {row["source_signal_date"]: row for row in proposed["strategies"][slug]["points"]}
        for row in current["strategies"][slug]["points"]:
            proposed_row = proposed_by_signal.get(row["source_signal_date"])
            if not proposed_row:
                continue
            rows.append(
                {
                    "source_signal_date": row["source_signal_date"],
                    "current_date": row["date"],
                    "proposed_realization_date": proposed_row["date"],
                    "regime": _simple_regime_for_date(returns, row["date"]),
                    "current_return": row["daily_return"],
                    "proposed_return": proposed_row["daily_return"],
                    "return_delta": _round(proposed_row["daily_return"] - row["daily_return"]),
                }
            )
        by_regime: dict[str, Any] = {}
        for regime, group in pd.DataFrame(rows).groupby("regime") if rows else []:
            by_regime[str(regime)] = {
                "count": int(len(group)),
                "avg_return_delta": _round(float(group["return_delta"].mean())),
                "max_abs_return_delta": _round(float(group["return_delta"].abs().max())),
            }
        strategies[slug] = {"strategy_name": DISPLAY_NAMES[slug], "by_regime": by_regime, "rows": rows}
    return {"schema_version": "fr028_regime_delta_analysis_v1", **PROVENANCE_PROPOSED, "strategies": strategies}


def build_governance_impact(divergence: dict[str, Any], ranking: dict[str, Any]) -> dict[str, Any]:
    material = []
    for slug, payload in divergence["strategies"].items():
        if slug == "spy_benchmark":
            continue
        sensitivity = payload.get("timing_sensitivity_abs")
        if sensitivity is not None and sensitivity >= 0.01:
            material.append(slug)
    rank_changed = [slug for slug, item in ranking["rank_changes"].items() if item.get("delta") not in (None, 0)]
    return {
        "schema_version": "fr028_governance_impact_review_v1",
        **PROVENANCE_PROPOSED,
        "status": "RESEARCH_ONLY_NO_GOVERNANCE_CHANGE",
        "promotion_thresholds_that_would_change": "NOT_EVALUATED_AS_GATE; compare metrics only.",
        "dashboards_requiring_reinterpretation": ["shadow scorecards", "CIO shadow report", "dashboard performance panels"],
        "confidence_recommendation": "Keep OPERATIONAL_SHADOW_NAV LOW until FR-028 Phase B/C governance review.",
        "historical_artifact_comparability": "Current and proposed surfaces are incomparable unless nav_surface_type and timing_semantics are displayed.",
        "legacy_labeling_required_if_migrated_later": True,
        "material_timing_sensitivity_strategies": material,
        "ranking_changed": rank_changed,
        "no_actions_taken": [
            "no promotion logic changed",
            "no dashboards changed",
            "no historical NAV chains rewritten",
            "no production accounting semantics modified",
        ],
    }


def build_summary_markdown(
    *,
    as_of_date: str,
    divergence: dict[str, Any],
    ranking: dict[str, Any],
    governance: dict[str, Any],
) -> str:
    lines = [
        "# FR-028 Phase A Timing Surface Summary",
        "",
        "## Executive Summary",
        "- Status: `RESEARCH_ONLY_PARALLEL_ANALYSIS`",
        f"- As of date: `{as_of_date}`",
        "- Current semantics: same-date target weights against same-date close-to-close returns.",
        "- Proposed semantics: signal-date weights against next-session close-to-close returns.",
        "- No production semantics, dashboards, promotion logic, or historical chains were modified.",
        "",
        "## Divergence",
        "| Strategy | Current Cum Return | Proposed Cum Return | Delta | Timing Sensitivity |",
        "|---|---:|---:|---:|---:|",
    ]
    for slug in STRATEGY_SLUGS:
        payload = divergence["strategies"][slug]
        current_ret = payload["current_metrics"].get("cumulative_return")
        proposed_ret = payload["proposed_metrics"].get("cumulative_return")
        delta = payload["deltas"].get("cumulative_return_delta")
        sensitivity = payload.get("timing_sensitivity_abs")
        lines.append(
            f"| {DISPLAY_NAMES[slug]} | {_fmt_pct(current_ret)} | {_fmt_pct(proposed_ret)} | {_fmt_pct(delta)} | {_fmt_pct(sensitivity)} |"
        )
    lines.extend(
        [
            "",
            "## Ranking Impact",
            f"- Current ranking: `{ranking['current_ranking']}`",
            f"- Proposed ranking: `{ranking['proposed_ranking']}`",
            "",
            "## Governance Impact",
            f"- Material timing sensitivity strategies: `{governance['material_timing_sensitivity_strategies']}`",
            f"- Ranking changed: `{governance['ranking_changed']}`",
            "- Recommendation: keep this surface research-only until a later FR-governed migration decision.",
        ]
    )
    return "\n".join(lines) + "\n"


def _fmt_pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value:+.2%}"


def _write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_fr028_phase_a(
    *,
    repo_root: Path,
    price_cache_path: Path,
    shadow_root: Path,
    output_root: Path,
    through_date: str | None,
) -> tuple[dict[str, Any], list[Path]]:
    snapshots = _load_snapshots(shadow_root, through_date=through_date)
    if not snapshots:
        raise SystemExit("No complete dated shadow snapshots found for FR-028 comparison.")
    as_of_date = max(snapshots)
    returns = _load_returns(price_cache_path, through_date=through_date)
    current, proposed = build_semantics_chains(snapshots=snapshots, returns=returns)
    divergence = build_divergence_analysis(current, proposed)
    ranking = build_ranking_delta(current, proposed)
    drawdown = build_drawdown_delta(current, proposed)
    attribution_delta = build_attribution_delta(divergence, repo_root / "outputs" / "attribution", as_of_date)
    regime_delta = build_regime_delta(current, proposed, returns)
    governance = build_governance_impact(divergence, ranking)
    timing_sensitivity = {
        "schema_version": "fr028_timing_sensitivity_report_v1",
        **PROVENANCE_PROPOSED,
        "ranked_timing_sensitivity": divergence["ranked_timing_sensitivity"],
        "interpretation": "Higher values indicate larger cumulative-return change under proposed timing-corrected realization.",
    }
    out_dir = output_root / as_of_date
    artifacts = {
        "current_semantics_nav.json": current,
        "proposed_semantics_nav.json": proposed,
        "nav_divergence_analysis.json": divergence,
        "timing_sensitivity_report.json": timing_sensitivity,
        "strategy_ranking_delta.json": ranking,
        "drawdown_delta_analysis.json": drawdown,
        "attribution_delta_analysis.json": attribution_delta,
        "regime_delta_analysis.json": regime_delta,
        "governance_impact_review.json": governance,
    }
    written = [_write_json(out_dir / name, payload) for name, payload in artifacts.items()]
    summary_md = build_summary_markdown(as_of_date=as_of_date, divergence=divergence, ranking=ranking, governance=governance)
    summary_path = out_dir / "fr028_phase_a_summary.md"
    summary_path.write_text(summary_md, encoding="utf-8")
    written.append(summary_path)
    summary = {
        "as_of_date": as_of_date,
        "artifact_dir": str(out_dir.relative_to(repo_root)),
        "surface_status": "RESEARCH_ONLY_PARALLEL_ANALYSIS",
        "snapshots_used": sorted(snapshots),
        "current_surface": PROVENANCE_CURRENT,
        "proposed_surface": PROVENANCE_PROPOSED,
        "governance_status": governance["status"],
        "ranked_timing_sensitivity": divergence["ranked_timing_sensitivity"],
    }
    return summary, written


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    price_cache_path = (repo_root / args.price_cache_path).resolve() if not Path(args.price_cache_path).is_absolute() else Path(args.price_cache_path)
    shadow_root = (repo_root / args.shadow_root).resolve() if not Path(args.shadow_root).is_absolute() else Path(args.shadow_root)
    output_root = (repo_root / args.output_root).resolve() if not Path(args.output_root).is_absolute() else Path(args.output_root)
    summary, written = run_fr028_phase_a(
        repo_root=repo_root,
        price_cache_path=price_cache_path,
        shadow_root=shadow_root,
        output_root=output_root,
        through_date=args.through_date,
    )
    print(f"[FR-028] phase=A status={summary['surface_status']} as_of={summary['as_of_date']}")
    for path in written:
        print(f"[FR-028] wrote {path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
