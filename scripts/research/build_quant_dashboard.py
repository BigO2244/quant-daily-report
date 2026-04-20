from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path
from typing import Any

REPO_CODE_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_CODE_ROOT))

from scripts.build_portfolio_history import build_portfolio_history


STALE_THRESHOLD_HOURS = 36


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("$", "").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def _resolve_metric(snapshot: dict[str, Any] | list[Any] | None, *keys: str) -> float | None:
    if not isinstance(snapshot, dict):
        return None
    base = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else snapshot
    for key in keys:
        if key in base:
            value = _float_or_none(base.get(key))
            if value is not None:
                return value
    return None


def _resolve_positions_count(snapshot: dict[str, Any] | list[Any] | None) -> int | None:
    if snapshot is None:
        return None
    if isinstance(snapshot, list):
        return len(snapshot)
    positions_count = _int_or_none(snapshot.get("positions_count"))
    if positions_count is not None:
        return positions_count
    positions = snapshot.get("positions")
    if isinstance(positions, list):
        return len(positions)
    normalized = snapshot.get("normalized_positions")
    if isinstance(normalized, dict):
        return len(normalized)
    return None


def _parse_date(value: Any) -> dt.date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.date.fromisoformat(text[:10])
    except ValueError:
        return None


def _parse_datetime(value: Any) -> dt.datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _status_is_complete(value: Any) -> bool:
    status = str(value or "").strip().lower()
    return status in {"success", "ok", "pass", "no_action", "executed", "idempotent_replay"}


def _status_is_failure(value: Any) -> bool:
    status = str(value or "").strip().lower()
    if not status:
        return False
    return "fail" in status or status in {"error", "halted", "degraded"}


