"""Build Phoenix crisis/recovery PIT evidence artifacts.

Research-only. This script uses the FR-068 PIT large-cap membership family,
the local Sharadar SEP adjusted-close cache, and the SPY price matrix. It does
not import or alter execution, allocation, broker, risk-control, cron, or live
signal paths.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.alpha_lab_v2.engine import StrategySpec, run_backtest
from research.shadow_tracking.strategies import build_strategy_lookup

SCHEMA_VERSION = "caerus_phoenix_crisis_recovery_evidence_v1"

DEFAULT_WARMUP_START = "2012-06-01"
DEFAULT_START = "2014-01-02"
DEFAULT_END = "2024-12-31"
DEFAULT_COST_BPS = (0.0, 10.0, 25.0, 50.0)
PHOENIX_TOP_N = 10
PHOENIX_MAX_GROSS = 0.80
CRISIS_DRAWDOWN_THRESHOLD = -0.12
CRISIS_MIN_SEPARATION_DAYS = 90


def _round(value: Any, digits: int = 10) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return round(f, digits)


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _norm_ticker(ticker: str) -> str:
    ticker = str(ticker or "").strip().upper()
    if "-" in ticker:
        head, _, tail = ticker.rpartition("-")
        if head and len(tail) <= 2 and tail.isalpha():
            return f"{head}.{tail}"
    return ticker


def _load_large_cap_tickers(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as fh:
        return sorted({_norm_ticker(row["ticker"]) for row in csv.DictReader(fh) if row.get("ticker")})


def _sep_close(cache: Path, ticker: str, end_date: str) -> pd.DataFrame:
    path = cache / f"{ticker}.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path)
    if "date" not in frame.columns or "closeadj" not in frame.columns:
        return pd.DataFrame()
    frame = frame[["date", "closeadj"]].rename(columns={"closeadj": "close"})
    frame["ticker"] = ticker
    frame = frame[frame["date"] <= end_date]
    return frame[["date", "ticker", "close"]]


def _spy_close(price_matrix: Path, end_date: str) -> pd.DataFrame:
    matrix = pd.read_parquet(price_matrix)
    if "SPY" not in matrix.columns:
        return pd.DataFrame()
    spy = matrix[["SPY"]].rename(columns={"SPY": "close"}).reset_index()
    spy.columns = ["date", "close"]
    spy["date"] = pd.to_datetime(spy["date"]).dt.strftime("%Y-%m-%d")
    spy["ticker"] = "SPY"
    return spy[spy["date"] <= end_date][["date", "ticker", "close"]]


def build_price_panel(*, repo: Path, end_date: str) -> tuple[pd.DataFrame, dict[str, Any]]:
    membership_path = repo / "data" / "pit_universe" / "membership_universe_large_cap.csv"
    sep_cache = repo / "data" / "research_cache" / "sharadar_sep"
    price_matrix = repo / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"
    tickers = _load_large_cap_tickers(membership_path)
    frames: list[pd.DataFrame] = []
    missing: list[str] = []
    for ticker in tickers:
        piece = _sep_close(sep_cache, ticker, end_date)
        if piece.empty:
            missing.append(ticker)
        else:
            frames.append(piece)
    spy = _spy_close(price_matrix, end_date)
    if spy.empty:
        raise RuntimeError(f"SPY price source missing from {price_matrix}")
    frames.append(spy)
    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"]).dt.normalize()
    panel["ticker"] = panel["ticker"].astype(str).str.upper().str.strip()
    panel["close"] = pd.to_numeric(panel["close"], errors="coerce")
    panel = panel.dropna(subset=["date", "ticker", "close"])
    panel = panel.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    inputs = {
        "universe_method": "pit_universe",
        "universe_family": "caerus_large_cap",
        "membership_path": str(membership_path),
        "membership_sha256": _sha256(membership_path),
        "membership_ticker_count": len(tickers),
        "price_source": "sharadar_sep_closeadj",
        "sep_cache_path": str(sep_cache),
        "price_matrix_path": str(price_matrix),
        "price_matrix_sha256": _sha256(price_matrix),
        "priced_ticker_count": len(tickers) - len(missing),
        "missing_from_sep_count": len(missing),
        "missing_from_sep_sample": missing[:25],
        "volume_source_available": False,
        "volume_source_reason": "repo_local_sharadar_sep_cache_contains_closeadj_close_only",
    }
    return panel, inputs


def _close_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    return (
        panel.pivot_table(index="date", columns="ticker", values="close", aggfunc="last")
        .sort_index()
        .ffill(limit=3)
    )


def detect_crisis_windows(
    spy_close: pd.Series,
    *,
    start_date: str,
    end_date: str,
    threshold: float = CRISIS_DRAWDOWN_THRESHOLD,
    min_separation_days: int = CRISIS_MIN_SEPARATION_DAYS,
) -> list[dict[str, Any]]:
    spy = spy_close.dropna().sort_index()
    spy = spy[(spy.index >= pd.Timestamp(start_date)) & (spy.index <= pd.Timestamp(end_date))]
    if spy.empty:
        return []
    peak = spy.cummax()
    drawdown = spy / peak - 1.0
    under = drawdown <= float(threshold)
    windows: list[dict[str, Any]] = []
    used_troughs: list[pd.Timestamp] = []
    segment_start: pd.Timestamp | None = None
    for date, is_under in under.items():
        if bool(is_under) and segment_start is None:
            segment_start = pd.Timestamp(date)
        if segment_start is not None and (not bool(is_under) or date == under.index[-1]):
            segment_end = pd.Timestamp(date if bool(is_under) else under.index[under.index.get_loc(date) - 1])
            segment = drawdown.loc[segment_start:segment_end]
            trough = pd.Timestamp(segment.idxmin())
            if all(abs((trough - prior).days) >= int(min_separation_days) for prior in used_troughs):
                prior = spy.loc[:trough]
                crisis_start = pd.Timestamp(prior.idxmax())
                idx = spy.index.get_loc(trough)
                recovery_20_end = pd.Timestamp(spy.index[min(idx + 20, len(spy.index) - 1)])
                recovery_60_end = pd.Timestamp(spy.index[min(idx + 60, len(spy.index) - 1)])
                windows.append(
                    {
                        "window_id": f"spy_drawdown_{len(windows) + 1}",
                        "definition": f"SPY drawdown <= {abs(float(threshold)):.0%}; trough separated by >= {min_separation_days} calendar days",
                        "crisis_start": crisis_start.strftime("%Y-%m-%d"),
                        "trough_date": trough.strftime("%Y-%m-%d"),
                        "recovery_20d_end": recovery_20_end.strftime("%Y-%m-%d"),
                        "recovery_60d_end": recovery_60_end.strftime("%Y-%m-%d"),
                        "spy_drawdown_at_trough": _round(drawdown.loc[trough]),
                        "spy_crisis_return": _round(spy.loc[trough] / spy.loc[crisis_start] - 1.0),
                    }
                )
                used_troughs.append(trough)
            segment_start = None
    return windows


def _phoenix_candidates(matrix: pd.DataFrame, trough: pd.Timestamp, *, top_n: int = PHOENIX_TOP_N) -> tuple[pd.Series, list[dict[str, Any]]]:
    if trough not in matrix.index:
        trough = pd.Timestamp(matrix.index[matrix.index.searchsorted(trough) - 1])
    idx = matrix.index.get_loc(trough)
    if idx < 252:
        return pd.Series(dtype=float), []
    current = matrix.iloc[idx].dropna()
    ret_5d = matrix.iloc[idx].div(matrix.iloc[max(idx - 5, 0)]).sub(1.0)
    ret_20d = matrix.iloc[idx].div(matrix.iloc[max(idx - 20, 0)]).sub(1.0)
    ret_252d = matrix.iloc[idx].div(matrix.iloc[max(idx - 252, 0)]).sub(1.0)
    frame = pd.DataFrame(
        {
            "close": current,
            "return_5d": ret_5d.reindex(current.index),
            "return_20d": ret_20d.reindex(current.index),
            "return_252d": ret_252d.reindex(current.index),
        }
    ).dropna()
    frame = frame[frame.index != "SPY"]
    frame = frame[frame["close"] >= 5.0]
    frame = frame[(frame["return_20d"] < 0.0) & (frame["return_5d"] < 0.0)]
    if frame.empty:
        return pd.Series(dtype=float), []
    frame["phoenix_dislocation_score"] = (
        (-frame["return_20d"]).clip(lower=0.0) * 0.60
        + (-frame["return_5d"]).clip(lower=0.0) * 0.30
        + (-frame["return_252d"]).clip(lower=0.0) * 0.10
    )
    frame = frame.sort_values(["phoenix_dislocation_score", "return_20d"], ascending=[False, True]).head(top_n)
    weight = float(PHOENIX_MAX_GROSS) / len(frame)
    weights = pd.Series(weight, index=pd.Index(frame.index.astype(str), dtype=str), dtype=float).round(10)
    records = []
    for ticker, row in frame.iterrows():
        records.append(
            {
                "ticker": str(ticker),
                "target_weight": _round(weights[str(ticker)]),
                "phoenix_dislocation_score": _round(row["phoenix_dislocation_score"]),
                "return_5d_at_trough": _round(row["return_5d"]),
                "return_20d_at_trough": _round(row["return_20d"]),
                "return_252d_at_trough": _round(row["return_252d"]),
                "close_at_trough": _round(row["close"]),
                "selection_reason": "pit_close_dislocation_recovery_candidate",
            }
        )
    return weights, records


def _weighted_period_return(matrix: pd.DataFrame, weights: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    if weights.empty:
        return None
    dates = matrix.index
    if start not in dates:
        start = pd.Timestamp(dates[dates.searchsorted(start) - 1])
    if end not in dates:
        pos = min(dates.searchsorted(end), len(dates) - 1)
        end = pd.Timestamp(dates[pos])
    start_prices = matrix.loc[start].reindex(weights.index)
    end_prices = matrix.loc[end].reindex(weights.index)
    returns = end_prices.div(start_prices).sub(1.0).replace([math.inf, -math.inf], pd.NA).fillna(0.0)
    return float(weights.mul(returns, fill_value=0.0).sum())


def _event_daily_returns(matrix: pd.DataFrame, weights: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    returns = matrix.reindex(columns=weights.index).pct_change().loc[start:end].fillna(0.0)
    return returns.mul(weights, axis=1).sum(axis=1)


def _max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return _round(drawdown.min())


def _cumulative_return(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    return _round((1.0 + returns.fillna(0.0)).prod() - 1.0)


def _volatility(returns: pd.Series) -> float | None:
    clean = returns.dropna()
    if len(clean) < 2:
        return None
    return _round(clean.std(ddof=1) * math.sqrt(252))


def _strategy_specs() -> dict[str, StrategySpec]:
    lookup = build_strategy_lookup()
    return {
        "caerus_polaris": lookup["caerus_polaris"].spec,
        "caerus_orion": lookup["caerus_orion"].spec,
        "caerus_lyra": lookup["caerus_lyra"].spec,
    }


def _daily_returns(result: dict[str, Any]) -> pd.Series:
    daily = result.get("daily")
    if not isinstance(daily, pd.DataFrame) or daily.empty:
        return pd.Series(dtype=float)
    series = pd.Series(daily["net_return"].values, index=pd.to_datetime(daily["date"]), dtype=float)
    series.name = "net_return"
    return series


def _weights(result: dict[str, Any]) -> pd.DataFrame:
    weights = result.get("weights")
    if not isinstance(weights, pd.DataFrame) or weights.empty:
        return pd.DataFrame()
    out = weights.copy()
    out.index = pd.to_datetime(out.index)
    return out.sort_index()


def _turnover_from_weights(weights: pd.DataFrame) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    turnover = weights.fillna(0.0).diff().abs().sum(axis=1)
    if not turnover.empty:
        turnover.iloc[0] = weights.iloc[0].abs().sum()
    return turnover


def _portfolio_overlap(left: pd.Series, right: pd.Series) -> dict[str, Any]:
    names = left.index.union(right.index)
    l = left.reindex(names, fill_value=0.0)
    r = right.reindex(names, fill_value=0.0)
    overlap = float(pd.concat([l, r], axis=1).min(axis=1).sum())
    active_share = 0.5 * float((l - r).abs().sum())
    return {"holdings_overlap": _round(overlap), "active_share": _round(active_share)}


def _strategy_summary(returns: pd.Series, weights: pd.DataFrame | None = None) -> dict[str, Any]:
    return {
        "cumulative_return": _cumulative_return(returns),
        "volatility": _volatility(returns),
        "max_drawdown": _max_drawdown(returns),
        "avg_turnover": _round(_turnover_from_weights(weights).mean()) if weights is not None and not weights.empty else None,
        "trading_days": int(len(returns.dropna())),
    }


def _cost_sensitivity(event_returns_gross: pd.Series, event_turnover: pd.Series) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cost in DEFAULT_COST_BPS:
        net = event_returns_gross.sub(event_turnover.mul(cost / 10000.0), fill_value=0.0)
        out[f"{int(cost) if float(cost).is_integer() else cost}_bps"] = {
            "cumulative_return": _cumulative_return(net),
            "max_drawdown": _max_drawdown(net),
            "average_turnover": _round(event_turnover[event_turnover > 0].mean()),
            "cost_bps": _round(cost),
        }
    return out


def _readiness_classification(
    *,
    windows: list[dict[str, Any]],
    recovery_20_avg: float | None,
    phoenix_drawdown: float | None,
    max_correlation: float | None,
) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []
    if len(windows) < 3:
        blockers.append("insufficient_crisis_window_count")
    if recovery_20_avg is None or recovery_20_avg <= 0:
        blockers.append("recovery_return_not_positive")
    if phoenix_drawdown is not None and phoenix_drawdown <= -0.35:
        blockers.append("event_drawdown_too_deep")
    if max_correlation is not None and max_correlation >= 0.80:
        warnings.append("high_correlation_to_existing_sleeves")
    if blockers:
        state = "RESEARCH_ONLY_NOT_SHADOW_READY"
    elif warnings:
        state = "RESEARCH_READY_WITH_WARNINGS"
    else:
        state = "RESEARCH_READY_FOR_SHADOW_SPEC"
    return {"classification": state, "blockers": blockers, "warnings": warnings, "reason_codes": blockers + warnings or ["ok"]}


def build_artifact(*, repo: Path, output_date: str, start_date: str, end_date: str) -> dict[str, Any]:
    panel, input_meta = build_price_panel(repo=repo, end_date=end_date)
    matrix = _close_matrix(panel)
    spy = matrix["SPY"].dropna()
    windows = detect_crisis_windows(spy, start_date=start_date, end_date=end_date)
    signals = build_alpha_lab_signal_frame(panel.assign(date=panel["date"].dt.strftime("%Y-%m-%d")))
    specs = _strategy_specs()
    strategy_results = {
        slug: run_backtest(signals, spec, start_date=start_date, end_date=end_date)
        for slug, spec in specs.items()
    }
    strategy_returns = {slug: _daily_returns(result) for slug, result in strategy_results.items()}
    strategy_weights = {slug: _weights(result) for slug, result in strategy_results.items()}

    event_rows: list[dict[str, Any]] = []
    phoenix_event_returns = pd.Series(0.0, index=matrix.loc[start_date:end_date].index, dtype=float)
    phoenix_event_turnover = pd.Series(0.0, index=phoenix_event_returns.index, dtype=float)
    overlap_rows: dict[str, list[float]] = {slug: [] for slug in strategy_results}
    active_share_rows: dict[str, list[float]] = {slug: [] for slug in strategy_results}
    previous_weights = pd.Series(dtype=float)
    for window in windows:
        trough = pd.Timestamp(window["trough_date"])
        weights, candidates = _phoenix_candidates(matrix, trough)
        recovery_20_end = pd.Timestamp(window["recovery_20d_end"])
        recovery_60_end = pd.Timestamp(window["recovery_60d_end"])
        ret20 = _weighted_period_return(matrix, weights, trough, recovery_20_end)
        ret60 = _weighted_period_return(matrix, weights, trough, recovery_60_end)
        gross_daily = _event_daily_returns(matrix, weights, trough, recovery_20_end)
        if not gross_daily.empty:
            phoenix_event_returns.loc[gross_daily.index] = gross_daily
            event_turnover = float(previous_weights.sub(weights, fill_value=0.0).abs().sum())
            phoenix_event_turnover.loc[gross_daily.index[0]] += event_turnover
            phoenix_event_turnover.loc[gross_daily.index[-1]] += float(weights.abs().sum())
            previous_weights = pd.Series(dtype=float)
        for slug, weight_frame in strategy_weights.items():
            if not weight_frame.empty and trough in weight_frame.index:
                overlap = _portfolio_overlap(weights, weight_frame.loc[trough])
                if overlap["holdings_overlap"] is not None:
                    overlap_rows[slug].append(float(overlap["holdings_overlap"]))
                if overlap["active_share"] is not None:
                    active_share_rows[slug].append(float(overlap["active_share"]))
        event_rows.append(
            {
                **window,
                "selected_count": int(len(weights)),
                "selected": candidates,
                "phoenix_recovery_20d_return": _round(ret20),
                "phoenix_recovery_60d_return": _round(ret60),
                "phoenix_20d_max_drawdown": _max_drawdown(gross_daily),
                "event_turnover": _round(float(weights.abs().sum()) * 2.0 if not weights.empty else 0.0),
            }
        )

    matched_index = phoenix_event_returns.index
    correlations: dict[str, Any] = {}
    for slug, series in strategy_returns.items():
        data = pd.concat([phoenix_event_returns.rename("phoenix"), series.reindex(matched_index).rename(slug)], axis=1).dropna()
        correlations[slug] = _round(data["phoenix"].corr(data[slug])) if len(data) > 2 else None
    max_corr = max([abs(float(v)) for v in correlations.values() if v is not None], default=None)
    recovery_20_values = [row["phoenix_recovery_20d_return"] for row in event_rows if row["phoenix_recovery_20d_return"] is not None]
    recovery_20_avg = sum(recovery_20_values) / len(recovery_20_values) if recovery_20_values else None
    phoenix_net_25 = phoenix_event_returns.sub(phoenix_event_turnover.mul(25.0 / 10000.0), fill_value=0.0)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": pd.Timestamp.now("UTC").isoformat(),
        "artifact_date": output_date,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "production_impact": "none",
        "decision_state": "research_evidence_generated",
        "matched_pit_date_range": {
            "warmup_start": DEFAULT_WARMUP_START,
            "start": start_date,
            "end": end_date,
            "holdout_2025_forward": "excluded",
            "matched_return_observations": int(len(matched_index)),
        },
        "inputs": input_meta,
        "crisis_window_definitions": {
            "source": "SPY close from alpha_stack_cache price matrix",
            "threshold": CRISIS_DRAWDOWN_THRESHOLD,
            "min_separation_days": CRISIS_MIN_SEPARATION_DAYS,
            "windows": event_rows,
        },
        "candidate_construction_methodology": {
            "strategy_id": "caerus_phoenix",
            "method": "At each SPY drawdown trough, rank PIT large-cap names by close-only 20d/5d/252d dislocation score using prices available through the trough date; equal-weight top 10 at max 80% gross and evaluate forward 20d/60d recovery.",
            "lookahead_control": "Selection uses only closes through the trough date; forward recovery returns are evaluation only.",
            "limitations": [
                "repo-local Sharadar SEP cache contains closeadj/close only, so volume/liquidity filters are unavailable",
                "this is a research evidence harness and not the live Phoenix signal implementation",
                "SPY crisis trough labels are ex-post evaluation windows, not production triggers",
            ],
        },
        "returns_during_crisis_and_recovery_windows": {
            "average_phoenix_recovery_20d_return": _round(recovery_20_avg),
            "average_phoenix_recovery_60d_return": _round(
                sum(row["phoenix_recovery_60d_return"] for row in event_rows if row["phoenix_recovery_60d_return"] is not None)
                / len([row for row in event_rows if row["phoenix_recovery_60d_return"] is not None])
                if any(row["phoenix_recovery_60d_return"] is not None for row in event_rows)
                else None
            ),
            "event_count": int(len(event_rows)),
        },
        "drawdown_behavior": {
            "phoenix_event_net_25bps_max_drawdown": _max_drawdown(phoenix_net_25),
            "existing_sleeves": {
                slug: {"max_drawdown": _max_drawdown(series.reindex(matched_index).dropna())}
                for slug, series in strategy_returns.items()
            },
        },
        "turnover": {
            "phoenix_event_average_turnover": _round(phoenix_event_turnover[phoenix_event_turnover > 0].mean()),
            "existing_sleeves": {
                slug: {"average_turnover": _round(_turnover_from_weights(strategy_weights[slug]).mean())}
                for slug in strategy_weights
            },
        },
        "liquidity": {
            "available": False,
            "reason_codes": ["volume_source_missing_from_repo_local_pit_price_cache"],
            "note": "Liquidity/capacity cannot be decision-grade until PIT volume or dollar-volume source is added.",
        },
        "cost_sensitivity": _cost_sensitivity(phoenix_event_returns, phoenix_event_turnover),
        "overlap_correlation_vs_existing_sleeves": {
            "daily_return_correlation": correlations,
            "average_holdings_overlap": {
                slug: _round(sum(vals) / len(vals)) if vals else None
                for slug, vals in overlap_rows.items()
            },
            "average_active_share": {
                slug: _round(sum(vals) / len(vals)) if vals else None
                for slug, vals in active_share_rows.items()
            },
        },
        "strategy_summaries": {
            "caerus_phoenix_event_net_25bps": _strategy_summary(phoenix_net_25),
            **{
                slug: _strategy_summary(strategy_returns[slug].reindex(matched_index).dropna(), strategy_weights[slug])
                for slug in strategy_returns
            },
        },
        "failure_modes": [
            "falling_knife_exposure_if_trough_identification_is_early",
            "missing_pit_volume_prevents_liquidity_capacity_validation",
            "close_only_data_cannot_validate_intraday_gap_or_range_risk",
            "crisis_windows_are_sparse_and_may_not_cover_all_future_stress_types",
            "event_returns_are_sensitive_to_transaction_cost_and_entry_timing",
        ],
        "readiness_classification": _readiness_classification(
            windows=event_rows,
            recovery_20_avg=_round(recovery_20_avg),
            phoenix_drawdown=_max_drawdown(phoenix_net_25),
            max_correlation=max_corr,
        ),
        "reason_codes": ["research_only_no_runtime_change", "holdout_2025_forward_excluded"],
    }
    return payload


def write_artifact(repo: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo / "outputs" / "research" / "phoenix_evidence"
    date = payload["artifact_date"]
    json_path = out_dir / f"phoenix_crisis_recovery_{date}.json"
    md_path = out_dir / f"phoenix_crisis_recovery_{date}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    readiness = payload.get("readiness_classification") or {}
    summary = payload.get("returns_during_crisis_and_recovery_windows") or {}
    md = [
        "# Phoenix Crisis/Recovery Evidence",
        "",
        f"Date: `{date}`",
        "",
        "RESEARCH_ONLY / NO_RUNTIME_CHANGE",
        "",
        f"Readiness: `{readiness.get('classification')}`",
        f"Event count: `{summary.get('event_count')}`",
        f"Average Phoenix 20D recovery return: `{summary.get('average_phoenix_recovery_20d_return')}`",
        f"Average Phoenix 60D recovery return: `{summary.get('average_phoenix_recovery_60d_return')}`",
        "",
        "## Blockers / Warnings",
        "",
    ]
    for item in readiness.get("reason_codes") or []:
        md.append(f"- `{item}`")
    md_path.write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--date", default=pd.Timestamp.now("UTC").strftime("%Y-%m-%d"))
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    args = parser.parse_args()
    repo = args.repo_root.resolve()
    payload = build_artifact(repo=repo, output_date=args.date, start_date=args.start_date, end_date=args.end_date)
    json_path, md_path = write_artifact(repo, payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "readiness_classification": (payload.get("readiness_classification") or {}).get("classification"),
        "event_count": (payload.get("returns_during_crisis_and_recovery_windows") or {}).get("event_count"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
