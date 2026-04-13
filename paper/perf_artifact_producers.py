from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


DEFAULT_BENCHMARK_PATH = Path("outputs/perf/benchmark_close_history.csv")
DEFAULT_BENCHMARK_RELATIVE_PATH = Path("outputs/perf/benchmark_relative_series.csv")
DEFAULT_ANALYZER_PATH = Path("outputs/perf/premarket_analyzer_scores.csv")
DEFAULT_CONCENTRATION_HISTORY_PATH = Path("outputs/perf/concentration_history.csv")
DEFAULT_CONSTRUCTION_PARITY_DIR = Path("outputs/perf")
DEFAULT_CONSTRUCTION_PARITY_LATEST_PATH = DEFAULT_CONSTRUCTION_PARITY_DIR / "construction_parity_latest.json"
DEFAULT_BROKER_SNAPSHOT_DIR = Path("outputs/broker_snapshot")
DEFAULT_VIX_PATH = Path("outputs/perf/vix_close_history.csv")
DEFAULT_SIGNALS_DIR = Path("signals")
DEFAULT_EXECUTION_EMAIL_DIR = Path("outputs/execution_email")
DEFAULT_TARGET_CASH_WEIGHT = 0.05
DEFAULT_GROSS_EXPOSURE_TOLERANCE = 0.05
DEFAULT_CASH_WEIGHT_TOLERANCE = 0.05
DEFAULT_POSITION_WEIGHT_TOLERANCE = 0.01


def _effective_inception_date() -> str:
    return str(os.getenv("PAPER_INCEPTION_DATE", "2026-02-23")).strip() or "2026-02-23"


def _safe_iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_side(value: Any) -> str:
    text = _normalize_text(value).upper()
    if "." in text:
        text = text.rsplit(".", 1)[-1]
    return text


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    try:
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except Exception:
        return None
    return None


def _empty_trade_day_payload(trade_date: str, run_root: Path, reason: str) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "run_id": run_root.name if str(run_root) else "",
        "generated_at": _safe_iso_now(),
        "status": "MISSING",
        "reason": reason,
        "summary": {
            "trade_count": 0,
            "sell_count": 0,
            "buy_count": 0,
            "realized_exit_pnl": 0.0,
            "open_buy_mark_pnl": 0.0,
            "winning_exits": 0,
            "losing_exits": 0,
            "winning_buys_on_mark": 0,
            "losing_buys_on_mark": 0,
            "median_exit_pnl": None,
            "best_exit": None,
            "worst_exit": None,
        },
        "rows": [],
    }


def _default_fetch_spy_close(start_date: str, end_date: str) -> pd.Series:
    data = yf.download("SPY", start=start_date, end=end_date, auto_adjust=False, progress=False, threads=False)
    if data is None or data.empty:
        return pd.Series(dtype=float)
    if "Close" in data.columns:
        series_raw = data["Close"]
    elif "Adj Close" in data.columns:
        series_raw = data["Adj Close"]
    else:
        return pd.Series(dtype=float)

    if isinstance(series_raw, pd.DataFrame):
        if series_raw.shape[1] == 0:
            return pd.Series(dtype=float)
        first_col = series_raw[series_raw.columns[0]]
        series = pd.to_numeric(first_col, errors="coerce")
    else:
        series = pd.to_numeric(series_raw, errors="coerce")

    series.index = pd.to_datetime(series.index)
    return series