def _relative_str(repo_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(path)


def _iso_date_prefix(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _same_trade_date(value: Any, trade_date: str | None) -> bool:
    if not trade_date:
        return False
    return _iso_date_prefix(value) == str(trade_date)


def _path_trade_date(path: Path | None) -> str | None:
    if path is None:
        return None
    for token in reversed(path.stem.split("_")):
        if _parse_date(token) is not None:
            return token
    return None


def _latest_row_on_or_before(
    rows: list[dict[str, Any]],
    target_date: str | None,
    value_key: str,
) -> dict[str, Any] | None:
    if not target_date:
        return None
    latest: dict[str, Any] | None = None
    for row in rows:
        row_date = str(row.get("date") or "").strip()
        if not row_date or row_date > target_date:
            continue
        if row.get(value_key) is None:
            continue
        latest = row
    return latest


def _mean(values: list[float]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if not numeric:
        return None
    return sum(numeric) / float(len(numeric))


def _sample_stddev(values: list[float]) -> float | None:
    numeric = [float(value) for value in values if value is not None]
    if len(numeric) < 2:
        return None
    mean_value = _mean(numeric)
    if mean_value is None:
        return None
    variance = sum((value - mean_value) ** 2 for value in numeric) / float(len(numeric) - 1)
    return math.sqrt(variance)


def _regress_alpha_beta(port_returns: list[float], bench_returns: list[float]) -> tuple[float | None, float | None]:
    if len(port_returns) != len(bench_returns) or len(port_returns) < 2:
        return None, None
    mean_port = _mean(port_returns)
    mean_bench = _mean(bench_returns)
    if mean_port is None or mean_bench is None:
        return None, None
    variance_bench = sum((value - mean_bench) ** 2 for value in bench_returns) / float(len(bench_returns) - 1)
    if variance_bench <= 0:
        return None, None
    covariance = sum(
        (bench - mean_bench) * (port - mean_port)
        for port, bench in zip(port_returns, bench_returns)
    ) / float(len(port_returns) - 1)
    beta = covariance / variance_bench
    alpha_daily = mean_port - beta * mean_bench
    return beta, alpha_daily * 252.0


def _correlation(port_returns: list[float], bench_returns: list[float]) -> float | None:
    if len(port_returns) != len(bench_returns) or len(port_returns) < 2:
        return None
    port_std = _sample_stddev(port_returns)
    bench_std = _sample_stddev(bench_returns)
    if port_std in (None, 0) or bench_std in (None, 0):
        return None
    mean_port = _mean(port_returns)
    mean_bench = _mean(bench_returns)
    if mean_port is None or mean_bench is None:
        return None
    covariance = sum(
        (bench - mean_bench) * (port - mean_port)
        for port, bench in zip(port_returns, bench_returns)
    ) / float(len(port_returns) - 1)
    return covariance / (port_std * bench_std)


def _capture_ratio(port_returns: list[float], bench_returns: list[float], *, positive: bool) -> float | None:
    selected = [
        (port, bench)
        for port, bench in zip(port_returns, bench_returns)
        if (bench > 0 if positive else bench < 0)
    ]
    if not selected:
        return None
    mean_port = _mean([port for port, _ in selected])
    mean_bench = _mean([bench for _, bench in selected])
    if mean_port is None or mean_bench in (None, 0):
        return None
    return mean_port / mean_bench


def _hit_rate(port_returns: list[float], bench_returns: list[float], *, positive: bool) -> float | None:
    selected = [
        (port, bench)
        for port, bench in zip(port_returns, bench_returns)
        if (bench > 0 if positive else bench < 0)
    ]
    if not selected:
        return None
    hits = sum(1 for port, bench in selected if port > bench)
    return hits / float(len(selected))


def _find_latest_dated_file(root: Path, pattern: str, max_date: str | None = None) -> Path | None:
    matches: list[tuple[str, Path]] = []
    for path in root.glob(pattern):
        trade_date = _path_trade_date(path)
        if trade_date is None:
            continue
        if max_date and trade_date > max_date:
            continue
        matches.append((trade_date, path))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


def _build_attribution_summary(performance: dict[str, Any]) -> dict[str, Any]:
    out = {
        "source_name": performance.get("source_name"),
        "window_start": None,
        "window_end": None,
        "n_days": 0,
        "cumulative_port_return": None,
        "cumulative_spy_return": None,
        "cumulative_alpha": None,
        "beta_since": None,
        "alpha_ann_since": None,
        "beta_recent_20d": None,
        "alpha_ann_recent_20d": None,
        "tracking_error_ann": None,
        "info_ratio": None,
        "correlation": None,
        "upside_capture": None,
        "downside_capture": None,
        "up_market_hit_rate": None,
        "down_market_hit_rate": None,
        "positive_excess_days": 0,
        "negative_excess_days": 0,
        "bench_up_days": 0,
        "bench_down_days": 0,
        "best_relative_day": None,
        "worst_relative_day": None,
        "reason": None,
    }

    nav_map = {
        str(row.get("date") or "").strip(): row.get("equity")
        for row in performance.get("nav_rows", [])
        if str(row.get("date") or "").strip() and row.get("equity") not in (None, 0)
    }
    benchmark_map = {
        str(row.get("date") or "").strip(): row.get("spy_close")
        for row in performance.get("benchmark_rows", [])
        if str(row.get("date") or "").strip() and row.get("spy_close") not in (None, 0)
    }
    common_dates = sorted(set(nav_map) & set(benchmark_map))
    if len(common_dates) < 2:
        out["reason"] = "insufficient_overlap"
        return out

    port_returns: list[float] = []
    bench_returns: list[float] = []
    dated_spreads: list[dict[str, Any]] = []
    for prev_date, curr_date in zip(common_dates[:-1], common_dates[1:]):
        prev_port = nav_map.get(prev_date)
        curr_port = nav_map.get(curr_date)
        prev_bench = benchmark_map.get(prev_date)
        curr_bench = benchmark_map.get(curr_date)
        if prev_port in (None, 0) or curr_port is None or prev_bench in (None, 0) or curr_bench is None:
            continue
        port_ret = (float(curr_port) / float(prev_port)) - 1.0
        bench_ret = (float(curr_bench) / float(prev_bench)) - 1.0
        spread = port_ret - bench_ret
        port_returns.append(port_ret)
        bench_returns.append(bench_ret)
        dated_spreads.append(
            {
                "date": curr_date,
                "port_return": port_ret,
                "benchmark_return": bench_ret,
                "spread": spread,
            }
        )

    if len(port_returns) < 2:
        out["reason"] = "insufficient_aligned_returns"
        return out

    out["window_start"] = common_dates[0]
    out["window_end"] = common_dates[-1]
    out["n_days"] = len(port_returns)
    out["cumulative_port_return"] = (float(nav_map[common_dates[-1]]) / float(nav_map[common_dates[0]])) - 1.0
    out["cumulative_spy_return"] = (float(benchmark_map[common_dates[-1]]) / float(benchmark_map[common_dates[0]])) - 1.0
    out["cumulative_alpha"] = out["cumulative_port_return"] - out["cumulative_spy_return"]

    beta_since, alpha_ann_since = _regress_alpha_beta(port_returns, bench_returns)
    out["beta_since"] = beta_since
    out["alpha_ann_since"] = alpha_ann_since

    recent_window = 20
    if len(port_returns) >= recent_window:
        beta_recent, alpha_recent = _regress_alpha_beta(port_returns[-recent_window:], bench_returns[-recent_window:])
        out["beta_recent_20d"] = beta_recent
        out["alpha_ann_recent_20d"] = alpha_recent

    excess_returns = [port - bench for port, bench in zip(port_returns, bench_returns)]
    excess_std = _sample_stddev(excess_returns)
    excess_mean = _mean(excess_returns)
    if excess_std not in (None, 0):
        out["tracking_error_ann"] = excess_std * math.sqrt(252.0)
        out["info_ratio"] = (
            (excess_mean / excess_std) * math.sqrt(252.0)
            if excess_mean is not None
            else None
        )
    out["correlation"] = _correlation(port_returns, bench_returns)
    out["upside_capture"] = _capture_ratio(port_returns, bench_returns, positive=True)
    out["downside_capture"] = _capture_ratio(port_returns, bench_returns, positive=False)
    out["up_market_hit_rate"] = _hit_rate(port_returns, bench_returns, positive=True)
    out["down_market_hit_rate"] = _hit_rate(port_returns, bench_returns, positive=False)
    out["positive_excess_days"] = sum(1 for spread in excess_returns if spread > 0)
    out["negative_excess_days"] = sum(1 for spread in excess_returns if spread < 0)
    out["bench_up_days"] = sum(1 for bench in bench_returns if bench > 0)
    out["bench_down_days"] = sum(1 for bench in bench_returns if bench < 0)
    if dated_spreads:
        out["best_relative_day"] = max(dated_spreads, key=lambda item: item["spread"])
        out["worst_relative_day"] = min(dated_spreads, key=lambda item: item["spread"])
    return out


def _load_contribution_snapshot(repo_root: Path, max_date: str | None) -> dict[str, Any]:
    out = {
        "asof_date": None,
        "source_mode": None,
        "age_days": None,
        "ticker_rows": 0,
        "sleeve_rows": 0,
        "net_contribution": None,
        "positive_contributors": 0,
        "negative_contributors": 0,
        "top_winners": [],
        "top_laggards": [],
        "top_sleeves": [],
        "paths": {"tickers": "", "sleeves": ""},
    }
    perf_dir = repo_root / "outputs" / "perf"
    tickers_path = _find_latest_dated_file(perf_dir, "contribution_tickers_*.csv", max_date=max_date)
    if tickers_path is None:
        return out

    asof_date = _path_trade_date(tickers_path)
    sleeves_path = (
        perf_dir / f"contribution_sleeves_{asof_date}.csv"
        if asof_date and (perf_dir / f"contribution_sleeves_{asof_date}.csv").exists()
        else _find_latest_dated_file(perf_dir, "contribution_sleeves_*.csv", max_date=asof_date)
    )

    ticker_rows: list[dict[str, Any]] = []
    with tickers_path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            ticker = str(row.get("ticker") or "").strip().upper()
            contribution = _float_or_none(row.get("contribution"))
            if not ticker or contribution is None:
                continue
            ticker_rows.append(
                {
                    "ticker": ticker,
                    "weight_start": _float_or_none(row.get("weight_start")),
                    "return": _float_or_none(row.get("return")),
                    "contribution": contribution,
                    "sleeve": str(row.get("sleeve") or "core").strip() or "core",
                }
            )

    sleeve_rows: list[dict[str, Any]] = []
    if sleeves_path is not None:
        with sleeves_path.open("r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                contribution = _float_or_none(row.get("contribution"))
                if contribution is None:
                    continue
                sleeve_rows.append(
                    {
                        "sleeve": str(row.get("sleeve") or "core").strip() or "core",
                        "weight_start": _float_or_none(row.get("weight_start")),
                        "sleeve_return": _float_or_none(row.get("sleeve_return")),
                        "contribution": contribution,
                    }
                )

    out["asof_date"] = asof_date
    out["source_mode"] = "governed_historical" if asof_date and max_date and asof_date < max_date else "current"
    out["paths"] = {
        "tickers": str(tickers_path),
        "sleeves": str(sleeves_path) if sleeves_path is not None else "",
    }
    out["ticker_rows"] = len(ticker_rows)
    out["sleeve_rows"] = len(sleeve_rows)
    out["net_contribution"] = sum(row["contribution"] for row in ticker_rows) if ticker_rows else None
    out["positive_contributors"] = sum(1 for row in ticker_rows if row["contribution"] > 0)
    out["negative_contributors"] = sum(1 for row in ticker_rows if row["contribution"] < 0)
    out["top_winners"] = sorted(ticker_rows, key=lambda row: row["contribution"], reverse=True)[:3]
    out["top_laggards"] = sorted(ticker_rows, key=lambda row: row["contribution"])[:3]
    out["top_sleeves"] = sorted(sleeve_rows, key=lambda row: abs(row["contribution"]), reverse=True)[:3]

    if asof_date and max_date and _parse_date(asof_date) and _parse_date(max_date):
        out["age_days"] = (_parse_date(max_date) - _parse_date(asof_date)).days
    return out


def _build_position_diagnostics(
    *,
    broker_snapshot: dict[str, Any],
    broker_day_snapshot: dict[str, Any] | None,
    orders_today: list[dict[str, Any]],
    positions_fallback: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    equity = _float_or_none(broker_snapshot.get("equity"))
    cash = _float_or_none(broker_snapshot.get("cash"))
    positions = (
        broker_day_snapshot.get("positions_current")
        if isinstance(broker_day_snapshot, dict) and isinstance(broker_day_snapshot.get("positions_current"), list)
        else positions_fallback or []
    )
    if equity in (None, 0) and positions:
        total_mv = sum(_float_or_none(p.get("market_value")) or 0.0 for p in positions if isinstance(p, dict))
        if total_mv > 0:
            equity = total_mv + (cash or 0.0)
    weights: list[dict[str, Any]] = []
    if equity not in (None, 0):
        for item in positions:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or "").strip().upper()
            market_value = _float_or_none(item.get("market_value"))
            if not symbol or market_value is None:
                continue
            weights.append(
                {
                    "ticker": symbol,
                    "weight": abs(market_value) / float(equity),
                    "market_value": market_value,
                    "unrealized_plpc": _float_or_none(item.get("unrealized_plpc")),
                }
            )
    weights.sort(key=lambda row: row["weight"], reverse=True)

    traded_notional = 0.0
    for order in orders_today:
        notional = _float_or_none(order.get("notional"))
        if notional is None:
            filled_price = _float_or_none(order.get("filled_avg_price")) or _float_or_none(order.get("limit_price")) or _float_or_none(order.get("price"))
            qty = _float_or_none(order.get("filled_qty")) or _float_or_none(order.get("qty"))
            if filled_price is not None and qty is not None:
                notional = filled_price * qty
        if notional is not None:
            traded_notional += abs(notional)

    current_cash_ratio = (
        (float(cash) / float(equity))
        if cash is not None and equity not in (None, 0)
        else None
    )
    return {
        "current_cash_ratio": current_cash_ratio,
        "capital_deployed": (1.0 - current_cash_ratio) if current_cash_ratio is not None else None,
        "largest_position_weight": weights[0]["weight"] if weights else None,
        "top5_concentration": sum(item["weight"] for item in weights[:5]) if weights else None,
        "positions_count": len(weights),
        "executed_turnover_pct": (traded_notional / float(equity)) if traded_notional and equity not in (None, 0) else None,
        "top_positions": weights[:5],
    }


def _build_edge_diagnostics(
    *,
    attribution: dict[str, Any],
    position_diag: dict[str, Any],
    current_benchmark_return: float | None = None,
) -> dict[str, Any]:
    diagnostics = {
        **position_diag,
        "signals": [],
        "recommendations": [],
    }
    signals: list[dict[str, Any]] = []
    recommendations: list[dict[str, Any]] = []

    current_cash_ratio = position_diag.get("current_cash_ratio")
    cumulative_spy = attribution.get("cumulative_spy_return")
    benchmark_reference = (
        current_benchmark_return
        if current_benchmark_return not in (None, 0)
        else cumulative_spy
    )
    if current_cash_ratio is not None:
        drag_estimate = (
            current_cash_ratio * benchmark_reference
            if benchmark_reference not in (None, 0)
            else None
        )
        signals.append(
            {
                "label": "Market Participation",
                "status": "warning" if current_cash_ratio >= 0.12 and (benchmark_reference or 0) > 0 else "pass",
                "detail": (
                    f"{current_cash_ratio:.1%} cash leaves {1.0 - current_cash_ratio:.1%} deployed."
                    + (
                        f" That implies roughly {drag_estimate:.2%} of benchmark participation drag over the active comparison horizon."
                        if drag_estimate is not None and drag_estimate > 0
                        else ""
                    )
                ),
            }
        )
        if current_cash_ratio >= 0.12 and (benchmark_reference or 0) > 0:
            recommendations.append(
                {
                    "label": "Reduce Cash Drag",
                    "detail": "Test lower cash floors or higher gross exposure in supportive tapes so the strategy participates when the market gaps higher.",
                }
            )

    upside_capture = attribution.get("upside_capture")
    bench_up_days = int(attribution.get("bench_up_days") or 0)
    if upside_capture is not None and bench_up_days >= 3:
        signals.append(
            {
                "label": "Upside Capture",
                "status": "warning" if upside_capture < 0.8 else "pass",
                "detail": f"Captured {upside_capture:.2f}x of SPY on {bench_up_days} up-market sessions.",
            }
        )
        if upside_capture < 0.8:
            recommendations.append(
                {
                    "label": "Raise Upside Capture",
                    "detail": "Review entry filters, position sizing, and regime overlays to avoid carrying a half-beta book during market upswings.",
                }
            )

    alpha_ann = attribution.get("alpha_ann_since")
    info_ratio = attribution.get("info_ratio")
    if alpha_ann is not None or info_ratio is not None:
        status = "pass"
        if (alpha_ann or 0) < 0 or (info_ratio or 0) < 0:
            status = "warning"
        signals.append(
            {
                "label": "Selection Quality",
                "status": status,
                "detail": (
                    f"Annualized alpha {alpha_ann:.2%}" if alpha_ann is not None else "Annualized alpha unavailable"
                )
                + (
                    f"; information ratio {info_ratio:.2f}."
                    if info_ratio is not None
                    else "."
                ),
            }
        )
        if (alpha_ann or 0) < 0 or (info_ratio or 0) < 0:
            recommendations.append(
                {
                    "label": "Re-test Stock Selection",
                    "detail": "Compare the live book against simpler baselines such as equal-weight top-N, sector-neutral top-N, and slower rebalance variants to isolate whether the edge is in ranking, timing, or risk overlay.",
                }
            )

    beta_since = attribution.get("beta_since")
    cumulative_alpha = attribution.get("cumulative_alpha")
    if beta_since is not None:
        beta_status = "pass"
        if (
            ((cumulative_spy is not None and cumulative_spy > 0) or (upside_capture is not None and upside_capture < 0.8))
            and beta_since < 0.75
        ):
            beta_status = "warning"
        elif cumulative_alpha is not None and cumulative_alpha < 0 and beta_since > 1.25:
            beta_status = "warning"
        signals.append(
            {
                "label": "Beta Alignment",
                "status": beta_status,
                "detail": f"Portfolio beta is {beta_since:.2f} versus SPY over the aligned window.",
            }
        )
        if beta_status == "warning":
            recommendations.append(
                {
                    "label": "Align Beta With Intent",
                    "detail": "Decide whether the engine should target benchmark-relative participation or absolute-return defensiveness, then tune exposure overlays and cash policy to match that objective.",
                }
            )

    largest_position = position_diag.get("largest_position_weight")
    top5_concentration = position_diag.get("top5_concentration")
    if largest_position is not None or top5_concentration is not None:
        concentration_status = "pass"
        if (largest_position or 0) > 0.12 or (top5_concentration or 0) > 0.45:
            concentration_status = "warning"
        signals.append(
            {
                "label": "Concentration",
                "status": concentration_status,
                "detail": (
                    f"Largest position {(largest_position or 0):.1%}; top 5 account for {(top5_concentration or 0):.1%} of equity."
                ),
            }
        )
        if concentration_status == "warning":
            recommendations.append(
                {
                    "label": "Check Concentration Risk",
                    "detail": "Run scenario tests with tighter single-name or top-5 caps to determine whether a small set of positions is dominating outcome variance.",
                }
            )

    executed_turnover = position_diag.get("executed_turnover_pct")
    if executed_turnover is not None:
        signals.append(
            {
                "label": "Rebalance Intensity",
                "status": "warning" if executed_turnover > 0.15 and (cumulative_alpha or 0) < 0 else "pass",
                "detail": f"Today's filled notional was about {executed_turnover:.1%} of current equity.",
            }
        )
        if executed_turnover > 0.15 and (cumulative_alpha or 0) < 0:
            recommendations.append(
                {
                    "label": "Lower Churn",
                    "detail": "Test wider rebalance bands, slower refresh cadence, and trade-netting rules to see whether turnover is eroding edge faster than it improves signal responsiveness.",
                }
            )

    diagnostics["signals"] = signals
    diagnostics["recommendations"] = recommendations
    return diagnostics


def _load_latest_pointer(repo_root: Path) -> dict[str, Any]:
    for path in (repo_root / "outputs/latest_run.json", repo_root / "outputs/latest.json"):
        payload = _read_json(path)
        if isinstance(payload, dict):
            return payload
    return {}


def _resolve_run_root(repo_root: Path, explicit_run_root: str | None) -> Path | None:
    if explicit_run_root:
        path = Path(explicit_run_root)
        return path if path.is_absolute() else repo_root / path
    latest = _load_latest_pointer(repo_root)
    run_root = latest.get("run_root") or latest.get("path")
    if run_root:
        path = Path(str(run_root))
        return path if path.is_absolute() else repo_root / path
    runs_dir = repo_root / "outputs/runs"
    if not runs_dir.exists():
        return None
    run_roots = [path for path in runs_dir.iterdir() if path.is_dir()]
    if not run_roots:
        return None
    return max(run_roots, key=lambda path: path.stat().st_mtime)


def _latest_glob(root: Path, pattern: str) -> Path | None:
    matches = [path for path in root.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def _find_artifact(repo_root: Path, run_root: Path | None, trade_date: str | None, names: list[str]) -> Path | None:
    search_roots: list[Path] = []
    if run_root is not None:
        search_roots.extend([run_root, run_root / "broker"])
    search_roots.append(repo_root / "outputs/broker")
    search_roots.append(repo_root / "outputs")

    for root in search_roots:
        if not root.exists():
            continue
        for name in names:
            direct = root / name
            if direct.exists():
                return direct
            if trade_date:
                dated = _latest_glob(root, f"*{trade_date}*{name}")
                if dated is not None:
                    return dated
    return None


def _load_operator_summary(repo_root: Path, run_root: Path | None) -> dict[str, Any]:
    candidates = []
    if run_root is not None:
        candidates.append(run_root / "operator_summary.json")
    candidates.append(repo_root / "outputs/latest_operator_summary.json")
    for path in candidates:
        payload = _read_json(path)
        if isinstance(payload, dict):
            return payload
    return {}


def _load_trading_day_summary(repo_root: Path, run_root: Path | None) -> dict[str, Any]:
    candidates = []
    if run_root is not None:
        candidates.append(run_root / "trading_day_summary.json")
    candidates.append(repo_root / "outputs/latest_trading_day_summary.json")
    candidates.append(repo_root / "outputs/trading_day_summary.json")
    for path in candidates:
        payload = _read_json(path)
        if isinstance(payload, dict):
            return payload
    return {}


def _derive_trade_date(
    repo_root: Path,
    run_root: Path | None,
    operator_summary: dict[str, Any],
    trading_day_summary: dict[str, Any],
    explicit: str | None,
) -> str:
    if explicit:
        return explicit
    latest = _load_latest_pointer(repo_root)
    meta = _read_json(run_root / "meta.json") if run_root is not None else None
    for candidate in (
        operator_summary.get("trade_date"),
        latest.get("trade_date"),
        latest.get("report_date"),
        meta.get("report_date") if isinstance(meta, dict) else None,
        trading_day_summary.get("trade_date"),
    ):
        if candidate:
            return str(candidate)
    return dt.date.today().isoformat()


def _derive_trust_level(
    operator_summary: dict[str, Any],
    pretrade_ok: bool,
    posttrade_ok: bool,
    recon_status: str | None,
) -> str:
    status = str(recon_status or "").strip().upper()
    authoritative = bool(operator_summary.get("broker_authoritative_state"))
    if authoritative and pretrade_ok and posttrade_ok and status in {"", "PASS", "WARN", "SELF_HEAL", "OK"}:
        return "HIGH"
    if authoritative or pretrade_ok or posttrade_ok:
        return "MEDIUM"
    return "LOW"


def _series_point(date_text: str, value: float | None) -> dict[str, Any]:
    return {"date": date_text, "value": value}


def build_dashboard_payload(repo_root: Path, *, run_root_arg: str | None = None, trade_date_arg: str | None = None) -> dict[str, Any]:
    run_root = _resolve_run_root(repo_root, run_root_arg)
    operator_summary = _load_operator_summary(repo_root, run_root)
    trading_day_summary = _load_trading_day_summary(repo_root, run_root)
    trade_date = _derive_trade_date(repo_root, run_root, operator_summary, trading_day_summary, trade_date_arg)
    latest = _load_latest_pointer(repo_root)

    pretrade_account_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        ["pretrade_account_snapshot.json", f"pretrade_account_snapshot_{trade_date}.json"],
    )
    pretrade_positions_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        ["pretrade_positions_snapshot.json", "pretrade_positions.json", f"pretrade_positions_{trade_date}.json"],
    )
    posttrade_account_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        [
            "posttrade_account_snapshot.json",
            "postsell_account_snapshot.json",
            f"posttrade_account_snapshot_{trade_date}.json",
        ],
    )
    posttrade_positions_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        ["posttrade_positions_snapshot.json", "posttrade_positions.json", f"posttrade_positions_{trade_date}.json"],
    )
    recon_posttrade_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        ["recon_posttrade.json", f"recon_posttrade_{trade_date}.json"],
    )
    recon_pretrade_path = _find_artifact(
        repo_root,
        run_root,
        trade_date,
        ["recon_pretrade.json", f"recon_pretrade_{trade_date}.json"],
    )

    pretrade_account = _read_json(pretrade_account_path) if pretrade_account_path else None
    pretrade_positions = _read_json(pretrade_positions_path) if pretrade_positions_path else None
    posttrade_account = _read_json(posttrade_account_path) if posttrade_account_path else None
    posttrade_positions = _read_json(posttrade_positions_path) if posttrade_positions_path else None
    recon_posttrade = _read_json(recon_posttrade_path) if recon_posttrade_path else None
    recon_pretrade = _read_json(recon_pretrade_path) if recon_pretrade_path else None

    pretrade_ok = bool(
        operator_summary.get("broker_pretrade_snapshot_ok")
        or pretrade_account is not None
        or pretrade_positions is not None
    )
    posttrade_ok = bool(
        operator_summary.get("broker_posttrade_snapshot_ok")
        or posttrade_account is not None
        or posttrade_positions is not None
    )

    pretrade_cash = _resolve_metric(pretrade_account, "cash")
    pretrade_equity = _resolve_metric(pretrade_account, "equity", "portfolio_value")
    pretrade_buying_power = _resolve_metric(pretrade_account, "buying_power", "daytrading_buying_power")
    posttrade_cash = _resolve_metric(posttrade_account, "cash")
    posttrade_equity = _resolve_metric(posttrade_account, "equity", "portfolio_value")

    recon_status = (
        operator_summary.get("post_execution_recon_status")
        or (recon_posttrade.get("verdict") if isinstance(recon_posttrade, dict) else None)
        or (recon_posttrade.get("drift_status") if isinstance(recon_posttrade, dict) else None)
    )

    trust_level = _derive_trust_level(operator_summary, pretrade_ok, posttrade_ok, recon_status)
    authoritative = bool(operator_summary.get("broker_authoritative_state"))

    pretrade_positions_count = _resolve_positions_count(pretrade_positions)
    posttrade_positions_count = _resolve_positions_count(posttrade_positions)

    payload = {
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "tradeDate": trade_date,
        "runId": str(operator_summary.get("run_id") or latest.get("run_id") or ""),
        "runRoot": str(run_root) if run_root is not None else "",
        "broker": {
            "authoritativeState": authoritative,
            "authoritativeMessage": (
                "Today's run used broker-authoritative state."
                if authoritative
                else "Broker-authoritative post-trade state was not confirmed."
            ),
            "trustLevel": trust_level,
            "pretrade": {
                "snapshotOk": pretrade_ok,
                "status": operator_summary.get("pretrade_status") or "UNKNOWN",
                "positionsCount": (
                    pretrade_positions_count
                    if pretrade_positions_count is not None
                    else trading_day_summary.get("pretrade_positions_count")
                ),
                "cash": (
                    pretrade_cash
                    if pretrade_cash is not None
                    else _float_or_none(trading_day_summary.get("pretrade_cash"))
                    if trading_day_summary.get("pretrade_cash") is not None
                    else _float_or_none(operator_summary.get("broker_preflight_cash"))
                ),
                "equity": (
                    pretrade_equity
                    if pretrade_equity is not None
                    else _float_or_none(trading_day_summary.get("pretrade_equity"))
                    if trading_day_summary.get("pretrade_equity") is not None
                    else _float_or_none(operator_summary.get("broker_preflight_equity"))
                ),
                "buyingPower": (
                    pretrade_buying_power
                    if pretrade_buying_power is not None
                    else _float_or_none(trading_day_summary.get("pretrade_buying_power"))
                    if trading_day_summary.get("pretrade_buying_power") is not None
                    else _float_or_none(operator_summary.get("broker_preflight_buying_power"))
                ),
                "restrictionFlags": operator_summary.get("broker_preflight_restriction_flags") or [],
                "warningFlags": operator_summary.get("broker_preflight_warning_flags") or [],
            },
            "posttrade": {
                "snapshotOk": posttrade_ok,
                "reconStatus": recon_status or "UNKNOWN",
                "positionsCount": (
                    posttrade_positions_count
                    if posttrade_positions_count is not None
                    else trading_day_summary.get("posttrade_positions_count")
                ),
                "cash": (
                    posttrade_cash
                    if posttrade_cash is not None
                    else _float_or_none(trading_day_summary.get("posttrade_cash"))
                ),
                "equity": (
                    posttrade_equity
                    if posttrade_equity is not None
                    else _float_or_none(trading_day_summary.get("posttrade_equity"))
                ),
                "repairSuggestions": operator_summary.get("repair_suggestions") or (
                    recon_posttrade.get("repair_suggestions") if isinstance(recon_posttrade, dict) else []
                ) or [],
                "affectedSymbols": operator_summary.get("affected_symbols") or (
                    recon_posttrade.get("affected_symbols") if isinstance(recon_posttrade, dict) else []
                ) or [],
            },
            "delta": {
                "positionsCount": (
                    posttrade_positions_count - pretrade_positions_count
                    if posttrade_positions_count is not None and pretrade_positions_count is not None
                    else trading_day_summary.get("positions_count_delta")
                    if trading_day_summary.get("positions_count_delta") is not None
                    else (
                        trading_day_summary.get("posttrade_positions_count") - trading_day_summary.get("pretrade_positions_count")
                        if trading_day_summary.get("posttrade_positions_count") is not None
                        and trading_day_summary.get("pretrade_positions_count") is not None
                        else None
                    )
                ),
                "cash": (
                    round(posttrade_cash - pretrade_cash, 2)
                    if pretrade_cash is not None and posttrade_cash is not None
                    else _float_or_none(trading_day_summary.get("cash_delta"))
                ),
                "equity": (
                    round(posttrade_equity - pretrade_equity, 2)
                    if pretrade_equity is not None and posttrade_equity is not None
                    else _float_or_none(trading_day_summary.get("equity_delta"))
                ),
            },
            "paths": {
                "pretradeAccount": str(pretrade_account_path) if pretrade_account_path else "",
                "pretradePositions": str(pretrade_positions_path) if pretrade_positions_path else "",
                "posttradeAccount": str(posttrade_account_path) if posttrade_account_path else "",
                "posttradePositions": str(posttrade_positions_path) if posttrade_positions_path else "",
                "reconPretrade": str(recon_pretrade_path) if recon_pretrade_path else "",
                "reconPosttrade": str(recon_posttrade_path) if recon_posttrade_path else "",
                "tradingDaySummary": (
                    str(run_root / "trading_day_summary.json")
                    if run_root is not None and (run_root / "trading_day_summary.json").exists()
                    else str(repo_root / "outputs/trading_day_summary.json")
                ),
            },
            "pretradeReconDecision": (
                recon_pretrade.get("reconciliation_decision") if isinstance(recon_pretrade, dict) else None
            ),
        },
    }
    return payload


