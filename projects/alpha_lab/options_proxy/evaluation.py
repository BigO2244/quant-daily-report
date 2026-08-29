"""Mature forward proxy signals without feeding returns back into signal creation."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from projects.alpha_lab.factory import ContractValidationError, canonical_hash

from .config import ProxyConfig


def _valid_bars(rows: Sequence[Mapping[str, Any]], after: date) -> List[Mapping[str, Any]]:
    result = []
    for row in rows:
        try:
            bar_date = date.fromisoformat(str(row["date"])[:10])
            open_price = float(row["open"])
            close_price = float(row["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if bar_date <= after or open_price <= 0 or close_price <= 0:
            continue
        if not math.isfinite(open_price) or not math.isfinite(close_price):
            continue
        result.append(
            {
                "date": bar_date,
                "open": open_price,
                "close": close_price,
            }
        )
    return sorted(result, key=lambda row: row["date"])


def _symbol_return(
    rows: Sequence[Mapping[str, Any]],
    *,
    decision_date: date,
    holding_days: int,
    cost_bps_per_side: float,
) -> Dict[str, Any]:
    bars = _valid_bars(rows, decision_date)
    if len(bars) < holding_days:
        return {"status": "NOT_MATURE", "available_bar_count": len(bars)}
    entry = bars[0]
    exit_bar = bars[holding_days - 1]
    gross_return = exit_bar["close"] / entry["open"] - 1.0
    one_side_cost = cost_bps_per_side / 10000.0
    net_return = (1.0 + gross_return) * (1.0 - one_side_cost) ** 2 - 1.0
    return {
        "status": "MATURE",
        "entry_date": entry["date"].isoformat(),
        "entry_open": entry["open"],
        "exit_date": exit_bar["date"].isoformat(),
        "exit_close": exit_bar["close"],
        "gross_return": gross_return,
        "net_return": net_return,
    }


def _equal_weight_average(
    symbols: Sequence[str],
    returns: Mapping[str, Mapping[str, Any]],
    field: str,
) -> Tuple[float, float]:
    values = [
        float(returns[symbol][field])
        for symbol in symbols
        if returns.get(symbol, {}).get("status") == "MATURE"
    ]
    coverage = len(values) / len(symbols) if symbols else 0.0
    return (sum(values) / len(values) if values else 0.0, coverage)


def evaluate_signal(
    *,
    signal: Mapping[str, Any],
    bars_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    config: ProxyConfig,
) -> Dict[str, Any]:
    if signal.get("schema_version") != "caerus_options_proxy_signal_v1":
        raise ContractValidationError("unsupported proxy signal schema")
    if signal.get("alpha_claim_permitted") is not False:
        raise ContractValidationError("proxy signal cannot permit an alpha claim")
    decision_date = date.fromisoformat(str(signal["as_of_date"]))
    candidate_symbols = [
        str(row["symbol"]) for row in signal.get("research_targets", [])
    ]
    baseline_symbols = [str(value) for value in signal.get("baseline_symbols", [])]
    all_symbols = sorted(
        set(candidate_symbols + baseline_symbols + [config.benchmark_symbol])
    )
    observed_dates = sorted(
        str(row.get("date"))[:10]
        for rows in bars_by_symbol.values()
        for row in rows
        if row.get("date")
    )
    returns = {
        symbol: _symbol_return(
            bars_by_symbol.get(symbol, []),
            decision_date=decision_date,
            holding_days=config.holding_period_trading_days,
            cost_bps_per_side=config.base_cost_bps_per_side,
        )
        for symbol in all_symbols
    }
    candidate_net, candidate_coverage = _equal_weight_average(
        candidate_symbols, returns, "net_return"
    )
    baseline_net, baseline_coverage = _equal_weight_average(
        baseline_symbols, returns, "net_return"
    )
    benchmark = returns.get(config.benchmark_symbol, {"status": "NOT_MATURE"})
    complete = (
        bool(candidate_symbols)
        and candidate_coverage == 1.0
        and baseline_coverage >= config.minimum_source_coverage
        and benchmark.get("status") == "MATURE"
    )
    payload = {
        "schema_version": "caerus_options_proxy_evaluation_v1",
        "hypothesis_id": config.hypothesis_id,
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "signal_hash": signal["signal_hash"],
        "snapshot_id": signal["snapshot_id"],
        "as_of_date": signal["as_of_date"],
        "through_date": observed_dates[-1] if observed_dates else None,
        "status": "MATURE_COMPLETE" if complete else "NOT_MATURE_OR_INCOMPLETE",
        "candidate_symbol_count": len(candidate_symbols),
        "candidate_coverage": candidate_coverage,
        "baseline_symbol_count": len(baseline_symbols),
        "baseline_coverage": baseline_coverage,
        "candidate_net_return": candidate_net if complete else None,
        "baseline_net_return": baseline_net if complete else None,
        "excess_return_vs_baseline": (
            candidate_net - baseline_net if complete else None
        ),
        "benchmark_net_return": (
            benchmark.get("net_return") if benchmark.get("status") == "MATURE" else None
        ),
        "excess_return_vs_benchmark": (
            candidate_net - float(benchmark["net_return"])
            if complete
            else None
        ),
        "cost_bps_per_side": config.base_cost_bps_per_side,
        "symbol_returns": returns,
        "return_data_used_for_signal": False,
        "limitations": [
            "short_forward_proxy_sample",
            "not_factor_adjusted",
            "yfinance_price_source",
            "not_evidence_for_frozen_trade_level_hypothesis",
        ],
    }
    payload["evaluation_hash"] = canonical_hash(payload)
    return payload


def build_scoreboard(evaluations: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    latest_by_signal: Dict[str, Mapping[str, Any]] = {}
    for row in evaluations:
        signal_hash = str(row.get("signal_hash") or "")
        if not signal_hash:
            continue
        existing = latest_by_signal.get(signal_hash)
        if existing is None or str(row.get("through_date") or "") > str(
            existing.get("through_date") or ""
        ):
            latest_by_signal[signal_hash] = row
    complete = [
        row
        for row in latest_by_signal.values()
        if row.get("status") == "MATURE_COMPLETE"
    ]
    excess = [float(row["excess_return_vs_baseline"]) for row in complete]
    candidate_returns = [float(row["candidate_net_return"]) for row in complete]
    baseline_returns = [float(row["baseline_net_return"]) for row in complete]
    observation_count = len(complete)
    status = "INSUFFICIENT_OBSERVATIONS"
    if observation_count >= 60:
        status = "READY_FOR_PRELIMINARY_SPEND_REVIEW"
    payload = {
        "schema_version": "caerus_options_proxy_scoreboard_v1",
        "classification": "PROXY_FORWARD_OBSERVATION_ONLY",
        "alpha_claim_permitted": False,
        "status": status,
        "mature_observation_count": observation_count,
        "mean_excess_return_vs_baseline": (
            sum(excess) / len(excess) if excess else None
        ),
        "positive_excess_hit_rate": (
            sum(1 for value in excess if value > 0) / len(excess) if excess else None
        ),
        "mean_candidate_cohort_return": (
            sum(candidate_returns) / len(candidate_returns) if candidate_returns else None
        ),
        "mean_baseline_cohort_return": (
            sum(baseline_returns) / len(baseline_returns) if baseline_returns else None
        ),
        "overlapping_cohort_returns_are_not_portfolio_nav": True,
        "spend_authorized": False,
        "promotion_authorized": False,
    }
    payload["scoreboard_hash"] = canonical_hash(payload)
    return payload