def update_benchmark_close_history(
    *,
    asof_date: str,
    output_path: Path = DEFAULT_BENCHMARK_PATH,
    inception_date: str | None = None,
    fetch_spy_close_fn: Callable[[str, str], pd.Series] | None = None,
) -> pd.DataFrame:
    start = pd.Timestamp(inception_date or _effective_inception_date())
    asof = pd.Timestamp(asof_date)
    if asof < start:
        return pd.DataFrame(columns=["date", "spy_close", "spy_return"])

    fetch_fn = fetch_spy_close_fn or _default_fetch_spy_close
    closes = fetch_fn(start.strftime("%Y-%m-%d"), (asof + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    if closes is None or closes.empty:
        return pd.DataFrame(columns=["date", "spy_close", "spy_return"])
    if isinstance(closes, pd.DataFrame):
        if closes.shape[1] == 0:
            return pd.DataFrame(columns=["date", "spy_close", "spy_return"])
        first_col = closes[closes.columns[0]]
        closes = pd.to_numeric(first_col, errors="coerce")

    closes = closes.loc[(closes.index >= start) & (closes.index <= asof)]
    if closes.empty:
        return pd.DataFrame(columns=["date", "spy_close", "spy_return"])

    closes.index = pd.DatetimeIndex(pd.to_datetime(closes.index, errors="coerce")).normalize()
    closes = closes.sort_index()
    closes = closes.groupby(closes.index).last()
    target_dates = pd.date_range(start, asof, freq="D")
    closes = (
        closes.reindex(closes.index.union(target_dates))
        .sort_index()
        .ffill()
        .reindex(target_dates)
        .dropna()
    )
    if closes.empty:
        return pd.DataFrame(columns=["date", "spy_close", "spy_return"])

    close_index = pd.DatetimeIndex(closes.index)
    out = pd.DataFrame({"date": close_index.strftime("%Y-%m-%d"), "spy_close": closes.to_numpy()})
    out["spy_close"] = pd.to_numeric(out["spy_close"], errors="coerce")
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out["spy_return"] = out["spy_close"].pct_change()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return out


def update_vix_close_history(
    *,
    asof_date: str,
    output_path: Path = DEFAULT_VIX_PATH,
    inception_date: str | None = None,
    fetch_vix_close_fn: Callable[[str, str], pd.Series] | None = None,
) -> pd.DataFrame:
    """
    Fetch and store VIX close history from inception through as-of date.
    
    Args:
        asof_date: Target date in YYYY-MM-DD format
        output_path: CSV output path for VIX close history
        inception_date: Optional inception date override
        fetch_vix_close_fn: Optional custom fetch function for VIX data
    
    Returns:
        DataFrame with columns: date, vix_close, vix_return
    """
    start = pd.Timestamp(inception_date or _effective_inception_date())
    asof = pd.Timestamp(asof_date)
    if asof < start:
        return pd.DataFrame(columns=["date", "vix_close", "vix_return"])

    fetch_fn = fetch_vix_close_fn or _default_fetch_vix_close
    closes = fetch_fn(start.strftime("%Y-%m-%d"), (asof + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    if closes is None or closes.empty:
        return pd.DataFrame(columns=["date", "vix_close", "vix_return"])
    if isinstance(closes, pd.DataFrame):
        if closes.shape[1] == 0:
            return pd.DataFrame(columns=["date", "vix_close", "vix_return"])
        first_col = closes[closes.columns[0]]
        closes = pd.to_numeric(first_col, errors="coerce")

    closes = closes.loc[(closes.index >= start) & (closes.index <= asof)]
    if closes.empty:
        return pd.DataFrame(columns=["date", "vix_close", "vix_return"])

    close_index = pd.DatetimeIndex(pd.to_datetime(closes.index, errors="coerce"))
    out = pd.DataFrame({"date": close_index.strftime("%Y-%m-%d"), "vix_close": closes.to_numpy()})
    out["vix_close"] = pd.to_numeric(out["vix_close"], errors="coerce")
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    out["vix_return"] = out["vix_close"].pct_change()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return out


def _rolling_compound_return(series: pd.Series, window: int) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").fillna(0.0)
    return values.rolling(window=window, min_periods=window).apply(
        lambda arr: float(pd.Series(arr).add(1.0).prod() - 1.0),
        raw=False,
    )


def build_benchmark_relative_series(
    *,
    nav_timeseries_path: Path | str = Path("outputs/perf/nav_timeseries.csv"),
    benchmark_path: Path | str = DEFAULT_BENCHMARK_PATH,
    output_path: Path | str = DEFAULT_BENCHMARK_RELATIVE_PATH,
    rolling_windows: tuple[int, ...] = (5, 10, 20),
) -> pd.DataFrame:
    columns = [
        "date",
        "equity",
        "cash",
        "gross_exposure",
        "net_exposure",
        "turnover",
        "strategy_return",
        "spy_close",
        "spy_return",
        "excess_return",
        "strategy_nav_indexed",
        "spy_nav_indexed",
        "strategy_return_cum",
        "spy_return_cum",
        "excess_return_cum",
        "drawdown",
    ] + [f"rolling_excess_{window}d" for window in rolling_windows]

    nav_path = Path(nav_timeseries_path)
    bench_path = Path(benchmark_path)
    out_path = Path(output_path)

    if not nav_path.exists() or not bench_path.exists():
        out = pd.DataFrame(columns=columns)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    nav_df = pd.read_csv(nav_path)
    bench_df = pd.read_csv(bench_path)
    if nav_df.empty or bench_df.empty:
        out = pd.DataFrame(columns=columns)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    nav_df = nav_df.copy()
    bench_df = bench_df.copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    bench_df["date"] = pd.to_datetime(bench_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    nav_df = nav_df[nav_df["date"].notna()].copy()
    bench_df = bench_df[bench_df["date"].notna()].copy()

    merge_cols = ["date", "spy_close", "spy_return"]
    merged = nav_df.merge(bench_df[merge_cols], on="date", how="left").sort_values("date").reset_index(drop=True)
    if merged.empty:
        out = pd.DataFrame(columns=columns)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    merged["equity"] = pd.to_numeric(merged.get("equity"), errors="coerce")
    merged["cash"] = pd.to_numeric(merged.get("cash"), errors="coerce")
    merged["gross_exposure"] = pd.to_numeric(merged.get("gross_exposure"), errors="coerce")
    merged["net_exposure"] = pd.to_numeric(merged.get("net_exposure"), errors="coerce")
    merged["turnover"] = pd.to_numeric(
        merged.get("turnover").fillna(merged.get("turnover_pct")) if "turnover" in merged.columns else merged.get("turnover_pct"),
        errors="coerce",
    )
    merged["spy_close"] = pd.to_numeric(merged.get("spy_close"), errors="coerce")
    merged["spy_return"] = pd.to_numeric(merged.get("spy_return"), errors="coerce")

    strategy_return = pd.to_numeric(merged.get("return_1d"), errors="coerce")
    if strategy_return.notna().sum() == 0:
        strategy_return = merged["equity"].pct_change()
    merged["strategy_return"] = strategy_return.fillna(0.0)
    merged["spy_return"] = merged["spy_return"].fillna(merged["spy_close"].pct_change()).fillna(0.0)
    merged["excess_return"] = merged["strategy_return"] - merged["spy_return"]

    first_equity = merged["equity"].dropna()
    first_spy = merged["spy_close"].dropna()
    merged["strategy_nav_indexed"] = (
        merged["equity"] / float(first_equity.iloc[0]) * 100.0 if not first_equity.empty and float(first_equity.iloc[0]) != 0 else None
    )
    merged["spy_nav_indexed"] = (
        merged["spy_close"] / float(first_spy.iloc[0]) * 100.0 if not first_spy.empty and float(first_spy.iloc[0]) != 0 else None
    )
    merged["strategy_return_cum"] = merged["strategy_return"].add(1.0).cumprod() - 1.0
    merged["spy_return_cum"] = merged["spy_return"].add(1.0).cumprod() - 1.0
    merged["excess_return_cum"] = merged["strategy_return_cum"] - merged["spy_return_cum"]
    merged["drawdown"] = merged["equity"] / merged["equity"].cummax() - 1.0

    for window in rolling_windows:
        merged[f"rolling_excess_{window}d"] = (
            _rolling_compound_return(merged["strategy_return"], window)
            - _rolling_compound_return(merged["spy_return"], window)
        )

    out = merged[columns].copy()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    return out


def build_concentration_history(
    *,
    holdings_dir: Path | str = Path("outputs/perf"),
    nav_timeseries_path: Path | str = Path("outputs/perf/nav_timeseries.csv"),
    output_path: Path | str = DEFAULT_CONCENTRATION_HISTORY_PATH,
) -> pd.DataFrame:
    columns = [
        "date",
        "equity",
        "cash",
        "cash_weight",
        "market_value",
        "gross_exposure",
        "net_exposure",
        "turnover",
        "holdings_count",
        "largest_position_weight",
        "top_5_concentration",
        "median_position_weight",
        "min_position_weight",
        "avg_position_weight",
    ]
    out_path = Path(output_path)
    nav_path = Path(nav_timeseries_path)
    holdings_root = Path(holdings_dir)

    if not nav_path.exists():
        out = pd.DataFrame(columns=columns)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    nav_df = pd.read_csv(nav_path)
    if nav_df.empty:
        out = pd.DataFrame(columns=columns)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    nav_df["date"] = pd.to_datetime(nav_df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    nav_df = nav_df[nav_df["date"].notna()].copy()
    nav_lookup = nav_df.set_index("date").to_dict(orient="index")

    rows: list[dict[str, Any]] = []
    for path in sorted(holdings_root.glob("holdings_mtm_*.csv")):
        date_str = path.stem.replace("holdings_mtm_", "").strip()
        row = nav_lookup.get(date_str, {})
        holdings_df = pd.read_csv(path)
        holdings_df["market_value"] = pd.to_numeric(holdings_df.get("market_value"), errors="coerce")
        holdings_df = holdings_df[holdings_df["market_value"].notna()].copy()
        total_market_value = float(holdings_df["market_value"].sum()) if not holdings_df.empty else 0.0
        equity = _to_float(row.get("equity"))
        cash = _to_float(row.get("cash"))
        if equity is None and cash is not None:
            equity = total_market_value + cash
        if equity is None or equity <= 0:
            continue

        weights = holdings_df["market_value"].abs() / float(equity) if not holdings_df.empty else pd.Series(dtype=float)
        rows.append(
            {
                "date": date_str,
                "equity": float(equity),
                "cash": cash,
                "cash_weight": float(cash / equity) if cash is not None else None,
                "market_value": total_market_value,
                "gross_exposure": _to_float(row.get("gross_exposure")) or float(total_market_value / equity),
                "net_exposure": _to_float(row.get("net_exposure")) or float(total_market_value / equity),
                "turnover": _to_float(row.get("turnover")) or _to_float(row.get("turnover_pct")),
                "holdings_count": int(len(holdings_df)),
                "largest_position_weight": float(weights.max()) if not weights.empty else None,
                "top_5_concentration": float(weights.nlargest(min(5, len(weights))).sum()) if not weights.empty else None,
                "median_position_weight": float(weights.median()) if not weights.empty else None,
                "min_position_weight": float(weights.min()) if not weights.empty else None,
                "avg_position_weight": float(weights.mean()) if not weights.empty else None,
            }
        )

    out = pd.DataFrame(rows, columns=columns).sort_values("date").reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    return out


def build_construction_parity_artifact(
    *,
    asof_date: str,
    concentration_history_path: Path | str = DEFAULT_CONCENTRATION_HISTORY_PATH,
    config_path: Path | str = Path("paper/config_paper.json"),
    output_dir: Path | str = DEFAULT_CONSTRUCTION_PARITY_DIR,
    target_cash_weight: float = DEFAULT_TARGET_CASH_WEIGHT,
) -> dict[str, Any]:
    output_root = Path(output_dir)
    output_root.mkdir(parents=True, exist_ok=True)
    latest_path = DEFAULT_CONSTRUCTION_PARITY_LATEST_PATH if output_root == DEFAULT_CONSTRUCTION_PARITY_DIR else output_root / "construction_parity_latest.json"
    dated_path = output_root / f"construction_parity_{asof_date}.json"

    concentration_path = Path(concentration_history_path)
    if not concentration_path.exists():
        payload = {
            "date": asof_date,
            "generated_at": _safe_iso_now(),
            "status": "MISSING",
            "reason": f"missing_concentration_history:{concentration_path}",
        }
        latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        dated_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    history = pd.read_csv(concentration_path)
    if history.empty or "date" not in history.columns:
        payload = {
            "date": asof_date,
            "generated_at": _safe_iso_now(),
            "status": "MISSING",
            "reason": "empty_concentration_history",
        }
        latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        dated_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    history["date"] = pd.to_datetime(history["date"], errors="coerce")
    history = history[history["date"].notna()].sort_values("date").copy()
    asof_ts = pd.Timestamp(asof_date)
    eligible = history[history["date"] <= asof_ts]
    if eligible.empty:
        payload = {
            "date": asof_date,
            "generated_at": _safe_iso_now(),
            "status": "MISSING",
            "reason": "no_concentration_row_for_asof",
        }
        latest_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        dated_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return payload

    row = eligible.iloc[-1].to_dict()
    config = _load_json_if_exists(Path(config_path)) or {}
    risk_cfg = config.get("risk") if isinstance(config.get("risk"), dict) else {}
    target_cash_weight_value = _to_float(os.getenv("TARGET_CASH_WEIGHT")) or float(target_cash_weight)
    max_position_weight = _to_float(risk_cfg.get("max_position_pct")) or 0.20
    min_position_weight = _to_float(risk_cfg.get("min_position_pct")) or 0.05
    target_min_gross_exposure = max(0.0, 1.0 - target_cash_weight_value)
    target_holdings_min = max(1, int((target_min_gross_exposure / max_position_weight) + 0.999999))
    target_holdings_max = int(target_min_gross_exposure // min_position_weight) if min_position_weight > 0 else None
    actual_cash_weight = _to_float(row.get("cash_weight"))
    actual_gross_exposure = _to_float(row.get("gross_exposure"))
    actual_holdings_count = int(_to_float(row.get("holdings_count")) or 0)
    actual_largest_position_weight = _to_float(row.get("largest_position_weight"))
    actual_median_position_weight = _to_float(row.get("median_position_weight"))
    actual_top_5_concentration = _to_float(row.get("top_5_concentration"))

    warnings: list[str] = []
    if actual_cash_weight is not None and actual_cash_weight > target_cash_weight_value + DEFAULT_CASH_WEIGHT_TOLERANCE:
        warnings.append("cash_weight_above_target")
    if actual_gross_exposure is not None and actual_gross_exposure < target_min_gross_exposure - DEFAULT_GROSS_EXPOSURE_TOLERANCE:
        warnings.append("gross_exposure_below_target")
    if actual_holdings_count and actual_holdings_count < target_holdings_min:
        warnings.append("holdings_count_below_target_range")
    if target_holdings_max and actual_holdings_count > target_holdings_max:
        warnings.append("holdings_count_above_target_range")
    if actual_largest_position_weight is not None and actual_largest_position_weight > max_position_weight + DEFAULT_POSITION_WEIGHT_TOLERANCE:
        warnings.append("largest_position_above_cap")
    if actual_median_position_weight is not None and actual_median_position_weight < max(0.0, min_position_weight - DEFAULT_POSITION_WEIGHT_TOLERANCE):
        warnings.append("median_position_below_min")

    payload = {
        "date": asof_date,
        "generated_at": _safe_iso_now(),
        "status": "ALIGNED" if not warnings else "DRIFTED",
        "actual": {
            "cash_weight": actual_cash_weight,
            "gross_exposure": actual_gross_exposure,
            "holdings_count": actual_holdings_count,
            "largest_position_weight": actual_largest_position_weight,
            "median_position_weight": actual_median_position_weight,
            "top_5_concentration": actual_top_5_concentration,
        },
        "targets": {
            "cash_weight": target_cash_weight_value,
            "min_gross_exposure": target_min_gross_exposure,
            "max_position_weight": max_position_weight,
            "min_position_weight": min_position_weight,
            "holdings_count_min": target_holdings_min,
            "holdings_count_max": target_holdings_max,
        },
        "drift": {
            "cash_weight_vs_target": (
                float(actual_cash_weight - target_cash_weight_value)
                if actual_cash_weight is not None
                else None
            ),
            "gross_exposure_vs_target": (
                float(actual_gross_exposure - target_min_gross_exposure)
                if actual_gross_exposure is not None
                else None
            ),
            "largest_position_vs_cap": (
                float(actual_largest_position_weight - max_position_weight)
                if actual_largest_position_weight is not None
                else None
            ),
        },
        "warnings": warnings,
    }
    text = json.dumps(payload, indent=2) + "\n"
    latest_path.write_text(text, encoding="utf-8")
    dated_path.write_text(text, encoding="utf-8")
    return payload


def _trade_day_pretrade_position_map(pretrade_positions: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in pretrade_positions.get("positions", []):
        symbol = _normalize_text(item.get("symbol") or (item.get("raw") or {}).get("symbol")).upper()
        if not symbol:
            continue
        raw = item.get("raw") or {}
        out[symbol] = {
            "qty": _to_float(raw.get("qty")),
            "avg_entry_price": _to_float(raw.get("avg_entry_price")),
            "cost_basis": _to_float(raw.get("cost_basis")),
            "current_price": _to_float(raw.get("current_price")),
        }
    return out


def _trade_day_current_position_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("positions_current", []):
        symbol = _normalize_text(item.get("symbol")).upper()
        if not symbol:
            continue
        out[symbol] = {
            "qty": _to_float(item.get("qty")),
            "current_price": _to_float(item.get("current_price")),
            "cost_basis": _to_float(item.get("cost_basis")),
            "unrealized_pl": _to_float(item.get("unrealized_pl")),
        }
    return out


def _trade_day_unique_orders(snapshot: dict[str, Any], trade_date: str) -> list[dict[str, Any]]:
    deduped: dict[str, dict[str, Any]] = {}
    for item in snapshot.get("orders_report_date", []):
        order_id = _normalize_text(item.get("id"))
        submitted_at = _normalize_text(item.get("submitted_at"))
        if submitted_at[:10] != trade_date or not order_id:
            continue
        deduped[order_id] = dict(item)
    return sorted(
        deduped.values(),
        key=lambda item: (
            _normalize_text(item.get("submitted_at")),
            _normalize_text(item.get("symbol")),
            _normalize_text(item.get("id")),
        ),
    )


def _trade_day_rows(
    *,
    trade_date: str,
    orders: list[dict[str, Any]],
    pretrade_by_symbol: dict[str, dict[str, Any]],
    current_by_symbol: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for order in orders:
        symbol = _normalize_text(order.get("symbol")).upper()
        side = _normalize_side(order.get("side"))
        qty = _to_float(order.get("filled_qty")) or _to_float(order.get("qty")) or 0.0
        fill_price = _to_float(order.get("filled_avg_price"))
        pretrade = pretrade_by_symbol.get(symbol) or {}
        current = current_by_symbol.get(symbol) or {}

        pretrade_qty = _to_float(pretrade.get("qty"))
        pretrade_avg_entry = _to_float(pretrade.get("avg_entry_price"))
        if pretrade_avg_entry is None:
            pretrade_cost_basis = _to_float(pretrade.get("cost_basis"))
            if pretrade_cost_basis is not None and pretrade_qty not in (None, 0.0):
                pretrade_avg_entry = pretrade_cost_basis / pretrade_qty

        current_price = _to_float(current.get("current_price"))
        realized_pnl = None
        open_mark_pnl = None
        if side == "SELL" and fill_price is not None and pretrade_avg_entry is not None:
            realized_pnl = qty * (fill_price - pretrade_avg_entry)
        elif side == "BUY" and fill_price is not None and current_price is not None:
            open_mark_pnl = qty * (current_price - fill_price)

        rows.append(
            {
                "trade_date": trade_date,
                "submitted_at": _normalize_text(order.get("submitted_at")),
                "filled_at": _normalize_text(order.get("filled_at")),
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "fill_price": fill_price,
                "pretrade_qty": pretrade_qty,
                "pretrade_avg_entry": pretrade_avg_entry,
                "current_position_qty": _to_float(current.get("qty")),
                "current_price": current_price,
                "status": _normalize_text(order.get("status")),
                "realized_pnl": realized_pnl,
                "open_mark_pnl": open_mark_pnl,
            }
        )
    return rows


def _trade_day_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sells = [row for row in rows if row["side"] == "SELL"]
    buys = [row for row in rows if row["side"] == "BUY"]
    realized_values = [float(row["realized_pnl"]) for row in sells if row["realized_pnl"] is not None]
    open_values = [float(row["open_mark_pnl"]) for row in buys if row["open_mark_pnl"] is not None]
    best_exit = None
    worst_exit = None
    if realized_values:
        best_row = max((row for row in sells if row["realized_pnl"] is not None), key=lambda row: float(row["realized_pnl"]))
        worst_row = min((row for row in sells if row["realized_pnl"] is not None), key=lambda row: float(row["realized_pnl"]))
        best_exit = {"ticker": best_row["symbol"], "pnl": round(float(best_row["realized_pnl"]), 6)}
        worst_exit = {"ticker": worst_row["symbol"], "pnl": round(float(worst_row["realized_pnl"]), 6)}
    median_exit = float(pd.Series(realized_values).median()) if realized_values else None
    return {
        "trade_count": len(rows),
        "sell_count": len(sells),
        "buy_count": len(buys),
        "realized_exit_pnl": round(sum(realized_values), 6),
        "open_buy_mark_pnl": round(sum(open_values), 6),
        "winning_exits": sum(1 for value in realized_values if value > 0),
        "losing_exits": sum(1 for value in realized_values if value < 0),
        "winning_buys_on_mark": sum(1 for value in open_values if value > 0),
        "losing_buys_on_mark": sum(1 for value in open_values if value < 0),
        "median_exit_pnl": round(median_exit, 6) if median_exit is not None else None,
        "best_exit": best_exit,
        "worst_exit": worst_exit,
    }


def build_trade_day_pnl_artifact(
    *,
    trade_date: str,
    run_root: Path | str,
    broker_snapshot_path: Path | str | None = None,
    output_json_path: Path | str | None = None,
    output_csv_path: Path | str | None = None,
) -> dict[str, Any]:
    run_root_path = Path(run_root)
    pretrade_positions_path = run_root_path / "broker" / "pretrade_positions.json"
    snapshot_path = Path(broker_snapshot_path) if broker_snapshot_path else DEFAULT_BROKER_SNAPSHOT_DIR / f"broker_snapshot_{trade_date}.json"

    payload = None
    if not pretrade_positions_path.exists():
        payload = _empty_trade_day_payload(trade_date, run_root_path, f"missing_pretrade_positions:{pretrade_positions_path}")
    elif not snapshot_path.exists():
        payload = _empty_trade_day_payload(trade_date, run_root_path, f"missing_broker_snapshot:{snapshot_path}")
    else:
        pretrade_positions = _load_json_if_exists(pretrade_positions_path) or {}
        broker_snapshot = _load_json_if_exists(snapshot_path) or {}
        rows = _trade_day_rows(
            trade_date=trade_date,
            orders=_trade_day_unique_orders(broker_snapshot, trade_date),
            pretrade_by_symbol=_trade_day_pretrade_position_map(pretrade_positions),
            current_by_symbol=_trade_day_current_position_map(broker_snapshot),
        )
        summary = _trade_day_summary(rows)
        status = "COMPLETE"
        if not rows:
            status = "MISSING"
        else:
            any_missing = any(
                (row["side"] == "SELL" and row["realized_pnl"] is None)
                or (row["side"] == "BUY" and row["open_mark_pnl"] is None)
                for row in rows
            )
            if any_missing:
                status = "PARTIAL"
        payload = {
            "trade_date": trade_date,
            "run_id": run_root_path.name,
            "generated_at": _safe_iso_now(),
            "status": status,
            "reason": None,
            "summary": summary,
            "rows": rows,
            "source_paths": {
                "run_root": str(run_root_path),
                "pretrade_positions": str(pretrade_positions_path),
                "broker_snapshot": str(snapshot_path),
            },
        }

    json_path = Path(output_json_path) if output_json_path else run_root_path / "trade_day_pnl.json"
    csv_path = Path(output_csv_path) if output_csv_path else run_root_path / "trade_day_pnl.csv"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    rows_df = pd.DataFrame(payload.get("rows") or [])
    if rows_df.empty:
        rows_df = pd.DataFrame(
            columns=[
                "trade_date",
                "submitted_at",
                "filled_at",
                "symbol",
                "side",
                "qty",
                "fill_price",
                "pretrade_qty",
                "pretrade_avg_entry",
                "current_position_qty",
                "current_price",
                "status",
                "realized_pnl",
                "open_mark_pnl",
            ]
        )
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    rows_df.to_csv(csv_path, index=False)
    return payload


def _default_fetch_vix_close(start_date: str, end_date: str) -> pd.Series | None:
    """
    Default VIX fetch function using yfinance.
    
    Args:
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Returns:
        Series of VIX close prices indexed by date, or None on error
    """
    try:
        import yfinance as yf
        download_vix = yf.download("^VIX", start=start_date, end=end_date, progress=False)
        if download_vix.empty:
            return None
        if isinstance(download_vix, pd.DataFrame):
            return download_vix["Close"]
        return download_vix
    except Exception:
        return None


@dataclass
class AnalyzerRow:
    date: str
    premarket_score: float | None
    bearish_flag: bool | None
    signal_bucket: str | None
    analyzer_version: str | None
    notes: str | None
    vix_component: float | None
    trend_component: float | None
    realized_vol_component: float | None
    gap_risk_component: float | None
    breadth_component: float | None
    macro_component: float | None


def _extract_score(data: dict[str, Any]) -> float | None:
    analyzer_raw = data.get("market_analyzer")
    analyzer: dict[str, Any] = analyzer_raw if isinstance(analyzer_raw, dict) else {}
    for key in ("premarket_score", "score", "pre_market_score"):
        if key in analyzer:
            try:
                value = analyzer.get(key)
                return float(value) if value is not None else None
            except Exception:
                return None
    if "premarket_score" in data:
        try:
            value = data.get("premarket_score")
            return float(value) if value is not None else None
        except Exception:
            return None
    return None


def _extract_row(payload: dict[str, Any], fallback_date: str | None = None) -> AnalyzerRow | None:
    date = str(payload.get("snapshot_date") or payload.get("trade_date") or fallback_date or "").strip()
    if not date:
        return None

    score = _extract_score(payload)
    analyzer_raw = payload.get("market_analyzer")
    analyzer: dict[str, Any] = analyzer_raw if isinstance(analyzer_raw, dict) else {}
    version = None
    for k in ("version", "analyzer_version", "model_version"):
        if k in analyzer:
            version = str(analyzer.get(k))
            break

    bearish_flag: bool | None = None
    signal_bucket: str | None = None
    notes: str | None = None

    def _opt_float(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    vix_component = _opt_float(analyzer.get("vix_component"))
    trend_component = _opt_float(analyzer.get("trend_component"))
    realized_vol_component = _opt_float(analyzer.get("realized_vol_component"))
    gap_risk_component = _opt_float(analyzer.get("gap_risk_component"))
    breadth_component = _opt_float(analyzer.get("breadth_component"))
    macro_component = _opt_float(analyzer.get("macro_component"))

    if isinstance(analyzer, dict) and analyzer:
        if "bearish_flag" in analyzer:
            bearish_flag = bool(analyzer.get("bearish_flag"))
        if "signal_bucket" in analyzer:
            signal_bucket = str(analyzer.get("signal_bucket"))
        if "notes" in analyzer:
            notes = str(analyzer.get("notes"))

    breaker_raw = payload.get("breaker")
    breaker: dict[str, Any] = breaker_raw if isinstance(breaker_raw, dict) else {}
    breaker_component = _opt_float(breaker.get("exposure_multiplier_today"))
    if score is None and breaker_component is not None:
        score = breaker_component
        notes = notes or "derived_from_breaker: exposure_multiplier_today"

    if signal_bucket is None and breaker:
        signal_bucket = str(breaker.get("exposure_label_today") or breaker.get("mode") or "").strip().upper() or None

    if bearish_flag is None and score is not None:
        bearish_flag = bool(float(score) <= 0.5)

    if score is None:
        notes = notes or "adapter_only: market_analyzer score not present in source payload"

    return AnalyzerRow(
        date=date,
        premarket_score=score,
        bearish_flag=bearish_flag,
        signal_bucket=signal_bucket,
        analyzer_version=version,
        notes=notes,
        vix_component=vix_component,
        trend_component=trend_component,
        realized_vol_component=realized_vol_component,
        gap_risk_component=gap_risk_component,
        breadth_component=breadth_component,
        macro_component=macro_component,
    )


def _rows_from_json_dir(path: Path) -> list[AnalyzerRow]:
    rows: list[AnalyzerRow] = []
    if not path.exists():
        return rows
    for file in sorted(path.glob("*.json")):
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        row = _extract_row(payload, fallback_date=file.stem.split(".")[0])
        if row is not None:
            rows.append(row)
    return rows


def rebuild_premarket_analyzer_scores(
    *,
    signals_dir: Path = DEFAULT_SIGNALS_DIR,
    execution_email_dir: Path = DEFAULT_EXECUTION_EMAIL_DIR,
    output_path: Path = DEFAULT_ANALYZER_PATH,
) -> pd.DataFrame:
    all_rows = _rows_from_json_dir(signals_dir) + _rows_from_json_dir(execution_email_dir)
    if not all_rows:
        out = pd.DataFrame(
            columns=[
                "date",
                "premarket_score",
                "bearish_flag",
                "signal_bucket",
                "analyzer_version",
                "notes",
                "vix_component",
                "trend_component",
                "realized_vol_component",
                "gap_risk_component",
                "breadth_component",
                "macro_component",
            ]
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(output_path, index=False)
        return out

    df = pd.DataFrame([r.__dict__ for r in all_rows])
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df[df["date"].notna()].copy()

    # Deterministic date-level choice: prefer rows with score, then with bucket, then stable sort.
    df["_score_rank"] = df["premarket_score"].notna().astype(int)
    df["_bucket_rank"] = df["signal_bucket"].notna().astype(int)
    df = df.sort_values(["date", "_score_rank", "_bucket_rank"], ascending=[True, False, False])
    df = df.drop_duplicates(subset=["date"], keep="first")

    out = df[["date", "premarket_score", "bearish_flag", "signal_bucket", "analyzer_version", "notes"]].sort_values("date")
    out = df[
        [
            "date",
            "premarket_score",
            "bearish_flag",
            "signal_bucket",
            "analyzer_version",
            "notes",
            "vix_component",
            "trend_component",
            "realized_vol_component",
            "gap_risk_component",
            "breadth_component",
            "macro_component",
        ]
    ].sort_values("date")
    out = out.reset_index(drop=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(output_path, index=False)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build benchmark, VIX, analyzer, and dashboard-support producer artifacts"
    )
    parser.add_argument("--asof-date", required=True, help="As-of date for benchmark close history update")
    parser.add_argument("--benchmark-out", default=str(DEFAULT_BENCHMARK_PATH), help="Benchmark output CSV path")
    parser.add_argument(
        "--benchmark-relative-out",
        default=str(DEFAULT_BENCHMARK_RELATIVE_PATH),
        help="Benchmark-relative series output CSV path",
    )
    parser.add_argument("--vix-out", default=str(DEFAULT_VIX_PATH), help="VIX output CSV path")
    parser.add_argument("--analyzer-out", default=str(DEFAULT_ANALYZER_PATH), help="Analyzer output CSV path")
    parser.add_argument(
        "--concentration-history-out",
        default=str(DEFAULT_CONCENTRATION_HISTORY_PATH),
        help="Concentration history output CSV path",
    )
    parser.add_argument(
        "--construction-parity-dir",
        default=str(DEFAULT_CONSTRUCTION_PARITY_DIR),
        help="Directory for construction parity JSON artifacts",
    )
    parser.add_argument("--signals-dir", default=str(DEFAULT_SIGNALS_DIR), help="Signals JSON directory")
    parser.add_argument("--execution-email-dir", default=str(DEFAULT_EXECUTION_EMAIL_DIR), help="Execution email JSON directory")
    parser.add_argument("--run-root", default="", help="Optional run root for trade-day P&L artifact generation")
    parser.add_argument(
        "--trade-date",
        default="",
        help="Optional trade date for trade-day P&L and construction parity. Defaults to --asof-date.",
    )
    parser.add_argument(
        "--broker-snapshot",
        default="",
        help="Optional broker snapshot JSON path for trade-day P&L artifact generation",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    bench = update_benchmark_close_history(
        asof_date=args.asof_date,
        output_path=Path(args.benchmark_out),
    )
    logger.info("[PERF_PRODUCERS] benchmark_rows=%d output=%s", len(bench), args.benchmark_out)

    vix = update_vix_close_history(
        asof_date=args.asof_date,
        output_path=Path(args.vix_out),
    )
    logger.info("[PERF_PRODUCERS] vix_rows=%d output=%s", len(vix), args.vix_out)

    analyzer = rebuild_premarket_analyzer_scores(
        signals_dir=Path(args.signals_dir),
        execution_email_dir=Path(args.execution_email_dir),
        output_path=Path(args.analyzer_out),
    )
    logger.info("[PERF_PRODUCERS] analyzer_rows=%d output=%s", len(analyzer), args.analyzer_out)

    bench_relative = build_benchmark_relative_series(
        nav_timeseries_path=Path("outputs/perf/nav_timeseries.csv"),
        benchmark_path=Path(args.benchmark_out),
        output_path=Path(args.benchmark_relative_out),
    )
    logger.info(
        "[PERF_PRODUCERS] benchmark_relative_rows=%d output=%s",
        len(bench_relative),
        args.benchmark_relative_out,
    )

    concentration = build_concentration_history(
        holdings_dir=Path("outputs/perf"),
        nav_timeseries_path=Path("outputs/perf/nav_timeseries.csv"),
        output_path=Path(args.concentration_history_out),
    )
    logger.info(
        "[PERF_PRODUCERS] concentration_history_rows=%d output=%s",
        len(concentration),
        args.concentration_history_out,
    )

    trade_date = str(args.trade_date or args.asof_date).strip()
    parity = build_construction_parity_artifact(
        asof_date=trade_date,
        concentration_history_path=Path(args.concentration_history_out),
        output_dir=Path(args.construction_parity_dir),
    )
    logger.info(
        "[PERF_PRODUCERS] construction_parity_status=%s output=%s",
        parity.get("status"),
        Path(args.construction_parity_dir) / f"construction_parity_{trade_date}.json",
    )

    if str(args.run_root or "").strip():
        trade_pnl = build_trade_day_pnl_artifact(
            trade_date=trade_date,
            run_root=Path(args.run_root),
            broker_snapshot_path=Path(args.broker_snapshot) if str(args.broker_snapshot or "").strip() else None,
        )
        logger.info(
            "[PERF_PRODUCERS] trade_day_pnl_status=%s run_root=%s",
            trade_pnl.get("status"),
            args.run_root,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