class DashboardBuilder:
    def __init__(
        self,
        repo_root: Path | str,
        *,
        run_root_arg: str | None = None,
        trade_date_arg: str | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.run_root_arg = run_root_arg
        self.trade_date_arg = trade_date_arg
        self._now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)

    def _find_latest_execution_payload(
        self,
        trade_date: str | None,
        *,
        run_id: str | None = None,
    ) -> tuple[str | None, dict[str, Any]]:
        candidates: list[Path] = []
        if run_id:
            run_root = self.repo_root / "outputs" / "runs" / str(run_id)
            candidates.extend(
                [
                    run_root / "operator_summary.json",
                    run_root / "execution_results.json",
                    run_root / "execution_payload.json",
                ]
            )

        if trade_date:
            candidates.append(self.repo_root / "outputs" / "execution_email" / f"{trade_date}.json")

        for path in candidates:
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            out = dict(payload)
            if path.name == "operator_summary.json":
                run_root = path.parent
                execution_results = _read_json(run_root / "execution_results.json")
                if isinstance(execution_results, dict):
                    out.update({key: value for key, value in execution_results.items() if value is not None})
            if out.get("execution_status") is None:
                out["execution_status"] = out.get("status") or out.get("pretrade_status")
            if out.get("status") is None and out.get("execution_status") is not None:
                out["status"] = out.get("execution_status")
            return _relative_str(self.repo_root, path), out

        return None, {}

    def _context(self) -> dict[str, Any]:
        run_root = _resolve_run_root(self.repo_root, self.run_root_arg)
        latest_run = _read_json(self.repo_root / "outputs/latest_run.json")
        latest_meta = _read_json(self.repo_root / "outputs/latest.json")
        latest = latest_run if isinstance(latest_run, dict) else {}
        if isinstance(latest_meta, dict):
            merged = dict(latest_meta)
            merged.update(latest)
            latest = merged
        operator_summary = _load_operator_summary(self.repo_root, run_root)
        trading_day_summary = _load_trading_day_summary(self.repo_root, run_root)
        trade_date = _derive_trade_date(
            self.repo_root,
            run_root,
            operator_summary,
            trading_day_summary,
            self.trade_date_arg,
        )
        return {
            "run_root": run_root,
            "latest": latest,
            "operator_summary": operator_summary,
            "trading_day_summary": trading_day_summary,
            "trade_date": trade_date,
        }

    def _normalize_broker_snapshot(self, path: Path, payload: dict[str, Any], trust_level: str, mode: str) -> tuple[dict[str, Any], str]:
        account = payload.get("account") if isinstance(payload.get("account"), dict) else payload
        cash = _float_or_none(account.get("cash"))
        equity = _float_or_none(account.get("equity") or account.get("portfolio_value"))
        last_equity = _float_or_none(account.get("last_equity"))
        buying_power = _float_or_none(account.get("buying_power") or account.get("daytrading_buying_power"))
        market_value = _float_or_none(payload.get("market_value"))
        if market_value is None and cash is not None and equity is not None:
            market_value = round(equity - cash, 2)

        positions_count = None
        if "posttrade" in path.name or "postsell" in path.name:
            positions_path = path.with_name("posttrade_positions.json")
            positions_count = _resolve_positions_count(_read_json(positions_path))
        elif "pretrade" in path.name:
            positions_path = path.with_name("pretrade_positions.json")
            positions_count = _resolve_positions_count(_read_json(positions_path))
        else:
            positions_count = _int_or_none(payload.get("positions_count"))

        snapshot = {
            "portfolio_value": equity,
            "cash": cash,
            "buying_power": buying_power,
            "buying_power_note": None if buying_power is not None else "Not provided by broker source",
            "equity": equity,
            "last_equity": last_equity,
            "market_value": market_value,
            "positions_count": positions_count,
            "trade_date": payload.get("trade_date"),
            "as_of": payload.get("captured_at") or payload.get("persisted_at") or payload.get("as_of"),
            "source": f"artifact:{_relative_str(self.repo_root, path)}",
            "source_detail": f"artifact snapshot {_relative_str(self.repo_root, path)}",
            "trust_level": trust_level,
            "status": payload.get("status") or "fresh",
            "suspicious": False,
            "confidence_note": "",
            "display_equity": equity,
        }
        return snapshot, mode

    def _artifact_broker_snapshot(self, run_id: str | None = None, report_date: str | None = None) -> tuple[dict[str, Any] | None, str]:
        candidates: list[tuple[Path, str, str]] = []
        if run_id:
            run_root = self.repo_root / "outputs" / "runs" / run_id / "broker"
            candidates.extend(
                [
                    (run_root / "posttrade_account_snapshot.json", "authoritative", "authoritative_artifact"),
                    (run_root / "postsell_account_snapshot.json", "authoritative", "authoritative_artifact"),
                    (run_root / "pretrade_account_snapshot.json", "authoritative", "pretrade_artifact"),
                ]
            )
        candidates.extend(
            [
                (self.repo_root / "outputs" / "broker" / "posttrade_account_snapshot.json", "authoritative", "authoritative_artifact"),
                (self.repo_root / "outputs" / "broker" / "postsell_account_snapshot.json", "authoritative", "authoritative_artifact"),
                (self.repo_root / "outputs" / "broker" / "pretrade_account_snapshot.json", "authoritative", "pretrade_artifact"),
                (self.repo_root / "outputs" / "broker" / "broker_snapshot_latest.json", "derived", "artifact_snapshot"),
            ]
        )

        for path, trust_level, mode in candidates:
            payload = _read_json(path)
            if not isinstance(payload, dict):
                continue
            if payload.get("ok") is False or payload.get("error"):
                continue
            if path.name == "broker_snapshot_latest.json":
                inferred_trust = str(payload.get("trust_level") or "").strip().lower()
                if not inferred_trust:
                    source = str(payload.get("source") or "").lower()
                    inferred_trust = "authoritative" if "alpaca" in source else "derived"
                snapshot, resolved_mode = self._normalize_broker_snapshot(path, payload, inferred_trust, mode)
                return snapshot, resolved_mode
            snapshot, resolved_mode = self._normalize_broker_snapshot(path, payload, trust_level, mode)
            return snapshot, resolved_mode
        return None, "missing"

    def _load_performance_dataset(
        self,
        *,
        nav_path: Path,
        benchmark_path: Path,
        source_name: str,
    ) -> dict[str, Any]:
        nav_rows_raw = _read_csv_rows(nav_path)
        benchmark_rows_raw = _read_csv_rows(benchmark_path)

        nav_rows: list[dict[str, Any]] = []
        for row in nav_rows_raw:
            date_text = str(row.get("date") or "").strip()
            if not date_text:
                continue
            nav_rows.append(
                {
                    "date": date_text,
                    "equity": _float_or_none(row.get("equity") or row.get("portfolio_value")),
                    "cash": _float_or_none(row.get("cash")),
                    "gross_exposure": _float_or_none(row.get("gross_exposure")),
                    "return_1d": _float_or_none(row.get("return_1d")),
                    "turnover_pct": _float_or_none(row.get("turnover_pct") or row.get("turnover")),
                }
            )
        nav_rows.sort(key=lambda row: row["date"])
        first_positive_index = next(
            (
                index
                for index, row in enumerate(nav_rows)
                if row.get("equity") is not None and float(row["equity"]) > 0.0
            ),
            None,
        )
        if first_positive_index not in (None, 0):
            nav_rows = nav_rows[first_positive_index:]

        benchmark_rows: list[dict[str, Any]] = []
        for row in benchmark_rows_raw:
            date_text = str(row.get("date") or "").strip()
            if not date_text:
                continue
            benchmark_rows.append(
                {
                    "date": date_text,
                    "spy_close": _float_or_none(row.get("spy_close")),
                    "spy_return": _float_or_none(row.get("spy_return")),
                }
            )
        benchmark_rows.sort(key=lambda row: row["date"])

        daily_returns = []
        for index, row in enumerate(nav_rows):
            value = row.get("return_1d")
            if value is None and index > 0:
                prev_equity = nav_rows[index - 1].get("equity")
                equity = row.get("equity")
                if prev_equity not in (None, 0) and equity is not None:
                    value = (equity / prev_equity) - 1.0
            if value is not None:
                daily_returns.append(_series_point(row["date"], value))

        nav_series = [
            _series_point(row["date"], row["equity"])
            for row in nav_rows
            if row.get("equity") is not None
        ]
        benchmark_series = [
            _series_point(row["date"], row["spy_close"])
            for row in benchmark_rows
            if row.get("spy_close") is not None
        ]
        excess_returns = []
        benchmark_idx = 0
        latest_benchmark_return = None
        for point in daily_returns:
            while benchmark_idx < len(benchmark_rows) and benchmark_rows[benchmark_idx]["date"] <= point["date"]:
                if benchmark_rows[benchmark_idx].get("spy_return") is not None:
                    latest_benchmark_return = benchmark_rows[benchmark_idx]["spy_return"]
                benchmark_idx += 1
            excess_returns.append(
                _series_point(
                    point["date"],
                    point["value"] - latest_benchmark_return
                    if latest_benchmark_return is not None
                    else None,
                )
            )

        drawdown = []
        peak = None
        for row in nav_rows:
            equity = row.get("equity")
            if equity is None:
                continue
            peak = equity if peak is None else max(peak, equity)
            drawdown_value = 0.0 if peak in (None, 0) else min(0.0, (equity / peak) - 1.0)
            drawdown.append(_series_point(row["date"], drawdown_value))

        return {
            "source_name": source_name,
            "nav_path": nav_path,
            "benchmark_path": benchmark_path,
            "nav_rows": nav_rows,
            "benchmark_rows": benchmark_rows,
            "series": {
                "nav": nav_series,
                "benchmark": benchmark_series,
                "daily_returns": daily_returns,
                "excess_returns": excess_returns,
                "drawdown": drawdown,
                "chart_metadata": {
                    "nav_chart": {
                        "title": "Portfolio NAV vs Benchmark",
                        "x_axis_label": "Date",
                        "y_axis_label": "Value",
                        "note": "Indexed to 100 at inception",
                    },
                    "daily_returns_chart": {
                        "title": "Daily Returns",
                        "x_axis_label": "Date",
                        "y_axis_label": "Return (%)",
                        "baseline": 0.0,
                        "note": "Daily return percentage",
                    },
                    "excess_returns_chart": {
                        "title": "Excess Return vs SPY",
                        "x_axis_label": "Date",
                        "y_axis_label": "Excess Return (%)",
                        "baseline": 0.0,
                        "note": "Daily outperformance vs SPY",
                    },
                },
            },
        }

    def _build_perf_summary(self, performance: dict[str, Any]) -> dict[str, Any]:
        nav_rows = performance["nav_rows"]
        benchmark_rows = performance["benchmark_rows"]
        daily_returns = [point["value"] for point in performance["series"]["daily_returns"] if point.get("value") is not None]
        drawdown_series = performance["series"]["drawdown"]

        if not nav_rows:
            return {
                "mtd_return": None,
                "qtd_return": None,
                "since_inception_return": None,
                "since_inception_alpha": None,
                "current_drawdown": None,
                "best_day": None,
                "worst_day": None,
            }

        latest = nav_rows[-1]
        latest_date = _parse_date(latest["date"])
        month_rows = [row for row in nav_rows if latest_date and _parse_date(row["date"]) and _parse_date(row["date"]).month == latest_date.month and _parse_date(row["date"]).year == latest_date.year]
        quarter_rows = [
            row
            for row in nav_rows
            if latest_date
            and _parse_date(row["date"])
            and _parse_date(row["date"]).year == latest_date.year
            and ((_parse_date(row["date"]).month - 1) // 3) == ((latest_date.month - 1) // 3)
        ]

        def _period_return(rows: list[dict[str, Any]]) -> float | None:
            if len(rows) < 2:
                return None
            first = rows[0].get("equity")
            last = rows[-1].get("equity")
            if first in (None, 0) or last is None:
                return None
            return (last / first) - 1.0

        nav_start_date = str(nav_rows[0].get("date") or "").strip()
        nav_end_date = str(nav_rows[-1].get("date") or "").strip()
        aligned_benchmark_rows = [
            {"equity": row.get("spy_close")}
            for row in benchmark_rows
            if nav_start_date <= str(row.get("date") or "").strip() <= nav_end_date
            and row.get("spy_close") is not None
        ]
        bench_first = aligned_benchmark_rows[0].get("equity") if aligned_benchmark_rows else None
        bench_last = aligned_benchmark_rows[-1].get("equity") if aligned_benchmark_rows else None
        benchmark_return = (
            ((bench_last / bench_first) - 1.0)
            if len(aligned_benchmark_rows) >= 2 and bench_first not in (None, 0) and bench_last is not None
            else None
        )
        drawdown_value = drawdown_series[-1]["value"] if len(drawdown_series) >= 2 else None

        return {
            "mtd_return": _period_return(month_rows),
            "qtd_return": _period_return(quarter_rows),
            "since_inception_return": _period_return(nav_rows),
            "since_inception_alpha": (
                _period_return(nav_rows) - benchmark_return
                if _period_return(nav_rows) is not None and benchmark_return is not None
                else None
            ),
            "current_drawdown": abs(drawdown_value) if drawdown_value is not None else None,
            "best_day": max(daily_returns) if daily_returns else None,
            "worst_day": min(daily_returns) if daily_returns else None,
        }

    def _load_intended_orders(self, report_date: str) -> tuple[dict[str, Any] | None, Path | None]:
        broker_dir = self.repo_root / "outputs" / "broker"
        direct = broker_dir / f"intended_orders_{report_date}.json"
        if direct.exists():
            payload = _read_json(direct)
            return (payload if isinstance(payload, dict) else None), direct
        latest = _latest_glob(broker_dir, "intended_orders_*.json")
        payload = _read_json(latest) if latest else None
        return (payload if isinstance(payload, dict) else None), latest

    def _load_orders_csv(self, report_date: str) -> tuple[list[dict[str, Any]], Path | None]:
        broker_dir = self.repo_root / "outputs" / "broker"
        direct = broker_dir / f"orders_{report_date}.csv"
        if direct.exists():
            return _read_csv_rows(direct), direct
        latest = _latest_glob(broker_dir, "orders_*.csv")
        return (_read_csv_rows(latest), latest) if latest else ([], None)

    def _load_recon_posttrade(self, report_date: str) -> tuple[dict[str, Any] | None, Path | None]:
        broker_dir = self.repo_root / "outputs" / "broker"
        direct = broker_dir / f"recon_posttrade_{report_date}.json"
        if direct.exists():
            payload = _read_json(direct)
            return (payload if isinstance(payload, dict) else None), direct
        latest = _latest_glob(broker_dir, "recon_posttrade*.json")
        payload = _read_json(latest) if latest else None
        return (payload if isinstance(payload, dict) else None), latest

    def _load_broker_day_snapshot(self, trade_date: str | None) -> tuple[dict[str, Any] | None, Path | None]:
        snapshot_dir = self.repo_root / "outputs" / "broker_snapshot"
        if trade_date:
            direct = snapshot_dir / f"broker_snapshot_{trade_date}.json"
            if direct.exists():
                payload = _read_json(direct)
                return (payload if isinstance(payload, dict) else None), direct
        latest = _latest_glob(snapshot_dir, "broker_snapshot_*.json")
        payload = _read_json(latest) if latest else None
        return (payload if isinstance(payload, dict) else None), latest

    def _build_data_freshness(self, report_date: str, broker_snapshot: dict[str, Any] | None) -> dict[str, Any]:
        broker_as_of = broker_snapshot.get("as_of") if broker_snapshot else None
        broker_trade_date = broker_snapshot.get("trade_date") if broker_snapshot else None
        report_day = _parse_date(report_date)
        broker_day = _parse_date(broker_trade_date) or _parse_date(broker_as_of)
        broker_ts = _parse_datetime(broker_as_of)

        alignment = "missing"
        detail = "Broker snapshot unavailable."
        if broker_snapshot:
            if report_day is not None and broker_day is not None and report_day == broker_day:
                alignment = "aligned"
                detail = "Broker snapshot aligned with dashboard run date."
            elif broker_ts is not None and (self._now - broker_ts) > dt.timedelta(hours=STALE_THRESHOLD_HOURS):
                alignment = "stale"
                detail = f"Broker snapshot older than {STALE_THRESHOLD_HOURS} hours."
            else:
                alignment = "mismatch"
                detail = "Broker snapshot date does not match the selected dashboard run date."

        return {
            "run_report_date": report_date,
            "run_last_updated": self._now.isoformat(),
            "broker_as_of": broker_as_of,
            "broker_vs_run_alignment": alignment,
            "alignment_detail": detail,
            "stale_threshold_hours": STALE_THRESHOLD_HOURS,
            "broker_trust_level": broker_snapshot.get("trust_level") if broker_snapshot else "missing",
            "broker_source_detail": broker_snapshot.get("source_detail") if broker_snapshot else "",
            "suspicious_broker_value": bool(broker_snapshot.get("suspicious")) if broker_snapshot else False,
            "suspicious_reason": broker_snapshot.get("confidence_note") if broker_snapshot else "",
        }

    def _build_summary_export(
        self,
        *,
        report_date: str,
        run_id: str,
        trading_day_summary: dict[str, Any],
        summary_matches_report: bool,
        broker_surface: dict[str, Any],
        benchmark_payload: dict[str, Any],
        broker_snapshot: dict[str, Any],
        live_broker_overlay: bool,
        recon_status: str,
    ) -> dict[str, Any]:
        if trading_day_summary and summary_matches_report:
            summary = json.loads(json.dumps(trading_day_summary))
        else:
            broker = broker_surface.get("broker", {})
            summary = {
                "generated_at": self._now.isoformat(),
                "run_id": run_id,
                "trade_date": report_date,
                "execution_summary": {
                    "orders_submitted": None,
                    "orders_accepted": None,
                    "orders_rejected": None,
                    "duplicate_orders": 0,
                    "buy_orders": None,
                    "sell_orders": None,
                    "executed": False,
                    "partial_execution": False,
                    "status": "UNKNOWN",
                },
                "portfolio_state": {
                    "cash_after": broker.get("posttrade", {}).get("cash"),
                    "positions_count": broker.get("posttrade", {}).get("positionsCount"),
                    "portfolio_market_value": None,
                },
                "broker_context": {
                    "broker_authoritative_state": bool(
                        broker.get("authoritativeState")
                        or live_broker_overlay
                        or broker_snapshot.get("trust_level") == "authoritative"
                    ),
                    "post_execution_recon_status": recon_status,
                    "duplicate_guard_status": "CLEAR",
                    "affected_symbols": broker.get("posttrade", {}).get("affectedSymbols") or [],
                    "repair_suggestions": broker.get("posttrade", {}).get("repairSuggestions") or [],
                    "duplicate_fill_suspicions_count": 0,
                    "pretrade_positions_count": broker.get("pretrade", {}).get("positionsCount"),
                    "pretrade_cash": broker.get("pretrade", {}).get("cash"),
                    "pretrade_equity": broker.get("pretrade", {}).get("equity"),
                    "pretrade_buying_power": broker.get("pretrade", {}).get("buyingPower"),
                    "posttrade_positions_count": broker.get("posttrade", {}).get("positionsCount"),
                    "posttrade_cash": broker_snapshot.get("cash") if live_broker_overlay else broker.get("posttrade", {}).get("cash"),
                    "posttrade_equity": broker_snapshot.get("equity") if live_broker_overlay else broker.get("posttrade", {}).get("equity"),
                },
            }

        summary["dashboard"] = {
            "generated": True,
            "path": "web/dashboard/dashboard_data.json",
        }
        summary["benchmark"] = benchmark_payload
        if live_broker_overlay:
            execution_summary = summary.get("execution_summary")
            if not isinstance(execution_summary, dict):
                execution_summary = {}
            execution_summary["orders_submitted"] = None
            execution_summary["orders_accepted"] = None
            execution_summary["orders_rejected"] = None
            execution_summary["buy_orders"] = None
            execution_summary["sell_orders"] = None
            execution_summary["executed"] = False
            execution_summary["partial_execution"] = False
            execution_summary["status"] = "OVERLAY_ONLY"
            summary["execution_summary"] = execution_summary
            broker_context = summary.get("broker_context")
            if not isinstance(broker_context, dict):
                broker_context = {}
            broker_context["broker_authoritative_state"] = True
            broker_context["post_execution_recon_status"] = recon_status
            broker_context["posttrade_cash"] = broker_snapshot.get("cash")
            broker_context["posttrade_equity"] = broker_snapshot.get("equity")
            broker_context["posttrade_positions_count"] = broker_snapshot.get("positions_count")
            summary["broker_context"] = broker_context
            portfolio_state = summary.get("portfolio_state")
            if not isinstance(portfolio_state, dict):
                portfolio_state = {}
            portfolio_state["cash_after"] = broker_snapshot.get("cash")
            portfolio_state["positions_count"] = broker_snapshot.get("positions_count")
            summary["portfolio_state"] = portfolio_state
        return summary

    def build(self) -> dict[str, Any]:
        context = self._context()
        run_root = context["run_root"]
        latest = context["latest"]
        operator_summary = context["operator_summary"]
        trading_day_summary = context["trading_day_summary"]
        report_date = context["trade_date"]

        broker_surface = build_dashboard_payload(
            self.repo_root,
            run_root_arg=str(run_root) if run_root is not None else self.run_root_arg,
            trade_date_arg=report_date,
        )
        governed_performance = self._load_performance_dataset(
            nav_path=self.repo_root / "outputs" / "perf" / "nav_timeseries.csv",
            benchmark_path=self.repo_root / "outputs" / "perf" / "benchmark_close_history.csv",
            source_name="governed",
        )
        overlay_nav_path = self.repo_root / "outputs" / "perf" / "live_overlay_nav_series.csv"
        overlay_benchmark_path = self.repo_root / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv"
        overlay_performance = self._load_performance_dataset(
            nav_path=overlay_nav_path,
            benchmark_path=overlay_benchmark_path if overlay_benchmark_path.exists() else governed_performance["benchmark_path"],
            source_name="live_overlay",
        )

        governed_latest_nav = governed_performance["nav_rows"][-1] if governed_performance["nav_rows"] else {}
        selected_governed_report_date = str(governed_latest_nav.get("date") or report_date)
        summary_trade_date = str(trading_day_summary.get("trade_date") or "").strip()
        summary_matches_selected_governed = (
            bool(trading_day_summary)
            and (not summary_trade_date or summary_trade_date == selected_governed_report_date)
        )
        run_id = str(
            operator_summary.get("run_id")
            or (trading_day_summary.get("run_id") if summary_matches_selected_governed else None)
            or latest.get("run_id")
            or ""
        )
        artifact_lookup_id = run_root.name if isinstance(run_root, Path) else run_id or None
        broker_snapshot, broker_mode = self._artifact_broker_snapshot(run_id=artifact_lookup_id, report_date=report_date)
        benchmark_summary = trading_day_summary.get("benchmark") if isinstance(trading_day_summary.get("benchmark"), dict) else {}

        governed_portfolio_value = governed_latest_nav.get("equity")
        if governed_portfolio_value is None and summary_matches_selected_governed:
            governed_portfolio_value = _float_or_none(benchmark_summary.get("portfolio_value"))
        if governed_portfolio_value is None:
            governed_portfolio_value = (
                broker_surface.get("broker", {}).get("posttrade", {}).get("equity")
                or broker_surface.get("broker", {}).get("pretrade", {}).get("equity")
            )
        governed_cash = (
            governed_latest_nav.get("cash")
            if governed_latest_nav.get("cash") is not None
            else _float_or_none(trading_day_summary.get("portfolio_state", {}).get("cash_after"))
            if summary_matches_selected_governed and isinstance(trading_day_summary.get("portfolio_state"), dict)
            else None
        )
        if governed_cash is None and broker_snapshot is not None:
            governed_cash = broker_snapshot.get("cash")

        governed_market_value = None
        if governed_latest_nav.get("gross_exposure") is not None and governed_portfolio_value is not None:
            governed_market_value = round(governed_portfolio_value * governed_latest_nav["gross_exposure"], 2)
        elif governed_portfolio_value is not None and governed_cash is not None:
            governed_market_value = round(governed_portfolio_value - governed_cash, 2)

        governed_snapshot = {
            "portfolio_value": governed_portfolio_value,
            "equity": governed_portfolio_value,
            "cash": governed_cash,
            "market_value": governed_market_value,
            "as_of": governed_latest_nav.get("date") or report_date,
            "source": "governed:nav_timeseries" if governed_performance["nav_rows"] else "governed:benchmark_summary",
            "status": "fresh" if governed_portfolio_value is not None else "missing",
        }

        if broker_snapshot is None:
            broker_snapshot = {
                "portfolio_value": None,
                "cash": None,
                "buying_power": None,
                "buying_power_note": "Broker snapshot unavailable",
                "equity": None,
                "market_value": None,
                "positions_count": None,
                "trade_date": report_date,
                "as_of": None,
                "source": "",
                "source_detail": "",
                "trust_level": "missing",
                "status": "missing",
                "suspicious": False,
                "confidence_note": "",
                "display_equity": None,
            }

        if broker_snapshot.get("equity") is not None and governed_snapshot.get("portfolio_value") not in (None, 0):
            governed_value = float(governed_snapshot["portfolio_value"])
            broker_value = float(broker_snapshot["equity"])
            ratio = max(broker_value / governed_value, governed_value / broker_value) if broker_value else None
            suspicious = bool(ratio and ratio >= 5.0 and abs(broker_value - governed_value) >= 1000.0)
            if suspicious:
                broker_snapshot["suspicious"] = True
                broker_snapshot["confidence_note"] = (
                    f"Broker equity ${broker_value:,.0f} is {ratio:.2f}x vs governed ${governed_value:,.0f} (implausible magnitude)"
                )
                broker_snapshot["display_equity"] = None

        latest_run_root = Path(str(latest.get("run_root") or latest.get("path") or "")).expanduser() if latest.get("run_root") or latest.get("path") else None
        if latest_run_root is not None and not latest_run_root.is_absolute():
            latest_run_root = self.repo_root / latest_run_root
        expected_latest = []
        if latest_run_root is not None:
            expected_latest = [
                latest_run_root / "operator_summary.json",
                latest_run_root / "execution_results.json",
                latest_run_root / "execution_payload.json",
            ]
        latest_missing_paths = [path for path in expected_latest if not path.exists()]
        latest_missing = [_relative_str(self.repo_root, path) for path in latest_missing_paths]
        latest_missing_artifacts = [path.name for path in latest_missing_paths]
        latest_has_execution_results = bool(
            latest_run_root is not None and (latest_run_root / "execution_results.json").exists()
        )
        latest_status = str(latest.get("status") or "").strip().lower()
        planning_statuses = {"no_action", "plan_only", "overlay_only"}
        latest_complete = bool(
            (
                _status_is_complete(latest.get("status"))
                or (latest_run_root is not None and _latest_glob(latest_run_root / "reports", "quant_report_*.html"))
            )
            and not (latest_status in planning_statuses and not latest_has_execution_results)
            or (
                latest_has_execution_results
                and latest_run_root is not None
                and (latest_run_root / "operator_summary.json").exists()
            )
        )
        fallback_in_use = not latest_complete
        broker_trade_date = _iso_date_prefix(broker_snapshot.get("trade_date") or broker_snapshot.get("as_of"))
        live_broker_overlay = bool(
            fallback_in_use
            and broker_snapshot.get("trust_level") == "authoritative"
            and broker_trade_date
            and broker_trade_date > selected_governed_report_date
        )
        selected_trade_date_for_daily = broker_trade_date if live_broker_overlay and broker_trade_date else selected_governed_report_date
        intended_orders, intended_path = self._load_intended_orders(selected_trade_date_for_daily)
        if intended_orders is not None and not _same_trade_date(intended_orders.get("report_date"), selected_trade_date_for_daily):
            intended_orders, intended_path = None, None
        orders_rows, orders_path = self._load_orders_csv(selected_trade_date_for_daily)
        if orders_path is not None and not _same_trade_date(_path_trade_date(orders_path), selected_trade_date_for_daily):
            orders_rows, orders_path = [], None
        recon_posttrade, recon_path = self._load_recon_posttrade(selected_trade_date_for_daily)
        if recon_posttrade is not None and not _same_trade_date(recon_posttrade.get("trade_date"), selected_trade_date_for_daily):
            recon_posttrade, recon_path = None, None
        broker_day_snapshot, broker_day_snapshot_path = self._load_broker_day_snapshot(selected_trade_date_for_daily)
        if broker_day_snapshot is not None:
            snapshot_trade_date = (
                broker_day_snapshot.get("meta", {}).get("report_date")
                if isinstance(broker_day_snapshot.get("meta"), dict)
                else None
            )
            if not _same_trade_date(snapshot_trade_date, selected_trade_date_for_daily):
                broker_day_snapshot, broker_day_snapshot_path = None, None

        data_freshness = self._build_data_freshness(selected_governed_report_date, broker_snapshot)
        if live_broker_overlay and broker_trade_date:
            data_freshness["broker_vs_run_alignment"] = "overlay"
            data_freshness["alignment_detail"] = (
                f"Authoritative broker overlay active for {broker_trade_date} over governed run date {selected_governed_report_date}."
            )

        performance = (
            overlay_performance
            if live_broker_overlay and overlay_performance["nav_rows"]
            else governed_performance
        )
        perf_summary = self._build_perf_summary(performance)
        latest_nav = performance["nav_rows"][-1] if performance["nav_rows"] else {}

        exec_summary = (
            trading_day_summary.get("execution_summary")
            if summary_matches_selected_governed and isinstance(trading_day_summary.get("execution_summary"), dict)
            else {}
        )
        effective_exec_summary = {} if live_broker_overlay else exec_summary
        broker_day_return = (
            ((float(broker_snapshot.get("equity")) / float(broker_snapshot.get("last_equity"))) - 1.0)
            if live_broker_overlay
            and broker_snapshot.get("equity") not in (None, 0)
            and broker_snapshot.get("last_equity") not in (None, 0)
            else None
        )
        portfolio_asof_date = (
            broker_trade_date
            if live_broker_overlay and broker_trade_date
            else str(latest_nav.get("date") or selected_governed_report_date)
        )
        portfolio_history = build_portfolio_history(self.repo_root, report_date=portfolio_asof_date)
        portfolio_return_fraction = (
            broker_day_return
            if broker_day_return is not None
            else latest_nav.get("return_1d")
        )
        benchmark_compare_row = _latest_row_on_or_before(
            performance["benchmark_rows"],
            portfolio_asof_date,
            "spy_close",
        )
        benchmark_asof_date = (
            str(benchmark_compare_row.get("date") or "").strip()
            if isinstance(benchmark_compare_row, dict)
            else ""
        )
        comparison_mode = (
            "same_day"
            if portfolio_asof_date and benchmark_asof_date and portfolio_asof_date == benchmark_asof_date
            else "previous_trading_day"
            if portfolio_asof_date and benchmark_asof_date and benchmark_asof_date < portfolio_asof_date
            else "portfolio_only"
            if portfolio_asof_date
            else "unavailable"
        )
        benchmark_payload = {
            "portfolio_value": (
                broker_snapshot.get("equity")
                if live_broker_overlay and broker_snapshot.get("equity") is not None
                else _float_or_none(benchmark_summary.get("portfolio_value"))
                if summary_matches_selected_governed and benchmark_summary.get("portfolio_value") is not None
                else governed_portfolio_value
            ),
            "spy_value": (
                _float_or_none(benchmark_summary.get("spy_value"))
                if summary_matches_selected_governed and benchmark_summary.get("spy_value") is not None and comparison_mode == "same_day"
                else (benchmark_compare_row.get("spy_close") if isinstance(benchmark_compare_row, dict) else None)
            ),
            "portfolio_return_pct": (
                round(broker_day_return * 100.0, 6)
                if broker_day_return is not None
                else _float_or_none(benchmark_summary.get("portfolio_return_pct"))
                if summary_matches_selected_governed and benchmark_summary.get("portfolio_return_pct") is not None and not live_broker_overlay
                else (portfolio_return_fraction * 100 if portfolio_return_fraction is not None else None)
            ),
            "spy_return_pct": (
                _float_or_none(benchmark_summary.get("spy_return_pct"))
                if summary_matches_selected_governed and benchmark_summary.get("spy_return_pct") is not None and comparison_mode == "same_day"
                else (
                    benchmark_compare_row.get("spy_return") * 100
                    if isinstance(benchmark_compare_row, dict) and benchmark_compare_row.get("spy_return") is not None
                    else None
                )
            ),
            "performance_vs_spy_pct": (
                _float_or_none(benchmark_summary.get("performance_vs_spy_pct"))
                if summary_matches_selected_governed and benchmark_summary.get("performance_vs_spy_pct") is not None and comparison_mode == "same_day"
                else (
                    (portfolio_return_fraction - benchmark_compare_row.get("spy_return")) * 100
                    if portfolio_return_fraction is not None
                    and isinstance(benchmark_compare_row, dict)
                    and benchmark_compare_row.get("spy_return") is not None
                    else None
                )
            ),
            "portfolio_asof_date": portfolio_asof_date,
            "benchmark_asof_date": benchmark_asof_date or None,
            "comparison_asof_date": benchmark_asof_date or portfolio_asof_date,
            "comparison_mode": comparison_mode,
        }
        attribution = _build_attribution_summary(performance)
        contribution_snapshot = _load_contribution_snapshot(self.repo_root, selected_governed_report_date)

        broker_orders_today = (
            broker_day_snapshot.get("orders_report_date")
            if isinstance(broker_day_snapshot, dict) and isinstance(broker_day_snapshot.get("orders_report_date"), list)
            else []
        )
        _pt_pos_path = self.repo_root / "outputs" / "broker" / "posttrade_positions.json"
        _pt_pos_raw = _read_json(_pt_pos_path) if _pt_pos_path.exists() else None
        posttrade_positions_list = (
            _pt_pos_raw.get("positions")
            if isinstance(_pt_pos_raw, dict) and isinstance(_pt_pos_raw.get("positions"), list)
            else None
        )
        position_diag = _build_position_diagnostics(
            broker_snapshot=broker_snapshot,
            broker_day_snapshot=broker_day_snapshot,
            orders_today=broker_orders_today,
            positions_fallback=posttrade_positions_list,
        )
        edge_diagnostics = _build_edge_diagnostics(
            attribution=attribution,
            position_diag=position_diag,
            current_benchmark_return=(
                benchmark_payload.get("spy_return_pct") / 100.0
                if benchmark_payload.get("spy_return_pct") is not None
                else None
            ),
        )
        order_rows_for_activity = broker_orders_today if broker_orders_today else orders_rows
        buys_from_orders = sum(1 for row in order_rows_for_activity if str(row.get("side") or "").upper() == "BUY")
        sells_from_orders = sum(1 for row in order_rows_for_activity if str(row.get("side") or "").upper() == "SELL")
        accepted_from_orders = sum(
            1
            for row in order_rows_for_activity
            if str(row.get("status") or "").lower() in {"accepted", "filled", "partially_filled", "done_for_day"}
        ) or (len(order_rows_for_activity) if order_rows_for_activity else 0)
        rejected_from_orders = sum(1 for row in order_rows_for_activity if str(row.get("status") or "").lower() == "rejected")

        orders_intended = intended_orders.get("orders_intended") if isinstance(intended_orders, dict) else []
        top_changes: list[dict[str, Any]] = []
        if live_broker_overlay and broker_orders_today:
            for order in broker_orders_today[:5]:
                filled_price = (
                    _float_or_none(order.get("filled_avg_price"))
                    or _float_or_none(order.get("limit_price"))
                    or _float_or_none(order.get("price"))
                )
                qty = _float_or_none(order.get("filled_qty")) or _float_or_none(order.get("qty"))
                notional = _float_or_none(order.get("notional"))
                if notional is None and filled_price is not None and qty is not None:
                    notional = filled_price * qty
                change_weight = (
                    (notional / broker_snapshot.get("equity"))
                    if notional is not None and broker_snapshot.get("equity") not in (None, 0)
                    else None
                )
                top_changes.append(
                    {
                        "ticker": order.get("symbol"),
                        "action": str(order.get("side") or "").upper() or "HOLD",
                        "change_weight": change_weight,
                        "reason": "executed_order",
                    }
                )
        elif isinstance(orders_intended, list):
            for order in orders_intended[:5]:
                notional = _float_or_none(order.get("notional"))
                change_weight = (
                    (notional / governed_portfolio_value)
                    if notional is not None and governed_portfolio_value not in (None, 0)
                    else None
                )
                top_changes.append(
                    {
                        "ticker": order.get("ticker"),
                        "action": str(order.get("side") or "").upper() or "HOLD",
                        "change_weight": change_weight,
                        "reason": str(order.get("reason") or "planned_rebalance").strip().replace(" ", "_").lower(),
                    }
                )
        elif broker_orders_today:
            for order in broker_orders_today[:5]:
                filled_price = (
                    _float_or_none(order.get("filled_avg_price"))
                    or _float_or_none(order.get("limit_price"))
                    or _float_or_none(order.get("price"))
                )
                qty = _float_or_none(order.get("filled_qty")) or _float_or_none(order.get("qty"))
                notional = _float_or_none(order.get("notional"))
                if notional is None and filled_price is not None and qty is not None:
                    notional = filled_price * qty
                change_weight = (
                    (notional / broker_snapshot.get("equity"))
                    if notional is not None and broker_snapshot.get("equity") not in (None, 0)
                    else None
                )
                top_changes.append(
                    {
                        "ticker": order.get("symbol"),
                        "action": str(order.get("side") or "").upper() or "HOLD",
                        "change_weight": change_weight,
                        "reason": "executed_order",
                    }
                )

        broker_context = (
            trading_day_summary.get("broker_context")
            if summary_matches_selected_governed and isinstance(trading_day_summary.get("broker_context"), dict)
            else {}
        )
        effective_broker_context = {} if live_broker_overlay else broker_context
        overlay_only_recon = bool(
            live_broker_overlay
            and not (
                isinstance(recon_posttrade, dict)
                or str(effective_broker_context.get("post_execution_recon_status") or "").strip()
            )
        )
        duplicate_guard_status = effective_broker_context.get("duplicate_guard_status") or "CLEAR"
        recon_status = (
            effective_broker_context.get("post_execution_recon_status")
            or (
                recon_posttrade.get("drift_status") or recon_posttrade.get("verdict")
                if isinstance(recon_posttrade, dict)
                else None
            )
            or ("OVERLAY_ONLY" if overlay_only_recon else None)
            or (broker_surface.get("broker", {}).get("posttrade", {}).get("reconStatus") if not live_broker_overlay else None)
            or "UNKNOWN"
        )
        affected_symbols = (
            effective_broker_context.get("affected_symbols")
            or (
                recon_posttrade.get("affected_symbols")
                if isinstance(recon_posttrade, dict)
                else None
            )
            or []
        )
        repair_suggestions = (
            effective_broker_context.get("repair_suggestions")
            or (
                recon_posttrade.get("repair_suggestions")
                if isinstance(recon_posttrade, dict)
                else None
            )
            or []
        )
        duplicate_fill_suspicions_count = (
            effective_broker_context.get("duplicate_fill_suspicions_count")
            or (
                recon_posttrade.get("duplicate_fill_suspicions_count")
                if isinstance(recon_posttrade, dict)
                else None
            )
            or 0
        )

        execution_integrity = {
            "duplicate_guard_status": duplicate_guard_status,
            "post_execution_recon_status": recon_status,
            "affected_symbols": affected_symbols,
            "repair_suggestions": repair_suggestions,
            "duplicate_fill_suspicions_count": duplicate_fill_suspicions_count,
            "visible": True,
        }

        activity = {
            "buys": (
                effective_exec_summary.get("buy_orders")
                if effective_exec_summary.get("buy_orders") is not None
                else buys_from_orders
            ),
            "sells": (
                effective_exec_summary.get("sell_orders")
                if effective_exec_summary.get("sell_orders") is not None
                else sells_from_orders
            ),
            "new_positions": (
                effective_exec_summary.get("buy_orders")
                if effective_exec_summary.get("buy_orders") is not None
                else buys_from_orders
            ),
            "full_exits": (
                effective_exec_summary.get("sell_orders")
                if effective_exec_summary.get("sell_orders") is not None
                else sells_from_orders
            ),
            "orders_filled": (
                effective_exec_summary.get("orders_accepted")
                if effective_exec_summary.get("orders_accepted") is not None
                else accepted_from_orders
            ),
            "orders_rejected": (
                effective_exec_summary.get("orders_rejected")
                if effective_exec_summary.get("orders_rejected") is not None
                else rejected_from_orders
            ),
            "source_run_id": intended_orders.get("run_id") if isinstance(intended_orders, dict) else run_id,
            "source_report_date": (
                broker_day_snapshot.get("meta", {}).get("report_date")
                if isinstance(broker_day_snapshot, dict) and isinstance(broker_day_snapshot.get("meta"), dict)
                else intended_orders.get("report_date")
                if isinstance(intended_orders, dict)
                else selected_trade_date_for_daily
            ),
            "activity_source": (
                "broker_snapshot_current"
                if broker_day_snapshot
                else "intended_orders"
                if intended_orders
                else "orders_csv"
                if orders_rows
                else "trading_day_summary"
            ),
            "is_simulated": str(latest.get("mode") or "").lower() in {"paper", "shadow", "simulated"},
            "note": (
                f"Activity source: {_relative_str(self.repo_root, broker_day_snapshot_path)}"
                if broker_day_snapshot_path is not None
                else f"Activity source: {_relative_str(self.repo_root, intended_path)}"
                if intended_path is not None
                else f"Activity source: {_relative_str(self.repo_root, orders_path)}"
                if orders_path is not None
                else "Activity source: trading_day_summary"
            ),
        }

        risk_cash_ratio = (
            governed_cash / governed_portfolio_value
            if governed_cash is not None and governed_portfolio_value not in (None, 0)
            else None
        )
        gross_exposure = latest_nav.get("gross_exposure")
        if gross_exposure is None and risk_cash_ratio is not None:
            gross_exposure = max(0.0, 1.0 - risk_cash_ratio)

        risk = {
            "drawdown": perf_summary.get("current_drawdown"),
            "cash_position": (
                position_diag.get("current_cash_ratio")
                if live_broker_overlay and position_diag.get("current_cash_ratio") is not None
                else risk_cash_ratio
            ),
            "gross_exposure": gross_exposure,
            "largest_position_weight": position_diag.get("largest_position_weight"),
            "turnover_pct": latest_nav.get("turnover_pct"),
            "turnover_limit_pct": 0.35,
            "breaker_status": (
                operator_summary.get("breaker_status")
                or effective_broker_context.get("broker_pdt_risk_status")
            ) or ("PARTIAL" if _status_is_failure(effective_exec_summary.get("status") or latest.get("status")) else "OFF"),
        }

        daily_pl = None
        if broker_day_return is not None and broker_snapshot.get("equity") is not None and broker_snapshot.get("last_equity") is not None:
            daily_pl = round(float(broker_snapshot["equity"]) - float(broker_snapshot["last_equity"]), 2)
        elif governed_portfolio_value is not None and benchmark_payload.get("portfolio_return_pct") is not None:
            daily_pl = round(governed_portfolio_value * (benchmark_payload["portfolio_return_pct"] / 100.0), 2)

        display_run_id = (
            run_id
            if not fallback_in_use or operator_summary.get("run_id") or summary_matches_selected_governed
            else f"governed-fallback-{selected_governed_report_date}"
        )
        overall_status = (
            "WARNING"
            if fallback_in_use and governed_portfolio_value is not None
            else "PASS"
            if effective_exec_summary.get("status") in {"EXECUTED", "IDEMPOTENT_REPLAY", "PASS", "OK"}
            else "FAIL"
            if _status_is_failure(latest.get("status")) or _status_is_failure(effective_exec_summary.get("status"))
            else str(effective_exec_summary.get("status") or latest.get("status") or "UNKNOWN").upper()
        )

        status_banner_parts = []
        latest_attempted_report_date = str(latest.get("trade_date") or latest.get("report_date") or "").strip()
        latest_created_at = _parse_datetime(latest.get("created_at"))
        broker_as_of = _parse_datetime(broker_snapshot.get("as_of")) if broker_snapshot else None
        broker_overlay_label_date = (
            broker_trade_date
            if broker_trade_date and broker_trade_date != "2099-01-01"
            else _iso_date_prefix(broker_snapshot.get("as_of")) or portfolio_asof_date
        )
        latest_attempted_report_date_display = (
            latest_attempted_report_date
            if latest_attempted_report_date and latest_attempted_report_date != "2099-01-01"
            else ""
        )
        historical_latest_attempt_issue = bool(
            live_broker_overlay
            and broker_trade_date
            and latest_attempted_report_date
            and latest_attempted_report_date < broker_trade_date
        )
        if fallback_in_use:
            status_banner_parts.append(f"Showing governed dashboard state from {selected_governed_report_date}.")
            if live_broker_overlay and broker_overlay_label_date:
                status_banner_parts.append(f"Live broker overlay active for {broker_overlay_label_date}.")
            if latest.get("run_id"):
                latest_attempted_label = f"Latest attempted run {latest.get('run_id')}"
                if latest_status in planning_statuses:
                    latest_attempted_label += " was a plan-only/no_action smoke run and did not complete."
                else:
                    if latest_attempted_report_date_display:
                        latest_attempted_label += f" ({latest_attempted_report_date_display})"
                    if broker_as_of is not None and latest_created_at is not None and broker_as_of > latest_created_at:
                        latest_attempted_label += " did not complete, but a newer broker snapshot is available."
                    else:
                        latest_attempted_label += " did not complete."
                status_banner_parts.append(latest_attempted_label)
        if broker_snapshot.get("trust_level") == "authoritative":
            broker_as_of_label = _iso_date_prefix(broker_snapshot.get("as_of"))
            if broker_as_of_label:
                status_banner_parts.append(f"Authoritative Alpaca snapshot as of {broker_as_of_label} loaded.")
            else:
                status_banner_parts.append("Authoritative Alpaca snapshot loaded.")
        elif broker_snapshot.get("trust_level") == "derived":
            status_banner_parts.append("Broker values are derived rather than directly confirmed.")
        if comparison_mode == "previous_trading_day" and benchmark_asof_date:
            status_banner_parts.append(f"SPY comparison uses {benchmark_asof_date} close.")
        elif comparison_mode == "portfolio_only":
            status_banner_parts.append("SPY comparison unavailable for the current portfolio date.")
        if recon_status and recon_status not in {"UNKNOWN", "PASS", "OK", "OK_RECONCILED"}:
            status_banner_parts.append(f"Post-trade reconciliation status: {recon_status}.")
        if not status_banner_parts:
            status_banner_parts.append("Dashboard loaded from latest available artifacts.")
        status_banner = " ".join(status_banner_parts)

        latest_attempted_run = {
            "run_id": latest.get("run_id"),
            "report_date": latest.get("trade_date") or latest.get("report_date"),
            "created_at": latest.get("created_at"),
        }
        selected_governed_run = {
            "run_id": display_run_id,
            "report_date": selected_governed_report_date,
            "selection_reason": "latest_attempted_is_complete" if latest_complete else "latest_successful_governed_snapshot",
        }

        run_meta = {
            "report_date": selected_governed_report_date,
            "run_id": display_run_id,
            "mode": str(operator_summary.get("mode") or latest.get("mode") or "alpaca").upper(),
            "overall_status": overall_status,
            "benchmark": "SPY",
            "last_updated": self._now.isoformat(),
            "status_banner": status_banner,
            "latest_attempted_run": latest_attempted_run,
            "selected_governed_run": selected_governed_run,
            "fallback_in_use": fallback_in_use,
            "live_broker_overlay": live_broker_overlay,
            "performance_source": performance.get("source_name"),
            "portfolio_asof_date": portfolio_asof_date or None,
            "benchmark_asof_date": benchmark_asof_date or None,
            "comparison_asof_date": benchmark_payload.get("comparison_asof_date"),
            "comparison_mode": comparison_mode,
            "latest_attempted_is_complete": latest_complete,
            "latest_attempted_terminal_status": latest.get("status"),
            "latest_attempted_missing_artifacts": latest_missing_artifacts,
        }

        kpis = {
            "portfolio_value": broker_snapshot.get("equity") if live_broker_overlay and broker_snapshot.get("equity") is not None else governed_portfolio_value,
            "daily_pl": daily_pl,
            "daily_return": (
                benchmark_payload["portfolio_return_pct"] / 100.0
                if benchmark_payload.get("portfolio_return_pct") is not None
                else None
            ),
            "benchmark_return": (
                benchmark_payload["spy_return_pct"] / 100.0
                if benchmark_payload.get("spy_return_pct") is not None
                else None
            ),
            "excess_return": (
                benchmark_payload["performance_vs_spy_pct"] / 100.0
                if benchmark_payload.get("performance_vs_spy_pct") is not None
                else None
            ),
            "holdings": (
                broker_snapshot.get("positions_count")
                if broker_snapshot.get("positions_count") is not None
                else broker_surface.get("broker", {}).get("posttrade", {}).get("positionsCount")
            ),
            "turnover": latest_nav.get("turnover_pct"),
            "run_status": (
                "FALLBACK_VIEW"
                if fallback_in_use and governed_portfolio_value is not None
                else effective_exec_summary.get("status") or latest.get("status") or "UNKNOWN"
            ),
        }

        exceptions = []
        if _status_is_failure(latest.get("status")) or _status_is_failure(effective_exec_summary.get("status")):
            exceptions.append(
                {
                    "category": "Execution",
                    "status": "warning" if historical_latest_attempt_issue else "fail",
                    "message": (
                        f"Latest attempted run status: {latest.get('status') or effective_exec_summary.get('status') or 'UNKNOWN'}."
                        if not historical_latest_attempt_issue
                        else (
                            f"Historical latest attempted run status: {latest.get('status') or effective_exec_summary.get('status') or 'UNKNOWN'}."
                            f" Live broker overlay is showing {broker_trade_date}."
                        )
                    ),
                }
            )
        if recon_status not in {"PASS", "OK", "OK_RECONCILED", "UNKNOWN", "OVERLAY_ONLY"}:
            exceptions.append(
                {
                    "category": "Reconciliation",
                    "status": "warning" if "DRIFT" in recon_status or recon_status == "WARN" else "fail",
                    "message": (
                        recon_posttrade.get("operator_message")
                        if isinstance(recon_posttrade, dict) and recon_posttrade.get("operator_message")
                        else f"Post-trade reconciliation status: {recon_status}."
                    ),
                }
            )
        if data_freshness["broker_vs_run_alignment"] not in {"aligned", "overlay"}:
            exceptions.append(
                {
                    "category": "Broker snapshot",
                    "status": "warning",
                    "message": data_freshness["alignment_detail"],
                }
            )
        if broker_snapshot.get("suspicious"):
            exceptions.append(
                {
                    "category": "Broker snapshot",
                    "status": "warning",
                    "message": broker_snapshot.get("confidence_note"),
                }
            )
        if latest_missing:
            missing_prefix = (
                "Missing historical run artifacts (expected for plan-only/no_action runs): "
                if live_broker_overlay or not _status_is_failure(latest.get("status"))
                else "Missing critical run artifacts: "
            )
            exceptions.append(
                {
                    "category": "Data / artifacts",
                    "status": "warning",
                    "message": missing_prefix + ", ".join(latest_missing),
                }
            )
        if live_broker_overlay and not overlay_performance["nav_rows"]:
            exceptions.append(
                {
                    "category": "Performance history",
                    "status": "warning",
                    "message": "Live overlay performance history is unavailable; governed NAV history is still in use.",
                }
            )
        if comparison_mode == "portfolio_only":
            exceptions.append(
                {
                    "category": "Benchmark",
                    "status": "warning",
                    "message": "SPY comparison is unavailable for the current portfolio date range.",
                }
            )

        operating_checks = [
            {
                "label": "Run completed",
                "status": (
                    "pass"
                    if latest_complete
                    else "warning"
                    if historical_latest_attempt_issue or _status_is_failure(latest.get("status"))
                    else "warning"
                ),
                "detail": (
                    "Latest attempted run has the expected completion artifacts."
                    if latest_complete
                    else (
                        f"Historical latest attempted run status: {latest.get('status') or 'UNKNOWN'}; live broker overlay is current."
                        if historical_latest_attempt_issue
                        else f"Latest attempted run status: {latest.get('status') or 'UNKNOWN'}."
                    )
                ),
            },
            {
                "label": "Trades executed",
                "status": "pass" if (activity.get("orders_filled") or 0) > 0 else "warning",
                "detail": f"Accepted orders: {activity.get('orders_filled') or 0}.",
            },
            {
                "label": "Reconciliation passed",
                "status": "pass" if recon_status in {"PASS", "OK", "OK_RECONCILED"} else "skip" if recon_status == "OVERLAY_ONLY" else "warning",
                "detail": (
                    "Live broker overlay active; same-day governed reconciliation was not run."
                    if recon_status == "OVERLAY_ONLY"
                    else recon_posttrade.get("operator_message")
                    if isinstance(recon_posttrade, dict) and recon_posttrade.get("operator_message")
                    else f"Reconciliation status: {recon_status}."
                ),
            },
            {
                "label": "Broker snapshot authoritative",
                "status": "pass" if broker_snapshot.get("trust_level") == "authoritative" else "warning",
                "detail": broker_snapshot.get("source_detail") or "Broker snapshot unavailable.",
            },
            {
                "label": "Dashboard payload generated",
                "status": "pass",
                "detail": "Monitor payload refreshed for static serving.",
            },
        ]

        sources = []
        for path in [
            self.repo_root / "outputs" / "latest.json",
            self.repo_root / "outputs" / "latest_run.json",
            self.repo_root / "outputs" / "trading_day_summary.json",
            performance["nav_path"],
            performance["benchmark_path"],
            self.repo_root / "outputs" / "portfolio_history" / "summary.json",
            self.repo_root / "outputs" / "portfolio_history" / "transactions.csv",
            self.repo_root / "outputs" / "portfolio_history" / "positions.csv",
            self.repo_root / "outputs" / "portfolio_history" / "nav.csv",
            self.repo_root / "outputs" / "portfolio_history" / "attribution.csv",
            broker_day_snapshot_path,
            intended_path,
            orders_path,
            recon_path,
            Path(contribution_snapshot["paths"]["tickers"]) if contribution_snapshot["paths"].get("tickers") else None,
            Path(contribution_snapshot["paths"]["sleeves"]) if contribution_snapshot["paths"].get("sleeves") else None,
        ]:
            if path is None:
                continue
            sources.append(
                {
                    "path": _relative_str(self.repo_root, path),
                    "status": "used" if path.exists() else "missing",
                }
            )
        if broker_snapshot.get("source"):
            broker_source = str(broker_snapshot["source"]).replace("artifact:", "")
            sources.append({"path": broker_source, "status": "used"})
        if run_root is not None:
            sources.append(
                {
                    "path": _relative_str(self.repo_root, run_root),
                    "status": "present" if run_root.exists() else "missing",
                }
            )

        warnings = [item["message"] for item in exceptions if item["status"] != "pass"]
        builder_notes = {
            "build_timestamp": self._now.isoformat(),
            "missing_files": latest_missing,
            "warnings": warnings[:4],
            "all_warnings": warnings,
            "degraded_metrics": (
                ["benchmark_comparison_unavailable"]
                if comparison_mode == "portfolio_only"
                else []
            ) + (
                ["live_overlay_history_missing"]
                if live_broker_overlay and not overlay_performance["nav_rows"]
                else []
            ) + (
                ["negative_alpha"]
                if attribution.get("cumulative_alpha") is not None and attribution.get("cumulative_alpha") < 0
                else []
            ),
            "performance": {
                "source_name": performance.get("source_name"),
                "nav_path": _relative_str(self.repo_root, performance.get("nav_path")),
                "benchmark_path": _relative_str(self.repo_root, performance.get("benchmark_path")),
                "portfolio_asof_date": portfolio_asof_date,
                "benchmark_asof_date": benchmark_asof_date or None,
                "comparison_mode": comparison_mode,
            },
            "portfolio_history": {
                "summary_path": "outputs/portfolio_history/summary.json",
                "transactions_path": "outputs/portfolio_history/transactions.csv",
                "positions_path": "outputs/portfolio_history/positions.csv",
                "nav_path": "outputs/portfolio_history/nav.csv",
                "attribution_path": "outputs/portfolio_history/attribution.csv",
                "warnings": (portfolio_history.get("summary") or {}).get("warnings", []),
            },
            "broker_snapshot": {
                "source_mode": broker_mode,
                "source_used": broker_snapshot.get("source"),
                "freshness": data_freshness.get("broker_vs_run_alignment"),
                "trust_level": broker_snapshot.get("trust_level"),
                "source_detail": broker_snapshot.get("source_detail"),
                "suspicious": broker_snapshot.get("suspicious"),
            },
        }

        return {
            "run_meta": run_meta,
            "kpis": kpis,
            "perf_summary": perf_summary,
            "series": performance["series"],
            "risk": risk,
            "activity": activity,
            "governed_snapshot": governed_snapshot,
            "broker_snapshot": broker_snapshot,
            "data_freshness": data_freshness,
            "top_changes": top_changes,
            "exceptions": exceptions,
            "operating_checks": operating_checks,
            "sources": sources,
            "builder_notes": builder_notes,
            "execution_integrity": execution_integrity,
            "attribution": attribution,
            "edge_diagnostics": edge_diagnostics,
            "contribution_snapshot": contribution_snapshot,
            "portfolio_history": portfolio_history,
            "_summary_export": self._build_summary_export(
                report_date=selected_governed_report_date,
                run_id=display_run_id,
                trading_day_summary=trading_day_summary,
                summary_matches_report=summary_matches_selected_governed,
                broker_surface=broker_surface,
                benchmark_payload=benchmark_payload,
                broker_snapshot=broker_snapshot,
                live_broker_overlay=live_broker_overlay,
                recon_status=recon_status,
            ),
        }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build dashboard payloads for the Caerus monitor and broker views.")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--run-root", default=None)
    parser.add_argument("--trade-date", default=None)
    parser.add_argument("--output-dir", default="web/dashboard")
    return parser.parse_args(argv)


