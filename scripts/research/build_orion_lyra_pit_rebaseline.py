"""Build FR-068 Orion/Lyra matched PIT rebaseline artifacts.

Research-only. This script reuses the alpha-lab signal/backtest engine and the
FR-068 PIT large-cap universe / Sharadar SEP cache. It does not import or alter
execution, allocation, broker, risk-control, or production signal paths.
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
from research.alpha_lab_v2.engine import StrategySpec, prepare_backtest_inputs, run_backtest
from research.regime_attribution import REGIME_LABELS, _classify_regimes
from research.shadow_tracking.strategies import build_strategy_lookup

SCHEMA_VERSION = "caerus_orion_lyra_pit_rebaseline_v1"

DEFAULT_WARMUP_START = "2012-06-01"
DEFAULT_START = "2014-01-02"
DEFAULT_END = "2024-12-31"
DEFAULT_COST_BPS = (0.0, 10.0, 25.0, 50.0)


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
    panel["date"] = pd.to_datetime(panel["date"]).dt.strftime("%Y-%m-%d")
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
    }
    return panel, inputs


def _strategy_specs() -> dict[str, StrategySpec]:
    lookup = build_strategy_lookup()
    return {
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


def _max_drawdown(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    nav = (1.0 + returns.fillna(0.0)).cumprod()
    drawdown = nav / nav.cummax() - 1.0
    return _round(drawdown.min())


def _volatility(returns: pd.Series) -> float | None:
    clean = returns.dropna()
    if len(clean) < 2:
        return None
    return _round(clean.std(ddof=1) * math.sqrt(252))


def _cumulative_return(returns: pd.Series) -> float | None:
    if returns.empty:
        return None
    return _round((1.0 + returns.fillna(0.0)).prod() - 1.0)


def _paired_stats(left: pd.Series, right: pd.Series) -> dict[str, Any]:
    data = pd.concat([left.rename("lyra"), right.rename("orion")], axis=1).dropna()
    if data.empty:
        return {"observation_count": 0, "reason_codes": ["matched_returns_missing"]}
    diff = data["lyra"] - data["orion"]
    sd = float(diff.std(ddof=1)) if len(diff) > 1 else 0.0
    mean = float(diff.mean())
    se = sd / math.sqrt(len(diff)) if sd > 0 else None
    t_stat = mean / se if se else None
    corr = float(data["lyra"].corr(data["orion"])) if len(data) > 1 else None
    lyra_total = _cumulative_return(data["lyra"])
    orion_total = _cumulative_return(data["orion"])
    return {
        "observation_count": int(len(data)),
        "mean_daily_diff_lyra_minus_orion": _round(mean),
        "std_daily_diff": _round(sd),
        "t_stat": _round(t_stat),
        "return_correlation": _round(corr),
        "lyra_total_return": lyra_total,
        "orion_total_return": orion_total,
        "total_return_diff_lyra_minus_orion": _round(
            lyra_total - orion_total if lyra_total is not None and orion_total is not None else None
        ),
        "lyra_win_days": int((diff > 0).sum()),
        "orion_win_days": int((diff < 0).sum()),
        "tie_days": int((diff == 0).sum()),
        "reason_codes": ["ok"],
    }


def _portfolio_overlap(left: pd.Series, right: pd.Series) -> dict[str, Any]:
    names = left.index.union(right.index)
    l = left.reindex(names, fill_value=0.0)
    r = right.reindex(names, fill_value=0.0)
    overlap = float(pd.concat([l, r], axis=1).min(axis=1).sum())
    active_share = 0.5 * float((l - r).abs().sum())
    return {
        "holdings_overlap": _round(overlap),
        "active_share": _round(active_share),
        "shared_names": sorted([name for name in names if l[name] > 0 and r[name] > 0]),
        "lyra_only_names": sorted([name for name in names if l[name] > 0 and r[name] <= 0]),
        "orion_only_names": sorted([name for name in names if r[name] > 0 and l[name] <= 0]),
    }


def _overlap_series(lyra_weights: pd.DataFrame, orion_weights: pd.DataFrame) -> dict[str, Any]:
    dates = lyra_weights.index.intersection(orion_weights.index).sort_values()
    rows = []
    for dt in dates:
        row = _portfolio_overlap(lyra_weights.loc[dt], orion_weights.loc[dt])
        row["date"] = dt.strftime("%Y-%m-%d")
        rows.append(row)
    if not rows:
        return {"observation_count": 0, "reason_codes": ["weights_missing"]}
    latest = rows[-1]
    return {
        "observation_count": len(rows),
        "average_holdings_overlap": _round(sum(r["holdings_overlap"] for r in rows if r["holdings_overlap"] is not None) / len(rows)),
        "average_active_share": _round(sum(r["active_share"] for r in rows if r["active_share"] is not None) / len(rows)),
        "latest": latest,
        "sample": rows[-5:],
        "reason_codes": ["ok"],
    }


def _turnover_from_weights(weights: pd.DataFrame) -> pd.Series:
    if weights.empty:
        return pd.Series(dtype=float)
    all_turnover = weights.fillna(0.0).diff().abs().sum(axis=1)
    if not all_turnover.empty:
        all_turnover.iloc[0] = weights.iloc[0].abs().sum()
    return all_turnover


def _strategy_summary(result: dict[str, Any]) -> dict[str, Any]:
    summary = dict(result.get("summary") or {})
    returns = _daily_returns(result)
    weights = _weights(result)
    return {
        "cumulative_return": summary.get("cumulative_return", _cumulative_return(returns)),
        "cagr": summary.get("cagr"),
        "sharpe": summary.get("sharpe"),
        "sortino": summary.get("sortino"),
        "volatility": summary.get("volatility", _volatility(returns)),
        "max_drawdown": summary.get("max_drawdown", _max_drawdown(returns)),
        "avg_turnover": summary.get("avg_turnover"),
        "hit_rate": summary.get("hit_rate"),
        "avg_holding_period_days": summary.get("avg_holding_period_days"),
        "trading_days": int(len(returns)),
        "average_position_count": _round((weights > 0).sum(axis=1).mean()) if not weights.empty else None,
        "average_top3_concentration": _round(
            weights.apply(lambda row: row.sort_values(ascending=False).head(3).sum(), axis=1).mean()
        ) if not weights.empty else None,
        "average_top5_concentration": _round(
            weights.apply(lambda row: row.sort_values(ascending=False).head(5).sum(), axis=1).mean()
        ) if not weights.empty else None,
    }


def _cost_sensitivity(signals: pd.DataFrame, specs: dict[str, StrategySpec], *, start_date: str, end_date: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for cost in DEFAULT_COST_BPS:
        row: dict[str, Any] = {}
        for slug, spec in specs.items():
            run = run_backtest(signals, replace(spec, transaction_cost_bps=cost), start_date=start_date, end_date=end_date)
            row[slug] = _strategy_summary(run)
        lyra = row["caerus_lyra"].get("cumulative_return")
        orion = row["caerus_orion"].get("cumulative_return")
        row["spread_lyra_minus_orion"] = _round(lyra - orion if lyra is not None and orion is not None else None)
        out[f"{int(cost) if cost.is_integer() else cost}_bps"] = row
    return out


def _regime_decomposition(returns: dict[str, pd.Series], spy: pd.Series) -> dict[str, Any]:
    frame = pd.DataFrame({"spy_benchmark": (1.0 + spy.fillna(0.0)).cumprod()})
    for slug, series in returns.items():
        frame[slug] = (1.0 + series.reindex(frame.index).fillna(0.0)).cumprod()
    frame = frame.reset_index().rename(columns={"index": "date"})
    classified = _classify_regimes(frame)
    out: dict[str, Any] = {}
    for regime in REGIME_LABELS:
        chunk = classified[classified["regime"] == regime]
        row: dict[str, Any] = {"observation_count": int(len(chunk))}
        for slug in ("caerus_orion", "caerus_lyra"):
            series = chunk[slug].pct_change().dropna() if slug in chunk else pd.Series(dtype=float)
            row[slug] = {
                "total_return": _cumulative_return(series),
                "average_return": _round(series.mean()) if not series.empty else None,
                "volatility": _volatility(series),
                "max_drawdown": _max_drawdown(series),
                "hit_rate": _round((series > 0).mean()) if not series.empty else None,
            }
        row["spread_lyra_minus_orion"] = _round(
            row["caerus_lyra"]["total_return"] - row["caerus_orion"]["total_return"]
            if row["caerus_lyra"]["total_return"] is not None and row["caerus_orion"]["total_return"] is not None
            else None
        )
        out[regime] = row
    return out


def _daily_return_records(returns: dict[str, pd.Series]) -> list[dict[str, Any]]:
    data = pd.concat({k: v for k, v in returns.items()}, axis=1).dropna()
    records = []
    for dt, row in data.iterrows():
        records.append(
            {
                "date": dt.strftime("%Y-%m-%d"),
                "caerus_orion": _round(row["caerus_orion"]),
                "caerus_lyra": _round(row["caerus_lyra"]),
                "lyra_minus_orion": _round(row["caerus_lyra"] - row["caerus_orion"]),
            }
        )
    return records


def build_artifact(*, repo: Path, output_date: str, start_date: str, end_date: str) -> dict[str, Any]:
    panel, input_meta = build_price_panel(repo=repo, end_date=end_date)
    signals = build_alpha_lab_signal_frame(panel)
    specs = _strategy_specs()
    results = {
        slug: run_backtest(signals, spec, start_date=start_date, end_date=end_date)
        for slug, spec in specs.items()
    }
    returns = {slug: _daily_returns(result) for slug, result in results.items()}
    matched_index = returns["caerus_orion"].index.intersection(returns["caerus_lyra"].index).sort_values()
    returns = {slug: series.reindex(matched_index).dropna() for slug, series in returns.items()}
    weights = {slug: _weights(result).reindex(matched_index).fillna(0.0) for slug, result in results.items()}
    spy_panel = panel[panel["ticker"] == "SPY"].copy()
    spy_panel["date"] = pd.to_datetime(spy_panel["date"])
    spy_returns = spy_panel.set_index("date")["close"].pct_change().reindex(matched_index).dropna()

    reason_codes = ["research_only_no_runtime_change", "holdout_2025_forward_excluded"]
    sector_overlap = {
        "available": False,
        "value": None,
        "reason_codes": ["sector_map_not_available_for_pit_rebaseline"],
    }
    factor_overlap = {
        "available": False,
        "value": None,
        "reason_codes": ["factor_exposure_model_not_available_for_pit_rebaseline"],
    }
    paired = _paired_stats(returns["caerus_lyra"], returns["caerus_orion"])
    if paired.get("t_stat") is not None and abs(float(paired["t_stat"])) >= 2.0:
        statistical_conclusion = "MATERIAL_LEAD_DETECTED"
    else:
        statistical_conclusion = "NO_STATISTICALLY_MEANINGFUL_LEAD"

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
        "strategy_specs": {
            slug: {
                "name": spec.name,
                "hypothesis_id": spec.hypothesis_id,
                "description": spec.description,
                "top_n": spec.top_n,
                "rebalance_mode": spec.rebalance_mode,
                "transaction_cost_bps": spec.transaction_cost_bps,
                "use_rank_decay_exit": spec.use_rank_decay_exit,
                "exit_rank_multiple": spec.exit_rank_multiple,
            }
            for slug, spec in specs.items()
        },
        "strategies": {
            slug: _strategy_summary(results[slug])
            for slug in ("caerus_orion", "caerus_lyra")
        },
        "paired_significance": paired,
        "statistical_conclusion": statistical_conclusion,
        "matched_daily_returns": _daily_return_records(returns),
        "drawdowns": {
            slug: {"max_drawdown": _max_drawdown(series)}
            for slug, series in returns.items()
        },
        "turnover": {
            slug: {
                "average_turnover": _round(_turnover_from_weights(weights[slug]).mean()),
                "total_turnover": _round(_turnover_from_weights(weights[slug]).sum()),
            }
            for slug in ("caerus_orion", "caerus_lyra")
        },
        "holdings_overlap": _overlap_series(weights["caerus_lyra"], weights["caerus_orion"]),
        "active_share": {
            "average": _overlap_series(weights["caerus_lyra"], weights["caerus_orion"]).get("average_active_share"),
            "latest": (_overlap_series(weights["caerus_lyra"], weights["caerus_orion"]).get("latest") or {}).get("active_share"),
        },
        "sector_overlap": sector_overlap,
        "factor_overlap": factor_overlap,
        "cost_sensitivity": _cost_sensitivity(signals, specs, start_date=start_date, end_date=end_date),
        "regime_decomposition": _regime_decomposition(returns, spy_returns),
        "methodology_review": {
            "pit_validity": "PIT large-cap membership and Sharadar SEP adjusted-close cache reused from FR-068 Polaris priced rebaseline.",
            "same_window": True,
            "same_price_source": True,
            "holdout_excluded": True,
            "known_limitations": [
                "sector overlap unavailable because no PIT sector map was found in repo-local inputs",
                "factor overlap unavailable because no PIT factor exposure model was found for the generated PIT holdings",
                "large-cap family uses current scalemarketcap approximation as documented in FR-068 Phase 2.5",
            ],
        },
        "governance_classification": (
            "REDUNDANT_CONTINUE_OBSERVING"
            if statistical_conclusion == "NO_STATISTICALLY_MEANINGFUL_LEAD"
            else "REVIEW_MATERIAL_LEAD"
        ),
        "reason_codes": reason_codes,
    }
    return payload


def write_artifacts(repo: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    out_dir = repo / "outputs" / "research" / "pit_rebaseline"
    date = payload["artifact_date"]
    json_path = out_dir / f"orion_lyra_matched_{date}.json"
    md_path = out_dir / f"orion_lyra_matched_{date}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paired = payload.get("paired_significance") or {}
    strategies = payload.get("strategies") or {}
    md = [
        "# Orion/Lyra PIT Matched Rebaseline",
        "",
        f"Date: `{date}`",
        "",
        "RESEARCH_ONLY / NO_RUNTIME_CHANGE",
        "",
        f"Classification: `{payload.get('governance_classification')}`",
        f"Statistical conclusion: `{payload.get('statistical_conclusion')}`",
        "",
        "| Metric | Orion | Lyra |",
        "|---|---:|---:|",
        f"| Cumulative return | {strategies.get('caerus_orion', {}).get('cumulative_return')} | {strategies.get('caerus_lyra', {}).get('cumulative_return')} |",
        f"| Volatility | {strategies.get('caerus_orion', {}).get('volatility')} | {strategies.get('caerus_lyra', {}).get('volatility')} |",
        f"| Max drawdown | {strategies.get('caerus_orion', {}).get('max_drawdown')} | {strategies.get('caerus_lyra', {}).get('max_drawdown')} |",
        f"| Avg turnover | {strategies.get('caerus_orion', {}).get('avg_turnover')} | {strategies.get('caerus_lyra', {}).get('avg_turnover')} |",
        "",
        "## Paired Test",
        "",
        f"- Observations: `{paired.get('observation_count')}`",
        f"- Lyra minus Orion total return: `{paired.get('total_return_diff_lyra_minus_orion')}`",
        f"- Mean daily diff: `{paired.get('mean_daily_diff_lyra_minus_orion')}`",
        f"- t-stat: `{paired.get('t_stat')}`",
        f"- return correlation: `{paired.get('return_correlation')}`",
        "",
        "## Limitations",
        "",
    ]
    for item in (payload.get("methodology_review") or {}).get("known_limitations") or []:
        md.append(f"- {item}")
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
    json_path, md_path = write_artifacts(repo, payload)
    print(json.dumps({
        "status": "OK",
        "json_path": str(json_path),
        "markdown_path": str(md_path),
        "classification": payload.get("governance_classification"),
        "statistical_conclusion": payload.get("statistical_conclusion"),
        "matched_return_observations": payload.get("matched_pit_date_range", {}).get("matched_return_observations"),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
