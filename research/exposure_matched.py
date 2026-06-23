"""Exposure-matched research utilities.

Separates deployment/cash effects from sizing effects for research-only
portfolio construction studies.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ExposureMetrics:
    average_gross_exposure: float
    average_cash_weight: float
    average_holdings_count: float
    average_hhi: float


def daily_gross_exposure(weights: pd.DataFrame, *, date_col: str = "trade_date") -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float, name="gross_exposure")
    required = {date_col, "target_weight"}
    missing = required - set(weights.columns)
    if missing:
        raise ValueError(f"weights missing columns: {sorted(missing)}")
    gross = (
        weights.assign(_abs_weight=weights["target_weight"].astype(float).abs())
        .groupby(date_col)["_abs_weight"]
        .sum()
        .sort_index()
    )
    gross.name = "gross_exposure"
    return gross


def exposure_match_weights(
    candidate: pd.DataFrame,
    baseline: pd.DataFrame,
    *,
    date_col: str = "trade_date",
) -> pd.DataFrame:
    """Scale candidate daily gross exposure to baseline daily gross exposure."""
    if candidate.empty:
        return candidate.copy()
    required = {date_col, "security_id", "target_weight"}
    for name, frame in {"candidate": candidate, "baseline": baseline}.items():
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"{name} weights missing columns: {sorted(missing)}")
    cand = candidate.copy()
    cand["_candidate_gross"] = cand[date_col].map(daily_gross_exposure(candidate, date_col=date_col))
    cand["_baseline_gross"] = cand[date_col].map(daily_gross_exposure(baseline, date_col=date_col))
    cand["_scale"] = 0.0
    mask = cand["_candidate_gross"] > 0
    cand.loc[mask, "_scale"] = cand.loc[mask, "_baseline_gross"].fillna(0.0) / cand.loc[mask, "_candidate_gross"]
    cand["target_weight"] = cand["target_weight"].astype(float) * cand["_scale"].astype(float)
    return cand.drop(columns=["_candidate_gross", "_baseline_gross", "_scale"])


def exposure_metrics(weights: pd.DataFrame, *, date_col: str = "trade_date") -> ExposureMetrics:
    if weights.empty:
        return ExposureMetrics(0.0, 1.0, 0.0, 0.0)
    gross = daily_gross_exposure(weights, date_col=date_col)
    active = weights[weights["target_weight"].astype(float).abs() > 0].copy()
    holdings = active.groupby(date_col)["security_id"].nunique()
    hhi = (
        active.assign(_sq=active["target_weight"].astype(float) ** 2)
        .groupby(date_col)["_sq"]
        .sum()
    )
    return ExposureMetrics(
        average_gross_exposure=round(float(gross.mean()) if not gross.empty else 0.0, 10),
        average_cash_weight=round(float((1.0 - gross).clip(lower=0.0).mean()) if not gross.empty else 1.0, 10),
        average_holdings_count=round(float(holdings.mean()) if not holdings.empty else 0.0, 10),
        average_hhi=round(float(hhi.mean()) if not hhi.empty else 0.0, 10),
    )


def portfolio_returns(
    weights: pd.DataFrame,
    returns: pd.DataFrame,
    *,
    date_col: str = "trade_date",
    return_col: str = "forward_return",
) -> pd.Series:
    required_weights = {date_col, "security_id", "target_weight"}
    required_returns = {date_col, "security_id", return_col}
    missing_weights = required_weights - set(weights.columns)
    missing_returns = required_returns - set(returns.columns)
    if missing_weights:
        raise ValueError(f"weights missing columns: {sorted(missing_weights)}")
    if missing_returns:
        raise ValueError(f"returns missing columns: {sorted(missing_returns)}")
    merged = weights.merge(returns[[date_col, "security_id", return_col]], on=[date_col, "security_id"], how="left")
    merged[return_col] = pd.to_numeric(merged[return_col], errors="coerce").fillna(0.0)
    merged["_contribution"] = merged["target_weight"].astype(float) * merged[return_col].astype(float)
    out = merged.groupby(date_col)["_contribution"].sum().sort_index()
    out.name = "portfolio_return"
    return out


def attribution_decomposition(
    *,
    baseline_weights: pd.DataFrame,
    candidate_weights: pd.DataFrame,
    forward_returns: pd.DataFrame,
    date_col: str = "trade_date",
) -> dict[str, float]:
    matched_candidate = exposure_match_weights(candidate_weights, baseline_weights, date_col=date_col)
    baseline_return = portfolio_returns(baseline_weights, forward_returns, date_col=date_col)
    matched_return = portfolio_returns(matched_candidate, forward_returns, date_col=date_col)
    unmatched_return = portfolio_returns(candidate_weights, forward_returns, date_col=date_col)
    idx = baseline_return.index.union(matched_return.index).union(unmatched_return.index)
    baseline_return = baseline_return.reindex(idx, fill_value=0.0)
    matched_return = matched_return.reindex(idx, fill_value=0.0)
    unmatched_return = unmatched_return.reindex(idx, fill_value=0.0)
    sizing_effect = matched_return - baseline_return
    deployment_effect = unmatched_return - matched_return
    total_effect = unmatched_return - baseline_return
    return {
        "baseline_total_return_proxy": round(float(baseline_return.sum()), 10),
        "candidate_matched_total_return_proxy": round(float(matched_return.sum()), 10),
        "candidate_unmatched_total_return_proxy": round(float(unmatched_return.sum()), 10),
        "sizing_effect": round(float(sizing_effect.sum()), 10),
        "deployment_effect": round(float(deployment_effect.sum()), 10),
        "total_effect": round(float(total_effect.sum()), 10),
    }
