from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any


DEFAULT_OBJECTIVE_CONTRACT: dict[str, Any] = {
    "benchmark": "SPY",
    "north_star": "Significantly outperform SPY with 20%+ annualized returns and strong risk-adjusted performance.",
    "annualized_return_target": 0.20,
    "annualized_excess_return_target": 0.05,
    "sharpe_target": 1.0,
    "max_drawdown_floor": -0.10,
    "beta_floor": 0.90,
    "upside_capture_floor": 0.90,
    "cash_ceiling": 0.10,
}


def _parse_float(value: object) -> float | None:
    if value in (None, "", "null"):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        lines = [line for line in handle.read().splitlines() if line.strip()]
    if not lines:
        return []
    return list(csv.DictReader(lines))


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _sample_stddev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    avg = _mean(values)
    if avg is None:
        return None
    return math.sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _correlation(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    mean_x = _mean(xs)
    mean_y = _mean(ys)
    std_x = _sample_stddev(xs)
    std_y = _sample_stddev(ys)
    if mean_x is None or mean_y is None or not std_x or not std_y:
        return None
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / (len(xs) - 1)
    return cov / (std_x * std_y)


def _regress_alpha_beta(port_returns: list[float], bench_returns: list[float]) -> tuple[float | None, float | None]:
    if len(port_returns) != len(bench_returns) or len(port_returns) < 2:
        return None, None
    x_mean = _mean(bench_returns)
    y_mean = _mean(port_returns)
    if x_mean is None or y_mean is None:
        return None, None
    denom = sum((x - x_mean) ** 2 for x in bench_returns)
    if denom <= 0:
        return None, None
    beta = sum((x - x_mean) * (y - y_mean) for x, y in zip(bench_returns, port_returns)) / denom
    alpha_daily = y_mean - beta * x_mean
    return alpha_daily, beta


def _capture_ratio(port_returns: list[float], bench_returns: list[float], *, positive: bool) -> float | None:
    filtered: list[tuple[float, float]] = []
    for port_return, bench_return in zip(port_returns, bench_returns):
        if positive and bench_return > 0:
            filtered.append((port_return, bench_return))
        if not positive and bench_return < 0:
            filtered.append((port_return, bench_return))
    if not filtered:
        return None
    port_sum = sum(item[0] for item in filtered)
    bench_sum = sum(item[1] for item in filtered)
    if bench_sum == 0:
        return None
    return port_sum / bench_sum


def _max_drawdown(equity_values: list[float]) -> float | None:
    if not equity_values:
        return None
    peak = equity_values[0]
    worst = 0.0
    for equity in equity_values:
        peak = max(peak, equity)
        if peak <= 0:
            continue
        worst = min(worst, equity / peak - 1.0)
    return worst


def _annualize_return(total_return: float | None, periods: int) -> float | None:
    if total_return is None or periods <= 0:
        return None
    years = periods / 252.0
    if years <= 0 or 1.0 + total_return <= 0:
        return None
    return (1.0 + total_return) ** (1.0 / years) - 1.0


def _annualized_sharpe(returns: list[float], risk_free_rate_annual: float = 0.04) -> float | None:
    if len(returns) < 2:
        return None
    mean_return = _mean(returns)
    std_return = _sample_stddev(returns)
    if mean_return is None or std_return in (None, 0):
        return None
    risk_free_daily = risk_free_rate_annual / 252.0
    return (mean_return - risk_free_daily) / std_return * math.sqrt(252)


def _load_objective_contract(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "config" / "engine_objectives.json"
    contract = dict(DEFAULT_OBJECTIVE_CONTRACT)
    if not path.exists():
        return contract

    payload = _read_json(path)
    for key, value in payload.items():
        if key in contract:
            contract[key] = value
    return contract


def _load_live_performance(repo_root: Path) -> dict[str, Any]:
    nav_path = repo_root / "outputs" / "perf" / "live_overlay_nav_series.csv"
    bench_path = repo_root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv"
    if not nav_path.exists() or not bench_path.exists():
        return {
            "available": False,
            "reason": "live_overlay_series_missing",
        }

    nav_rows = _read_csv_rows(nav_path)
    bench_rows = _read_csv_rows(bench_path)
    nav_rows = [row for row in nav_rows if row.get("date") and _parse_float(row.get("equity")) is not None]
    bench_rows = [row for row in bench_rows if row.get("date") and _parse_float(row.get("spy_close")) is not None]
    if len(nav_rows) < 2 or len(bench_rows) < 2:
        return {
            "available": False,
            "reason": "insufficient_live_overlay_history",
        }

    nav_by_date = {str(row["date"]): float(row["equity"]) for row in nav_rows}
    bench_by_date = {str(row["date"]): float(row["spy_close"]) for row in bench_rows}

    nav_returns: dict[str, float] = {}
    prev_equity: float | None = None
    for row in nav_rows:
        trade_date = str(row["date"])
        explicit_return = _parse_float(row.get("return_1d"))
        equity = float(row["equity"])
        if explicit_return is not None:
            nav_returns[trade_date] = explicit_return
        elif prev_equity not in (None, 0):
            nav_returns[trade_date] = equity / prev_equity - 1.0
        prev_equity = equity

    bench_returns: dict[str, float] = {}
    prev_close: float | None = None
    for row in bench_rows:
        trade_date = str(row["date"])
        explicit_return = _parse_float(row.get("spy_return"))
        close = float(row["spy_close"])
        if explicit_return is not None:
            bench_returns[trade_date] = explicit_return
        elif prev_close not in (None, 0):
            bench_returns[trade_date] = close / prev_close - 1.0
        prev_close = close

    aligned_return_dates = sorted(set(nav_returns).intersection(bench_returns))
    aligned_price_dates = sorted(set(nav_by_date).intersection(bench_by_date))
    if len(aligned_return_dates) < 2 or len(aligned_price_dates) < 2:
        return {
            "available": False,
            "reason": "insufficient_aligned_history",
        }

    port_returns = [nav_returns[trade_date] for trade_date in aligned_return_dates]
    bench_returns_list = [bench_returns[trade_date] for trade_date in aligned_return_dates]
    excess_returns = [port - bench for port, bench in zip(port_returns, bench_returns_list)]

    port_total_return = nav_by_date[aligned_price_dates[-1]] / nav_by_date[aligned_price_dates[0]] - 1.0
    bench_total_return = bench_by_date[aligned_price_dates[-1]] / bench_by_date[aligned_price_dates[0]] - 1.0
    excess_total_return = port_total_return - bench_total_return
    alpha_daily, beta = _regress_alpha_beta(port_returns, bench_returns_list)
    excess_mean = _mean(excess_returns)
    excess_std = _sample_stddev(excess_returns)
    tracking_error_ann = excess_std * math.sqrt(252) if excess_std else None
    info_ratio = (
        excess_mean / excess_std * math.sqrt(252)
        if excess_mean is not None and excess_std and excess_std > 0
        else None
    )
    benchmark_up_days = sum(1 for value in bench_returns_list if value > 0)
    benchmark_down_days = sum(1 for value in bench_returns_list if value < 0)
    benchmark_hit_rate = (
        sum(1 for port, bench in zip(port_returns, bench_returns_list) if port > bench) / len(port_returns)
        if port_returns
        else None
    )

    return {
        "available": True,
        "window_start": aligned_price_dates[0],
        "window_end": aligned_price_dates[-1],
        "n_return_days": len(aligned_return_dates),
        "portfolio_total_return": port_total_return,
        "benchmark_total_return": bench_total_return,
        "excess_total_return": excess_total_return,
        "portfolio_annualized_return": _annualize_return(port_total_return, len(aligned_return_dates)),
        "benchmark_annualized_return": _annualize_return(bench_total_return, len(aligned_return_dates)),
        "portfolio_sharpe": _annualized_sharpe(port_returns),
        "benchmark_sharpe": _annualized_sharpe(bench_returns_list),
        "alpha_daily": alpha_daily,
        "alpha_annualized": alpha_daily * 252 if alpha_daily is not None else None,
        "beta": beta,
        "tracking_error_annualized": tracking_error_ann,
        "information_ratio": info_ratio,
        "correlation": _correlation(port_returns, bench_returns_list),
        "upside_capture": _capture_ratio(port_returns, bench_returns_list, positive=True),
        "downside_capture": _capture_ratio(port_returns, bench_returns_list, positive=False),
        "benchmark_outperformance_hit_rate": benchmark_hit_rate,
        "benchmark_up_days": benchmark_up_days,
        "benchmark_down_days": benchmark_down_days,
        "max_drawdown": _max_drawdown([nav_by_date[trade_date] for trade_date in aligned_price_dates]),
        "portfolio_return_std": _sample_stddev(port_returns),
        "benchmark_return_std": _sample_stddev(bench_returns_list),
    }


def _objective_check(
    *,
    label: str,
    current: float | None,
    target: float | None,
    comparison: str,
    note: str | None = None,
) -> dict[str, Any]:
    status = "insufficient_data"
    if current is not None and target is not None:
        if comparison == ">=":
            status = "pass" if current >= target else "fail"
        elif comparison == "<=":
            status = "pass" if current <= target else "fail"
        else:
            raise ValueError(f"Unsupported comparison: {comparison}")
    return {
        "label": label,
        "current": current,
        "target": target,
        "comparison": comparison,
        "status": status,
        "note": note,
    }


def _build_objective_scorecard(summary: dict[str, Any]) -> dict[str, Any]:
    contract = summary["objective_contract"]
    live = summary["live_performance"]
    portfolio = summary["portfolio_structure"]
    research = summary["research_stack"]
    alpha_variant = research.get("alpha_variant_backtest") or {}

    live_annualized = live.get("portfolio_annualized_return")
    live_benchmark_annualized = live.get("benchmark_annualized_return")
    live_excess_annualized = None
    if live_annualized is not None and live_benchmark_annualized is not None:
        live_excess_annualized = live_annualized - live_benchmark_annualized

    checks = [
        _objective_check(
            label="Live annualized return",
            current=live_annualized,
            target=_parse_float(contract.get("annualized_return_target")),
            comparison=">=",
            note="Short-window annualization is noisy, but directionally important.",
        ),
        _objective_check(
            label="Live annualized excess return vs benchmark",
            current=live_excess_annualized,
            target=_parse_float(contract.get("annualized_excess_return_target")),
            comparison=">=",
            note="Measures whether live returns are materially beating SPY, not just matching it.",
        ),
        _objective_check(
            label="Live Sharpe",
            current=live.get("portfolio_sharpe"),
            target=_parse_float(contract.get("sharpe_target")),
            comparison=">=",
            note="Computed from live daily returns with a 4% annual risk-free assumption.",
        ),
        _objective_check(
            label="Live beta participation",
            current=live.get("beta"),
            target=_parse_float(contract.get("beta_floor")),
            comparison=">=",
            note="Long-only outperformance requires participation, not accidental defensiveness.",
        ),
        _objective_check(
            label="Live upside capture",
            current=live.get("upside_capture"),
            target=_parse_float(contract.get("upside_capture_floor")),
            comparison=">=",
            note="If upside capture is too low, selection edge cannot overcome market underparticipation.",
        ),
        _objective_check(
            label="Current cash ratio",
            current=portfolio.get("cash_ratio"),
            target=_parse_float(contract.get("cash_ceiling")),
            comparison="<=",
            note="Cash above the ceiling should be an explicit risk-off decision, not a default state.",
        ),
        _objective_check(
            label="Research alpha-variant CAGR",
            current=alpha_variant.get("net_cagr"),
            target=_parse_float(contract.get("annualized_return_target")),
            comparison=">=",
            note="Confirms whether the research stack still supports the north star.",
        ),
        _objective_check(
            label="Research alpha-variant Sharpe",
            current=alpha_variant.get("net_sharpe"),
            target=_parse_float(contract.get("sharpe_target")),
            comparison=">=",
            note="Uses the production-adjacent alpha-variant backtest as the main research reference.",
        ),
    ]

    pass_count = sum(1 for check in checks if check["status"] == "pass")
    fail_count = sum(1 for check in checks if check["status"] == "fail")
    insufficient_count = sum(1 for check in checks if check["status"] == "insufficient_data")
    if fail_count:
        overall_status = "off_track"
    elif pass_count and not insufficient_count:
        overall_status = "on_track"
    else:
        overall_status = "insufficient_data"

    return {
        "benchmark": contract.get("benchmark"),
        "north_star": contract.get("north_star"),
        "overall_status": overall_status,
        "checks": checks,
        "pass_count": pass_count,
        "fail_count": fail_count,
        "insufficient_count": insufficient_count,
    }


def _extract_trade_date_from_order(order: dict[str, Any]) -> str | None:
    client_order_id = str(order.get("client_order_id") or "").strip()
    if client_order_id:
        prefix = client_order_id.split(":", 1)[0]
        if len(prefix) == 10 and prefix[4] == "-" and prefix[7] == "-":
            return prefix
    timestamp = str(order.get("filled_at") or order.get("submitted_at") or "").strip()
    if len(timestamp) >= 10:
        return timestamp[:10]
    return None


def _load_broker_activity(repo_root: Path) -> dict[str, Any]:
    snapshot_path = repo_root / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-08.json"
    if not snapshot_path.exists():
        snapshot_path = repo_root / "outputs" / "broker_snapshot" / "broker_snapshot_latest.json"
    if not snapshot_path.exists():
        snapshot_path = repo_root / "outputs" / "broker" / "broker_snapshot_latest.json"
    if not snapshot_path.exists():
        return {
            "available": False,
            "reason": "broker_snapshot_missing",
        }

    snapshot = _read_json(snapshot_path)
    orders = [order for order in snapshot.get("orders_closed_recent", []) if isinstance(order, dict)]
    filled_orders = [order for order in orders if str(order.get("status") or "").lower() == "filled"]
    fills_by_date: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    daily_notional: defaultdict[str, float] = defaultdict(float)
    for order in filled_orders:
        trade_date = _extract_trade_date_from_order(order)
        if not trade_date:
            continue
        fills_by_date[trade_date].append(order)
        qty = _parse_float(order.get("filled_qty")) or _parse_float(order.get("qty")) or 0.0
        price = _parse_float(order.get("filled_avg_price")) or _parse_float(order.get("limit_price")) or 0.0
        daily_notional[trade_date] += qty * price

    quick_flips: list[dict[str, Any]] = []
    by_symbol: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in filled_orders:
        symbol = str(order.get("symbol") or "").strip().upper()
        trade_date = _extract_trade_date_from_order(order)
        if not symbol or not trade_date:
            continue
        by_symbol[symbol].append(order)

    for symbol, symbol_orders in by_symbol.items():
        symbol_orders.sort(key=lambda item: str(item.get("filled_at") or item.get("submitted_at") or ""))
        for previous, current in zip(symbol_orders, symbol_orders[1:]):
            prev_side = str(previous.get("side") or "").lower()
            curr_side = str(current.get("side") or "").lower()
            if prev_side == curr_side or not prev_side or not curr_side:
                continue
            prev_date = _extract_trade_date_from_order(previous)
            curr_date = _extract_trade_date_from_order(current)
            if not prev_date or not curr_date:
                continue
            delta_days = (date.fromisoformat(curr_date) - date.fromisoformat(prev_date)).days
            if delta_days > 5:
                continue
            quick_flips.append(
                {
                    "symbol": symbol,
                    "from_date": prev_date,
                    "to_date": curr_date,
                    "from_side": prev_side.upper(),
                    "to_side": curr_side.upper(),
                    "delta_days": delta_days,
                    "from_qty": _parse_float(previous.get("filled_qty")) or _parse_float(previous.get("qty")),
                    "to_qty": _parse_float(current.get("filled_qty")) or _parse_float(current.get("qty")),
                    "from_price": _parse_float(previous.get("filled_avg_price")) or _parse_float(previous.get("limit_price")),
                    "to_price": _parse_float(current.get("filled_avg_price")) or _parse_float(current.get("limit_price")),
                }
            )

    fill_dates = sorted(fills_by_date)
    total_notional = sum(daily_notional.values())
    return {
        "available": True,
        "snapshot_path": str(snapshot_path),
        "filled_orders_count": len(filled_orders),
        "fill_days_count": len(fill_dates),
        "fill_dates": fill_dates,
        "filled_orders_by_date": {trade_date: len(items) for trade_date, items in sorted(fills_by_date.items())},
        "buy_orders_by_date": {
            trade_date: sum(1 for item in items if str(item.get("side") or "").lower() == "buy")
            for trade_date, items in sorted(fills_by_date.items())
        },
        "sell_orders_by_date": {
            trade_date: sum(1 for item in items if str(item.get("side") or "").lower() == "sell")
            for trade_date, items in sorted(fills_by_date.items())
        },
        "avg_fills_per_day": len(filled_orders) / len(fill_dates) if fill_dates else None,
        "daily_notional": {trade_date: round(value, 2) for trade_date, value in sorted(daily_notional.items())},
        "avg_daily_notional": total_notional / len(fill_dates) if fill_dates else None,
        "max_daily_notional": max(daily_notional.values()) if daily_notional else None,
        "buy_count": sum(1 for order in filled_orders if str(order.get("side") or "").lower() == "buy"),
        "sell_count": sum(1 for order in filled_orders if str(order.get("side") or "").lower() == "sell"),
        "top_symbols_by_order_count": Counter(
            str(order.get("symbol") or "").strip().upper() for order in filled_orders if order.get("symbol")
        ).most_common(10),
        "quick_flip_count": len(quick_flips),
        "same_day_flip_count": sum(1 for item in quick_flips if item["delta_days"] == 0),
        "next_day_flip_count": sum(1 for item in quick_flips if item["delta_days"] <= 1),
        "quick_flip_examples": sorted(
            quick_flips,
            key=lambda item: (item["delta_days"], item["symbol"], item["from_date"], item["to_date"]),
        )[:12],
    }


def _load_artifact_coverage(repo_root: Path, broker_activity: dict[str, Any]) -> dict[str, Any]:
    fill_dates = broker_activity.get("fill_dates") or []
    signal_dates = {path.stem for path in (repo_root / "signals").glob("*.json")}
    email_dates = {
        path.name.split(".json")[0]
        for path in (repo_root / "outputs" / "execution_email").glob("*.json")
        if ".empty." not in path.name and path.stem not in {"2000-01-01", "2099-01-01"}
    }

    signal_covered = sorted(trade_date for trade_date in fill_dates if trade_date in signal_dates)
    email_covered = sorted(trade_date for trade_date in fill_dates if trade_date in email_dates)

    broker_fills_by_date = broker_activity.get("filled_orders_by_date") or {}

    telemetry_mismatches: list[dict[str, Any]] = []
    for trade_date in email_covered:
        artifact_path = repo_root / "outputs" / "execution_email" / f"{trade_date}.json"
        if not artifact_path.exists():
            continue
        artifact = _read_json(artifact_path)
        broker_fill_count = int(broker_fills_by_date.get(trade_date) or 0)
        email_fill_count = int(artifact.get("orders_filled_count") or artifact.get("orders_filled") or 0)
        email_submitted = int(artifact.get("orders_submitted_count") or artifact.get("submitted_count") or 0)
        if email_fill_count != broker_fill_count:
            telemetry_mismatches.append(
                {
                    "trade_date": trade_date,
                    "execution_email_filled_count": email_fill_count,
                    "execution_email_submitted_count": email_submitted,
                    "broker_filled_orders": broker_fill_count,
                    "execution_status": artifact.get("execution_status"),
                    "plan_only": bool(artifact.get("plan_only")),
                }
            )

    total_fill_dates = len(fill_dates)
    return {
        "signal_snapshot_fill_date_coverage": len(signal_covered) / total_fill_dates if total_fill_dates else None,
        "execution_email_fill_date_coverage": len(email_covered) / total_fill_dates if total_fill_dates else None,
        "signal_snapshot_covered_dates": signal_covered,
        "execution_email_covered_dates": email_covered,
        "signal_snapshot_missing_dates": sorted(trade_date for trade_date in fill_dates if trade_date not in signal_dates),
        "execution_email_missing_dates": sorted(trade_date for trade_date in fill_dates if trade_date not in email_dates),
        "telemetry_mismatches": telemetry_mismatches,
    }


def _load_portfolio_structure(repo_root: Path) -> dict[str, Any]:
    positions_path = repo_root / "outputs" / "broker" / "posttrade_positions.json"
    account_path = repo_root / "outputs" / "broker" / "posttrade_account_snapshot.json"
    universe_path = repo_root / "data" / "universe.csv"
    if not positions_path.exists() or not account_path.exists():
        return {
            "available": False,
            "reason": "broker_positions_missing",
        }

    positions_payload = _read_json(positions_path)
    account_payload = _read_json(account_path)
    positions = positions_payload.get("positions") if isinstance(positions_payload.get("positions"), list) else []

    equity = _parse_float(account_payload.get("equity")) or _parse_float(account_payload.get("portfolio_value"))
    cash = _parse_float(account_payload.get("cash"))
    if equity in (None, 0):
        return {
            "available": False,
            "reason": "missing_equity",
        }

    sector_map: dict[str, str] = {}
    if universe_path.exists():
        for row in _read_csv_rows(universe_path):
            ticker = str(row.get("ticker") or "").strip().upper()
            if ticker:
                sector_map[ticker] = str(row.get("sector") or "Unknown").strip() or "Unknown"

    sector_weights: defaultdict[str, float] = defaultdict(float)
    position_rows: list[dict[str, Any]] = []
    for row in positions:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        market_value = _parse_float(row.get("market_value")) or 0.0
        weight = market_value / equity
        sector = sector_map.get(symbol, "Unknown")
        sector_weights[sector] += weight
        position_rows.append(
            {
                "symbol": symbol,
                "sector": sector,
                "market_value": market_value,
                "weight": weight,
                "qty": _parse_float(row.get("qty")),
                "unrealized_pl": _parse_float(row.get("unrealized_pl")),
                "unrealized_plpc": _parse_float(row.get("unrealized_plpc")),
            }
        )

    top_positions = sorted(position_rows, key=lambda item: item["weight"], reverse=True)
    best_unrealized = [
        row for row in sorted(position_rows, key=lambda item: item["unrealized_plpc"] or -999.0, reverse=True)
        if row["unrealized_plpc"] is not None
    ][:5]
    worst_unrealized = [
        row for row in sorted(position_rows, key=lambda item: item["unrealized_plpc"] or 999.0)
        if row["unrealized_plpc"] is not None
    ][:5]

    return {
        "available": True,
        "equity": equity,
        "cash": cash,
        "cash_ratio": cash / equity if cash is not None else None,
        "positions_count": len(position_rows),
        "invested_ratio": sum(row["weight"] for row in position_rows),
        "top5_concentration": sum(row["weight"] for row in top_positions[:5]),
        "sector_weights": dict(sorted(sector_weights.items(), key=lambda item: item[1], reverse=True)),
        "top_positions": top_positions[:10],
        "best_unrealized": best_unrealized,
        "worst_unrealized": worst_unrealized,
    }


def _select_row(rows: list[dict[str, str]], key: str, value: str) -> dict[str, str] | None:
    for row in rows:
        if str(row.get(key) or "") == value:
            return row
    return rows[0] if rows else None


def _load_research_stack(repo_root: Path) -> dict[str, Any]:
    baseline_path = repo_root / "outputs" / "research" / "sleeve1_backtest_2009_2025_summary.csv"
    alpha_variant_path = repo_root / "outputs" / "research" / "sleeve1_alpha_variant_summary.csv"
    worst_window_path = repo_root / "outputs" / "research" / "worst_window_full.json"

    baseline_row = _select_row(_read_csv_rows(baseline_path), "window_name", "full_period") if baseline_path.exists() else None
    alpha_variant_row = _select_row(_read_csv_rows(alpha_variant_path), "start_date", "2009-01-01") if alpha_variant_path.exists() else None
    worst_window = _read_json(worst_window_path) if worst_window_path.exists() else {}

    baseline = None
    if baseline_row:
        baseline = {
            "window_start": baseline_row.get("start_date"),
            "window_end": baseline_row.get("end_date"),
            "portfolio_cagr": _parse_float(baseline_row.get("port_cagr")),
            "portfolio_sharpe": _parse_float(baseline_row.get("port_sharpe")),
            "portfolio_max_drawdown": _parse_float(baseline_row.get("port_max_dd")),
            "spy_cagr": _parse_float(baseline_row.get("spy_cagr")),
            "spy_sharpe": _parse_float(baseline_row.get("spy_sharpe")),
            "spy_max_drawdown": _parse_float(baseline_row.get("spy_max_dd")),
            "avg_holdings": _parse_float(baseline_row.get("avg_holdings")),
        }

    alpha_variant = None
    if alpha_variant_row:
        alpha_variant = {
            "window_start": alpha_variant_row.get("start_date"),
            "window_end": alpha_variant_row.get("end_date"),
            "net_cagr": _parse_float(alpha_variant_row.get("net_cagr")),
            "net_sharpe": _parse_float(alpha_variant_row.get("net_sharpe")),
            "net_max_drawdown": _parse_float(alpha_variant_row.get("net_max_drawdown")),
            "net_beta_vs_spy": _parse_float(alpha_variant_row.get("net_beta_vs_spy")),
            "circuit_breaker_net_cagr": _parse_float(alpha_variant_row.get("net_cagr_cb")),
            "circuit_breaker_net_sharpe": _parse_float(alpha_variant_row.get("net_sharpe_cb")),
            "circuit_breaker_net_max_drawdown": _parse_float(alpha_variant_row.get("net_max_drawdown_cb")),
            "circuit_breaker_beta_vs_spy": _parse_float(alpha_variant_row.get("net_beta_vs_spy_cb")),
            "avg_turnover": _parse_float(alpha_variant_row.get("avg_turnover")),
            "cost_bps": _parse_float(alpha_variant_row.get("cost_bps")),
            "spy_cagr": _parse_float(alpha_variant_row.get("spy_cagr")),
        }

    return {
        "baseline_backtest": baseline,
        "alpha_variant_backtest": alpha_variant,
        "worst_window_full": worst_window or None,
    }


def _load_ic_monitor(repo_root: Path) -> dict[str, Any]:
    path = repo_root / "outputs" / "ic_monitor" / "ic_summary.json"
    if not path.exists():
        return {
            "available": False,
            "reason": "IC monitor artifacts unavailable — run research.ic_monitor",
            "path": str(path),
        }
    payload = _read_json(path)
    if not payload:
        return {
            "available": False,
            "reason": "IC monitor artifacts unavailable — run research.ic_monitor",
            "path": str(path),
        }
    return {
        "available": True,
        "path": str(path),
        "summary": payload,
    }


def _build_findings(summary: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    live = summary["live_performance"]
    if live.get("available"):
        excess_return = live.get("excess_total_return")
        beta = live.get("beta")
        upside_capture = live.get("upside_capture")
        downside_capture = live.get("downside_capture")
        if excess_return is not None and beta is not None and upside_capture is not None and downside_capture is not None:
            findings.append(
                {
                    "severity": "high" if excess_return < 0 else "medium",
                    "title": "Live engine is lagging SPY with muted participation",
                    "evidence": (
                        f"From {live['window_start']} to {live['window_end']}, portfolio total return was "
                        f"{excess_return + live['benchmark_total_return']:+.2%} versus SPY {live['benchmark_total_return']:+.2%}, "
                        f"for excess return {excess_return:+.2%}. Beta was {beta:.2f}, upside capture {upside_capture:.2f}x, "
                        f"downside capture {downside_capture:.2f}x."
                    ),
                    "implication": "The engine is not simply conservative. It is participating weakly on up days while still taking meaningful downside.",
                }
            )

    coverage = summary["artifact_coverage"]
    signal_coverage = coverage.get("signal_snapshot_fill_date_coverage")
    email_coverage = coverage.get("execution_email_fill_date_coverage")
    mismatches = coverage.get("telemetry_mismatches") or []
    if signal_coverage is not None and email_coverage is not None:
        findings.append(
            {
                "severity": "high" if signal_coverage < 0.5 or email_coverage < 0.5 or mismatches else "medium",
                "title": "The engine is not auditable enough to prove missed-trade opportunity cost",
                "evidence": (
                    f"Only {signal_coverage:.0%} of live fill dates have local signal snapshots and {email_coverage:.0%} have "
                    f"execution-email artifacts. Telemetry mismatches detected on {len(mismatches)} documented trading dates."
                ),
                "implication": "Before we can say which missed trades hurt us, we need canonical daily signal, proposed-trade, and executed-trade artifacts.",
            }
        )

    broker_activity = summary["broker_activity"]
    if broker_activity.get("available") and broker_activity.get("quick_flip_count"):
        quick_flip_count = broker_activity["quick_flip_count"]
        filled_orders = broker_activity.get("filled_orders_count") or 0
        findings.append(
            {
                "severity": "medium" if quick_flip_count < 20 else "high",
                "title": "Trade churn is high relative to account size",
                "evidence": (
                    f"There were {filled_orders} filled orders across {broker_activity.get('fill_days_count') or 0} recent trading days, "
                    f"including {quick_flip_count} direction changes within five calendar days and "
                    f"{broker_activity.get('next_day_flip_count') or 0} within one day."
                ),
                "implication": "The engine may be paying for decisiveness without getting enough signal persistence in return.",
            }
        )

    portfolio = summary["portfolio_structure"]
    if portfolio.get("available"):
        cash_ratio = portfolio.get("cash_ratio")
        sectors = portfolio.get("sector_weights") or {}
        leading_sector = next(iter(sectors.items()), None)
        if cash_ratio is not None:
            sector_text = ""
            if leading_sector:
                sector_text = f" Largest sector exposure is {leading_sector[0]} at {leading_sector[1]:.1%} of equity."
            findings.append(
                {
                    "severity": "medium",
                    "title": "The current book is still carrying meaningful idle cash and defensive bias",
                    "evidence": f"Cash is {cash_ratio:.1%} of equity; invested capital is {portfolio.get('invested_ratio', 0.0):.1%}.{sector_text}",
                    "implication": "If the objective is to beat SPY on the long side, the book needs an explicit beta and participation target rather than accidental defensiveness.",
                }
            )

    research = summary["research_stack"]
    baseline = research.get("baseline_backtest")
    alpha_variant = research.get("alpha_variant_backtest")
    if baseline and alpha_variant:
        findings.append(
            {
                "severity": "medium",
                "title": "The research stack looks stronger than the live deployment",
                "evidence": (
                    f"The baseline sleeve backtest shows {baseline.get('portfolio_cagr', 0.0):.2%} CAGR versus SPY {baseline.get('spy_cagr', 0.0):.2%}. "
                    f"The alpha variant shows {alpha_variant.get('net_cagr', 0.0):.2%} net CAGR with beta {alpha_variant.get('net_beta_vs_spy', 0.0):.2f}, "
                    f"or {alpha_variant.get('circuit_breaker_net_cagr', 0.0):.2%} with the circuit breaker."
                ),
                "implication": "The repo likely already contains stronger ideas than the live engine is expressing. The gap looks operational and selection-translation related, not purely idea scarcity.",
            }
        )

    return findings


def _build_recommendations(summary: dict[str, Any]) -> list[str]:
    recommendations: list[str] = []
    coverage = summary["artifact_coverage"]
    live = summary["live_performance"]
    broker_activity = summary["broker_activity"]
    research = summary["research_stack"]

    signal_coverage = coverage.get("signal_snapshot_fill_date_coverage")
    email_coverage = coverage.get("execution_email_fill_date_coverage")
    if signal_coverage is None or signal_coverage < 1.0 or email_coverage is None or email_coverage < 1.0:
        recommendations.append(
            "Build a canonical daily audit tape that stores signals, proposed trades, eligible trades, broker fills, and 1/5/10-day forward opportunity cost for every trade date."
        )

    beta = live.get("beta")
    upside_capture = live.get("upside_capture")
    downside_capture = live.get("downside_capture")
    if beta is not None and upside_capture is not None and downside_capture is not None and beta < 0.8:
        recommendations.append(
            "Separate the market-participation overlay from stock selection and enforce a target beta band, because the current live book is too defensive to beat SPY consistently."
        )

    if broker_activity.get("quick_flip_count", 0) >= 20:
        recommendations.append(
            "Add trade hysteresis: minimum holding period, rebalance thresholds, and stronger replacement criteria so the engine stops flipping names after one or two sessions."
        )

    alpha_variant = research.get("alpha_variant_backtest")
    if alpha_variant and alpha_variant.get("net_cagr") and alpha_variant.get("spy_cagr"):
        if alpha_variant["net_cagr"] > alpha_variant["spy_cagr"]:
            recommendations.append(
                "Promote the alpha-variant stack into a controlled A/B lane against the live engine and against a simple equal-weight signal baseline; the research edge needs a cleaner path into production."
            )

    recommendations.append(
        "Add per-sleeve attribution and rank-order diagnostics so each live rebalance can be decomposed into selection alpha, overlay effect, and implementation drag."
    )
    return recommendations


def _format_pct(value: float | None, *, signed: bool = False) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _format_ratio(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}x"


def _format_number(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _format_objective_value(label: str, value: float | None) -> str:
    lowered = label.lower()
    if value is None:
        return "N/A"
    if "capture" in lowered:
        return _format_ratio(value)
    if "cash ratio" in lowered:
        return _format_pct(value)
    if "return" in lowered:
        return _format_pct(value, signed=True)
    return _format_number(value)


def _build_markdown(summary: dict[str, Any]) -> str:
    live = summary["live_performance"]
    coverage = summary["artifact_coverage"]
    broker_activity = summary["broker_activity"]
    portfolio = summary["portfolio_structure"]
    research = summary["research_stack"]
    ic_monitor = summary.get("ic_monitor") or {}
    objective = summary["objective_scorecard"]
    contract = summary["objective_contract"]

    lines = [
        "# Engine Evaluation",
        "",
        f"- As of: {summary['as_of_date']}",
        f"- Repo root: `{summary['repo_root']}`",
        "",
        "## North Star",
        "",
        f"- Objective: {contract.get('north_star')}",
        f"- Benchmark: {contract.get('benchmark')}",
        f"- Target annualized return: {_format_pct(_parse_float(contract.get('annualized_return_target')))}",
        f"- Target annualized excess return vs benchmark: {_format_pct(_parse_float(contract.get('annualized_excess_return_target')))}",
        f"- Target Sharpe: {_format_number(_parse_float(contract.get('sharpe_target')))}",
        f"- Cash ceiling: {_format_pct(_parse_float(contract.get('cash_ceiling')))}",
        f"- Beta floor: {_format_number(_parse_float(contract.get('beta_floor')))}",
        f"- Upside capture floor: {_format_ratio(_parse_float(contract.get('upside_capture_floor')))}",
        f"- Scorecard status: {str(objective.get('overall_status') or '').upper()}",
        "",
        "## Objective Scorecard",
        "",
    ]

    for check in objective.get("checks") or []:
        lines.append(
            f"- {check['label']}: {str(check['status']).upper()} "
            f"(current={_format_objective_value(check['label'], check['current'])}, "
            f"target {check['comparison']} "
            f"{_format_objective_value(check['label'], check['target'])})"
            + (f". {check['note']}" if check.get("note") else "")
        )

    lines.extend(
        [
            "",
        "## Verdict",
        "",
        ]
    )

    for finding in summary["findings"]:
        lines.append(f"- [{finding['severity'].upper()}] {finding['title']}: {finding['evidence']} {finding['implication']}")

    lines.extend(
        [
            "",
            "## Live Performance",
            "",
            f"- Window: {live.get('window_start', 'N/A')} to {live.get('window_end', 'N/A')} ({live.get('n_return_days', 0)} aligned return days)",
            f"- Portfolio total return: {_format_pct(live.get('portfolio_total_return'), signed=True)}",
            f"- SPY total return: {_format_pct(live.get('benchmark_total_return'), signed=True)}",
            f"- Excess return: {_format_pct(live.get('excess_total_return'), signed=True)}",
            f"- Portfolio Sharpe: {_format_number(live.get('portfolio_sharpe'))}",
            f"- Benchmark Sharpe: {_format_number(live.get('benchmark_sharpe'))}",
            f"- Beta: {_format_number(live.get('beta'))}",
            f"- Alpha annualized: {_format_pct(live.get('alpha_annualized'), signed=True)}",
            f"- Information ratio: {_format_number(live.get('information_ratio'))}",
            f"- Upside capture: {_format_ratio(live.get('upside_capture'))}",
            f"- Downside capture: {_format_ratio(live.get('downside_capture'))}",
            f"- Max drawdown over window: {_format_pct(live.get('max_drawdown'), signed=True)}",
            "",
            "## Auditability",
            "",
            f"- Signal snapshot coverage on live fill dates: {_format_pct(coverage.get('signal_snapshot_fill_date_coverage'))}",
            f"- Execution email coverage on live fill dates: {_format_pct(coverage.get('execution_email_fill_date_coverage'))}",
            f"- Missing signal dates: {', '.join(coverage.get('signal_snapshot_missing_dates') or []) or 'None'}",
            f"- Missing execution-email dates: {', '.join(coverage.get('execution_email_missing_dates') or []) or 'None'}",
        ]
    )

    mismatches = coverage.get("telemetry_mismatches") or []
    if mismatches:
        lines.append(f"- Telemetry mismatches: {len(mismatches)} documented dates disagree between execution artifacts and broker fills.")
        for mismatch in mismatches[:5]:
            lines.append(
                f"- Mismatch {mismatch['trade_date']}: execution_email filled={mismatch['execution_email_filled_count']}, "
                f"submitted={mismatch['execution_email_submitted_count']}, broker filled={mismatch['broker_filled_orders']}, "
                f"status={mismatch.get('execution_status')}, plan_only={mismatch.get('plan_only')}."
            )
    else:
        lines.append("- Telemetry mismatches: none detected in overlapping dates.")

    lines.extend(
        [
            "",
            "## Execution",
            "",
            f"- Filled orders reviewed: {broker_activity.get('filled_orders_count', 0)} across {broker_activity.get('fill_days_count', 0)} fill dates",
            f"- Average fills per day: {broker_activity.get('avg_fills_per_day', 0.0):.2f}" if broker_activity.get("avg_fills_per_day") is not None else "- Average fills per day: N/A",
            f"- Average daily notional: ${broker_activity.get('avg_daily_notional', 0.0):,.2f}" if broker_activity.get("avg_daily_notional") is not None else "- Average daily notional: N/A",
            f"- Max daily notional: ${broker_activity.get('max_daily_notional', 0.0):,.2f}" if broker_activity.get("max_daily_notional") is not None else "- Max daily notional: N/A",
            f"- Quick flips within 5 calendar days: {broker_activity.get('quick_flip_count', 0)}",
            f"- Same-day flips: {broker_activity.get('same_day_flip_count', 0)}",
            f"- Next-day flips: {broker_activity.get('next_day_flip_count', 0)}",
        ]
    )

    if broker_activity.get("quick_flip_examples"):
        lines.append("- Quick-flip examples:")
        for example in broker_activity["quick_flip_examples"][:8]:
            lines.append(
                f"- {example['symbol']}: {example['from_side']} {example['from_date']} -> {example['to_side']} {example['to_date']} ({example['delta_days']}d)"
            )

    lines.extend(
        [
            "",
            "## Current Book",
            "",
            f"- Equity: ${portfolio.get('equity', 0.0):,.2f}" if portfolio.get("equity") is not None else "- Equity: N/A",
            f"- Cash: ${portfolio.get('cash', 0.0):,.2f}" if portfolio.get("cash") is not None else "- Cash: N/A",
            f"- Cash ratio: {_format_pct(portfolio.get('cash_ratio'))}",
            f"- Positions: {portfolio.get('positions_count', 0)}",
            f"- Top-5 concentration: {_format_pct(portfolio.get('top5_concentration'))}",
            "- Sector weights:",
        ]
    )
    for sector, weight in (portfolio.get("sector_weights") or {}).items():
        lines.append(f"- {sector}: {weight:.1%}")
    lines.append("- Largest positions:")
    for row in portfolio.get("top_positions") or []:
        lines.append(f"- {row['symbol']}: {row['weight']:.1%} of equity, sector={row['sector']}, unrealized={_format_pct(row['unrealized_plpc'])}")

    lines.extend(
        [
            "",
            "## Research Stack",
            "",
        ]
    )
    baseline = research.get("baseline_backtest")
    if baseline:
        lines.append(
            f"- Baseline sleeve backtest ({baseline['window_start']} to {baseline['window_end']}): "
            f"CAGR {baseline.get('portfolio_cagr', 0.0):.2%}, Sharpe {baseline.get('portfolio_sharpe', 0.0):.2f}, "
            f"max drawdown {baseline.get('portfolio_max_drawdown', 0.0):.2%} versus SPY CAGR {baseline.get('spy_cagr', 0.0):.2%}."
        )
    alpha_variant = research.get("alpha_variant_backtest")
    if alpha_variant:
        lines.append(
            f"- Alpha variant backtest ({alpha_variant['window_start']} to {alpha_variant['window_end']}): "
            f"net CAGR {alpha_variant.get('net_cagr', 0.0):.2%}, Sharpe {alpha_variant.get('net_sharpe', 0.0):.2f}, "
            f"beta {alpha_variant.get('net_beta_vs_spy', 0.0):.2f}; circuit-breaker net CAGR {alpha_variant.get('circuit_breaker_net_cagr', 0.0):.2%}."
        )
    worst_window = research.get("worst_window_full") or {}
    if worst_window:
        lines.append(
            f"- Worst full-exposure window: {worst_window.get('start_date')} to {worst_window.get('end_date')}, "
            f"max drawdown {_format_pct(_parse_float(worst_window.get('max_drawdown')), signed=True)}, CAGR {_format_pct(_parse_float(worst_window.get('cagr')), signed=True)}."
        )

    lines.extend(
        [
            "## Signal IC Monitor",
            "",
        ]
    )
    if not ic_monitor.get("available"):
        lines.append("- IC monitor artifacts unavailable — run research.ic_monitor")
    else:
        ic_summary = ic_monitor.get("summary") or {}
        sleeves = ic_summary.get("sleeves") or {}
        if not sleeves:
            lines.append("- No sleeve IC data available.")
        else:
            for sleeve_name in sorted(sleeves):
                sleeve_payload = sleeves.get(sleeve_name) or {}
                latest_ic = (sleeve_payload.get("latest_ic_by_horizon") or {}).get("1")
                rolling_20 = ((sleeve_payload.get("latest_rolling_ic_by_horizon") or {}).get("1") or {}).get("20")
                lines.append(
                    f"- {sleeve_name}: 20d rolling IC {_format_number(_parse_float(rolling_20))}, "
                    f"latest 1d IC {_format_number(_parse_float(latest_ic))}"
                )
        alerts = ic_summary.get("alerts") or []
        lines.append("- Active alerts:")
        if alerts:
            for alert in alerts:
                lines.append(f"- {alert}")
        else:
            lines.append("- none")
    lines.extend(
        [
            "",
        ]
    )
    lines.extend(
        [
            "",
            "## Recommendations",
            "",
        ]
    )
    for recommendation in summary["recommendations"]:
        lines.append(f"- {recommendation}")

    lines.append("")
    return "\n".join(lines)


def _build_audit_tape_rows(repo_root: Path, broker_activity: dict[str, Any]) -> list[dict[str, Any]]:
    signal_dates = {path.stem for path in (repo_root / "signals").glob("*.json")}
    email_dir = repo_root / "outputs" / "execution_email"
    email_dates = {
        path.name.split(".json")[0]
        for path in email_dir.glob("*.json")
        if ".empty." not in path.name and path.stem not in {"2000-01-01", "2099-01-01"}
    }
    fill_dates = set(broker_activity.get("fill_dates") or [])
    all_dates = sorted(signal_dates | email_dates | fill_dates)

    rows: list[dict[str, Any]] = []
    filled_orders_by_date = broker_activity.get("filled_orders_by_date") or {}
    buy_orders_by_date = broker_activity.get("buy_orders_by_date") or {}
    sell_orders_by_date = broker_activity.get("sell_orders_by_date") or {}
    daily_notional = broker_activity.get("daily_notional") or {}

    for trade_date in all_dates:
        execution_email_path = email_dir / f"{trade_date}.json"
        execution_email = _read_json(execution_email_path) if execution_email_path.exists() else {}
        email_fill_count = int(execution_email.get("orders_filled_count") or execution_email.get("orders_filled") or 0)
        email_submitted = int(execution_email.get("orders_submitted_count") or execution_email.get("submitted_count") or 0)
        email_intended = int(
            execution_email.get("planner_intended_trades_count")
            or execution_email.get("proposed_trades_intent_count")
            or execution_email.get("proposed_trades_intent")
            or 0
        )
        broker_fills = int(filled_orders_by_date.get(trade_date) or 0)
        rows.append(
            {
                "trade_date": trade_date,
                "signal_snapshot_present": trade_date in signal_dates,
                "execution_email_present": trade_date in email_dates,
                "execution_email_status": execution_email.get("execution_status") or "",
                "execution_email_plan_only": bool(execution_email.get("plan_only")) if execution_email else False,
                "execution_email_intended_trades": email_intended,
                "execution_email_submitted_orders": email_submitted,
                "execution_email_filled_orders": email_fill_count,
                "broker_filled_orders": broker_fills,
                "broker_buy_orders": int(buy_orders_by_date.get(trade_date) or 0),
                "broker_sell_orders": int(sell_orders_by_date.get(trade_date) or 0),
                "broker_filled_notional": daily_notional.get(trade_date) or 0.0,
                "telemetry_mismatch": email_fill_count != broker_fills if trade_date in email_dates else False,
            }
        )
    return rows


def build_engine_evaluation(repo_root: Path) -> dict[str, Any]:
    live_performance = _load_live_performance(repo_root)
    broker_activity = _load_broker_activity(repo_root)
    artifact_coverage = _load_artifact_coverage(repo_root, broker_activity) if broker_activity.get("available") else {}
    portfolio_structure = _load_portfolio_structure(repo_root)
    research_stack = _load_research_stack(repo_root)
    ic_monitor = _load_ic_monitor(repo_root)
    objective_contract = _load_objective_contract(repo_root)

    as_of_date = live_performance.get("window_end")
    if not as_of_date and portfolio_structure.get("available"):
        account_path = repo_root / "outputs" / "broker" / "posttrade_account_snapshot.json"
        account_payload = _read_json(account_path) if account_path.exists() else {}
        as_of_date = str(account_payload.get("trade_date") or "")
    if not as_of_date and broker_activity.get("fill_dates"):
        as_of_date = broker_activity["fill_dates"][-1]
    as_of_date = as_of_date or ""

    summary = {
        "as_of_date": as_of_date,
        "repo_root": str(repo_root),
        "live_performance": live_performance,
        "broker_activity": broker_activity,
        "artifact_coverage": artifact_coverage,
        "portfolio_structure": portfolio_structure,
        "research_stack": research_stack,
        "ic_monitor": ic_monitor,
        "objective_contract": objective_contract,
    }
    summary["objective_scorecard"] = _build_objective_scorecard(summary)
    summary["findings"] = _build_findings(summary)
    summary["recommendations"] = _build_recommendations(summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a dedicated engine evaluation report from live and research artifacts")
    parser.add_argument("--repo-root", default=".", help="Repository root")
    parser.add_argument("--output-dir", default="outputs/engine_evaluation", help="Output directory for evaluation artifacts")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    output_dir = (repo_root / args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    summary = build_engine_evaluation(repo_root)
    report_md = _build_markdown(summary)
    audit_tape_rows = _build_audit_tape_rows(repo_root, summary["broker_activity"])

    summary_path = output_dir / "summary.json"
    report_path = output_dir / "report.md"
    audit_tape_path = output_dir / "audit_tape.csv"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report_path.write_text(report_md, encoding="utf-8")
    fieldnames = [
        "trade_date",
        "signal_snapshot_present",
        "execution_email_present",
        "execution_email_status",
        "execution_email_plan_only",
        "execution_email_intended_trades",
        "execution_email_submitted_orders",
        "execution_email_filled_orders",
        "broker_filled_orders",
        "broker_buy_orders",
        "broker_sell_orders",
        "broker_filled_notional",
        "telemetry_mismatch",
    ]
    with audit_tape_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(audit_tape_rows)

    as_of_date = str(summary.get("as_of_date") or "").strip()
    if as_of_date:
        (output_dir / f"summary_{as_of_date}.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (output_dir / f"report_{as_of_date}.md").write_text(report_md, encoding="utf-8")

    print(f"[ENGINE_EVAL] wrote {summary_path}")
    print(f"[ENGINE_EVAL] wrote {report_path}")
    print(f"[ENGINE_EVAL] wrote {audit_tape_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
