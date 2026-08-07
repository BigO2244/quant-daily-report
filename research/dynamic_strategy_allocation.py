"""Tier 3 Caerus dynamic strategy allocation — RESEARCH ONLY.

Evaluates hypothetical portfolio policies that combine the three
strategies (Polaris, Orion, Lyra) without changing production weights,
execution payloads, or strategy config. Every policy name carries the
``_research_only`` suffix where appropriate, and the artifact records
``is_research_only: true`` at the top level. The module never writes to
``configs/`` or ``outputs/strategy_weights/``.

Five candidate policies are scored against the historical NAV series:

  1. static_equal_weight              — 1/3 each
  2. benchmark_heavy                  — 50% Polaris, 25% Orion, 25% Lyra
  3. challenger_balanced              — 40% Polaris, 30% Orion, 30% Lyra
  4. lyra_tilt_research_only          — 35% Polaris, 25% Orion, 40% Lyra
  5. regime_conditioned_research_only — uses regime_attribution.json to
                                        pick per-day weights; falls back
                                        to ``available=False`` when the
                                        regime artifact is missing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

SCHEMA_VERSION = "caerus_dynamic_strategy_allocation_v1"

STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BENCHMARK_STRATEGY = "caerus_polaris"
TRADING_DAYS_PER_YEAR = 252

MIN_HISTORY_DAYS = 40
MIN_OBS_HIGH_CONFIDENCE = 252
MIN_OBS_MEDIUM_CONFIDENCE = 60

# Risk-adjusted score weights.
SCORE_WEIGHT_EXCESS = 0.5
SCORE_WEIGHT_VOL_PENALTY = 0.3
SCORE_WEIGHT_DRAWDOWN_PENALTY = 0.2

STATIC_POLICIES: dict[str, dict[str, float]] = {
    "static_equal_weight": {
        "caerus_polaris": 1.0 / 3.0,
        "caerus_orion": 1.0 / 3.0,
        "caerus_lyra": 1.0 / 3.0,
    },
    "benchmark_heavy": {
        "caerus_polaris": 0.50,
        "caerus_orion": 0.25,
        "caerus_lyra": 0.25,
    },
    "challenger_balanced": {
        "caerus_polaris": 0.40,
        "caerus_orion": 0.30,
        "caerus_lyra": 0.30,
    },
    "lyra_tilt_research_only": {
        "caerus_polaris": 0.35,
        "caerus_orion": 0.25,
        "caerus_lyra": 0.40,
    },
}

# Regime-conditioned weight table. Each weight tuple must sum to 1.0.
# The policy treats panic as a flight-to-control state, bear/high_vol as
# defensive, and bull/recovery/low_vol as risk-on. These are hypothetical
# heuristics for research evaluation only — never production weights.
REGIME_WEIGHTS_RESEARCH_ONLY: dict[str, dict[str, float]] = {
    "panic": {"caerus_polaris": 0.70, "caerus_orion": 0.15, "caerus_lyra": 0.15},
    "bear_trend": {"caerus_polaris": 0.55, "caerus_orion": 0.225, "caerus_lyra": 0.225},
    "high_vol": {"caerus_polaris": 0.50, "caerus_orion": 0.25, "caerus_lyra": 0.25},
    "recovery": {"caerus_polaris": 0.30, "caerus_orion": 0.35, "caerus_lyra": 0.35},
    "bull_trend": {"caerus_polaris": 0.30, "caerus_orion": 0.30, "caerus_lyra": 0.40},
    "low_vol": {"caerus_polaris": 0.40, "caerus_orion": 0.30, "caerus_lyra": 0.30},
    "neutral": {"caerus_polaris": 0.40, "caerus_orion": 0.30, "caerus_lyra": 0.30},
}


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except Exception:
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _round(value: Any, digits: int = 10) -> float | None:
    f = _safe_float(value)
    return round(f, digits) if f is not None else None


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_nav_series(repo: Path) -> pd.DataFrame | None:
    path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    if "date" not in frame.columns:
        return None
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    return frame.dropna(subset=["date"]).sort_values("date", kind="mergesort").reset_index(drop=True)


def _load_regime_attribution(repo: Path, trade_date: str) -> tuple[dict[str, Any] | None, str]:
    path = repo / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json"
    payload = _read_json(path)
    return payload, str(path)


# ---------------------------------------------------------------------------
# Per-policy metrics
# ---------------------------------------------------------------------------

def _compute_metrics(
    strategy_returns: pd.DataFrame,
    weights_per_day: pd.DataFrame,
) -> dict[str, Any]:
    """Compute portfolio metrics given daily strategy returns and per-day weights.

    Both inputs share the same date index and column set.
    """
    aligned = strategy_returns.reindex(columns=STRATEGIES, fill_value=0.0)
    weights = weights_per_day.reindex(columns=STRATEGIES, fill_value=0.0)
    portfolio_returns = (aligned * weights).sum(axis=1)
    polaris_returns = aligned.get(BENCHMARK_STRATEGY, pd.Series(0.0, index=aligned.index))
    excess = portfolio_returns - polaris_returns

    obs = int(portfolio_returns.dropna().shape[0])
    if obs == 0:
        return {
            "observation_count": 0,
            "total_return": None,
            "excess_return_vs_polaris": None,
            "realized_volatility": None,
            "max_drawdown": None,
            "hit_rate": None,
            "turnover_proxy": None,
            "concentration_proxy": None,
            "risk_adjusted_score": None,
        }
    total_return = float((1.0 + portfolio_returns.fillna(0.0)).prod() - 1.0)
    polaris_total = float((1.0 + polaris_returns.fillna(0.0)).prod() - 1.0)
    excess_total = total_return - polaris_total
    vol = float(portfolio_returns.std() * math.sqrt(TRADING_DAYS_PER_YEAR)) if obs > 1 else 0.0
    cum = (1.0 + portfolio_returns.fillna(0.0)).cumprod()
    peak = cum.cummax()
    drawdown = float((cum / peak - 1.0).min())
    hit_rate = float((portfolio_returns > 0).sum() / obs)
    turnover_proxy = float(weights.diff().abs().sum(axis=1).fillna(0.0).mean())
    concentration_proxy = float(weights.max(axis=1).mean())

    # Risk-adjusted score: rewards excess vs Polaris, penalizes vol and
    # depth of drawdown. Bounded above by 1.0 to keep it intuitive.
    excess_term = max(min(excess_total, 1.0), -1.0)
    vol_penalty = -min(vol, 1.0)
    dd_penalty = -min(abs(drawdown), 1.0)
    risk_adjusted_score = (
        SCORE_WEIGHT_EXCESS * excess_term
        + SCORE_WEIGHT_VOL_PENALTY * vol_penalty
        + SCORE_WEIGHT_DRAWDOWN_PENALTY * dd_penalty
    )
    return {
        "observation_count": obs,
        "total_return": _round(total_return),
        "excess_return_vs_polaris": _round(excess_total),
        "realized_volatility": _round(vol),
        "max_drawdown": _round(drawdown),
        "hit_rate": _round(hit_rate),
        "turnover_proxy": _round(turnover_proxy),
        "concentration_proxy": _round(concentration_proxy),
        "risk_adjusted_score": _round(risk_adjusted_score),
    }


# ---------------------------------------------------------------------------
# Per-policy assembly
# ---------------------------------------------------------------------------

def _confidence_from_obs(observation_count: int) -> str:
    if observation_count >= MIN_OBS_HIGH_CONFIDENCE:
        return "HIGH"
    if observation_count >= MIN_OBS_MEDIUM_CONFIDENCE:
        return "MEDIUM"
    return "LOW"


def _evaluate_static_policy(
    policy_name: str,
    weights: dict[str, float],
    strategy_returns: pd.DataFrame,
) -> dict[str, Any]:
    index = strategy_returns.index
    weights_per_day = pd.DataFrame(
        {strategy: [float(weights.get(strategy, 0.0))] * len(index) for strategy in STRATEGIES},
        index=index,
    )
    metrics = _compute_metrics(strategy_returns, weights_per_day)
    obs = int(metrics.get("observation_count") or 0)
    reason_codes: list[str] = []
    available = obs >= MIN_HISTORY_DAYS
    if not available:
        reason_codes.append("insufficient_history")
    weight_sum = sum(float(w) for w in weights.values())
    if abs(weight_sum - 1.0) > 1e-9:
        reason_codes.append("weights_do_not_sum_to_one")
        available = False
    return {
        "policy": policy_name,
        "is_research_only": True,
        "policy_kind": "static",
        "weights": {k: _round(v) for k, v in sorted(weights.items())},
        "weights_sum": _round(weight_sum),
        "available": available,
        "confidence": _confidence_from_obs(obs) if available else "LOW",
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
        **metrics,
    }


def _evaluate_regime_conditioned_policy(
    strategy_returns: pd.DataFrame,
    regime_payload: dict[str, Any] | None,
    nav_frame: pd.DataFrame,
    trade_date: str,
) -> dict[str, Any]:
    if regime_payload is None or not bool(regime_payload.get("available")):
        return {
            "policy": "regime_conditioned_research_only",
            "is_research_only": True,
            "policy_kind": "regime_conditioned",
            "weights": {},
            "weights_sum": None,
            "available": False,
            "confidence": "LOW",
            "reason_codes": ["regime_attribution_unavailable"],
            "observation_count": 0,
            "total_return": None,
            "excess_return_vs_polaris": None,
            "realized_volatility": None,
            "max_drawdown": None,
            "hit_rate": None,
            "turnover_proxy": None,
            "concentration_proxy": None,
            "risk_adjusted_score": None,
        }
    # We need a per-day regime classification series. Re-classify locally
    # so that the daily mapping is deterministic and doesn't depend on
    # how regime_attribution chose to roll up days.
    from research.regime_attribution import _classify_regimes, _filter_to_target_date  # type: ignore

    filtered = _filter_to_target_date(nav_frame, trade_date)
    classified = _classify_regimes(filtered)
    if "regime" not in classified.columns:
        return {
            "policy": "regime_conditioned_research_only",
            "is_research_only": True,
            "policy_kind": "regime_conditioned",
            "weights": {},
            "weights_sum": None,
            "available": False,
            "confidence": "LOW",
            "reason_codes": ["regime_classification_failed"],
            "observation_count": 0,
            "total_return": None,
            "excess_return_vs_polaris": None,
            "realized_volatility": None,
            "max_drawdown": None,
            "hit_rate": None,
            "turnover_proxy": None,
            "concentration_proxy": None,
            "risk_adjusted_score": None,
        }
    # Build per-day weights using prior-day regime (lag by 1) so the
    # policy is causally honest: we set today's weights using yesterday's
    # observed regime, never today's lookahead.
    regimes_lagged = classified["regime"].shift(1)
    index = strategy_returns.index
    aligned_regimes = (
        pd.Series(regimes_lagged.values, index=classified["date"])
        .reindex(index)
        .fillna("neutral")
        .astype(str)
    )
    weights_per_day = pd.DataFrame(
        index=index,
        columns=list(STRATEGIES),
        data={s: [0.0] * len(index) for s in STRATEGIES},
    )
    for regime, mapping in REGIME_WEIGHTS_RESEARCH_ONLY.items():
        mask = aligned_regimes == regime
        if not mask.any():
            continue
        for strategy in STRATEGIES:
            weights_per_day.loc[mask, strategy] = float(mapping.get(strategy, 0.0))
    metrics = _compute_metrics(strategy_returns, weights_per_day)
    obs = int(metrics.get("observation_count") or 0)
    reason_codes: list[str] = []
    available = obs >= MIN_HISTORY_DAYS
    if not available:
        reason_codes.append("insufficient_history")
    return {
        "policy": "regime_conditioned_research_only",
        "is_research_only": True,
        "policy_kind": "regime_conditioned",
        "weights": {
            regime: {k: _round(v) for k, v in sorted(mapping.items())}
            for regime, mapping in sorted(REGIME_WEIGHTS_RESEARCH_ONLY.items())
        },
        "weights_sum": None,
        "available": available,
        "confidence": _confidence_from_obs(obs) if available else "LOW",
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
        **metrics,
    }


# ---------------------------------------------------------------------------
# Main build
# ---------------------------------------------------------------------------

def build_dynamic_strategy_allocation(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    promotion_governance_allows_change: bool = False,
) -> dict[str, Any]:
    repo = Path(repo_root)
    nav_path = repo / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    nav_frame = _load_nav_series(repo)

    if nav_frame is None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "is_research_only": True,
            "production_weights_modified": False,
            "available": False,
            "confidence": "LOW",
            "policies": [],
            "ranking": [],
            "promotion_governance_allows_change": bool(promotion_governance_allows_change),
            "allocation_recommendation": "no_allocation_change_recommended",
            "reason_codes": ["missing_shadow_nav_series"],
            "source_artifacts": [],
        }
        out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "dynamic_strategy_allocation") / trade_date
        _write_json(out_dir / "dynamic_strategy_allocation.json", payload)
        _write_text(out_dir / "dynamic_strategy_allocation.md", render_markdown(payload))
        return payload

    cutoff = pd.Timestamp(trade_date)
    nav_in_window = nav_frame.loc[nav_frame["date"] <= cutoff].copy().reset_index(drop=True)
    if nav_in_window.empty:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "date": trade_date,
            "is_research_only": True,
            "production_weights_modified": False,
            "available": False,
            "confidence": "LOW",
            "policies": [],
            "ranking": [],
            "promotion_governance_allows_change": bool(promotion_governance_allows_change),
            "allocation_recommendation": "no_allocation_change_recommended",
            "reason_codes": ["no_history_at_or_before_target_date"],
            "source_artifacts": [str(nav_path)],
        }
        out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "dynamic_strategy_allocation") / trade_date
        _write_json(out_dir / "dynamic_strategy_allocation.json", payload)
        _write_text(out_dir / "dynamic_strategy_allocation.md", render_markdown(payload))
        return payload

    strategy_columns_present = [c for c in STRATEGIES if c in nav_in_window.columns]
    missing_strategies = [c for c in STRATEGIES if c not in nav_in_window.columns]
    nav_indexed = nav_in_window.set_index("date")
    available_nav = nav_indexed.reindex(columns=list(STRATEGIES))
    strategy_returns = available_nav.astype(float).pct_change(fill_method=None).dropna(how="all")

    reason_codes: list[str] = []
    if missing_strategies:
        reason_codes.append("strategy_returns_missing")

    regime_payload, regime_path = _load_regime_attribution(repo, trade_date)

    policies: list[dict[str, Any]] = []
    for policy_name, weights in STATIC_POLICIES.items():
        policies.append(_evaluate_static_policy(policy_name, weights, strategy_returns))
    policies.append(
        _evaluate_regime_conditioned_policy(strategy_returns, regime_payload, nav_frame, trade_date)
    )

    # Ranking: among available policies, sort by risk_adjusted_score desc.
    ranking = []
    for row in policies:
        if not row.get("available"):
            continue
        ranking.append(
            {
                "policy": row["policy"],
                "risk_adjusted_score": row["risk_adjusted_score"],
                "excess_return_vs_polaris": row["excess_return_vs_polaris"],
                "realized_volatility": row["realized_volatility"],
                "max_drawdown": row["max_drawdown"],
                "confidence": row["confidence"],
            }
        )
    ranking.sort(
        key=lambda r: (
            -(_safe_float(r.get("risk_adjusted_score")) or -1e9),
            str(r.get("policy") or ""),
        )
    )
    for index, row in enumerate(ranking):
        row["rank"] = index + 1

    source_artifacts = [str(nav_path)]
    if regime_payload is not None:
        source_artifacts.append(regime_path)

    obs_counts = [int(row.get("observation_count") or 0) for row in policies if row.get("available")]
    if not obs_counts:
        confidence = "LOW"
    else:
        min_obs = min(obs_counts)
        confidence = _confidence_from_obs(min_obs)

    available = any(row.get("available") for row in policies)

    # Conservative final recommendation: never recommend an allocation
    # change unless promotion_governance explicitly clears it AND a
    # ranked policy is available.
    if available and ranking and promotion_governance_allows_change:
        allocation_recommendation = ranking[0]["policy"]
    else:
        allocation_recommendation = "no_allocation_change_recommended"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "is_research_only": True,
        "production_weights_modified": False,
        "available": available,
        "confidence": confidence,
        "policies": policies,
        "ranking": ranking,
        "promotion_governance_allows_change": bool(promotion_governance_allows_change),
        "allocation_recommendation": allocation_recommendation,
        "reason_codes": sorted(set(reason_codes)) or ["ok"],
        "source_artifacts": sorted(set(source_artifacts)),
    }

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "research" / "dynamic_strategy_allocation") / trade_date
    _write_json(out_dir / "dynamic_strategy_allocation.json", payload)
    _write_text(out_dir / "dynamic_strategy_allocation.md", render_markdown(payload))
    return payload


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        f"# Dynamic Strategy Allocation (Research Only) - {payload.get('date')}",
        "",
        f"- Available: {payload.get('available')}",
        f"- Confidence: {payload.get('confidence')}",
        f"- Is research only: {payload.get('is_research_only')}",
        f"- Production weights modified: {payload.get('production_weights_modified')}",
        f"- Allocation recommendation: {payload.get('allocation_recommendation')}",
        f"- Promotion governance allows change: {payload.get('promotion_governance_allows_change')}",
        f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
        "",
        "## Policy Ranking",
        "",
        "| Rank | Policy | Excess vs Polaris | Vol | MaxDD | Score | Confidence |",
        "|---:|---|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("ranking") or []:
        lines.append(
            f"| {row.get('rank')} | {row.get('policy')} | {row.get('excess_return_vs_polaris')} | {row.get('realized_volatility')} | {row.get('max_drawdown')} | {row.get('risk_adjusted_score')} | {row.get('confidence')} |"
        )
    lines += [
        "",
        "## All Policies",
        "",
        "| Policy | Available | Obs | Total Ret | Excess vs Polaris | Vol | MaxDD | Hit Rate | Score | Reasons |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in payload.get("policies") or []:
        lines.append(
            f"| {row.get('policy')} | {row.get('available')} | {row.get('observation_count')} | {row.get('total_return')} | {row.get('excess_return_vs_polaris')} | {row.get('realized_volatility')} | {row.get('max_drawdown')} | {row.get('hit_rate')} | {row.get('risk_adjusted_score')} | {', '.join(row.get('reason_codes') or [])} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Tier 3 research-only dynamic strategy allocation artifacts.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-root", default=None)
    parser.add_argument(
        "--promotion-governance-allows-change",
        action="store_true",
        help=(
            "Set when promotion_governance.json explicitly authorizes an "
            "allocation change. When false (default) the final "
            "allocation_recommendation is always no_allocation_change_recommended."
        ),
    )
    args = parser.parse_args(argv)
    payload = build_dynamic_strategy_allocation(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        promotion_governance_allows_change=bool(args.promotion_governance_allows_change),
    )
    print(
        json.dumps(
            {
                "date": args.date,
                "available": payload["available"],
                "confidence": payload["confidence"],
                "allocation_recommendation": payload["allocation_recommendation"],
                "ranking_top": payload["ranking"][0]["policy"] if payload["ranking"] else None,
                "reason_codes": payload["reason_codes"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
