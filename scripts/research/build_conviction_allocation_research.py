"""Build conviction-allocation falsification artifacts.

Research-only. This script reuses the existing alpha-lab signal and backtest
surfaces to test whether momentum rank/score supports more concentrated capital
allocation. It does not import or alter execution, broker, scheduler, allocation,
or production signal paths.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from alpha_stack.research.metrics import (  # noqa: E402
    annualised_vol,
    cagr,
    max_drawdown,
    sharpe_ratio,
    sortino_ratio,
)
from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame  # noqa: E402
from research.alpha_lab_v2.engine import (  # noqa: E402
    StrategySpec,
    prepare_backtest_inputs,
    run_backtest,
)
from research.flow_detection.data import ensure_price_panel, load_universe  # noqa: E402
from research.shadow_tracking.strategies import build_strategy_lookup  # noqa: E402

SCHEMA_VERSION = "caerus_conviction_allocation_research_v1"

DEFAULT_START_DATE = "2014-01-02"
DEFAULT_END_DATE = "2026-06-05"
DEFAULT_PRICE_CACHE_PATH = Path("outputs/research/flow_detection_v1/price_panel.parquet")
DEFAULT_OUTPUT_ROOT = Path("outputs/research/conviction_allocation")
DEFAULT_TRANSACTION_COST_BPS = 10.0
DEFAULT_CURRENT_MAX_POSITION = 0.10
DEFAULT_CURRENT_MIN_GROSS_EXPOSURE = 0.90
DEFAULT_CONVICTION_TOP_N = 10
TRADING_DAYS_PER_YEAR = 252

MODEL_STRATEGIES = ("caerus_polaris", "caerus_orion", "caerus_lyra")
BENCHMARK_SYMBOL = "SPY"

INSPECTED_FILES = [
    "config/research/strategy_registry.json",
    "research/shadow_tracking/strategies.py",
    "research/shadow_tracking/run.py",
    "research/alpha_lab_v1/signals.py",
    "research/alpha_lab_v2/engine.py",
    "core/portfolio_alloc.py",
    "core/execution_target_attainment.py",
    "research/dynamic_strategy_allocation.py",
    "scripts/research/build_orion_lyra_pit_rebaseline.py",
    "scripts/research/build_research_clarity_wave.py",
]


@dataclass(frozen=True)
class PolicySpec:
    policy_id: str
    policy_family: str
    max_position_weight: float
    min_position_weight: float
    top_n: int = DEFAULT_CONVICTION_TOP_N


def _round(value: Any, digits: int = 10) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, digits)


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _strategy_specs() -> dict[str, StrategySpec]:
    lookup = build_strategy_lookup()
    return {slug: lookup[slug].spec for slug in MODEL_STRATEGIES}


def _strategy_spec_payload(spec: StrategySpec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "hypothesis_id": spec.hypothesis_id,
        "description": spec.description,
        "top_n": int(spec.top_n),
        "rebalance_mode": spec.rebalance_mode,
        "transaction_cost_bps": float(spec.transaction_cost_bps),
        "use_rank_decay_exit": bool(spec.use_rank_decay_exit),
        "exit_rank_multiple": float(spec.exit_rank_multiple),
    }


def load_research_panel(
    *,
    repo: Path,
    start_date: str,
    end_date: str,
    price_cache_path: Path,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    universe = load_universe(repo / "data" / "universe.csv")
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + [BENCHMARK_SYMBOL])),
        start_date=start_date,
        end_date=end_date,
        cache_path=repo / price_cache_path,
        allow_download=False,
    )
    panel_meta = dict(panel_meta or {})
    panel_meta.update(
        {
            "data_mode": "local_shadow_price_panel",
            "universe_path": "data/universe.csv",
            "universe_ticker_count": len(universe),
            "price_cache_path": str(price_cache_path),
            "price_cache_sha256": _sha256(repo / price_cache_path),
            "allow_download": False,
            "pit_security_membership": False,
            "point_in_time_note": (
                "Signal math is point-in-time within each ticker's price history, "
                "but this local shadow panel uses the current research universe. "
                "Treat results as a concentration falsification screen, not a "
                "final decision-grade PIT allocator backtest."
            ),
        }
    )
    return panel, panel_meta


def _policy_metrics(returns: pd.Series, weights: pd.DataFrame, cash: pd.Series) -> dict[str, Any]:
    returns = returns.dropna()
    if returns.empty:
        return {
            "observation_count": 0,
            "total_return": None,
            "cagr": None,
            "sharpe": None,
            "sortino": None,
            "max_drawdown": None,
            "volatility": None,
            "turnover": None,
            "average_holdings_count": None,
            "capital_deployed_top_ranked_ideas": None,
            "cash_drag": None,
        }
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    holdings_count = (weights > 1e-12).sum(axis=1) if not weights.empty else pd.Series(dtype=float)
    turnover = weights.fillna(0.0).diff().abs().sum(axis=1) if not weights.empty else pd.Series(dtype=float)
    if not turnover.empty:
        turnover.iloc[0] = weights.iloc[0].abs().sum()
    return {
        "observation_count": int(len(returns)),
        "total_return": _round(float(nav.iloc[-1] - 1.0)),
        "cagr": _round(cagr(nav)),
        "sharpe": _round(sharpe_ratio(returns)),
        "sortino": _round(sortino_ratio(returns)),
        "max_drawdown": _round(max_drawdown(nav)),
        "volatility": _round(annualised_vol(returns)),
        "turnover": _round(float(turnover.mean())) if not turnover.empty else None,
        "average_holdings_count": _round(float(holdings_count.mean())) if not holdings_count.empty else None,
        "capital_deployed_top_ranked_ideas": _round(float(weights.sum(axis=1).mean())) if not weights.empty else None,
        "cash_drag": _round(float(cash.mean())) if not cash.empty else None,
    }


def _cap_weights(weights: pd.Series, max_position_weight: float) -> pd.Series:
    if weights.empty:
        return weights
    cap = float(max_position_weight)
    if cap <= 0:
        return weights * 0.0
    return weights.clip(upper=cap)


def _boost_to_min_gross(weights: pd.Series, *, max_position_weight: float, min_gross_exposure: float) -> pd.Series:
    if weights.empty:
        return weights
    out = weights.copy()
    gross = float(out.sum())
    if gross <= 0 or gross >= min_gross_exposure:
        return out
    headroom = (float(max_position_weight) - out).clip(lower=0.0)
    total_headroom = float(headroom.sum())
    if total_headroom <= 1e-12:
        return out
    needed = min(float(min_gross_exposure) - gross, total_headroom)
    if needed <= 1e-12:
        return out
    return out + headroom / total_headroom * needed


def _combine_current_strategy_weights(strategy_weights: dict[str, pd.DataFrame]) -> pd.DataFrame:
    indexes = [frame.index for frame in strategy_weights.values() if not frame.empty]
    if not indexes:
        return pd.DataFrame()
    dates = indexes[0]
    for index in indexes[1:]:
        dates = dates.intersection(index)
    dates = dates.sort_values()
    rows: list[pd.Series] = []
    budget = 1.0 / len(strategy_weights)
    for dt in dates:
        row = pd.Series(dtype=float)
        for weights in strategy_weights.values():
            sleeve_row = weights.loc[dt] if dt in weights.index else pd.Series(dtype=float)
            row = row.add(pd.to_numeric(sleeve_row, errors="coerce").fillna(0.0) * budget, fill_value=0.0)
        row = row[row > 1e-12]
        row = _cap_weights(row, DEFAULT_CURRENT_MAX_POSITION)
        row = _boost_to_min_gross(
            row,
            max_position_weight=DEFAULT_CURRENT_MAX_POSITION,
            min_gross_exposure=DEFAULT_CURRENT_MIN_GROSS_EXPOSURE,
        )
        rows.append(row.rename(dt))
    out = pd.DataFrame(rows).fillna(0.0).sort_index()
    out.columns = [str(col) for col in out.columns]
    return out


def _positive_conviction_scores(candidates: pd.DataFrame) -> pd.Series:
    scores = pd.to_numeric(candidates["momentum_score"], errors="coerce").fillna(0.0)
    if scores.empty:
        return pd.Series(dtype=float)
    shifted = scores - min(float(scores.min()), 0.0)
    if float(shifted.sum()) <= 1e-12 or float(shifted.max() - shifted.min()) <= 1e-12:
        ranks = pd.to_numeric(candidates["momentum_rank"], errors="coerce")
        ranks = ranks.fillna(float(len(candidates)))
        shifted = 1.0 / ranks.clip(lower=1.0)
    else:
        shifted = shifted + 1e-12
    shifted.index = pd.Index(candidates["ticker"].astype(str), dtype=str)
    return shifted.astype(float)


def allocate_score_weighted(
    candidates: pd.DataFrame,
    *,
    max_position_weight: float,
    min_position_weight: float,
    top_n: int,
) -> pd.Series:
    eligible = candidates[candidates["signal_ready"]].copy()
    eligible = eligible.sort_values(["momentum_score", "ticker"], ascending=[False, True]).head(top_n)
    if eligible.empty:
        return pd.Series(dtype=float)

    while True:
        scores = _positive_conviction_scores(eligible)
        if scores.empty or float(scores.sum()) <= 1e-12:
            return pd.Series(dtype=float)
        weights = scores / float(scores.sum())

        capped = pd.Series(0.0, index=weights.index, dtype=float)
        remaining_scores = scores.copy()
        remaining_capital = 1.0
        for _ in range(len(weights) + 1):
            if remaining_scores.empty or remaining_capital <= 1e-12:
                break
            proposal = remaining_scores / float(remaining_scores.sum()) * remaining_capital
            over_cap = proposal[proposal > max_position_weight]
            if over_cap.empty:
                capped.loc[proposal.index] = proposal
                break
            capped.loc[over_cap.index] = float(max_position_weight)
            remaining_capital -= float(max_position_weight) * len(over_cap)
            remaining_scores = remaining_scores.drop(over_cap.index)
        capped = capped[capped > 1e-12]
        if min_position_weight <= 0 or capped.empty:
            return capped.sort_values(ascending=False)
        keep = capped[capped >= min_position_weight].index
        if len(keep) == len(eligible):
            return capped.sort_values(ascending=False)
        eligible = eligible[eligible["ticker"].astype(str).isin(set(keep))].copy()
        if eligible.empty:
            return pd.Series(dtype=float)
        if float(max_position_weight) * len(eligible) < min(1.0, float(min_position_weight)):
            return pd.Series(dtype=float)


def _backtest_weight_history(
    weights: pd.DataFrame,
    returns_matrix: pd.DataFrame,
    *,
    transaction_cost_bps: float,
) -> tuple[pd.Series, pd.Series]:
    if weights.empty:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    dates = weights.index.intersection(returns_matrix.index).sort_values()
    aligned_weights = weights.reindex(dates).fillna(0.0).copy()
    aligned_weights.columns = [str(col) for col in aligned_weights.columns]
    aligned_weights = aligned_weights.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    aligned_returns = returns_matrix.reindex(index=dates, columns=aligned_weights.columns).fillna(0.0)
    gross_returns = (aligned_weights * aligned_returns).sum(axis=1)
    turnover = aligned_weights.diff().abs().sum(axis=1)
    if not turnover.empty:
        turnover.iloc[0] = aligned_weights.iloc[0].abs().sum()
    net_returns = gross_returns - turnover * (transaction_cost_bps / 10000.0)
    cash = (1.0 - aligned_weights.sum(axis=1)).clip(lower=0.0)
    net_returns.name = "net_return"
    cash.name = "cash_weight"
    return net_returns, cash


def _run_conviction_policy(
    frame: pd.DataFrame,
    trading_dates: list[pd.Timestamp],
    spec: PolicySpec,
    daily_frames: dict[pd.Timestamp, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    rows: list[pd.Series] = []
    for dt in trading_dates:
        daily = daily_frames.get(dt, pd.DataFrame()) if daily_frames is not None else frame[frame["date"] == dt].copy()
        weights = allocate_score_weighted(
            daily,
            max_position_weight=spec.max_position_weight,
            min_position_weight=spec.min_position_weight,
            top_n=spec.top_n,
        )
        rows.append(weights.rename(dt))
    out = pd.DataFrame(rows).fillna(0.0).sort_index()
    out.columns = [str(col) for col in out.columns]
    return out


def _rank_bucket_for_rank(rank: float) -> str:
    if rank <= 1:
        return "top_1"
    if rank <= 3:
        return "top_3"
    if rank <= 5:
        return "top_5"
    if rank <= 10:
        return "top_10"
    return "residual_after_10"


def rank_bucket_forward_returns(
    frame: pd.DataFrame,
    returns_matrix: pd.DataFrame,
    *,
    horizons: tuple[int, ...] = (1, 5, 20),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    price_dates = returns_matrix.index.sort_values()
    close_matrix = frame.pivot(index="date", columns="ticker", values="close").sort_index()
    for horizon in horizons:
        if horizon == 1:
            forward = returns_matrix
        else:
            forward = close_matrix.shift(-horizon) / close_matrix - 1.0
        daily_bucket_returns: dict[str, list[float]] = {
            "top_1": [],
            "top_3": [],
            "top_5": [],
            "top_10": [],
            "residual_after_10": [],
        }
        for dt in price_dates:
            if dt not in forward.index:
                continue
            daily = frame[(frame["date"] == dt) & frame["signal_ready"]].copy()
            if daily.empty:
                continue
            returns = pd.to_numeric(forward.loc[dt], errors="coerce")
            daily["rank_bucket"] = daily["momentum_rank"].astype(float).map(_rank_bucket_for_rank)
            for bucket, group in daily.groupby("rank_bucket"):
                values = returns.reindex(group["ticker"].astype(str)).dropna()
                if values.empty:
                    continue
                daily_bucket_returns[bucket].append(float(values.mean()))
        for bucket, values in daily_bucket_returns.items():
            series = pd.Series(values, dtype=float)
            rows.append(
                {
                    "horizon_days": horizon,
                    "rank_bucket": bucket,
                    "observation_count": int(len(series)),
                    "mean_forward_return": _round(series.mean()) if not series.empty else None,
                    "median_forward_return": _round(series.median()) if not series.empty else None,
                    "hit_rate": _round(float((series > 0).mean())) if not series.empty else None,
                    "annualized_mean_return_proxy": _round(float(series.mean()) * (TRADING_DAYS_PER_YEAR / horizon)) if not series.empty else None,
                }
            )
    return rows


def _selected_position_records(
    weights: pd.DataFrame,
    returns_matrix: pd.DataFrame,
    rank_frame: pd.DataFrame,
    *,
    policy_id: str,
) -> pd.DataFrame:
    if weights.empty:
        return pd.DataFrame()
    dates = weights.index.intersection(returns_matrix.index).sort_values()
    aligned_weights = weights.reindex(dates).fillna(0.0).copy()
    aligned_weights.columns = [str(col) for col in aligned_weights.columns]
    selected = aligned_weights.where(aligned_weights > 1e-12).stack().rename("weight").reset_index()
    if selected.empty:
        return pd.DataFrame()
    selected.columns = ["date", "ticker", "weight"]
    aligned_returns = returns_matrix.reindex(index=dates, columns=aligned_weights.columns)
    return_long = aligned_returns.stack().rename("next_day_return").reset_index()
    return_long.columns = ["date", "ticker", "next_day_return"]
    ranks = rank_frame[["date", "ticker", "momentum_score", "momentum_rank"]].copy()
    ranks["ticker"] = ranks["ticker"].astype(str)
    out = selected.merge(return_long, on=["date", "ticker"], how="left")
    out = out.merge(ranks, on=["date", "ticker"], how="left")
    out.insert(0, "policy_id", policy_id)
    return out


def _correlation_rows(records_by_policy: list[pd.DataFrame]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for records in records_by_policy:
        if records.empty:
            continue
        policy_id = str(records["policy_id"].iloc[0])
        clean = records.dropna(subset=["weight", "next_day_return"]).copy()
        if len(clean) < 5:
            pearson = None
            spearman = None
        else:
            pearson = clean["weight"].corr(clean["next_day_return"], method="pearson")
            spearman = clean["weight"].corr(clean["next_day_return"], method="spearman")
        rows.append(
            {
                "policy_id": policy_id,
                "observation_count": int(len(clean)),
                "weight_return_pearson": _round(pearson),
                "weight_return_spearman": _round(spearman),
                "average_weight": _round(clean["weight"].mean()) if not clean.empty else None,
                "average_next_day_return": _round(clean["next_day_return"].mean()) if not clean.empty else None,
            }
        )
    return rows


def _small_position_rows(policy_weights: dict[str, pd.DataFrame], policy_cash: dict[str, pd.Series]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for policy_id, weights in sorted(policy_weights.items()):
        if weights.empty:
            continue
        cash = policy_cash.get(policy_id, pd.Series(dtype=float))
        for threshold in (0.01, 0.02, 0.03):
            below = weights.where((weights > 1e-12) & (weights < threshold), 0.0)
            counts = ((weights > 1e-12) & (weights < threshold)).sum(axis=1)
            rows.append(
                {
                    "policy_id": policy_id,
                    "threshold": threshold,
                    "average_capital_below_threshold": _round(float(below.sum(axis=1).mean())),
                    "max_capital_below_threshold": _round(float(below.sum(axis=1).max())),
                    "average_positions_below_threshold": _round(float(counts.mean())),
                    "max_positions_below_threshold": int(counts.max()) if not counts.empty else 0,
                    "average_cash_weight": _round(float(cash.mean())) if not cash.empty else None,
                }
            )
    return rows


def _live_residual_position_rows(repo: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    source_paths: list[str] = []
    for path in sorted((repo / "outputs" / "perf").glob("holdings_mtm_*.csv")):
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if "market_value" not in frame.columns or "ticker" not in frame.columns:
            continue
        total = float(pd.to_numeric(frame["market_value"], errors="coerce").abs().sum())
        if total <= 0:
            continue
        date = str(frame.get("date", pd.Series([path.stem[-10:]])).iloc[0])[:10]
        source_paths.append(str(path.relative_to(repo)))
        for _, row in frame.iterrows():
            market_value = float(row.get("market_value") or 0.0)
            weight = abs(market_value) / total
            rows.append(
                {
                    "source": str(path.relative_to(repo)),
                    "date": date,
                    "ticker": str(row.get("ticker") or "").upper(),
                    "market_value": _round(market_value),
                    "weight_of_reported_holdings": _round(weight),
                    "below_1pct": weight < 0.01,
                    "below_2pct": weight < 0.02,
                    "below_3pct": weight < 0.03,
                }
            )
    summary_rows = pd.DataFrame(rows)
    summary: dict[str, Any] = {
        "source_type": "outputs_perf_holdings_mtm_csv",
        "source_files": source_paths,
        "position_rows": int(len(rows)),
        "basis": "weight_of_reported_holdings_market_value_sum_not_full_broker_equity",
        "reason_codes": ["ok"] if rows else ["live_or_paper_holdings_mtm_missing"],
    }
    if not summary_rows.empty:
        for threshold in (0.01, 0.02, 0.03):
            mask = summary_rows["weight_of_reported_holdings"] < threshold
            summary[f"positions_below_{int(threshold * 100)}pct"] = int(mask.sum())
            summary[f"capital_below_{int(threshold * 100)}pct"] = _round(float(summary_rows.loc[mask, "weight_of_reported_holdings"].sum()))
    return rows, summary


def _policy_specs() -> list[PolicySpec]:
    specs: list[PolicySpec] = []
    for cap in (0.10, 0.20, 0.30, 0.40):
        for floor in (0.00, 0.01, 0.02, 0.03):
            cap_label = int(round(cap * 100))
            floor_label = int(round(floor * 100))
            specs.append(
                PolicySpec(
                    policy_id=f"conviction_score_top10_cap{cap_label}_min{floor_label}",
                    policy_family="conviction_score_weighted",
                    max_position_weight=cap,
                    min_position_weight=floor,
                )
            )
    return specs


def build_artifact(
    *,
    repo: Path,
    artifact_date: str,
    start_date: str,
    end_date: str,
    price_cache_path: Path,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    panel, input_meta = load_research_panel(
        repo=repo,
        start_date=start_date,
        end_date=end_date,
        price_cache_path=price_cache_path,
    )
    signals = build_alpha_lab_signal_frame(panel)
    frame, returns_matrix, trading_dates = prepare_backtest_inputs(signals, start_date=start_date, end_date=end_date)
    daily_frames = {dt: group.copy() for dt, group in frame.groupby("date", sort=False)}
    specs = _strategy_specs()
    strategy_results = {
        slug: run_backtest(signals, spec, start_date=start_date, end_date=end_date)
        for slug, spec in specs.items()
    }
    strategy_weights = {
        slug: result.get("weights").copy() if isinstance(result.get("weights"), pd.DataFrame) else pd.DataFrame()
        for slug, result in strategy_results.items()
    }
    for slug, weights in strategy_weights.items():
        if not weights.empty:
            weights.index = pd.to_datetime(weights.index)
            strategy_weights[slug] = weights.sort_index()

    policy_weights: dict[str, pd.DataFrame] = {}
    policy_returns: dict[str, pd.Series] = {}
    policy_cash: dict[str, pd.Series] = {}
    position_record_frames: list[pd.DataFrame] = []

    current_weights = _combine_current_strategy_weights(strategy_weights)
    current_returns, current_cash = _backtest_weight_history(
        current_weights,
        returns_matrix,
        transaction_cost_bps=DEFAULT_TRANSACTION_COST_BPS,
    )
    policy_weights["current_target_attainment_proxy"] = current_weights
    policy_returns["current_target_attainment_proxy"] = current_returns
    policy_cash["current_target_attainment_proxy"] = current_cash
    position_record_frames.append(
        _selected_position_records(
            current_weights,
            returns_matrix,
            frame,
            policy_id="current_target_attainment_proxy",
        )
    )

    for spec in _policy_specs():
        weights = _run_conviction_policy(frame, trading_dates, spec, daily_frames=daily_frames)
        returns, cash = _backtest_weight_history(
            weights,
            returns_matrix,
            transaction_cost_bps=DEFAULT_TRANSACTION_COST_BPS,
        )
        policy_weights[spec.policy_id] = weights
        policy_returns[spec.policy_id] = returns
        policy_cash[spec.policy_id] = cash
        position_record_frames.append(
            _selected_position_records(weights, returns_matrix, frame, policy_id=spec.policy_id)
        )

    policy_metric_rows = []
    for policy_id, returns in sorted(policy_returns.items()):
        weights = policy_weights[policy_id]
        row = {"policy_id": policy_id, **_policy_metrics(returns, weights, policy_cash[policy_id])}
        if policy_id == "current_target_attainment_proxy":
            row.update(
                {
                    "policy_family": "current_target_attainment_proxy",
                    "max_position_weight": DEFAULT_CURRENT_MAX_POSITION,
                    "min_position_weight": 0.0,
                }
            )
        else:
            match = next(spec for spec in _policy_specs() if spec.policy_id == policy_id)
            row.update(
                {
                    "policy_family": match.policy_family,
                    "max_position_weight": match.max_position_weight,
                    "min_position_weight": match.min_position_weight,
                }
            )
        policy_metric_rows.append(row)

    rank_bucket_rows = rank_bucket_forward_returns(frame, returns_matrix)
    small_position_rows = _small_position_rows(policy_weights, policy_cash)
    correlation_rows = _correlation_rows(position_record_frames)
    live_rows, live_summary = _live_residual_position_rows(repo)

    strategy_summaries = {}
    for slug, result in strategy_results.items():
        returns = pd.Series(result["daily"]["net_return"].values, index=pd.to_datetime(result["daily"]["date"])) if not result.get("daily", pd.DataFrame()).empty else pd.Series(dtype=float)
        weights = strategy_weights[slug]
        cash = pd.Series(1.0 - weights.sum(axis=1), index=weights.index) if not weights.empty else pd.Series(dtype=float)
        strategy_summaries[slug] = {
            "spec": _strategy_spec_payload(specs[slug]),
            **_policy_metrics(returns, weights, cash),
        }

    best_conviction = max(
        [row for row in policy_metric_rows if row["policy_id"] != "current_target_attainment_proxy"],
        key=lambda row: (
            -999.0 if row.get("sharpe") is None else float(row["sharpe"]),
            -999.0 if row.get("total_return") is None else float(row["total_return"]),
        ),
    )
    current_row = next(row for row in policy_metric_rows if row["policy_id"] == "current_target_attainment_proxy")

    stock_alpha_evidence = _classify_stock_selection(rank_bucket_rows)
    sizing_alpha_evidence = _classify_position_sizing(correlation_rows)
    recommendation = _recommendation(current_row, best_conviction, stock_alpha_evidence, sizing_alpha_evidence)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "artifact_date": artifact_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "none",
        "runtime_behavior_changed": False,
        "start_date": start_date,
        "end_date": end_date,
        "inputs": input_meta,
        "files_inspected": INSPECTED_FILES,
        "methodology": {
            "candidate_generation": "research.alpha_lab_v1.signals.build_alpha_lab_signal_frame over the local shadow price panel",
            "score": "momentum_score = 0.5*r12_1 + 0.3*r6_1 + 0.2*r3",
            "rank": "daily cross-sectional descending momentum_score rank",
            "current_policy_proxy": (
                "Equal budget across Polaris, Orion, and Lyra shadow target weights, "
                "ticker-level cap at 10%, then min gross exposure boost to 90% where headroom exists."
            ),
            "conviction_policy": (
                "Daily score-weighted allocation among top-10 ranked names, with max position caps "
                "of 10/20/30/40% and minimum new-position floors of 0/1/2/3%."
            ),
            "transaction_cost_bps": DEFAULT_TRANSACTION_COST_BPS,
            "cash_return_assumption": 0.0,
            "lookahead_controls": [
                "Signal features use lagged prices only through the decision date.",
                "Forward returns are used only after ranks/weights are fixed for each decision date.",
                "No production execution, broker, scheduler, or allocator state is read or mutated.",
            ],
            "known_limitations": [
                "Default local shadow panel uses current data/universe.csv, so survivorship bias remains; use this as falsification evidence, not final PIT allocator evidence.",
                "Current target-attainment proxy approximates allocator intent and caps; it is not a replay of every historical broker fill or residual position.",
                "Live/paper residual scan is limited to available holdings_mtm CSVs and uses market value divided by reported holdings market value, not full broker equity when equity is absent.",
                "Conviction sizing uses momentum_score only; it does not include liquidity, borrow, tax lots, slippage beyond the 10 bps turnover cost, or sector/factor constraints.",
            ],
        },
        "strategy_specs": {slug: _strategy_spec_payload(spec) for slug, spec in specs.items()},
        "strategy_current_summaries": strategy_summaries,
        "policy_metrics": policy_metric_rows,
        "rank_bucket_forward_returns": rank_bucket_rows,
        "position_size_correlations": correlation_rows,
        "small_position_exposure": small_position_rows,
        "live_or_paper_residual_position_scan": live_summary,
        "classification": {
            "stock_selection_alpha": stock_alpha_evidence,
            "position_sizing_alpha": sizing_alpha_evidence,
            "overall": _overall_classification(stock_alpha_evidence, sizing_alpha_evidence),
        },
        "executive_summary": _executive_summary(
            current_row=current_row,
            best_conviction=best_conviction,
            stock_alpha=stock_alpha_evidence,
            sizing_alpha=sizing_alpha_evidence,
            recommendation=recommendation,
        ),
        "recommendation": recommendation,
        "next_recommended_fr": {
            "fr": "FR-069 child: conviction allocator PIT replay and paper-residual bridge",
            "reason": (
                "Concentration should not move toward production until this screen is rerun on the "
                "FR-069 PIT sleeve lab contract and reconciled against actual broker residuals."
            ),
        },
        "reason_codes": ["research_only_no_runtime_change", "local_shadow_panel_not_final_pit_evidence"],
    }
    tables = {
        "policy_metrics.csv": policy_metric_rows,
        "rank_bucket_forward_returns.csv": rank_bucket_rows,
        "position_size_correlation.csv": correlation_rows,
        "small_position_exposure.csv": small_position_rows,
        "live_residual_positions.csv": live_rows,
    }
    return payload, tables


def _classify_stock_selection(rank_rows: list[dict[str, Any]]) -> dict[str, Any]:
    one_day = {
        row["rank_bucket"]: row
        for row in rank_rows
        if int(row.get("horizon_days") or 0) == 1
    }
    top = one_day.get("top_1", {}).get("mean_forward_return")
    residual = one_day.get("residual_after_10", {}).get("mean_forward_return")
    spread = top - residual if top is not None and residual is not None else None
    if spread is None:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif spread > 0:
        verdict = "EVIDENCE_OF_STOCK_SELECTION_ALPHA"
    else:
        verdict = "NO_STOCK_SELECTION_ALPHA_DETECTED"
    return {
        "verdict": verdict,
        "top1_minus_residual_mean_1d": _round(spread),
        "basis": "top_1 mean 1-day forward return minus residual_after_10 mean 1-day forward return",
    }


def _classify_position_sizing(correlation_rows: list[dict[str, Any]]) -> dict[str, Any]:
    current = next((row for row in correlation_rows if row["policy_id"] == "current_target_attainment_proxy"), None)
    corr = None if current is None else current.get("weight_return_spearman")
    if corr is None:
        verdict = "INSUFFICIENT_EVIDENCE"
    elif corr > 0.02:
        verdict = "EVIDENCE_OF_POSITION_SIZING_ALPHA"
    elif corr < -0.02:
        verdict = "NEGATIVE_POSITION_SIZING_ALPHA"
    else:
        verdict = "NO_POSITION_SIZING_ALPHA_DETECTED"
    return {
        "verdict": verdict,
        "current_policy_weight_return_spearman": corr,
        "basis": "Spearman correlation between realized position weight and next-day return",
    }


def _overall_classification(stock_alpha: dict[str, Any], sizing_alpha: dict[str, Any]) -> str:
    stock = stock_alpha.get("verdict") == "EVIDENCE_OF_STOCK_SELECTION_ALPHA"
    sizing = sizing_alpha.get("verdict") == "EVIDENCE_OF_POSITION_SIZING_ALPHA"
    if stock and sizing:
        return "BOTH"
    if stock:
        return "STOCK_SELECTION_ALPHA_ONLY"
    if sizing:
        return "POSITION_SIZING_ALPHA_ONLY"
    if "INSUFFICIENT" in str(stock_alpha.get("verdict")) or "INSUFFICIENT" in str(sizing_alpha.get("verdict")):
        return "INSUFFICIENT_EVIDENCE"
    return "NEITHER"


def _recommendation(
    current_row: dict[str, Any],
    best_conviction: dict[str, Any],
    stock_alpha: dict[str, Any],
    sizing_alpha: dict[str, Any],
) -> str:
    current_sharpe = current_row.get("sharpe")
    best_sharpe = best_conviction.get("sharpe")
    current_return = current_row.get("total_return")
    best_return = best_conviction.get("total_return")
    sharpe_edge = (
        best_sharpe - current_sharpe
        if best_sharpe is not None and current_sharpe is not None
        else None
    )
    return_edge = (
        best_return - current_return
        if best_return is not None and current_return is not None
        else None
    )
    if stock_alpha.get("verdict") != "EVIDENCE_OF_STOCK_SELECTION_ALPHA":
        return "insufficient evidence"
    if sharpe_edge is not None and return_edge is not None and sharpe_edge > 0.10 and return_edge > 0:
        if sizing_alpha.get("verdict") == "EVIDENCE_OF_POSITION_SIZING_ALPHA":
            return "pursue conviction allocator"
        return "pursue hybrid allocator"
    return "keep current allocator"


def _executive_summary(
    *,
    current_row: dict[str, Any],
    best_conviction: dict[str, Any],
    stock_alpha: dict[str, Any],
    sizing_alpha: dict[str, Any],
    recommendation: str,
) -> dict[str, Any]:
    return {
        "concentration_appears_promising": recommendation in {"pursue conviction allocator", "pursue hybrid allocator"},
        "recommendation": recommendation,
        "current_policy": {
            "policy_id": current_row.get("policy_id"),
            "total_return": current_row.get("total_return"),
            "cagr": current_row.get("cagr"),
            "sharpe": current_row.get("sharpe"),
            "sortino": current_row.get("sortino"),
            "max_drawdown": current_row.get("max_drawdown"),
            "volatility": current_row.get("volatility"),
            "turnover": current_row.get("turnover"),
            "average_holdings_count": current_row.get("average_holdings_count"),
            "cash_drag": current_row.get("cash_drag"),
        },
        "best_conviction_policy": {
            "policy_id": best_conviction.get("policy_id"),
            "max_position_weight": best_conviction.get("max_position_weight"),
            "min_position_weight": best_conviction.get("min_position_weight"),
            "total_return": best_conviction.get("total_return"),
            "cagr": best_conviction.get("cagr"),
            "sharpe": best_conviction.get("sharpe"),
            "sortino": best_conviction.get("sortino"),
            "max_drawdown": best_conviction.get("max_drawdown"),
            "volatility": best_conviction.get("volatility"),
            "turnover": best_conviction.get("turnover"),
            "average_holdings_count": best_conviction.get("average_holdings_count"),
            "cash_drag": best_conviction.get("cash_drag"),
        },
        "stock_selection_alpha": stock_alpha,
        "position_sizing_alpha": sizing_alpha,
    }


def render_markdown(payload: dict[str, Any]) -> str:
    summary = payload["executive_summary"]
    current = summary["current_policy"]
    best = summary["best_conviction_policy"]
    classification = payload["classification"]
    lines = [
        "# Conviction Allocation Research",
        "",
        "RESEARCH_ONLY / NON_EXECUTIONAL",
        "",
        "## Executive Summary",
        "",
        f"- Recommendation: `{payload['recommendation']}`",
        f"- Concentration promising: `{summary['concentration_appears_promising']}`",
        f"- Classification: `{classification['overall']}`",
        f"- Stock-selection alpha: `{classification['stock_selection_alpha']['verdict']}`",
        f"- Position-sizing alpha: `{classification['position_sizing_alpha']['verdict']}`",
        "",
        "## Current vs Best Conviction Policy",
        "",
        "| Metric | Current target-attainment proxy | Best conviction policy |",
        "|---|---:|---:|",
        f"| Policy | `{current['policy_id']}` | `{best['policy_id']}` |",
        f"| Total return | {current['total_return']} | {best['total_return']} |",
        f"| CAGR | {current['cagr']} | {best['cagr']} |",
        f"| Sharpe | {current['sharpe']} | {best['sharpe']} |",
        f"| Sortino | {current['sortino']} | {best['sortino']} |",
        f"| Max drawdown | {current['max_drawdown']} | {best['max_drawdown']} |",
        f"| Volatility | {current['volatility']} | {best['volatility']} |",
        f"| Turnover | {current['turnover']} | {best['turnover']} |",
        f"| Avg holdings | {current['average_holdings_count']} | {best['average_holdings_count']} |",
        f"| Cash drag | {current['cash_drag']} | {best['cash_drag']} |",
        "",
        "## Methodology",
        "",
    ]
    for key, value in payload["methodology"].items():
        if isinstance(value, list):
            lines.append(f"- {key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Files Inspected",
            "",
            *[f"- `{path}`" for path in payload["files_inspected"]],
            "",
            "## Next Recommended FR",
            "",
            f"- `{payload['next_recommended_fr']['fr']}`",
            f"- {payload['next_recommended_fr']['reason']}",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(repo: Path, payload: dict[str, Any], tables: dict[str, list[dict[str, Any]]], output_root: Path) -> dict[str, Any]:
    out_dir = repo / output_root / payload["artifact_date"]
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "conviction_allocation_research.json"
    md_path = out_dir / "conviction_allocation_research.md"
    _write_json(json_path, payload)
    md_path.write_text(render_markdown(payload).rstrip() + "\n", encoding="utf-8")
    written = [json_path, md_path]
    for name, rows in sorted(tables.items()):
        path = out_dir / name
        _write_csv(path, rows)
        written.append(path)
    manifest = {
        "schema_version": "caerus_conviction_allocation_manifest_v1",
        "artifact_date": payload["artifact_date"],
        "artifact_count": len(written),
        "artifacts": [
            {
                "path": str(path.relative_to(repo)),
                "sha256": _sha256(path),
            }
            for path in sorted(written)
        ],
    }
    manifest_path = out_dir / "manifest.json"
    _write_json(manifest_path, manifest)
    return {
        "output_dir": str(out_dir),
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "manifest_path": str(manifest_path),
        "artifact_count": len(written) + 1,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--artifact-date", default="2026-06-22")
    parser.add_argument("--start-date", default=DEFAULT_START_DATE)
    parser.add_argument("--end-date", default=DEFAULT_END_DATE)
    parser.add_argument("--price-cache-path", type=Path, default=DEFAULT_PRICE_CACHE_PATH)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo = args.repo_root.resolve()
    payload, tables = build_artifact(
        repo=repo,
        artifact_date=args.artifact_date,
        start_date=args.start_date,
        end_date=args.end_date,
        price_cache_path=args.price_cache_path,
        output_root=args.output_root,
    )
    written = write_artifacts(repo, payload, tables, args.output_root)
    print(
        json.dumps(
            {
                "status": "OK",
                "recommendation": payload["recommendation"],
                "classification": payload["classification"]["overall"],
                **written,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
