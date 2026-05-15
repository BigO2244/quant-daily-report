from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd


STRATEGY_SLUGS = ("caerus_polaris", "caerus_orion", "caerus_lyra")
STRATEGY_NAMES = {
    "caerus_polaris": "polaris",
    "caerus_orion": "orion",
    "caerus_lyra": "lyra",
}
BENCHMARK_SLUG = "spy_benchmark"
ROLLING_INDEX_CSV = "feedback_loop_rolling_index.csv"
ROLLING_INDEX_JSON = "feedback_loop_rolling_index.json"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _weights(payload: dict[str, Any] | None) -> dict[str, float]:
    raw = (payload or {}).get("target_weights") or {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for ticker, value in raw.items():
        number = _as_float(value)
        if number is not None:
            result[str(ticker)] = number
    return result


def _returns_for_trade_date(panel: pd.DataFrame | None, trade_date: str) -> dict[str, float]:
    if panel is None or panel.empty:
        return {}
    required = {"date", "ticker", "close"}
    if not required.issubset(set(panel.columns)):
        return {}
    frame = panel.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame = frame.dropna(subset=["date"]).sort_values(["ticker", "date"])
    frame["daily_return"] = frame.groupby("ticker")["close"].pct_change()
    current = frame[frame["date"] == pd.Timestamp(trade_date)]
    return {
        str(row["ticker"]): round(float(row["daily_return"] or 0.0), 10)
        for _, row in current.iterrows()
    }


def _load_prior_strategy(output_root: Path, previous_trade_date: str | None, slug: str) -> dict[str, Any] | None:
    if not previous_trade_date:
        return None
    return _read_json(output_root / previous_trade_date / f"{slug}.json")


def _portfolio_summary(current_weights: dict[str, float], prior_weights: dict[str, float]) -> dict[str, Any]:
    all_names = sorted(set(current_weights) | set(prior_weights))
    turnover = sum(abs(current_weights.get(ticker, 0.0) - prior_weights.get(ticker, 0.0)) for ticker in all_names)
    sorted_weights = sorted(current_weights.values(), reverse=True)
    gross = sum(abs(value) for value in current_weights.values())
    net = sum(current_weights.values())
    return {
        "gross_exposure": round(float(gross), 10),
        "net_exposure": round(float(net), 10),
        "position_count": len(current_weights),
        "top_3_concentration": round(float(sum(sorted_weights[:3])), 10),
        "turnover_vs_prior": round(float(turnover), 10),
    }


def _changes_from_delta(delta_payload: dict[str, Any] | None, slug: str) -> dict[str, list[Any]]:
    item = (((delta_payload or {}).get("strategies") or {}).get(slug) or {})
    return {
        "new_entries": list(item.get("adds") or []),
        "exits": list(item.get("removes") or []),
        "weight_increases": list(item.get("increases") or []),
        "weight_decreases": list(item.get("decreases") or []),
        "unchanged": list(item.get("unchanged") or []),
    }


def _decision_trace(
    *,
    trade_date: str,
    slug: str,
    strategy_payload: dict[str, Any] | None,
    prior_payload: dict[str, Any] | None,
    delta_payload: dict[str, Any] | None,
    shadow_performance: dict[str, Any] | None,
) -> dict[str, Any]:
    current_weights = _weights(strategy_payload)
    prior_weights = _weights(prior_payload)
    chain_status = str((shadow_performance or {}).get("status") or "NO_DATA")
    data_status = str((shadow_performance or {}).get("data_status") or "NO_DATA")
    if not strategy_payload:
        status = "NO_DATA"
    elif chain_status == "BROKEN_CHAIN":
        status = "BROKEN_CHAIN"
    elif prior_payload is None:
        status = "NO_PRIOR"
    else:
        status = "OK"

    selected_positions = []
    signal_statuses: list[str] = []
    for idx, holding in enumerate((strategy_payload or {}).get("holdings") or [], start=1):
        signals: dict[str, Any] = {}
        if holding.get("momentum_score") is not None:
            signals["momentum_score"] = holding.get("momentum_score")
        if holding.get("estimated_holding_period_days") is not None:
            signals["estimated_holding_period_days"] = holding.get("estimated_holding_period_days")
        signal_statuses.append("PARTIAL" if signals else "UNAVAILABLE")
        selected_positions.append(
            {
                "ticker": str(holding.get("ticker") or ""),
                "weight": _as_float(holding.get("target_weight")) or 0.0,
                "rank": int(_as_float(holding.get("momentum_rank")) or idx),
                "signals": signals,
            }
        )

    if not selected_positions:
        signal_detail_status = "UNAVAILABLE"
    elif all(status == "UNAVAILABLE" for status in signal_statuses):
        signal_detail_status = "UNAVAILABLE"
    else:
        signal_detail_status = "PARTIAL"

    return {
        "trade_date": trade_date,
        "strategy": STRATEGY_NAMES[slug],
        "status": status,
        "data_status": data_status,
        "return_convention": str((shadow_performance or {}).get("return_convention") or "UNAVAILABLE"),
        "selected_positions": selected_positions,
        "prior_positions_available": prior_payload is not None,
        "changes_vs_prior": _changes_from_delta(delta_payload, slug),
        "portfolio_summary": _portfolio_summary(current_weights, prior_weights),
        "signal_detail_status": signal_detail_status,
    }


def _decision_bucket(ticker: str, changes: dict[str, list[Any]]) -> str:
    if ticker in set(changes.get("new_entries") or []):
        return "new_entries"
    if ticker in set(changes.get("exits") or []):
        return "exits"
    increase_tickers = {str(item.get("ticker")) for item in changes.get("weight_increases") or [] if isinstance(item, dict)}
    decrease_tickers = {str(item.get("ticker")) for item in changes.get("weight_decreases") or [] if isinstance(item, dict)}
    if ticker in increase_tickers:
        return "weight_increases"
    if ticker in decrease_tickers:
        return "weight_decreases"
    return "holds"


def _attribution(
    *,
    trade_date: str,
    slug: str,
    strategy_payload: dict[str, Any] | None,
    prior_payload: dict[str, Any] | None,
    shadow_performance: dict[str, Any] | None,
    delta_payload: dict[str, Any] | None,
    returns_by_ticker: dict[str, float],
) -> dict[str, Any]:
    strategy_perf = (((shadow_performance or {}).get("strategies") or {}).get(slug) or {})
    benchmark_perf = (((shadow_performance or {}).get("strategies") or {}).get(BENCHMARK_SLUG) or {})
    current_weights = _weights(strategy_payload)
    prior_weights = _weights(prior_payload)
    if not strategy_payload:
        status = "NO_DATA"
    elif not returns_by_ticker:
        status = "UNAVAILABLE"
    elif any(ticker not in returns_by_ticker for ticker in current_weights):
        status = "PARTIAL"
    else:
        status = "OK"

    changes = _changes_from_delta(delta_payload, slug)
    position_contribution = []
    decision_totals = {
        "new_entries": 0.0,
        "exits": 0.0,
        "weight_increases": 0.0,
        "weight_decreases": 0.0,
        "holds": 0.0,
    }
    for ticker, current_weight in sorted(current_weights.items()):
        asset_return = returns_by_ticker.get(ticker)
        contribution = None if asset_return is None else round(float(current_weight * asset_return), 10)
        contribution_status = "OK" if contribution is not None else "UNAVAILABLE"
        if contribution is not None:
            decision_totals[_decision_bucket(ticker, changes)] += contribution
        position_contribution.append(
            {
                "ticker": ticker,
                "prior_weight": round(float(prior_weights.get(ticker, 0.0)), 10),
                "current_weight": round(float(current_weight), 10),
                "asset_return": asset_return,
                "contribution": contribution,
                "contribution_status": contribution_status,
            }
        )

    decision_status = "UNAVAILABLE" if status in {"NO_DATA", "UNAVAILABLE"} or not position_contribution else "PARTIAL" if status == "PARTIAL" else "OK"
    decision_contribution = {key: round(float(value), 10) for key, value in decision_totals.items()}
    decision_contribution["status"] = decision_status
    return {
        "trade_date": trade_date,
        "strategy": STRATEGY_NAMES[slug],
        "status": status,
        "daily_return": strategy_perf.get("daily_return"),
        "benchmark_return": benchmark_perf.get("daily_return"),
        "excess_return_vs_spy": (
            round(float(strategy_perf.get("daily_return") or 0.0) - float(benchmark_perf.get("daily_return") or 0.0), 10)
            if strategy_perf.get("daily_return") is not None and benchmark_perf.get("daily_return") is not None
            else None
        ),
        "position_contribution": position_contribution,
        "decision_contribution": decision_contribution,
        "signal_contribution": {
            "status": "UNAVAILABLE",
            "signals": {},
        },
    }


def _history_dates(output_root: Path, trade_date: str) -> list[str]:
    dates = []
    for child in output_root.iterdir() if output_root.exists() else []:
        if child.is_dir() and child.name <= trade_date:
            try:
                dates.append(pd.Timestamp(child.name).strftime("%Y-%m-%d"))
            except Exception:
                continue
    return sorted(set(dates))


def _window_metrics(output_root: Path, dates: list[str], slug: str, window: int, evaluation: dict[str, Any] | None) -> dict[str, Any]:
    selected = dates[-window:]
    returns = []
    spy_returns = []
    turnovers = []
    concentrations = []
    constituents: set[str] = set()
    for date in selected:
        performance = _read_json(output_root / date / "shadow_performance.json") or {}
        strategy_perf = ((performance.get("strategies") or {}).get(slug) or {})
        spy_perf = ((performance.get("strategies") or {}).get(BENCHMARK_SLUG) or {})
        if performance.get("data_status") == "OK" and strategy_perf.get("daily_return") is not None:
            returns.append(float(strategy_perf.get("daily_return") or 0.0))
            spy_returns.append(float(spy_perf.get("daily_return") or 0.0))
        payload = _read_json(output_root / date / f"{slug}.json") or {}
        if payload.get("expected_turnover") is not None:
            turnovers.append(float(payload.get("expected_turnover") or 0.0))
        top3 = ((payload.get("weight_concentration") or {}).get("top3_concentration"))
        if top3 is not None:
            concentrations.append(float(top3))
        constituents.update((_weights(payload)).keys())
    eval_payload = (((evaluation or {}).get("strategies") or {}).get(slug) or {})
    return {
        "valid_days": len(returns),
        "return": round(float(pd.Series(returns).add(1.0).prod() - 1.0), 10) if returns else 0.0,
        "excess_return_vs_spy": round(float((pd.Series(returns).add(1.0).prod() - 1.0) - (pd.Series(spy_returns).add(1.0).prod() - 1.0)), 10) if returns and spy_returns else 0.0,
        "avg_turnover": round(float(sum(turnovers) / len(turnovers)), 10) if turnovers else 0.0,
        "max_turnover": round(float(max(turnovers)), 10) if turnovers else 0.0,
        "avg_top_3_concentration": round(float(sum(concentrations) / len(concentrations)), 10) if concentrations else 0.0,
        "constituent_change_count": int(eval_payload.get("constituent_change_count") or 0),
        "top_position_contribution_share": None,
    }


def _stability_analysis(*, trade_date: str, slug: str, output_root: Path, evaluation: dict[str, Any] | None) -> dict[str, Any]:
    dates = _history_dates(output_root, trade_date)
    windows = {
        "10d": _window_metrics(output_root, dates, slug, 10, evaluation),
        "30d": _window_metrics(output_root, dates, slug, 30, evaluation),
    }
    flags = []
    current = windows["10d"]
    if current["valid_days"] < 10:
        flags.append("INSUFFICIENT_VALID_DAYS")
    if current["avg_turnover"] > 0.5:
        flags.append("HIGH_TURNOVER")
    if current["max_turnover"] > 0.8:
        flags.append("TURNOVER_SPIKE")
    if current["avg_top_3_concentration"] > 0.6:
        flags.append("HIGH_CONCENTRATION")
    if current["constituent_change_count"] > 20:
        flags.append("HIGH_CONSTITUENT_CHURN")
    status = "NO_DATA" if not dates else "PARTIAL" if flags and current["valid_days"] < 10 else "OK"
    return {
        "trade_date": trade_date,
        "strategy": STRATEGY_NAMES[slug],
        "status": status,
        "rolling_windows": windows,
        "flags": flags,
    }


def _load_regime_history(regime_path: Path) -> list[dict[str, str]]:
    if not regime_path.exists():
        return []
    try:
        with regime_path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _regime_performance(*, trade_date: str, slug: str, output_root: Path, repo_root: Path) -> dict[str, Any]:
    regime_root = repo_root / "outputs" / "vix_regime"
    current = _read_json(regime_root / "regime_current.json")
    history = _load_regime_history(regime_root / "regime_history.csv")
    if current is None and not history:
        return {
            "trade_date": trade_date,
            "strategy": STRATEGY_NAMES[slug],
            "status": "NO_REGIME_DATA",
            "current_regime": {
                "regime": None,
                "vix": None,
                "position_scale": None,
                "max_positions": None,
            },
            "performance_by_regime": {
                "LOW": {},
                "ELEVATED": {},
                "STRESS": {},
            },
        }
    current_regime = {
        "regime": current.get("regime") if current else None,
        "vix": _as_float((current or {}).get("vix")),
        "position_scale": _as_float((current or {}).get("position_scale")),
        "max_positions": int(_as_float((current or {}).get("max_positions")) or 0),
    }
    by_date = {str(row.get("date") or row.get("trade_date") or ""): str(row.get("regime") or "").upper() for row in history}
    buckets: dict[str, list[float]] = {"LOW": [], "ELEVATED": [], "STRESS": []}
    for date in _history_dates(output_root, trade_date):
        regime = by_date.get(date)
        if regime not in buckets:
            continue
        performance = _read_json(output_root / date / "shadow_performance.json") or {}
        daily_return = (((performance.get("strategies") or {}).get(slug) or {}).get("daily_return"))
        if daily_return is not None:
            buckets[regime].append(float(daily_return))
    return {
        "trade_date": trade_date,
        "strategy": STRATEGY_NAMES[slug],
        "status": "OK" if history else "PARTIAL",
        "current_regime": current_regime,
        "performance_by_regime": {
            regime: {
                "valid_days": len(values),
                "return": round(float(pd.Series(values).add(1.0).prod() - 1.0), 10) if values else 0.0,
            }
            for regime, values in buckets.items()
        },
    }


def _learning_readiness(decision: dict[str, Any], attribution: dict[str, Any], stability: dict[str, Any], regime: dict[str, Any]) -> tuple[str, str]:
    gaps = []
    if decision.get("signal_detail_status") != "OK":
        gaps.append("signal metadata unavailable")
    if attribution.get("status") != "OK":
        gaps.append("return attribution unavailable or partial")
    if stability.get("rolling_windows", {}).get("10d", {}).get("valid_days", 0) < 10:
        gaps.append("insufficient 10d valid history")
    if regime.get("status") == "NO_REGIME_DATA":
        gaps.append("regime data unavailable")
    if len(gaps) >= 3:
        return "LOW", gaps[0]
    if gaps:
        return "MEDIUM", gaps[0]
    return "HIGH", "none"


def _rolling_index_row(
    *,
    trade_date: str,
    slug: str,
    shadow_performance: dict[str, Any] | None,
    evaluation: dict[str, Any] | None,
    strategy_payload: dict[str, Any] | None,
    attribution: dict[str, Any],
    regime: dict[str, Any],
    readiness: str,
) -> dict[str, Any]:
    strategy_perf = (((shadow_performance or {}).get("strategies") or {}).get(slug) or {})
    eval_payload = (((evaluation or {}).get("strategies") or {}).get(slug) or {})
    concentration = (strategy_payload or {}).get("weight_concentration") or {}
    current_regime = regime.get("current_regime") if isinstance(regime.get("current_regime"), dict) else {}
    return {
        "trade_date": trade_date,
        "strategy_slug": slug,
        "strategy": STRATEGY_NAMES[slug],
        "daily_return": strategy_perf.get("daily_return"),
        "turnover": (strategy_payload or {}).get("expected_turnover"),
        "top_3_concentration": concentration.get("top3_concentration"),
        "valid_days": eval_payload.get("rolling_count_of_valid_days"),
        "attribution_status": attribution.get("status"),
        "regime": current_regime.get("regime"),
        "learning_readiness": readiness,
    }


def _merge_rolling_index(output_root: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    csv_path = output_root / "performance" / ROLLING_INDEX_CSV
    existing: list[dict[str, Any]] = []
    if csv_path.exists():
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                existing = list(csv.DictReader(handle))
        except Exception:
            existing = []
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in existing + rows:
        trade_date = str(row.get("trade_date") or "")
        slug = str(row.get("strategy_slug") or "")
        if not trade_date or not slug:
            continue
        merged[(trade_date, slug)] = dict(row)
    return [merged[key] for key in sorted(merged)]


def _write_rolling_index(output_root: Path, rows: list[dict[str, Any]]) -> None:
    performance_dir = output_root / "performance"
    performance_dir.mkdir(parents=True, exist_ok=True)
    merged = _merge_rolling_index(output_root, rows)
    fieldnames = [
        "trade_date",
        "strategy_slug",
        "strategy",
        "daily_return",
        "turnover",
        "top_3_concentration",
        "valid_days",
        "attribution_status",
        "regime",
        "learning_readiness",
    ]
    with (performance_dir / ROLLING_INDEX_CSV).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in merged:
            writer.writerow({key: row.get(key) for key in fieldnames})
    _write_json(
        performance_dir / ROLLING_INDEX_JSON,
        {
            "schema_version": "feedback_loop_rolling_index_v1",
            "row_count": len(merged),
            "rows": merged,
        },
    )


def write_feedback_loop_artifacts(
    *,
    output_root: Path,
    trade_date: str,
    panel: pd.DataFrame | None = None,
    repo_root: Path | None = None,
) -> dict[str, Any]:
    dated_dir = output_root / trade_date
    repo_root = repo_root or Path(".")
    delta_payload = _read_json(dated_dir / "delta.json")
    shadow_performance = _read_json(dated_dir / "shadow_performance.json")
    evaluation = _read_json(dated_dir / "shadow_evaluation.json")
    previous_trade_date = (shadow_performance or {}).get("previous_trade_date") or (delta_payload or {}).get("previous_date")
    returns_by_ticker = _returns_for_trade_date(panel, trade_date)

    summary: dict[str, Any] = {
        "trade_date": trade_date,
        "status": "OK",
        "strategies": {},
        "system_learning_summary": {
            "can_explain_decisions": False,
            "can_attribute_returns": False,
            "can_assess_stability": False,
            "can_compare_by_regime": False,
            "ready_for_promotion_logic": False,
        },
    }
    statuses = []
    rolling_rows: list[dict[str, Any]] = []
    for slug in STRATEGY_SLUGS:
        strategy_payload = _read_json(dated_dir / f"{slug}.json")
        prior_payload = _load_prior_strategy(output_root, previous_trade_date, slug)
        strategy_dir = dated_dir / STRATEGY_NAMES[slug]
        decision = _decision_trace(
            trade_date=trade_date,
            slug=slug,
            strategy_payload=strategy_payload,
            prior_payload=prior_payload,
            delta_payload=delta_payload,
            shadow_performance=shadow_performance,
        )
        attribution = _attribution(
            trade_date=trade_date,
            slug=slug,
            strategy_payload=strategy_payload,
            prior_payload=prior_payload,
            shadow_performance=shadow_performance,
            delta_payload=delta_payload,
            returns_by_ticker=returns_by_ticker,
        )
        stability = _stability_analysis(trade_date=trade_date, slug=slug, output_root=output_root, evaluation=evaluation)
        regime = _regime_performance(trade_date=trade_date, slug=slug, output_root=output_root, repo_root=repo_root)
        _write_json(strategy_dir / "decision_trace.json", decision)
        _write_json(strategy_dir / "attribution.json", attribution)
        _write_json(strategy_dir / "stability_analysis.json", stability)
        _write_json(strategy_dir / "regime_performance.json", regime)
        readiness, gap = _learning_readiness(decision, attribution, stability, regime)
        rolling_rows.append(
            _rolling_index_row(
                trade_date=trade_date,
                slug=slug,
                shadow_performance=shadow_performance,
                evaluation=evaluation,
                strategy_payload=strategy_payload,
                attribution=attribution,
                regime=regime,
                readiness=readiness,
            )
        )
        summary["strategies"][STRATEGY_NAMES[slug]] = {
            "decision_trace_status": decision.get("status"),
            "attribution_status": attribution.get("status"),
            "stability_status": stability.get("status"),
            "regime_status": regime.get("status"),
            "learning_readiness": readiness,
            "primary_learning_gap": gap,
        }
        statuses.extend([decision.get("status"), attribution.get("status"), stability.get("status"), regime.get("status")])

    system = summary["system_learning_summary"]
    system["can_explain_decisions"] = any(item.get("decision_trace_status") in {"OK", "NO_PRIOR", "PARTIAL"} for item in summary["strategies"].values())
    system["can_attribute_returns"] = any(item.get("attribution_status") == "OK" for item in summary["strategies"].values())
    system["can_assess_stability"] = any(item.get("stability_status") in {"OK", "PARTIAL"} for item in summary["strategies"].values())
    system["can_compare_by_regime"] = any(item.get("regime_status") in {"OK", "PARTIAL"} for item in summary["strategies"].values())
    if all(status in {"NO_DATA", "NO_REGIME_DATA", "UNAVAILABLE"} for status in statuses):
        summary["status"] = "NO_DATA"
    elif any(status in {"NO_DATA", "NO_PRIOR", "BROKEN_CHAIN", "PARTIAL", "UNAVAILABLE", "NO_REGIME_DATA"} for status in statuses):
        summary["status"] = "PARTIAL"
    _write_json(dated_dir / "feedback_loop_summary.json", summary)
    _write_rolling_index(output_root, rolling_rows)
    return summary