def write_dashboard_payload(
    repo_root: Path,
    payload: dict[str, Any],
    output_dir: Path,
    *,
    legacy_payload: dict[str, Any] | None = None,
    summary_payload: dict[str, Any] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "dashboard-data.json"
    js_path = output_dir / "dashboard-data.js"
    json_text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    js_text = "window.DASHBOARD_DATA = " + json.dumps(payload, indent=2, sort_keys=True) + ";\n"
    json_path.write_text(json_text, encoding="utf-8")
    js_path.write_text(js_text, encoding="utf-8")

    if legacy_payload is not None:
        clean_legacy = {key: value for key, value in legacy_payload.items() if not str(key).startswith("_")}
        (output_dir / "dashboard_data.json").write_text(
            json.dumps(clean_legacy, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    if summary_payload is not None:
        (output_dir / "trading_day_summary.json").write_text(
            json.dumps(summary_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    broker_payload = build_dashboard_payload(repo_root, run_root_arg=args.run_root, trade_date_arg=args.trade_date)

    builder = DashboardBuilder(
        repo_root=repo_root,
        run_root_arg=args.run_root,
        trade_date_arg=args.trade_date,
    )
    legacy_payload = builder.build()
    summary_payload = legacy_payload.pop("_summary_export", None)

    output_dir = repo_root / args.output_dir
    write_dashboard_payload(
        repo_root,
        broker_payload,
        output_dir,
        legacy_payload=legacy_payload,
        summary_payload=summary_payload if isinstance(summary_payload, dict) else None,
    )
    print(str(output_dir / "dashboard_data.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
