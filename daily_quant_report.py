"""
daily_quant_report.py

Keep module import side-effects minimal so `-h/--help` returns immediately.
Heavy modules are loaded lazily after argparse parsing.
"""

import datetime as dt
import json
import argparse
import uuid
import logging
import math
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
from paper.signals_io import write_signals_snapshot
from paper.state_paths import (
    ensure_paper_state_files,
    LEDGER_HEADERS as PAPER_LEDGER_HEADERS,
    TRADES_HEADERS as PAPER_TRADES_HEADERS,
)
from paper.paper_report import build_paper_report_html
from paper.build_execution_email import build_execution_email_html, build_execution_email_text
from paper.alpha import compute_alpha_attribution
from paper.email_styles import base_email_css
from paper.trading_calendar import prev_trading_day
from paper.ledger import compute_signal_hash
from paper.ledger2 import (
    append_rows as append_ledger2_rows,
    payload_to_rows as ledger2_payload_to_rows,
    LEDGER2_COLUMNS,
)
from paper.paths import (
    LEDGER_TRADES_PATH,
    ensure_no_legacy_ledger,
)
from paper.nav2 import update_nav
from paper.reporting_consistency import compute_exposure, determine_sleeve_state
from core.benchmark_v4 import update_inception_nav_series, INCEPTION_DATE
from reporting.attribution import compute_daily_attribution, write_attribution_outputs
from research.signal_store import persist_signal_snapshot
from engine.breaker import get_breaker_config, apply_portfolio_exposure_overlay

from sleeves.sleeve_trend.build_sleeve_output import build_trend_sleeve_output
from sleeves.sleeve_trend import config as trend_cfg
# ============================================================
# Portfolio allocation (dynamic)
# ============================================================
from core.portfolio_alloc import (  # noqa: E402
    PortfolioAllocator,
    SleeveOutput,
    AllocationResult,
    create_sleeve_output,
    allocation_summary_df,
    holdings_snapshot_df,
    validate_allocation_result,
    CASH_TICKER,
    DEFAULT_PORTFOLIO_BASE_EQUITY,
    WEIGHT_TOLERANCE,
)
from core.alpha_attribution import load_benchmark_prices  # noqa: E402
from engine.backtest_engine import (  # noqa: E402
    infer_latest_entries,
    attach_entry_prices,
)

# Lazy-imported symbols (defined for monkeypatch compatibility in tests).
run_paper_day = None
reset_orders_sent_ledger_for_date = None
fetch_prev_closes_yfinance = None
send_email = None
download_prices = None
add_atr = None
s1_prepare_data = None
s1_backtest = None
st_prepare_data = None
st_backtest = None
write_audit_bundle = None
audit_default_run_id = None
load_sleeve1_dataset = None
run_window_backtest = None

logger = logging.getLogger(__name__)
# Backward-compatible alias for tests/patch points
calc_alpha_stats = compute_alpha_attribution
# ============================================================
# Output config
# ============================================================
OUTPUT_DIR = "outputs/daily"
DATE_FORMAT = os.getenv("DATE_FORMAT", "US")
DISPLAY_DECIMALS = int(os.getenv("DISPLAY_DECIMALS", "2"))
CHARLIE_MIN = 0.20
CHARLIE_MAX = 0.30
STOP_ATR_MULT_DEFAULT = 2.0
TAKE_PROFIT_ATR_MULT_DEFAULT = 3.0
STOP_PCT_DEFAULT = 0.08
TAKE_PROFIT_PCT_DEFAULT = 0.12


def _ensure_paper_broker_imports() -> None:
    """Load paper broker functions lazily to keep CLI help lightweight."""
    global run_paper_day, reset_orders_sent_ledger_for_date, fetch_prev_closes_yfinance
    if (
        run_paper_day is not None
        and reset_orders_sent_ledger_for_date is not None
        and fetch_prev_closes_yfinance is not None
    ):
        return
    from paper.paper_broker import (
        run_paper_day as _run_paper_day,
        reset_orders_sent_ledger_for_date as _reset_orders_sent_ledger_for_date,
        fetch_prev_closes_yfinance as _fetch_prev_closes_yfinance,
    )
    if run_paper_day is None:
        run_paper_day = _run_paper_day
    if reset_orders_sent_ledger_for_date is None:
        reset_orders_sent_ledger_for_date = _reset_orders_sent_ledger_for_date
    if fetch_prev_closes_yfinance is None:
        fetch_prev_closes_yfinance = _fetch_prev_closes_yfinance


def _ensure_quant_report_imports() -> None:
    """Load quant report helpers lazily to avoid yfinance import at module load."""
    global send_email, download_prices, add_atr
    if send_email is not None and download_prices is not None and add_atr is not None:
        return
    from core.quant_report import (
        send_email as _send_email,
        download_prices as _download_prices,
        add_atr as _add_atr,
    )
    if send_email is None:
        send_email = _send_email
    if download_prices is None:
        download_prices = _download_prices
    if add_atr is None:
        add_atr = _add_atr


def _ensure_sleeve_backtest_imports() -> None:
    """Load sleeve backtest modules lazily to avoid import-time print side effects."""
    global s1_prepare_data, s1_backtest, st_prepare_data, st_backtest
    if (
        s1_prepare_data is not None
        and s1_backtest is not None
        and st_prepare_data is not None
        and st_backtest is not None
    ):
        return
    from sleeves.sleeve_1.backtest import (
        prepare_data as _s1_prepare_data,
        backtest as _s1_backtest,
    )
    from sleeves.sleeve_trend.backtest import (
        prepare_data as _st_prepare_data,
        backtest as _st_backtest,
    )
    if s1_prepare_data is None:
        s1_prepare_data = _s1_prepare_data
    if s1_backtest is None:
        s1_backtest = _s1_backtest
    if st_prepare_data is None:
        st_prepare_data = _st_prepare_data
    if st_backtest is None:
        st_backtest = _st_backtest


def _ensure_audit_imports() -> None:
    """Load audit/backtest modules lazily to avoid import-time side effects on CLI help."""
    global write_audit_bundle, audit_default_run_id, load_sleeve1_dataset, run_window_backtest
    if (
        write_audit_bundle is not None
        and audit_default_run_id is not None
        and load_sleeve1_dataset is not None
        and run_window_backtest is not None
    ):
        return
    from audit.export import write_audit_bundle as _write_audit_bundle
    from audit.policy_backtest import (
        default_run_id as _audit_default_run_id,
        load_sleeve1_dataset as _load_sleeve1_dataset,
        run_window_backtest as _run_window_backtest,
    )
    if write_audit_bundle is None:
        write_audit_bundle = _write_audit_bundle
    if audit_default_run_id is None:
        audit_default_run_id = _audit_default_run_id
    if load_sleeve1_dataset is None:
        load_sleeve1_dataset = _load_sleeve1_dataset
    if run_window_backtest is None:
        run_window_backtest = _run_window_backtest


def _snapshot_risk_value(name: str, default: float) -> float:
    env_val = os.getenv(name)
    if env_val:
        try:
            return float(env_val)
        except Exception:
            logger.warning("[SNAPSHOT] Invalid %s env value '%s'; using default %.4f", name, env_val, default)
    try:
        with open("paper/config_paper.json", "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        risk_cfg = cfg.get("risk") or {}
        config_val = risk_cfg.get(name.lower())
        if config_val is not None:
            return float(config_val)
    except Exception:
        pass
    return default
# ============================================================
# Helpers
# ============================================================
def _safe_df(df):
    return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
def _fmt_money(x):
    if isinstance(x, str) and x.strip().startswith("$"):
        return x
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "n/a"
def _fmt_pct(x):
    if isinstance(x, str) and "%" in x:
        return x
    try:
        return f"{100 * float(x):.{DISPLAY_DECIMALS}f}%"
    except Exception:
        return "n/a"
def _fmt_number(x):
    if isinstance(x, str):
        return x
    try:
        return f"{float(x):,.{DISPLAY_DECIMALS}f}"
    except Exception:
        return "n/a"
def _alpha_min_overlap_days(default: int = 5) -> int:
    env_val = os.getenv("ALPHA_MIN_OVERLAP_DAYS")
    if env_val:
        try:
            return max(1, int(env_val))
        except Exception:
            pass
    try:
        with open("paper/config_paper.json", "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
        return max(1, int((((cfg.get("reporting") or {}).get("alpha_min_overlap_days")) or default)))
    except Exception:
        return default
def _fmt_date(value) -> str:
    try:
        dt_value = pd.to_datetime(value)
        if DATE_FORMAT.upper() == "US":
            return dt_value.strftime("%m/%d/%Y")
        return dt_value.strftime("%Y-%m-%d")
    except Exception:
        return "n/a"


def _ranked_signal_tickers(signals_df: pd.DataFrame | None) -> list[str]:
    signals_df = _safe_df(signals_df)
    if signals_df.empty:
        return []
    if "ticker" in signals_df.columns:
        ranked = signals_df.copy()
        if "rank" in ranked.columns:
            ranked = ranked.sort_values("rank", ascending=True)
        elif "score" in ranked.columns:
            ranked = ranked.sort_values("score", ascending=False)
        return [str(t).strip().upper() for t in ranked["ticker"].tolist() if str(t).strip()]
    latest = signals_df.iloc[-1]
    if isinstance(latest, pd.Series):
        ranked = latest.sort_values(ascending=False)
        return [str(t).strip().upper() for t in ranked.index.tolist() if str(t).strip()]
    return []


def _expand_sleeve_holdings_for_cap(
    sleeve_output: SleeveOutput,
    max_position_weight: float,
    target_cash_weight: float,
    ranked_candidates: list[str] | None,
) -> tuple[SleeveOutput, dict]:
    cap = float(max_position_weight or 0.0)
    target_cash = float(target_cash_weight or 0.0)
    positions_df = _safe_df(sleeve_output.positions_df).copy()
    diagnostics = {
        "selected_names": int(len(positions_df)),
        "min_required_names": None,
        "constraint": f"max_position_weight={cap:.0%}" if cap > 0 else "none",
    }
    if cap <= WEIGHT_TOLERANCE or target_cash > WEIGHT_TOLERANCE:
        return sleeve_output, diagnostics
    min_required = max(1, int(math.ceil(1.0 / cap)))
    diagnostics["min_required_names"] = min_required
    if len(positions_df) >= min_required:
        return sleeve_output, diagnostics
    existing = set(positions_df.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().tolist())
    additions: list[dict] = []
    for ticker in (ranked_candidates or []):
        t = str(ticker).strip().upper()
        if not t or t in existing:
            continue
        additions.append({"ticker": t, "target_weight": 0.0, "reason": "cap_fill", "signal_strength": 1.0})
        existing.add(t)
        if len(existing) >= min_required:
            break
    if additions:
        positions_df = pd.concat([positions_df, pd.DataFrame(additions)], ignore_index=True)
    if len(positions_df) > 0:
        positions_df["target_weight"] = 1.0 / len(positions_df)
    diagnostics["selected_names"] = int(len(positions_df))
    return create_sleeve_output(
        positions_df,
        sleeve_output.meta.sleeve_name,
        strength=sleeve_output.meta.strength,
        notes=sleeve_output.meta.notes,
    ), diagnostics


def _benchmark_since_inception_stats(bench_prices: pd.Series | pd.DataFrame | None) -> dict:
    if isinstance(bench_prices, pd.DataFrame):
        series = bench_prices["close"] if "close" in bench_prices.columns else pd.Series(dtype=float)
    elif isinstance(bench_prices, pd.Series):
        series = bench_prices
    else:
        series = pd.Series(dtype=float)
    series = series.dropna()
    if series.empty:
        return {"cumulative_return": None, "max_drawdown": None, "start": None, "end": None}
    equity = series / float(series.iloc[0])
    peak = equity.cummax()
    drawdown = (equity / peak) - 1.0
    return {
        "cumulative_return": float((series.iloc[-1] / series.iloc[0]) - 1.0),
        "max_drawdown": float(drawdown.min()),
        "start": pd.to_datetime(series.index.min()).strftime("%Y-%m-%d"),
        "end": pd.to_datetime(series.index.max()).strftime("%Y-%m-%d"),
    }
def _asof_date_from_df(df: pd.DataFrame) -> pd.Timestamp | None:
    if df is None or df.empty:
        return None
    if "date" in df.columns:
        return pd.to_datetime(df["date"]).max()
    return None


_REPORT_DATE_PLACEHOLDERS = {
    "yyyy-mm-dd",
    "yyyy/mm/dd",
    "<date>",
    "date",
}


def _parse_report_date_env(raw_value: str | None) -> pd.Timestamp | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    lowered = value.lower()
    if lowered in _REPORT_DATE_PLACEHOLDERS:
        raise ValueError(
            "REPORT_DATE is set to placeholder 'YYYY-MM-DD'. "
            "Set it to an actual date, for example: export REPORT_DATE=2026-02-24"
        )
    try:
        return pd.to_datetime(value).normalize()
    except Exception as exc:
        raise ValueError(
            f"Invalid REPORT_DATE='{value}'. Use YYYY-MM-DD, for example: 2026-02-24"
        ) from exc


def _infer_report_date(
    *,
    sleeve_details: list[dict | None] | None,
    fallback: pd.Timestamp,
) -> pd.Timestamp:
    report_date_env = _parse_report_date_env(os.getenv("REPORT_DATE", ""))
    if report_date_env is not None:
        return report_date_env
    asof_candidates: list[pd.Timestamp] = []
    target_weight_candidates: list[pd.Timestamp] = []
    for details in (sleeve_details or []):
        if not isinstance(details, dict):
            continue
        asof = details.get("asof")
        if asof is not None:
            asof_candidates.append(pd.to_datetime(asof).normalize())
        target_weights = details.get("target_weights")
        if isinstance(target_weights, pd.DataFrame) and not target_weights.empty:
            try:
                target_weight_candidates.append(
                    pd.to_datetime(target_weights.index).max().normalize()
                )
            except Exception:
                pass
    if asof_candidates:
        return max(asof_candidates)
    if target_weight_candidates:
        return max(target_weight_candidates)
    return pd.to_datetime(fallback).normalize()
def _write_execution_email_payload(payload: dict, run_date: str) -> tuple[str, bool, str | None]:
    out_dir = Path("outputs") / "execution_email"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_date}.json"
    preserved = False
    preserved_path = None
    write_path = out_path
    if out_path.exists():
        try:
            existing_payload = json.loads(out_path.read_text(encoding="utf-8"))
        except Exception:
            existing_payload = {}
        existing_num = _payload_num_trades(existing_payload)
        incoming_num = _payload_num_trades(payload)
        incoming_status = str((payload or {}).get("execution_status", "")).upper() if isinstance(payload, dict) else ""
        if existing_num > 0 and incoming_num == 0:
            suffix = "halted" if incoming_status == "HALTED" else "empty"
            ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            write_path = out_dir / f"{run_date}.{suffix}.{ts}.json"
            preserved = True
            preserved_path = str(out_path)
            logger.warning(
                "[EXECUTION_EMAIL] preserving non-empty payload=%s; writing new %s payload to %s",
                out_path,
                suffix,
                write_path,
            )
    with open(write_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    logger.info("[EXECUTION_EMAIL] payload written: %s", write_path)
    return str(write_path), preserved, preserved_path
def write_integrity_artifact(asof_date: str, payload: dict) -> str:
    integrity_path = Path("outputs") / "daily" / f"integrity_{asof_date}.json"
    integrity_path.parent.mkdir(parents=True, exist_ok=True)
    integrity_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return str(integrity_path)


def write_health_artifact(trade_date: str, payload: dict) -> str:
    health_path = Path("outputs") / "daily" / f"health_{trade_date}.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return str(health_path)


def _finalize_health_payload(trade_date: str, health_payload: dict) -> str:
    path = write_health_artifact(trade_date, health_payload)
    logger.info(
        "[HEALTH] status=%s model_equity=%s broker_equity=%s exec_equity=%s ledger_path=%s",
        str(health_payload.get("status", "UNKNOWN")).upper(),
        health_payload.get("model_equity_recon"),
        health_payload.get("broker_equity"),
        health_payload.get("execution_basis_equity"),
        health_payload.get("ledger_path_used") or str(LEDGER_TRADES_PATH),
    )
    if str(health_payload.get("status", "PASS")).upper() == "FAIL":
        logger.error("[HEALTH][FAIL] %s", health_payload.get("error") or "health_check_failed")
        raise AssertionError(str(health_payload.get("error") or "health_check_failed"))
    return str(path)


def _ensure_csv_with_headers(path: str | Path, headers: list[str]) -> None:
    """
    Create CSV with headers if missing/empty.
    Never overwrite a non-empty file (append-only safety).
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        pd.DataFrame(columns=headers).to_csv(p, index=False)
        return
    try:
        if p.stat().st_size == 0:
            pd.DataFrame(columns=headers).to_csv(p, index=False)
    except FileNotFoundError:
        pd.DataFrame(columns=headers).to_csv(p, index=False)


def _apply_paper_reset(
    *,
    trade_date: str,
    paper_start_cash: float,
    paper_ledger_path: str,
    paper_trades_path: str,
) -> dict:
    ensure_no_legacy_ledger(logger=logger, when="paper_reset_pre")
    start_cash = float(paper_start_cash)

    _ensure_csv_with_headers(paper_ledger_path, PAPER_LEDGER_HEADERS)
    _ensure_csv_with_headers(paper_trades_path, PAPER_TRADES_HEADERS)
    ledger2_path = LEDGER_TRADES_PATH
    _ensure_csv_with_headers(ledger2_path, LEDGER2_COLUMNS)
    _ensure_csv_with_headers(
        "outputs/shadow_orders/orders_sent.csv",
        ["date", "run_id", "order_id", "ticker", "side"],
    )

    nav_seed = pd.DataFrame(
        [
            {
                "date": str(trade_date),
                "equity": start_cash,
                "cash": start_cash,
                "gross_exposure": 0.0,
                "net_exposure": 0.0,
                "return_1d": 0.0,
                "turnover_dollars": 0.0,
                "turnover_pct": 0.0,
                "turnover": 0.0,
            }
        ]
    )
    nav_path = Path("outputs/perf/nav_timeseries.csv")
    nav_path.parent.mkdir(parents=True, exist_ok=True)
    nav_seed.to_csv(nav_path, index=False)

    return {
        "start_cash": start_cash,
        "paper_ledger_path": str(paper_ledger_path),
        "paper_trades_path": str(paper_trades_path),
        "ledger2_path": str(ledger2_path),
        "nav_timeseries_path": str(nav_path),
        "orders_sent_path": "outputs/shadow_orders/orders_sent.csv",
    }


def _load_execution_ledger_dedup(ledger_path: str | None) -> tuple[pd.DataFrame, int]:
    ensure_no_legacy_ledger(logger=logger, when="health_ledger_load")
    if not ledger_path:
        return pd.DataFrame(), 0
    path = Path(ledger_path)
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(), 0
    df = pd.read_csv(path)
    if df.empty:
        return df, 0

    removed = 0
    if all(col in df.columns for col in ("trade_date", "order_id")):
        before = len(df)
        sort_cols = ["trade_date"]
        if "timestamp_et" in df.columns:
            sort_cols.append("timestamp_et")
        df = (
            df.sort_values(sort_cols, na_position="last")
            .drop_duplicates(subset=["trade_date", "order_id"], keep="last")
            .reset_index(drop=True)
        )
        removed = before - len(df)

    for col in ("quantity", "fill_price", "notional", "fees"):
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    for col in ("trade_date", "ticker", "side"):
        if col not in df.columns:
            df[col] = ""
    return df, int(removed)


def _compute_execution_basis_metrics(
    *,
    trade_date: str,
    ledger_path: str | None,
    starting_cash: float,
) -> dict[str, float | int | None]:
    df, dedup_removed = _load_execution_ledger_dedup(ledger_path)
    ledger_rows_total = int(len(df))
    if df.empty:
        return {
            "equity": float(starting_cash),
            "cash": float(starting_cash),
            "holdings_value": 0.0,
            "turnover_dollars": 0.0,
            "turnover_pct": 0.0,
            "prior_equity_for_turnover": float(starting_cash),
            "rows_used": 0,
            "dedup_removed": int(dedup_removed),
            "ledger_rows_total": int(ledger_rows_total),
        }

    up_to_date = df[df["trade_date"].astype(str) <= str(trade_date)].copy()
    if "execution_status" in up_to_date.columns:
        exec_status = up_to_date["execution_status"].astype(str).str.upper()
        executed_mask = exec_status.isin({"FILLED", "FILLED_ESTIMATE", "READY", "EXECUTED"})
        up_to_date = up_to_date[executed_mask].copy()
    if up_to_date.empty:
        return {
            "equity": float(starting_cash),
            "cash": float(starting_cash),
            "holdings_value": 0.0,
            "turnover_dollars": 0.0,
            "turnover_pct": 0.0,
            "prior_equity_for_turnover": float(starting_cash),
            "rows_used": 0,
            "dedup_removed": int(dedup_removed),
            "ledger_rows_total": int(ledger_rows_total),
        }

    sort_cols = ["trade_date"]
    if "timestamp_et" in up_to_date.columns:
        sort_cols.append("timestamp_et")
    up_to_date = up_to_date.sort_values(sort_cols, na_position="last").reset_index(drop=True)

    cash = float(starting_cash)
    holdings: dict[str, float] = {}
    last_fill: dict[str, float] = {}
    turnover_by_date: dict[str, float] = {}
    prior_equity_by_date: dict[str, float] = {}
    current_date: str | None = None

    for _, row in up_to_date.iterrows():
        row_date = str(row.get("trade_date") or "")
        if not row_date:
            continue
        if row_date != current_date:
            holdings_value = float(
                sum(
                    max(0.0, float(shares)) * float(last_fill.get(ticker, 0.0))
                    for ticker, shares in holdings.items()
                )
            )
            prior_equity_by_date[row_date] = float(cash + holdings_value)
            turnover_by_date.setdefault(row_date, 0.0)
            current_date = row_date

        ticker = str(row.get("ticker") or "").upper()
        side = str(row.get("side") or "").upper()
        qty = float(row.get("quantity") or 0.0)
        fill_price = float(row.get("fill_price") or 0.0)
        fees = float(row.get("fees") or 0.0)
        if qty <= 0 or fill_price <= 0:
            continue

        row_notional = float(row.get("notional") or 0.0)
        notional = abs(row_notional) if abs(row_notional) > 0 else abs(qty * fill_price)

        if side == "BUY":
            cash -= notional + fees
            holdings[ticker] = max(0.0, float(holdings.get(ticker, 0.0)) + qty)
            last_fill[ticker] = fill_price
        elif side == "SELL":
            prev_shares = max(0.0, float(holdings.get(ticker, 0.0)))
            sell_qty = min(qty, prev_shares)
            cash += (sell_qty * fill_price) - fees
            holdings[ticker] = max(0.0, prev_shares - sell_qty)
            last_fill[ticker] = fill_price

        turnover_by_date[row_date] = float(turnover_by_date.get(row_date, 0.0) + notional)

    holdings_value = float(
        sum(
            max(0.0, float(shares)) * float(last_fill.get(ticker, 0.0))
            for ticker, shares in holdings.items()
        )
    )
    equity = float(cash + holdings_value)
    prior_equity = float(prior_equity_by_date.get(str(trade_date), starting_cash))
    turnover_dollars = float(turnover_by_date.get(str(trade_date), 0.0))
    turnover_pct = float(turnover_dollars / prior_equity) if prior_equity > 0 else 0.0
    return {
        "equity": equity,
        "cash": float(cash),
        "holdings_value": holdings_value,
        "turnover_dollars": turnover_dollars,
        "turnover_pct": turnover_pct,
        "prior_equity_for_turnover": prior_equity,
        "rows_used": int(len(up_to_date)),
        "dedup_removed": int(dedup_removed),
        "ledger_rows_total": int(ledger_rows_total),
    }


def _build_health_payload(
    *,
    trade_date: str,
    paper_summary: dict | None,
    execution_payload: dict | None,
    nav_ts_path: str | None,
    ledger_path: str | None = str(LEDGER_TRADES_PATH),
    should_execute: bool,
    leverage_enabled: bool,
    tolerance: float = 1e-6,
    execution_equity_tolerance_dollars: float = 5.0,
    execution_equity_tolerance_bps: float = 5.0,
) -> dict:
    summary = paper_summary or {}
    payload = execution_payload or {}
    warnings: list[str] = []
    errors: list[str] = []

    planned_trade_count = int(len((summary.get("trade_plan") or []))) if isinstance(summary.get("trade_plan"), list) else int(len((payload.get("trades") or [])))
    executed_trade_count = int(summary.get("num_trades") or 0)
    broker_equity = _coerce_float_or_none(summary.get("total_equity"))
    broker_cash = _coerce_float_or_none(summary.get("cash"))
    achieved_cash_weight = _coerce_float_or_none(summary.get("achieved_cash_weight"))
    gross_exposure = _coerce_float_or_none(summary.get("gross_exposure"))
    net_exposure = _coerce_float_or_none(summary.get("net_exposure"))
    turnover_dollars = _coerce_float_or_none(summary.get("turnover_notional")) or 0.0
    turnover_pct = _coerce_float_or_none(summary.get("turnover_pct")) or 0.0
    broker_reconciliation = (
        summary.get("broker_reconciliation")
        if isinstance(summary.get("broker_reconciliation"), dict)
        else {}
    )
    model_equity_recon = _coerce_float_or_none(
        broker_reconciliation.get("model_equity")
    )
    broker_equity_recon = _coerce_float_or_none(
        broker_reconciliation.get("broker_equity")
    )
    recon_delta = None
    if model_equity_recon is not None and broker_equity_recon is not None:
        recon_delta = float(broker_equity_recon) - float(model_equity_recon)
    recon_tolerance = _coerce_float_or_none(
        broker_reconciliation.get("equity_tolerance")
    )
    if broker_equity is None and broker_equity_recon is not None:
        broker_equity = float(broker_equity_recon)
    if recon_delta is None and model_equity_recon is not None and broker_equity is not None:
        recon_delta = float(broker_equity) - float(model_equity_recon)

    nav_equity_last_row = None
    if nav_ts_path and Path(nav_ts_path).exists() and Path(nav_ts_path).stat().st_size > 0:
        nav_ts = pd.read_csv(nav_ts_path)
        if not nav_ts.empty and "date" in nav_ts.columns:
            nav_ts["date"] = pd.to_datetime(nav_ts["date"])
            before = len(nav_ts)
            nav_ts = nav_ts.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
            if len(nav_ts) != before:
                warnings.append(f"nav_timeseries_duplicates_removed:{before - len(nav_ts)}")
                nav_ts["date"] = nav_ts["date"].dt.strftime("%Y-%m-%d")
                nav_ts.to_csv(nav_ts_path, index=False)
            nav_equity_last_row = _coerce_float_or_none(nav_ts.iloc[-1].get("equity"))

    execution_basis = _compute_execution_basis_metrics(
        trade_date=str(trade_date),
        ledger_path=ledger_path,
        starting_cash=float(os.getenv("PAPER_START_CASH", "10000")),
    )
    execution_basis_equity = _coerce_float_or_none(execution_basis.get("equity"))
    execution_basis_cash = _coerce_float_or_none(execution_basis.get("cash"))
    execution_basis_holdings_value = _coerce_float_or_none(execution_basis.get("holdings_value"))
    execution_basis_turnover_dollars = _coerce_float_or_none(execution_basis.get("turnover_dollars")) or 0.0
    execution_basis_turnover_pct = _coerce_float_or_none(execution_basis.get("turnover_pct")) or 0.0
    execution_basis_rows_used = int(execution_basis.get("rows_used") or 0)
    execution_basis_dedup_removed = int(execution_basis.get("dedup_removed") or 0)
    execution_vs_broker_equity_delta = None
    execution_vs_broker_equity_tolerance = None
    if execution_basis_dedup_removed > 0:
        warnings.append(f"ledger_duplicates_removed:{execution_basis_dedup_removed}")

    market_guard = summary.get("market_guard") if isinstance(summary.get("market_guard"), dict) else {}
    market_guard_status = str(market_guard.get("status") or summary.get("market_status") or "UNKNOWN").upper()
    run_id = str(summary.get("run_id") or payload.get("run_id") or "")

    if should_execute:
        if execution_basis_rows_used > 0:
            turnover_dollars = float(execution_basis_turnover_dollars)
            turnover_pct = float(execution_basis_turnover_pct)
        else:
            warnings.append("execution_basis_rows_missing")
        if broker_equity is None:
            errors.append(
                "health_check_failed: missing broker equity on execution run"
            )
        if (
            model_equity_recon is None
            and execution_basis_rows_used > 0
            and execution_basis_equity is None
        ):
            errors.append(
                "health_check_failed: missing broker/execution-basis equity on execution run"
            )
        if model_equity_recon is not None and broker_equity is not None:
            execution_vs_broker_equity_delta = float(broker_equity) - float(
                model_equity_recon
            )
            execution_vs_broker_equity_tolerance = max(
                float(execution_equity_tolerance_dollars),
                abs(float(model_equity_recon))
                * (float(execution_equity_tolerance_bps) / 10000.0),
            )
            if abs(float(execution_vs_broker_equity_delta)) > float(
                execution_vs_broker_equity_tolerance
            ):
                errors.append(
                    "health_check_failed: broker-recon equity drift exceeds tolerance "
                    f"(delta={execution_vs_broker_equity_delta:.6f}, "
                    f"tol={execution_vs_broker_equity_tolerance:.6f}, "
                    f"model={float(model_equity_recon):.6f}, broker={float(broker_equity):.6f})"
                )
        elif (
            execution_basis_rows_used > 0
            and execution_basis_equity is not None
            and broker_equity is not None
        ):
            # Fallback when broker reconciliation metrics are unavailable.
            execution_vs_broker_equity_delta = float(execution_basis_equity) - float(
                broker_equity
            )
            execution_vs_broker_equity_tolerance = max(
                float(execution_equity_tolerance_dollars),
                abs(float(execution_basis_equity))
                * (float(execution_equity_tolerance_bps) / 10000.0),
            )
            if abs(float(execution_vs_broker_equity_delta)) > float(
                execution_vs_broker_equity_tolerance
            ):
                errors.append(
                    "health_check_failed: execution-basis equity drift exceeds tolerance "
                    f"(delta={execution_vs_broker_equity_delta:.6f}, "
                    f"tol={execution_vs_broker_equity_tolerance:.6f}, "
                    f"exec={float(execution_basis_equity):.6f}, broker={float(broker_equity):.6f})"
                )
        if (
            nav_equity_last_row is not None
            and broker_equity is not None
            and abs(float(nav_equity_last_row) - float(broker_equity))
            > float(
                execution_vs_broker_equity_tolerance
                if execution_vs_broker_equity_tolerance is not None
                else max(
                    float(execution_equity_tolerance_dollars),
                    abs(float(nav_equity_last_row))
                    * (float(execution_equity_tolerance_bps) / 10000.0),
                )
            )
        ):
            warnings.append(
                f"valuation_basis_mismatch: mark_basis={float(nav_equity_last_row):.6f} broker={float(broker_equity):.6f}"
            )
        if broker_cash is not None and broker_equity and broker_equity > 0 and achieved_cash_weight is not None:
            implied = float(broker_cash) / float(broker_equity)
            if abs(float(achieved_cash_weight) - implied) > float(tolerance):
                errors.append(
                    f"health_check_failed: achieved_cash_weight {achieved_cash_weight} != broker_cash/broker_equity {implied}"
                )
        if gross_exposure is not None and (not leverage_enabled) and float(gross_exposure) > (1.0 + float(tolerance)):
            errors.append(
                f"health_check_failed: gross_exposure {gross_exposure} exceeds 1.0 without leverage"
            )
    else:
        # Planning/non-exec runs must not report executed turnover.
        turnover_dollars = 0.0
        turnover_pct = 0.0

    status = "FAIL" if errors else "PASS"
    error_text = "; ".join(errors) if errors else None

    return {
        "status": status,
        "error": error_text,
        "trade_date": str(trade_date),
        "run_id": run_id,
        "market_guard_status": market_guard_status,
        "planned_trade_count": int(planned_trade_count),
        "executed_trade_count": int(executed_trade_count),
        "model_equity_recon": _coerce_float_or_none(model_equity_recon),
        "broker_equity": broker_equity,
        "recon_delta": _coerce_float_or_none(recon_delta),
        "recon_equity_tolerance": _coerce_float_or_none(recon_tolerance),
        "broker_cash": broker_cash,
        "achieved_cash_weight": achieved_cash_weight,
        "gross_exposure": gross_exposure,
        "net_exposure": net_exposure,
        "nav_equity_last_row": nav_equity_last_row,
        "nav_last_equity": nav_equity_last_row,
        "mark_basis_equity": nav_equity_last_row,
        "execution_basis_equity": execution_basis_equity,
        "execution_vs_broker_equity_delta": _coerce_float_or_none(execution_vs_broker_equity_delta),
        "execution_vs_broker_equity_tolerance": _coerce_float_or_none(execution_vs_broker_equity_tolerance),
        "execution_basis_cash": execution_basis_cash,
        "execution_basis_holdings_value": execution_basis_holdings_value,
        "execution_basis_rows_used": int(execution_basis_rows_used),
        "execution_basis_dedup_removed": int(execution_basis_dedup_removed),
        "ledger_path_used": str(ledger_path or ""),
        "ledger_rows": int(execution_basis.get("ledger_rows_total") or 0),
        "ledger_dupe_rows": int(execution_basis_dedup_removed),
        "turnover_dollars": float(turnover_dollars),
        "turnover_pct": float(turnover_pct),
        "execution_basis_turnover_dollars": float(execution_basis_turnover_dollars),
        "execution_basis_turnover_pct": float(execution_basis_turnover_pct),
        "warnings": warnings,
    }


def _payload_num_trades(payload: object) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        trades = payload.get("trades", [])
        return len(trades) if isinstance(trades, list) else 0
    return 0
def _coerce_whole_shares(value: object) -> int:
    try:
        return max(0, int(float(value)))
    except Exception:
        return 0


def _coerce_filter_stats(execution_filter: object) -> dict | None:
    if not isinstance(execution_filter, dict):
        return None
    required_keys = ["raw", "rounded", "kept", "dropped_zero_shares", "dropped_min_notional"]
    alias_map = {
        "dropped_zero_shares": ("dropped_zero_shares", "dropped_zero"),
        "dropped_min_notional": ("dropped_min_notional",),
    }
    coerced: dict[str, int] = {}
    for key in required_keys:
        keys_to_try = alias_map.get(key, (key,))
        value = None
        for alias in keys_to_try:
            if execution_filter.get(alias) is not None:
                value = execution_filter.get(alias)
                break
        if value is None:
            return None
        try:
            coerced[key] = int(value)
        except Exception:
            return None
    return coerced


def _coerce_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _today_et_str() -> str:
    return dt.datetime.now(ZoneInfo("America/New_York")).date().strftime("%Y-%m-%d")


def _market_is_open_for_trade_date(paper_summary: dict | None) -> bool:
    market_guard = (paper_summary or {}).get("market_guard") if isinstance(paper_summary, dict) else None
    if isinstance(market_guard, dict):
        if market_guard.get("is_trading_session") is not None:
            return bool(market_guard.get("is_trading_session"))
        if market_guard.get("is_trading_day") is not None:
            return bool(market_guard.get("is_trading_day"))
    market_status = str((paper_summary or {}).get("market_status", "")).upper()
    return market_status == "OPEN"


def _should_execute_run(*, trade_date_str: str, today_et_str: str, paper_summary: dict | None) -> tuple[bool, bool, bool]:
    is_planning_run = trade_date_str != today_et_str
    market_is_open_for_trade_date = _market_is_open_for_trade_date(paper_summary)
    should_execute = (not is_planning_run) and market_is_open_for_trade_date
    return should_execute, is_planning_run, market_is_open_for_trade_date


def _apply_breaker_allocation_diagnostics(
    allocation_diagnostics: dict | None,
    daily_snapshot: dict | None,
) -> dict:
    diagnostics = dict(allocation_diagnostics or {})
    sleeve1 = dict((diagnostics.get("sleeve_1") or {}))
    snapshot = daily_snapshot or {}
    breaker = (snapshot.get("breaker") or {}) if isinstance(snapshot, dict) else {}

    mode = str(breaker.get("mode", "")).strip().lower()
    multiplier = _coerce_float_or_none(breaker.get("exposure_multiplier_today"))
    invested_after = _coerce_float_or_none(breaker.get("invested_after_overlay"))
    if invested_after is None:
        target_cash = _coerce_float_or_none(snapshot.get("target_cash_weight"))
        if target_cash is not None:
            invested_after = max(0.0, min(1.0, 1.0 - target_cash))

    if invested_after is None:
        diagnostics["sleeve_1"] = sleeve1
        return diagnostics

    invested_after = max(0.0, min(1.0, invested_after))
    forced_cash_post = max(0.0, min(1.0, 1.0 - invested_after))

    desired_pre = _coerce_float_or_none(sleeve1.get("desired_allocation"))
    if desired_pre is not None and multiplier is not None:
        multiplier = max(0.0, min(1.0, multiplier))
        sleeve1["desired_allocation_pre_breaker"] = desired_pre
        sleeve1["desired_allocation"] = max(0.0, min(1.0, desired_pre * multiplier))

    sleeve1["achieved_invested"] = invested_after
    sleeve1["forced_cash"] = forced_cash_post

    existing_constraint = str(sleeve1.get("limiting_constraint", "") or "").strip()
    if mode == "lock":
        sleeve1["limiting_constraint"] = "BREAKER_MODE=lock"
    elif mode == "partial":
        if existing_constraint and existing_constraint.lower() != "none":
            sleeve1["limiting_constraint"] = f"BREAKER_MODE=partial + {existing_constraint}"
        else:
            sleeve1["limiting_constraint"] = "BREAKER_MODE=partial"

    diagnostics["sleeve_1"] = sleeve1
    return diagnostics


def _merge_nav_metrics_into_snapshot(
    daily_snapshot: dict,
    nav_timeseries: pd.DataFrame,
    *,
    asof_date: str | None = None,
    tolerance: float = 1e-6,
) -> None:
    nav_ts = _safe_df(nav_timeseries).copy()
    if nav_ts.empty or "date" not in nav_ts.columns or "equity" not in nav_ts.columns:
        return

    nav_ts["date"] = pd.to_datetime(nav_ts["date"])
    nav_ts = nav_ts.sort_values("date").reset_index(drop=True)
    target_row = nav_ts.iloc[-1]
    if asof_date:
        same_day = nav_ts[nav_ts["date"] == pd.to_datetime(asof_date)]
        if not same_day.empty:
            target_row = same_day.iloc[-1]

    eq = float(target_row["equity"])
    run_date = pd.to_datetime(target_row["date"])
    month_rows = nav_ts[nav_ts["date"].dt.to_period("M") == run_date.to_period("M")]
    week_rows = nav_ts[
        nav_ts["date"].dt.isocalendar().week == run_date.isocalendar().week
    ]
    si_base = float(nav_ts["equity"].iloc[0]) if len(nav_ts) else None
    week_base = float(week_rows["equity"].iloc[0]) if not week_rows.empty else None
    month_base = float(month_rows["equity"].iloc[0]) if not month_rows.empty else None

    nav_metrics = {
        "equity": eq,
        "cash": _coerce_float_or_none(target_row.get("cash")),
        "gross_exposure": _coerce_float_or_none(target_row.get("gross_exposure")),
        "net_exposure": _coerce_float_or_none(target_row.get("net_exposure")),
        "return_1d": float(target_row.get("return_1d", 0.0) or 0.0),
        "turnover_dollars": float(
            _coerce_float_or_none(target_row.get("turnover_dollars")) or 0.0
        ),
        "turnover_pct": float(
            _coerce_float_or_none(target_row.get("turnover_pct"))
            if _coerce_float_or_none(target_row.get("turnover_pct")) is not None
            else (_coerce_float_or_none(target_row.get("turnover")) or 0.0)
        ),
        "wtd": (eq / week_base - 1.0) if week_base else 0.0,
        "mtd": (eq / month_base - 1.0) if month_base else 0.0,
        "si": (eq / si_base - 1.0) if si_base else 0.0,
    }
    daily_snapshot["nav_metrics"] = nav_metrics

    perf_diag = dict(daily_snapshot.get("performance_diagnostics") or {})
    perf_diag["current_equity"] = eq
    daily_snapshot["performance_diagnostics"] = perf_diag


def build_execution_email_payload(
    trade_date: str,
    daily_snapshot: dict,
    paper_summary: dict | None,
) -> dict:
    mode = (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow")
    mode = str(mode).upper()
    if mode == "LIVE":
        return {
            "trade_date": trade_date,
            "mode": mode,
            "execution_status": "HALTED",
            "halt_reason": "LIVE MODE BLOCKED",
            "trades": [],
            "run_id": (paper_summary or {}).get("run_id", ""),
            "order_ids": [],
        }
    halted_reason = None
    status = "READY"
    planned_for = (paper_summary or {}).get("planned_for")
    plan_only = bool((paper_summary or {}).get("plan_only", False))
    if paper_summary:
        market_open = str(paper_summary.get("market_status", "")).upper() == "OPEN"
        if not market_open:
            if mode in {"SHADOW", "ALPACA"} or plan_only:
                status = "PLANNED"
            else:
                status = "HALTED"
                halted_reason = "MARKET CLOSED"
        if plan_only:
            status = "PLANNED"
        blocked = paper_summary.get("blocked_reasons", []) or []
        if any("stale_prices" in str(r) for r in blocked):
            status = "HALTED"
            halted_reason = "STALE PRICES"
        if any("signal_date_mismatch" in str(r) for r in blocked):
            status = "HALTED"
            halted_reason = "SIGNAL DATE MISMATCH"
    paper_breaker = (paper_summary or {}).get("breaker") if isinstance(paper_summary, dict) else {}
    snapshot_breaker = (daily_snapshot or {}).get("breaker") if isinstance(daily_snapshot, dict) else {}
    breaker_mode = str(
        (paper_breaker or {}).get("mode")
        or (snapshot_breaker or {}).get("mode")
        or os.getenv("BREAKER_MODE", "partial")
    ).strip().lower()
    if breaker_mode not in {"off", "partial", "lock"}:
        breaker_mode = "partial"
    risk_map = {r.get("ticker"): r for r in (daily_snapshot.get("risk_levels", []) or []) if r.get("ticker")}
    holdings_shares = {
        str(h.get("ticker")): _coerce_whole_shares(h.get("shares"))
        for h in (daily_snapshot.get("holdings", []) or [])
        if h.get("ticker")
    }
    orders = (paper_summary or {}).get("shadow_orders", []) or []
    execution_trades = (paper_summary or {}).get("execution_trades", []) or []
    planned_trades = (paper_summary or {}).get("trade_plan", []) or []
    execution_filter = (paper_summary or {}).get("execution_filter", {}) or {}
    min_trade_dollars_raw = (paper_summary or {}).get("min_trade_dollars")
    min_trade_dollars = float(min_trade_dollars_raw) if min_trade_dollars_raw is not None else None
    filter_stats = _coerce_filter_stats(execution_filter)
    risk_meta = (paper_summary or {}).get("risk_meta", {}) or {}
    turnover_scaled = bool(risk_meta.get("turnover_scaled", False))
    turnover_requested_raw = risk_meta.get("turnover_requested")
    turnover_cap_raw = risk_meta.get("turnover_cap")
    turnover_scale_raw = risk_meta.get("turnover_scale")
    turnover_requested = _coerce_float_or_none(turnover_requested_raw)
    turnover_cap = _coerce_float_or_none(turnover_cap_raw)
    turnover_scale = _coerce_float_or_none(turnover_scale_raw)
    turnover_dollars = _coerce_float_or_none((paper_summary or {}).get("turnover_notional"))
    turnover_pct = _coerce_float_or_none((paper_summary or {}).get("turnover_pct"))
    trades = []
    source_rows = []
    if status == "PLANNED" and planned_trades:
        for tr in planned_trades:
            source_rows.append(
                {
                    "ticker": tr.get("ticker"),
                    "side": str(tr.get("side", "")).upper(),
                    "shares": tr.get("shares", tr.get("quantity")),
                    "price": tr.get("price"),
                    "reason": tr.get("reason"),
                    "order_id": tr.get("order_id"),
                    "notional": tr.get("notional"),
                    "source": "trade_plan",
                }
            )
    elif execution_trades:
        for tr in execution_trades:
            source_rows.append(
                {
                    "ticker": tr.get("ticker"),
                    "side": str(tr.get("side", "")).upper(),
                    "shares": tr.get("shares"),
                    "price": tr.get("price"),
                    "reason": tr.get("reason"),
                    "order_id": tr.get("order_id"),
                    "notional": tr.get("notional"),
                    "source": "execution_trades",
                }
            )
    elif status == "READY":
        for order in orders:
            source_rows.append(
                {
                    "ticker": order.get("ticker"),
                    "side": str(order.get("side", "")).upper(),
                    "shares": order.get("quantity"),
                    "price": None,
                    "reason": order.get("reason"),
                    "order_id": order.get("order_id"),
                    "notional": order.get("notional"),
                    "source": "shadow_orders",
                }
            )
    for row in source_rows:
        ticker = row.get("ticker")
        side = str(row.get("side", "")).upper()
        risk = risk_map.get(ticker, {})
        shares = _coerce_whole_shares(row.get("shares"))
        if side in {"SELL", "CLOSE", "REDUCE"}:
            available = holdings_shares.get(str(ticker))
            if available is not None:
                shares = min(shares, available)
        if shares < 1:
            continue
        entry_price = risk.get("entry_price")
        notional = None
        try:
            if entry_price is not None:
                notional = shares * float(entry_price)
            elif row.get("notional") is not None:
                notional = abs(float(row.get("notional")))
        except Exception:
            notional = None
        if min_trade_dollars is not None and notional is not None and abs(float(notional)) < float(min_trade_dollars):
            continue
        trades.append(
            {
                "ticker": ticker,
                "side": side,
                "shares": shares,
                "entry_price": entry_price if entry_price is not None else row.get("price"),
                "stop_loss": risk.get("stop_loss"),
                "take_profit": risk.get("take_profit"),
                "notional": notional if notional is not None else row.get("notional"),
                "reason": row.get("reason"),
                "notes": row.get("reason"),
                "order_id": row.get("order_id"),
            }
        )
    if breaker_mode == "lock":
        trades = [
            t for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"}
        ]
    trades = sorted(trades, key=lambda x: (x.get("ticker") or "", x.get("side") or ""))
    buy_count = sum(1 for t in trades if str(t.get("side", "")).upper() == "BUY")
    sell_count = sum(1 for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"})
    status_label = None
    status_reason = None
    if breaker_mode == "lock":
        if trades and buy_count == 0 and sell_count > 0:
            status_label = "EXIT ORDERS (BREAKER LOCK)"
            status_reason = "BREAKER_MODE=lock — liquidation-only"
        elif not trades:
            status_label = "LOCKED (NO ORDERS)"
            status_reason = "BREAKER_MODE=lock — already at cash"
    order_ids = sorted(
        [t.get("order_id") for t in trades if t.get("order_id")],
        key=lambda oid: tuple((str(oid).split(":"))[-2:]) if ":" in str(oid) else ("", str(oid)),
    )
    blocked_reasons = [str(r) for r in ((paper_summary or {}).get("blocked_reasons", []) or [])]
    blocked_display = [f"{r.replace('_', ' ')}" for r in blocked_reasons]
    blocked_tickers = {
        str(ticker): [str(reason) for reason in reasons]
        for ticker, reasons in (((paper_summary or {}).get("blocked_tickers") or {}).items())
    }
    blocked_tickers: list[str] = []
    for reason in blocked_reasons:
        if "missing_open_prices:" not in reason:
            continue
        _, _, raw_tickers = reason.partition("missing_open_prices:")
        for ticker in [t.strip().upper() for t in raw_tickers.split(",") if t.strip()]:
            blocked_tickers.append(f"{ticker} (missing_open_prices)")
    blocked_tickers = sorted(set(blocked_tickers))
    pricing_source = (paper_summary or {}).get("pricing_source") or ("PREV_CLOSE" if status == "PLANNED" else "OPEN")
    pricing_asof = (paper_summary or {}).get("pricing_asof") or trade_date
    total_equity = _coerce_float_or_none((paper_summary or {}).get("total_equity"))
    holdings = (daily_snapshot or {}).get("holdings", []) or []
    snapshot_target_cash_weight_raw = (daily_snapshot or {}).get("target_cash_weight")
    snapshot_target_cash_weight = _coerce_float_or_none(snapshot_target_cash_weight_raw)
    target_cash_weight_raw = (paper_summary or {}).get("target_cash_weight")
    achieved_cash_weight_raw = (paper_summary or {}).get("achieved_cash_weight")
    target_cash_weight = _coerce_float_or_none(target_cash_weight_raw)
    if target_cash_weight is None:
        target_cash_weight = snapshot_target_cash_weight
    achieved_cash_weight = _coerce_float_or_none(achieved_cash_weight_raw)

    gross_exposure = _coerce_float_or_none((paper_summary or {}).get("gross_exposure"))
    net_exposure = _coerce_float_or_none((paper_summary or {}).get("net_exposure"))
    exposure_map: dict[str, float] = {}
    if isinstance((paper_summary or {}).get("position_reconciliation"), list):
        for row in (paper_summary or {}).get("position_reconciliation", []) or []:
            ticker = str((row or {}).get("ticker", "")).upper()
            if not ticker or ticker == CASH_TICKER:
                continue
            w = _coerce_float_or_none((row or {}).get("achieved_weight"))
            if w is None:
                continue
            exposure_map[ticker] = w
    if exposure_map:
        exposure_stats = compute_exposure(exposure_map, leverage_enabled=False, enforce_bounds=True)
        gross_exposure = float(exposure_stats["gross_exposure"])
        net_exposure = float(exposure_stats["net_exposure"])
    elif gross_exposure is None or net_exposure is None:
        holdings_weight_map: dict[str, float] = {}
        snapshot_equity = _coerce_float_or_none(
            ((daily_snapshot or {}).get("performance_diagnostics") or {}).get("current_equity")
        )
        denom = snapshot_equity if snapshot_equity and snapshot_equity > 0 else total_equity
        if holdings and denom and denom > 0:
            for h in holdings:
                ticker = str(h.get("ticker", "")).upper()
                if not ticker or ticker == CASH_TICKER:
                    continue
                try:
                    shares = float(h.get("shares"))
                    px = float(h.get("last_price"))
                    direction = str(h.get("direction", "LONG")).upper()
                    sign = -1.0 if direction == "SHORT" else 1.0
                    holdings_weight_map[ticker] = sign * abs(shares * px) / float(denom)
                except Exception:
                    continue
        if holdings_weight_map:
            exposure_stats = compute_exposure(
                holdings_weight_map,
                leverage_enabled=any(v < 0 for v in holdings_weight_map.values()),
                enforce_bounds=not any(v < 0 for v in holdings_weight_map.values()),
            )
            gross_exposure = float(exposure_stats["gross_exposure"])
            net_exposure = float(exposure_stats["net_exposure"])

    max_position_weight = None
    if exposure_map:
        try:
            max_position_weight = max(abs(float(v)) for v in exposure_map.values()) if exposure_map else None
        except Exception:
            max_position_weight = None
    elif holdings and total_equity and total_equity > 0:
        try:
            max_position_weight = max(
                abs(float(h.get("shares", 0.0)) * float(h.get("last_price", 0.0))) / float(total_equity)
                for h in holdings
                if str(h.get("ticker", "")).upper() != CASH_TICKER
            )
        except Exception:
            max_position_weight = None

    position_count = len(exposure_map) if exposure_map else (len(holdings) if holdings else None)
    risk_summary = {
        "Turnover requested ($)": f"${turnover_requested:,.2f}" if turnover_requested is not None else "unavailable",
        "Turnover cap ($)": f"${turnover_cap:,.2f}" if turnover_cap is not None else "unavailable",
        "Turnover scale": f"{turnover_scale:.4f}" if turnover_scale is not None else "unavailable",
        "Executed turnover ($)": f"${turnover_dollars:,.2f}" if turnover_dollars is not None else "unavailable",
        "Executed turnover (%)": f"{turnover_pct * 100:.2f}%" if turnover_pct is not None else "unavailable",
        "Target cash weight (%)": f"{target_cash_weight * 100:.2f}%" if target_cash_weight is not None else "unavailable",
        "Achieved cash weight (%)": f"{achieved_cash_weight * 100:.2f}%" if achieved_cash_weight is not None else "unavailable",
        "Gross exposure (%)": f"{gross_exposure * 100:.2f}%" if gross_exposure is not None else "unavailable",
        "Net exposure (%)": f"{net_exposure * 100:.2f}%" if net_exposure is not None else "unavailable",
        "# positions": str(position_count) if position_count is not None else "unavailable",
        "Max position weight (%)": f"{max_position_weight * 100:.2f}%" if max_position_weight is not None else "unavailable",
    }
    intent_list = (daily_snapshot or {}).get("proposed_trades") if isinstance(daily_snapshot, dict) else None
    proposed_trades_intent_count = len(intent_list) if isinstance(intent_list, list) else None
    sizing_equity = _coerce_float_or_none((paper_summary or {}).get("sizing_equity"))
    total_equity_fallback = _coerce_float_or_none((paper_summary or {}).get("total_equity"))

    payload = {
        "trade_date": trade_date,
        "mode": mode,
        "execution_status": status,
        "halt_reason": halted_reason,
        "status_label": status_label,
        "status_reason": status_reason,
        "market_status": (paper_summary or {}).get("market_status"),
        "market_reason": (paper_summary or {}).get("market_reason"),
        "planned_for": planned_for,
        "plan_only": plan_only,
        "pricing_source": pricing_source,
        "pricing_asof": pricing_asof,
        "trades": trades,
        "run_id": (paper_summary or {}).get("run_id", ""),
        "order_ids": order_ids,
        "cash_target_weight": target_cash_weight,
        "achieved_cash_weight": achieved_cash_weight,
        "investable_dollars": _coerce_float_or_none((paper_summary or {}).get("investable_dollars")),
        "equity": sizing_equity if sizing_equity is not None else total_equity_fallback,
        "cash_target_dollars": _coerce_float_or_none((paper_summary or {}).get("target_cash_dollars")),
        "blocked_tickers": blocked_tickers,
        "proposed_trades_intent_count": proposed_trades_intent_count,
        "proposed_trades_intent": proposed_trades_intent_count,
        "executable_trades_count": int(len(trades)),
        "buys": int(buy_count),
        "sells": int(sell_count),
        "min_trade_dollars": min_trade_dollars,
        "turnover_dollars": turnover_dollars,
        "turnover_pct": turnover_pct,
        "filter_stats": filter_stats,
        "breaker": {
            "mode": breaker_mode,
        },
        "risk_meta": {
            "turnover_requested": turnover_requested,
            "turnover_cap": turnover_cap,
            "turnover_scaled": turnover_scaled,
            "turnover_scale": turnover_scale,
            "turnover_dollars": turnover_dollars,
            "turnover_pct": turnover_pct,
        },
        "risk_summary": risk_summary,
    }
    if mode == "SHADOW" and not trades:
        payload.update(
            {
                "recommended_action": "NO",
                "confidence_level": "HIGH",
                "human_override_required": "NO",
                "rationale": [
                    "Sleeve 1 (Momentum): Signals present but blocked by portfolio cash constraint",
                    "Sleeve 2 (Valuation): Rebalance signals generated but position caps exceeded",
                    "Charlie Munger Sleeve: No new accumulation opportunities near 200-week MA",
                    "Portfolio cash currently above target due to constraint enforcement",
                ],
                "recommended_trades": [],
                "blocked_by_constraints": blocked_display,
                "next_checkpoint": "Re-evaluate at next rebalance window or upon signal state change",
                "signals_status": "VALID",
                "constraints_status": "ENFORCED",
                "execution_payload_status": "NOT GENERATED (Expected in SHADOW)",
                "no_trades_reason": (
                    f"No executable trades after rounding and ${min_trade_dollars:.0f} minimum trade filter"
                    if min_trade_dollars is not None
                    else "No executable trades after rounding and minimum trade filter"
                ),
            }
        )
        if breaker_mode == "lock":
            payload["no_trades_reason"] = "BREAKER_MODE=lock — already at cash"
        if turnover_scaled and turnover_requested is not None and turnover_cap is not None and turnover_scale is not None:
            payload["no_trades_reason"] = (
                f"Turnover cap scaling applied (requested ${turnover_requested:,.2f} vs cap ${turnover_cap:,.2f}, "
                f"scale={turnover_scale:.4f}); no trades remained after rounding/minimum filters"
            )
    if turnover_scaled and turnover_requested is not None and turnover_cap is not None and turnover_scale is not None:
        payload["turnover_note"] = (
            f"Turnover cap applied: requested ${turnover_requested:,.2f}, "
            f"cap ${turnover_cap:,.2f}, scale {turnover_scale:.4f}."
        )
    if status == "PLANNED":
        payload["planning_disclaimer"] = "Planning email only — no orders were sent."
        if str(pricing_source).upper() == "PREV_CLOSE":
            payload["pricing_disclaimer"] = "Prices are estimated from prior close; actual execution may differ."
    return payload
def _format_text_table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "(none)"
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(str(cell)))
    def _fmt_row(cells: list[str]) -> str:
        return " | ".join(
            str(cell).ljust(col_widths[i]) for i, cell in enumerate(cells)
        )
    lines = [_fmt_row(headers), "-+-".join("-" * w for w in col_widths)]
    for row in rows:
        lines.append(_fmt_row(row))
    return "\n".join(lines)
def create_pm_first_trade_email(snapshot: dict) -> tuple[str, str]:
    """Backward-compatible PM-first digest email formatter."""
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"Daily Trade Rundown — {asof_str}"
    allocations = (snapshot.get("allocations") or {})
    sleeve_splits = (allocations.get("sleeves") or {})
    diagnostics = snapshot.get("performance_diagnostics") or {}
    perf = snapshot.get("performance_summary") or {}
    cm_sig = snapshot.get("charlie_munger") or {}
    near_ma = (cm_sig.get("meta") or {}).get("near_ma_candidates")
    if near_ma in (None, ""):
        charlie_status = "Pending"
    elif int(near_ma) <= 0:
        charlie_status = "Pending (insufficient lookback window)"
    else:
        charlie_status = f"Active ({int(near_ma)} near-MA candidates)"
    lines = [
        "ENVIRONMENT: SHADOW (NO CAPITAL AT RISK)",
        "",
        "PORTFOLIO AT A GLANCE",
        f"• Total Equity: {_fmt_money(diagnostics.get('current_equity'))}",
        f"• Day Move: {_fmt_pct(diagnostics.get('day_return'))}",
        f"• WTD: {_fmt_pct(perf.get('wtd'))}",
        f"• MTD: {_fmt_pct(perf.get('mtd'))}",
        f"• Total Return: {_fmt_pct(perf.get('total_return'))}",
        "",
        "SLEEVE ALLOCATION (DYNAMIC)",
        f"• Sleeve 1 — Momentum: {_fmt_pct(sleeve_splits.get('sleeve_trend', 0.0))}",
        f"• Sleeve 2 — Valuation: {_fmt_pct(sleeve_splits.get('sleeve_2', 0.0))}",
        "• Charlie Munger — Long Hold",
        f"  • Allocation: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"  • Status: {charlie_status}",
        "",
        "— Automated Portfolio Engine",
    ]
    return subject, "\n".join(lines)
def create_snapshot_email(snapshot: dict, execution_payload: dict | None = None) -> tuple[str, str]:
    asof = snapshot.get("asof")
    asof_str = _fmt_date(asof) if asof is not None else "n/a"
    subject = f"MODEL & PERFORMANCE SNAPSHOT — {asof_str}"
    allocations = snapshot.get("allocations", {}) or {}
    sleeve_splits = allocations.get("sleeves", {}) or {}
    cash_pct = allocations.get("cash", 0.0)
    achieved_cash = _coerce_float_or_none((execution_payload or {}).get("achieved_cash_weight"))
    display_cash_pct = achieved_cash if achieved_cash is not None else cash_pct
    turnover_dollars = _coerce_float_or_none((execution_payload or {}).get("turnover_dollars"))
    turnover_pct = _coerce_float_or_none((execution_payload or {}).get("turnover_pct"))
    target_cash = snapshot.get("target_cash_weight")
    if target_cash is None:
        target_cash = snapshot.get("cash_target", cash_pct)
    sleeve_states = snapshot.get("sleeve_states", {}) or {}
    perf = snapshot.get("performance_summary", {}) or {}
    diagnostics = snapshot.get("performance_diagnostics", {}) or {}
    alpha = snapshot.get("alpha_attribution", {}) or {}
    cm_sig = snapshot.get("charlie_munger", {}) or {}
    cm_near_ma = (cm_sig.get("meta") or {}).get("near_ma_candidates", 0)
    cm_selected = cm_sig.get("selected", []) or []
    total_equity = diagnostics.get("current_equity")
    day_return = diagnostics.get("day_return")
    orders = snapshot.get("orders", []) or []
    skipped = snapshot.get("skipped_trades", []) or []
    def _trades_today(sleeve_name: str) -> str:
        count = len([o for o in orders if o.get("sleeve") == sleeve_name])
        return "NONE" if count == 0 else str(count)
    raw_mode = str((execution_payload or {}).get("mode") or os.getenv("TRADING_MODE", "SHADOW")).upper()
    env_mode = "LIVE" if raw_mode == "LIVE" else "SHADOW"
    exec_trades = (execution_payload or {}).get("trades", []) or []
    intent_from_payload = (execution_payload or {}).get("proposed_trades_intent_count") if execution_payload else None
    if intent_from_payload is None:
        intent_from_payload = (execution_payload or {}).get("proposed_trades_intent") if execution_payload else None
    if intent_from_payload is None:
        intent_from_payload = len(snapshot.get("proposed_trades", []) or [])
    executable_count = (execution_payload or {}).get("executable_trades_count") if execution_payload else None
    if executable_count is None:
        executable_count = len(exec_trades)
    exec_status_label = (
        str((execution_payload or {}).get("status_label"))
        if (execution_payload or {}).get("status_label")
        else ("NO TRADES" if not exec_trades else "TRADES READY")
    )
    def _primary_no_trade_reason() -> str:
        payload = execution_payload or {}
        if not payload:
            return "execution payload missing"
        if int(executable_count or 0) > 0:
            return "executable trades available"
        for key in ["halt_reason", "market_reason", "no_trades_reason"]:
            if payload.get(key):
                return str(payload.get(key))
        risk_meta = payload.get("risk_meta", {}) or {}
        if bool(risk_meta.get("turnover_scaled")) and float(risk_meta.get("turnover_scale", 1.0)) <= 0.0001:
            return "turnover cap scaled to ~0"
        min_trade = payload.get("min_trade_dollars")
        if min_trade is not None:
            return f"minimum notional filter (${float(min_trade):,.0f})"
        return "already at target / rounded to zero shares"
    exec_no_trade_reason = _primary_no_trade_reason()
    exec_status_reason = (execution_payload or {}).get("status_reason") if execution_payload else None
    if not exec_status_reason:
        exec_status_reason = exec_no_trade_reason
    breaker_mode_for_exec = str((((execution_payload or {}).get("breaker") or {}).get("mode") or "")).strip().lower()
    intent_suffix = ""
    if breaker_mode_for_exec == "lock":
        try:
            if int(intent_from_payload or 0) == 0:
                intent_suffix = " (breaker overrides)"
        except Exception:
            intent_suffix = " (breaker overrides)"
    nav_metrics = snapshot.get("nav_metrics", {}) or {}
    nav_cash = _coerce_float_or_none(nav_metrics.get("cash"))
    nav_equity = _coerce_float_or_none(nav_metrics.get("equity"))
    if achieved_cash is None and nav_cash is not None and nav_equity is not None and nav_equity > 0:
        display_cash_pct = max(0.0, min(1.0, float(nav_cash) / float(nav_equity)))
    if turnover_dollars is None:
        turnover_dollars = _coerce_float_or_none(nav_metrics.get("turnover_dollars"))
    if turnover_pct is None:
        turnover_pct = _coerce_float_or_none(nav_metrics.get("turnover_pct"))
    inception_metrics = snapshot.get("inception_metrics", {}) or {}
    alloc_diag = snapshot.get("allocation_diagnostics", {}) or {}
    sleeve1_diag = alloc_diag.get("sleeve_1", {}) or {}
    def _sleeve_state_status(name: str) -> str:
        state = sleeve_states.get(name, {}) if isinstance(sleeve_states, dict) else {}
        if bool(state.get("active")):
            return "ACTIVE"
        reason = str(state.get("reason", "")).strip()
        return f"INACTIVE ({reason})" if reason else "INACTIVE"

    if isinstance(sleeve_states, dict) and sleeve_states:
        active_sleeve_count = int(sum(1 for st in sleeve_states.values() if bool((st or {}).get("active"))))
    else:
        active_sleeve_count = len([k for k, v in sleeve_splits.items() if float(v) > WEIGHT_TOLERANCE])

    lines = [
        f"ENVIRONMENT: {env_mode}",
        "",
        "PORTFOLIO AT A GLANCE",
        f"• Total Equity: {_fmt_money(nav_metrics.get('equity', total_equity))}",
        f"• Day Move: {_fmt_pct(nav_metrics.get('return_1d', day_return))}",
        f"• Cash: {_fmt_pct(display_cash_pct)} (Target: {_fmt_pct(target_cash)})",
        f"• Turnover ($): {_fmt_money(turnover_dollars)}",
        f"• Turnover (%): {_fmt_pct(turnover_pct)}",
        f"• Active Sleeves: {active_sleeve_count}",
        "",
        "RUN CONTEXT",
        f"• Inception: {_fmt_date(inception_metrics.get('inception_date'))}",
        "",
        "SLEEVE ALLOCATION (DYNAMIC)",
        f"• Sleeve 1 — Momentum: {_fmt_pct(sleeve_splits.get('sleeve_trend', 0.0))}",
        f"• Sleeve 2 — Valuation: {_fmt_pct(sleeve_splits.get('sleeve_2', 0.0))}",
        f"• Charlie Munger — Long Hold: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"• Cash: {_fmt_pct(cash_pct)}",
        "",
        "Charlie allocation is policy-driven and may vary by risk regime / constraints.",
        "",
        "---",
        "",
        "SLEEVE 1 — MOMENTUM (FAST)",
        f"• Status: {_sleeve_state_status('sleeve_trend')}",
        f"• Signal State: {'ON' if _sleeve_state_status('sleeve_trend') == 'ACTIVE' else 'OFF'}",
        f"• Trades Today: {_trades_today('sleeve_trend')}",
        f"• Constraint Impact: {'Position caps + cash target' if skipped else 'None'}",
        "• Role: Capture short- to mid-term trend persistence",
        "",
        "SLEEVE 2 — VALUATION (OPPORTUNISTIC)",
        f"• Status: {_sleeve_state_status('sleeve_2')}",
        f"• Signal State: {'ON' if _sleeve_state_status('sleeve_2') == 'ACTIVE' else 'OFF'}",
        f"• Trades Today: {_trades_today('sleeve_2')}",
        f"• Constraint Impact: {'Position caps' if skipped else 'None'}",
        "• Role: Mean reversion and valuation dislocations",
        "",
        "CHARLIE MUNGER SLEEVE — LONG HOLD",
        f"• Status: {_sleeve_state_status('charlie_munger')}",
        f"• Allocation: {_fmt_pct(sleeve_splits.get('charlie_munger', 0.0))}",
        f"• New Buys Today: {'NONE' if not cm_selected else len(cm_selected)}",
        f"• Candidates Near 200-Week MA: {cm_near_ma}",
        "• Role: Long-duration quality accumulation hedge",
        "",
        "This sleeve acts as a stabilizer against higher-velocity trading in Sleeves 1 & 2.",
        "",
        "---",
        "",
        "PERFORMANCE SUMMARY",
        f"• Week-to-Date: {_fmt_pct(nav_metrics.get('wtd', perf.get('wtd')))}",
        f"• Month-to-Date: {_fmt_pct(nav_metrics.get('mtd', perf.get('mtd')))}",
        f"• Since Inception: {_fmt_pct(nav_metrics.get('si', perf.get('total_return')))}",
        f"• SPY Since Inception: {_fmt_pct(inception_metrics.get('spy_return_since_inception'))}",
        "",
        "ALLOCATION DIAGNOSTICS",
        f"• Sleeve 1 desired allocation (post-breaker): {_fmt_pct(sleeve1_diag.get('desired_allocation'))}",
        f"• Sleeve 1 desired allocation (pre-breaker): {_fmt_pct(sleeve1_diag.get('desired_allocation_pre_breaker'))}" if sleeve1_diag.get("desired_allocation_pre_breaker") is not None else None,
        f"• Sleeve 1 achieved invested: {_fmt_pct(sleeve1_diag.get('achieved_invested'))}",
        f"• Sleeve 1 forced cash: {_fmt_pct(sleeve1_diag.get('forced_cash'))}",
        f"• Sleeve 1 cap names: {sleeve1_diag.get('selected_names', 'n/a')} selected / {sleeve1_diag.get('min_required_names', 'n/a')} required",
        f"• Limiting constraint: {sleeve1_diag.get('limiting_constraint', 'n/a')}",
        "",
        "ALPHA ATTRIBUTION VS SPY",
    ]
    if alpha and alpha.get("ok"):
        summary = alpha.get("summary", {}) or {}
        lines.extend(
            [
                "• Status: Available",
                f"• Overlap: {alpha.get('overlap_start')} to {alpha.get('overlap_end')} ({alpha.get('overlap_days')} days)",
                f"• Cumulative Portfolio Return: {_fmt_pct(summary.get('cumulative_port_return'))}",
                f"• Cumulative SPY Return: {_fmt_pct(summary.get('cumulative_spy_return'))}",
                f"• Cumulative Alpha: {_fmt_pct(summary.get('cumulative_alpha'))}",
                "• Last 10 daily spreads:",
            ]
        )
        for row in (alpha.get("rows") or [])[-10:]:
            lines.append(
                "  - {date}: Port {port}, SPY {spy}, Spread {spread}".format(
                    date=row.get("date"),
                    port=_fmt_pct(row.get("port_ret")),
                    spy=_fmt_pct(row.get("spy_ret")),
                    spread=_fmt_pct(row.get("spread")),
                )
            )
    else:
        reason = (alpha or {}).get("reason") or "Alpha attribution unavailable."
        lines.extend([f"• Status: Pending — {reason}"])
    lines.extend(
        [
            "",
            "---",
            "",
            "TRADES FOR TODAY (NEW ORDERS — EXECUTION PAYLOAD)",
            f"• Executable Trades: {int(executable_count or 0)}" if execution_payload else "• Executable Trades: unavailable (execution payload missing)",
            f"• Status: {exec_status_label}" if execution_payload else "• Status: unavailable (execution payload missing)",
            f"• Reason: {exec_status_reason}" if execution_payload else "• Reason: execution payload missing",
            "",
            "MODEL INTENT (PRE-CONSTRAINTS)",
            f"• Proposed Intent Count: {int(intent_from_payload)}{intent_suffix}" if intent_from_payload is not None else "• Proposed Intent Count: unavailable (execution payload missing)",
            f"• Executable Trades Count: {int(executable_count or 0)}" if execution_payload else "• Executable Trades Count: unavailable (execution payload missing)",
            f"• Primary Non-Execution Reason: {exec_no_trade_reason}" if execution_payload and int(executable_count or 0) == 0 else "• Primary Non-Execution Reason: n/a (orders are executable)",
            f"• Breaker Mode: {breaker_mode_for_exec.upper()}" if breaker_mode_for_exec else "• Breaker Mode: n/a",
            "• Note: Model intent is pre-constraints; executable orders reflect market/constraint filters.",
            "",
            "SYSTEM HEALTH",
            "• Signals: VALID",
            "• Data Freshness: OK",
            "• Constraint Engine: OPERATING AS DESIGNED",
            "",
            "— Automated Portfolio Engine",
        ]
    )
    lines = [line for line in lines if line is not None]
    return subject, "\n".join(lines)
def _normalize_weights(sleeve_allocations: dict[str, float], cash_weight: float) -> tuple[dict[str, float], float]:
    sleeves = {k: max(0.0, float(v)) for k, v in (sleeve_allocations or {}).items()}
    cash = max(0.0, float(cash_weight))
    total = sum(sleeves.values()) + cash
    if total <= WEIGHT_TOLERANCE:
        return sleeves, 1.0
    if abs(total - 1.0) <= WEIGHT_TOLERANCE:
        return sleeves, cash
    factor = 1.0 / total
    sleeves = {k: v * factor for k, v in sleeves.items()}
    cash = cash * factor
    return sleeves, cash
def enforce_charlie_bounds(
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    *,
    charlie_active: bool = True,
) -> tuple[dict[str, float], float]:
    """Clamp Charlie allocation to configured bounds and rebalance other sleeves first."""
    sleeves, cash = _normalize_weights(sleeve_allocations, cash_weight)
    if not charlie_active:
        return sleeves, cash
    sleeves.setdefault("sleeve_trend", 0.0)
    sleeves.setdefault("sleeve_2", 0.0)
    sleeves.setdefault("charlie_munger", 0.0)
    orig_charlie = float(sleeves.get("charlie_munger", 0.0))
    target_charlie = min(max(orig_charlie, CHARLIE_MIN), CHARLIE_MAX)
    delta = target_charlie - orig_charlie
    if abs(delta) <= WEIGHT_TOLERANCE:
        return sleeves, cash
    other_keys = ["sleeve_trend", "sleeve_2"]
    others_total = sum(float(sleeves.get(k, 0.0)) for k in other_keys)
    if delta > 0:
        if others_total > WEIGHT_TOLERANCE:
            for key in other_keys:
                share = float(sleeves.get(key, 0.0)) / others_total
                sleeves[key] = max(0.0, float(sleeves.get(key, 0.0)) - delta * share)
        else:
            logger.warning(
                "[ALLOCATION] Unable to increase Charlie allocation without adjusting CASH or active sleeves."
            )
            target_charlie = orig_charlie
    else:
        give = -delta
        if others_total > WEIGHT_TOLERANCE:
            for key in other_keys:
                share = float(sleeves.get(key, 0.0)) / others_total
                sleeves[key] = max(0.0, float(sleeves.get(key, 0.0)) + give * share)
        else:
            logger.warning(
                "[ALLOCATION] Unable to reduce Charlie allocation because non-Charlie sleeves are inactive."
            )
            sleeves["charlie_munger"] = max(0.0, orig_charlie)
            sleeves, cash = _normalize_weights(sleeves, cash)
            return sleeves, cash
    sleeves["charlie_munger"] = max(0.0, target_charlie)
    sleeves, cash = _normalize_weights(sleeves, cash)
    return sleeves, cash
def _apply_enforced_allocations_to_result(
    alloc_result: AllocationResult,
    old_allocations: dict[str, float],
    new_allocations: dict[str, float],
    new_cash_weight: float,
) -> None:
    alloc_result.sleeve_allocations = dict(new_allocations)
    combined = _safe_df(alloc_result.combined_weights).copy()
    if combined.empty:
        alloc_result.combined_weights = pd.DataFrame([
            {"ticker": CASH_TICKER, "target_weight": float(new_cash_weight), "sleeve_name": CASH_TICKER, "reason": "post_enforce", "signal_strength": 1.0}
        ])
        return
    if "sleeve_name" not in combined.columns:
        combined["sleeve_name"] = combined.get("ticker", pd.Series(dtype=str)).apply(
            lambda t: CASH_TICKER if str(t) == CASH_TICKER else ""
        )
    if "target_weight" in combined.columns:
        def _scale_row(row):
            names = [n.strip() for n in str(row.get("sleeve_name", "")).split(",") if n.strip()]
            ratios = []
            for name in names:
                old = float(old_allocations.get(name, 0.0))
                new = float(new_allocations.get(name, 0.0))
                if old > WEIGHT_TOLERANCE:
                    ratios.append(new / old)
                elif new <= WEIGHT_TOLERANCE:
                    ratios.append(0.0)
            scale = sum(ratios) / len(ratios) if ratios else 1.0
            return float(row.get("target_weight", 0.0)) * scale
        mask_cash = combined.get("ticker", pd.Series(dtype=str)) == CASH_TICKER
        non_cash = combined[~mask_cash].copy()
        if not non_cash.empty:
            non_cash["target_weight"] = non_cash.apply(_scale_row, axis=1)
            non_cash = non_cash[non_cash["target_weight"].abs() > WEIGHT_TOLERANCE]
        cash_row = pd.DataFrame([
            {
                "ticker": CASH_TICKER,
                "target_weight": float(new_cash_weight),
                "sleeve_name": CASH_TICKER,
                "reason": "post_enforce",
                "signal_strength": 1.0,
            }
        ])
        alloc_result.combined_weights = pd.concat([non_cash, cash_row], ignore_index=True)
def _equity_series_from_df(df: pd.DataFrame) -> pd.Series:
    df = _safe_df(df)
    if df.empty or "equity" not in df.columns:
        return pd.Series(dtype=float)
    series = pd.Series(
        df["equity"].values,
        index=pd.to_datetime(df["date"]) if "date" in df.columns else df.index,
    )
    return series.dropna().sort_index()
def _series_date_range(series: pd.Series) -> str:
    series = pd.Series(series).dropna()
    if series.empty:
        return "empty"
    idx = pd.to_datetime(series.index)
    return f"{idx.min().date()} -> {idx.max().date()}"
def _load_equity_history(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()
def _load_portfolio_fixture(path: str) -> pd.Series:
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return pd.Series(dtype=float)
    df = pd.read_csv(path)
    if df.empty or "date" not in df.columns or "equity" not in df.columns:
        return pd.Series(dtype=float)
    df["date"] = pd.to_datetime(df["date"])
    return pd.Series(df["equity"].values, index=df["date"]).dropna().sort_index()
def _append_equity_history(
    path: str, report_date: pd.Timestamp, equity: float
) -> pd.Series:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    report_date = pd.to_datetime(report_date).normalize()
    rows = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        df = pd.read_csv(path)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"]).dt.normalize()
            rows = df.to_dict(orient="records")
            if (df["date"] == report_date).any():
                return (
                    pd.Series(df["equity"].values, index=df["date"])
                    .dropna()
                    .sort_index()
                )
    rows.append({"date": report_date.strftime("%Y-%m-%d"), "equity": float(equity)})
    out_df = pd.DataFrame(rows)
    out_df.to_csv(path, index=False)
    out_df["date"] = pd.to_datetime(out_df["date"])
    return (
        pd.Series(out_df["equity"].values, index=out_df["date"]).dropna().sort_index()
    )
def compute_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> pd.Series:
    returns_map = {}
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        series = _equity_series_from_df(
            sleeve_equity_map.get(sleeve_name, pd.DataFrame())
        )
        if series.empty:
            continue
        returns_map[sleeve_name] = series.pct_change(fill_method=None).dropna()
    if not returns_map:
        return pd.Series(dtype=float)
    aligned = pd.concat(returns_map, axis=1, join="inner").dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    weights = pd.Series({k: sleeve_allocations.get(k, 0.0) for k in aligned.columns})
    portfolio_ret = aligned.mul(weights, axis=1).sum(axis=1)
    portfolio_equity = base_equity * (1.0 + portfolio_ret).cumprod()
    if cash_weight > WEIGHT_TOLERANCE:
        portfolio_equity = portfolio_equity * (1.0 + 0.0)
    return portfolio_equity
def _compute_execution_price(price_map: dict, ticker: str) -> float | None:
    entry = price_map.get(ticker, {})
    return entry.get("next_open") or entry.get("last_close")
def _build_price_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    price_map = {}
    if prices.empty:
        return price_map
    df = prices.copy()
    df["date"] = pd.to_datetime(df["date"])
    for ticker, group in df.groupby("ticker"):
        group = group.sort_values("date")
        past = group[group["date"] <= asof]
        future = group[group["date"] > asof]
        last_close = past["close"].iloc[-1] if not past.empty else None
        next_open = future["open"].iloc[0] if not future.empty else None
        price_map[ticker] = {
            "last_close": float(last_close) if last_close is not None else None,
            "next_open": float(next_open) if next_open is not None else None,
        }
    return price_map
def _build_atr_map(prices: pd.DataFrame, asof: pd.Timestamp) -> dict:
    if prices.empty:
        return {}
    atr_df = add_atr(prices)
    atr_df = atr_df.sort_values(["ticker", "date"])
    atr_map = {}
    for ticker, group in atr_df.groupby("ticker"):
        past = group[group["date"] <= asof]
        if past.empty:
            atr_map[ticker] = None
        else:
            atr_map[ticker] = (
                float(past["atr"].iloc[-1]) if pd.notna(past["atr"].iloc[-1]) else None
            )
    return atr_map
def _format_df_for_email(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        col_lower = str(col).lower()
        if "shares" in col_lower:
            df[col] = df[col].apply(_fmt_number)
            continue
        if "%" in col_lower or "pct" in col_lower or "percent" in col_lower:
            df[col] = df[col].apply(_fmt_pct)
        elif "date" in col_lower:
            df[col] = df[col].apply(_fmt_date)
        elif any(
            key in col_lower
            for key in [
                "p&l",
                "pnl",
                "equity",
                "notional",
                "allocated",
                "price",
                "value",
                "capital",
                "cash",
                "difference",
                "model",
                "broker",
                "amount",
            ]
        ):
            df[col] = df[col].apply(_fmt_money)
        elif any(
            key in col_lower
            for key in ["return", "weight", "delta", "change", "gross", "net"]
        ):
            df[col] = df[col].apply(_fmt_pct)
    return df
def html_table(
    df: pd.DataFrame, title: str, max_rows: int = 25, empty_message: str = "No data"
) -> str:
    df = _safe_df(df).copy()
    df = df.where(pd.notnull(df), "—")
    if df.empty:
        return f"<h3>{title}</h3><p><em>{empty_message}</em></p>"
    df = _format_df_for_email(df)
    return f"<h3>{title}</h3>" + df.head(max_rows).to_html(
        index=False, border=0, classes="tbl", justify="left"
    )
def filter_sleeve2_cash_proxy(trades: pd.DataFrame) -> pd.DataFrame:
    """Remove SGOV 'cash_proxy_fund_entries' rows from trades."""
    trades = _safe_df(trades).copy()
    if trades.empty:
        return trades
    if "ticker" in trades.columns and "reason_exit" in trades.columns:
        trades = trades[
            ~(
                (trades["ticker"] == "SGOV")
                & (trades["reason_exit"] == "cash_proxy_fund_entries")
            )
        ].copy()
    return trades
# ============================================================
# Sleeve health check
# ============================================================
def _sleeve_is_valid(equity_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Check whether a sleeve produced valid results.
    Returns (is_valid, reason) where reason is empty string if valid.
    """
    equity_df = _safe_df(equity_df)
    if equity_df.empty:
        return False, "empty equity_df"
    if "equity" not in equity_df.columns:
        return False, "no 'equity' column"
    if len(equity_df) < 1:
        return False, "zero rows"
    last_eq = equity_df["equity"].iloc[-1]
    if pd.isna(last_eq) or last_eq <= 0:
        return False, f"invalid terminal equity ({last_eq})"
    return True, ""


def _inactive_input_hint(details: dict | None) -> str:
    if not isinstance(details, dict):
        return "missing details dict"
    target_weights = details.get("target_weights")
    if not isinstance(target_weights, pd.DataFrame) or target_weights.empty:
        return "missing/empty target_weights"
    weights_df = details.get("weights_df")
    if isinstance(weights_df, pd.DataFrame) and weights_df.empty:
        return "weights_df empty"
    return "input present but sleeve output empty"
# ============================================================
# Sleeve runners
# ============================================================
def run_sleeve_1():
    _ensure_sleeve_backtest_imports()
    logger.info("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data()
    logger.info("[SLEEVE 1] Running backtest...")
    return s1_backtest(signals)
def run_sleeve_trend():
    _ensure_sleeve_backtest_imports()
    logger.info("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data()
    logger.info("[SLEEVE TREND] Running backtest...")
    equity_df, trades_df = st_backtest(signals)
    return equity_df, trades_df, signals
def run_sleeve_2():
    """Sleeve 2 is temporarily disabled for robustness testing."""
    logger.info("[SLEEVE 2] Disabled")
    return {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "target_weights": pd.DataFrame()}
def run_charlie_munger():
    """Charlie sleeve is temporarily disabled for robustness testing."""
    logger.info("[CHARLIE] Disabled")
    return {"equity_df": pd.DataFrame(), "trades_df": pd.DataFrame(), "target_weights": pd.DataFrame()}
def run_sleeve_charlie_munger():
    """Backward-compatible alias."""
    return run_charlie_munger()
# ============================================================
# Sleeve output extraction (for dynamic allocation)
# ============================================================
def extract_sleeve_output(
    equity_df: pd.DataFrame,
    trades_df: pd.DataFrame,
    sleeve_name: str,
    base_strength: float = 1.0,
    target_weights: pd.DataFrame | None = None,
) -> SleeveOutput:
    """
    Extract a SleeveOutput from backtest results.
    Does NOT modify any signal logic - just reads backtest output.
    """
    equity_df = _safe_df(equity_df)
    trades_df = _safe_df(trades_df)
    positions = []
    if equity_df.empty:
        return create_sleeve_output([], sleeve_name, 0.0, "No equity data")
    latest_equity = (
        float(equity_df["equity"].iloc[-1])
        if "equity" in equity_df.columns
        else 10000.0
    )
    start_equity = (
        float(equity_df["equity"].iloc[0]) if "equity" in equity_df.columns else 10000.0
    )
    if not trades_df.empty and "ticker" in trades_df.columns:
        real_trades = trades_df.copy()
        if "reason_exit" in real_trades.columns:
            real_trades = real_trades[
                ~(
                    (real_trades.get("ticker", "") == "SGOV")
                    & (real_trades.get("reason_exit", "") == "cash_proxy_fund_entries")
                )
            ].copy()
        if not real_trades.empty and "entry_date" in real_trades.columns:
            real_trades["entry_date"] = pd.to_datetime(
                real_trades["entry_date"], errors="coerce"
            )
            latest_trades = real_trades.nlargest(5, "entry_date")
            for _, row in latest_trades.iterrows():
                ticker = row.get("ticker", "")
                shares = row.get("shares", 0)
                entry_price = row.get("entry_price", 0)
                if ticker and shares > 0 and entry_price > 0:
                    notional = shares * entry_price
                    weight = notional / latest_equity if latest_equity > 0 else 0
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": weight,
                            "reason": row.get("reason_exit", "signal"),
                            "signal_strength": 1.0,
                        }
                    )
        # Also handle engine-style trades (which have "date" + "weight_to" instead
        # of "entry_date" + "shares").  This allows Sleeve 2 engine trades to
        # register as active positions for allocation purposes.
        if (
            not real_trades.empty
            and "weight_to" in real_trades.columns
            and not positions
        ):
            real_trades_sorted = real_trades.copy()
            if "date" in real_trades_sorted.columns:
                real_trades_sorted["date"] = pd.to_datetime(
                    real_trades_sorted["date"], errors="coerce"
                )
                real_trades_sorted = real_trades_sorted.sort_values(
                    "date", ascending=False
                )
            latest_engine_trades = real_trades_sorted.head(5)
            for _, row in latest_engine_trades.iterrows():
                ticker = row.get("ticker", "")
                w = abs(row.get("weight_to", 0.0))
                if ticker and w > 1e-6:
                    positions.append(
                        {
                            "ticker": ticker,
                            "target_weight": w,
                            "reason": "engine_signal",
                            "signal_strength": 1.0,
                        }
                    )
    if not positions and target_weights is not None and not target_weights.empty:
        target_last = target_weights.iloc[-1]
        for ticker, weight in target_last.items():
            if abs(weight) > WEIGHT_TOLERANCE:
                positions.append(
                    {
                        "ticker": ticker,
                        "target_weight": float(weight),
                        "reason": "target_weights",
                        "signal_strength": 1.0,
                    }
                )
    is_active = len(positions) > 0
    if is_active and start_equity > 0:
        sleeve_return = (latest_equity / start_equity) - 1.0
        strength = min(1.0, base_strength * max(0.5, min(1.5, 1.0 + sleeve_return)))
    else:
        strength = 0.0
    notes = (
        f"Active: {len(positions)} positions, equity ${latest_equity:,.0f}"
        if is_active
        else "Inactive"
    )
    return create_sleeve_output(positions, sleeve_name, strength, notes)
# ============================================================
# Portfolio equity computation (FIXED)
# ============================================================
def compute_portfolio_equity(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    cash_weight: float,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
) -> dict:
    """
    Compute TRUE portfolio equity from sleeve returns and allocations.
    This computes:
        portfolio_return = sum(sleeve_alloc_i * sleeve_return_i) + cash_weight * 0
        portfolio_equity = base_equity * (1 + portfolio_return)
    Returns dict with: equity, prev_equity, day_pnl, day_return, cumulative_return
    NOTE: This is the ONLY correct way to compute portfolio total.
    Do NOT sum sleeve equities - they are independent backtests on their own base.
    """
    portfolio_return = 0.0
    portfolio_prev_return = 0.0
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue
        df = equity_df.reset_index(drop=True)
        start = float(df["equity"].iloc[0])
        last = float(df["equity"].iloc[-1])
        prev = float(df["equity"].iloc[-2]) if len(df) > 1 else last
        if start <= 0:
            continue
        # Sleeve cumulative return (from its own backtest)
        sleeve_cum_return = (last / start) - 1.0
        sleeve_prev_return = (prev / start) - 1.0
        # Weighted contribution to portfolio return
        portfolio_return += alloc * sleeve_cum_return
        portfolio_prev_return += alloc * sleeve_prev_return
    # Cash contributes 0 return (already accounted for by not adding anything)
    # Portfolio equity
    portfolio_equity = base_equity * (1.0 + portfolio_return)
    portfolio_prev_equity = base_equity * (1.0 + portfolio_prev_return)
    day_pnl = portfolio_equity - portfolio_prev_equity
    day_return = day_pnl / portfolio_prev_equity if portfolio_prev_equity > 0 else 0.0
    return {
        "equity": portfolio_equity,
        "prev_equity": portfolio_prev_equity,
        "day_pnl": day_pnl,
        "day_return": day_return,
        "cumulative_return": portfolio_return,
    }
def resolve_portfolio_equity_series(
    sleeve_equity_map: dict[str, pd.DataFrame],
    alloc_result: AllocationResult,
    report_date: pd.Timestamp,
    portfolio_equity: float,
    offline_fixture: bool,
    base_equity: float = DEFAULT_PORTFOLIO_BASE_EQUITY,
    portfolio_stats: dict | None = None,
    st_equity: pd.DataFrame | None = None,
    s2_equity: pd.DataFrame | None = None,
    st_signals: pd.DataFrame | None = None,
    s2_details: dict | None = None,
) -> pd.Series:
    history_path = os.path.join(OUTPUT_DIR, "equity_history.csv")
    history_series = _load_equity_history(history_path)
    if not history_series.empty:
        return history_series
    ledger_path, _ = ensure_paper_state_files()
    if os.path.exists(ledger_path) and os.path.getsize(ledger_path) > 0:
        ledger = pd.read_csv(ledger_path)
        if (
            not ledger.empty
            and "date" in ledger.columns
            and "total_equity" in ledger.columns
        ):
            ledger["date"] = pd.to_datetime(ledger["date"])
            ledger = ledger.sort_values("date")
            ledger_daily = ledger.groupby("date", as_index=True)["total_equity"].last()
            ledger_series = ledger_daily.dropna().sort_index()
            if not ledger_series.empty:
                return ledger_series
    derived = compute_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=base_equity,
    )
    if not derived.empty:
        return derived
    if offline_fixture:
        return pd.Series(dtype=float)
    return _append_equity_history(history_path, report_date, portfolio_equity)
def compute_portfolio_equity_df(
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> pd.DataFrame:
    """Compute portfolio equity series from sleeve equity curves and allocations."""
    series = []
    for sleeve_name, alloc in sleeve_allocations.items():
        if alloc <= WEIGHT_TOLERANCE:
            continue
        equity_df = _safe_df(sleeve_equity_map.get(sleeve_name, pd.DataFrame()))
        if equity_df.empty or "equity" not in equity_df.columns:
            continue
        df = equity_df.copy()
        df["date"] = pd.to_datetime(df.get("date", df.index))
        df = df.sort_values("date")
        start = float(df["equity"].iloc[0])
        if start <= 0:
            continue
        df["return"] = (df["equity"] / start) - 1.0
        series.append(
            df[["date", "return"]].rename(columns={"return": f"{sleeve_name}_return"})
        )
    if not series:
        return pd.DataFrame(columns=["date", "portfolio_equity"])
    merged = series[0]
    for s in series[1:]:
        merged = merged.merge(s, on="date", how="outer")
    merged = merged.sort_values("date").reset_index(drop=True)
    for col in merged.columns:
        if col.endswith("_return"):
            merged[col] = merged[col].ffill().fillna(0.0)
    merged["portfolio_return"] = 0.0
    for sleeve_name, alloc in sleeve_allocations.items():
        col = f"{sleeve_name}_return"
        if col in merged.columns:
            merged["portfolio_return"] += alloc * merged[col]
    merged["portfolio_equity"] = base_equity * (1.0 + merged["portfolio_return"])
    return merged[["date", "portfolio_equity"]]
def compute_performance_summary(
    report_date: pd.Timestamp,
    sleeve_equity_map: dict[str, pd.DataFrame],
    sleeve_allocations: dict[str, float],
    base_equity: float,
) -> dict:
    series = compute_portfolio_equity_df(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=sleeve_allocations,
        base_equity=base_equity,
    )
    if series.empty:
        return {
            "total_return": None,
            "wtd": None,
            "mtd": None,
            "ytd": None,
        }
    series["date"] = pd.to_datetime(series["date"])
    series = series.sort_values("date")
    def _equity_asof(target_date: pd.Timestamp) -> float:
        eligible = series[series["date"] <= target_date]
        if eligible.empty:
            return float(series["portfolio_equity"].iloc[0])
        return float(eligible["portfolio_equity"].iloc[-1])
    asof_date = pd.to_datetime(report_date)
    asof_equity = _equity_asof(asof_date)
    inception_equity = float(series["portfolio_equity"].iloc[0])
    week_start = (asof_date - pd.Timedelta(days=asof_date.weekday())).normalize()
    month_start = asof_date.replace(day=1).normalize()
    year_start = asof_date.replace(month=1, day=1).normalize()
    wtd_equity = _equity_asof(week_start)
    mtd_equity = _equity_asof(month_start)
    ytd_equity = _equity_asof(year_start)
    return {
        "total_return": (
            (asof_equity / inception_equity) - 1.0 if inception_equity > 0 else None
        ),
        "wtd": (asof_equity / wtd_equity) - 1.0 if wtd_equity > 0 else None,
        "mtd": (asof_equity / mtd_equity) - 1.0 if mtd_equity > 0 else None,
        "ytd": (asof_equity / ytd_equity) - 1.0 if ytd_equity > 0 else None,
    }
def build_proposed_trades(
    alloc_result: AllocationResult,
    previous_weights: pd.DataFrame | None,
    price_map: dict,
    model_equity: float,
) -> list[dict]:
    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    if weights_df.empty:
        return []
    prev_map = {}
    if previous_weights is not None and not previous_weights.empty:
        prev_map = previous_weights.set_index("ticker")["target_weight"].to_dict()
    proposed = []
    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        target_weight = float(row["target_weight"])
        current_weight = float(prev_map.get(ticker, target_weight))
        delta_weight = target_weight - current_weight
        if abs(delta_weight) <= WEIGHT_TOLERANCE:
            continue
        action = "HOLD"
        if (
            abs(current_weight) <= WEIGHT_TOLERANCE
            and abs(target_weight) > WEIGHT_TOLERANCE
        ):
            action = "BUY" if target_weight > 0 else "SELL"
        elif (
            abs(target_weight) <= WEIGHT_TOLERANCE
            and abs(current_weight) > WEIGHT_TOLERANCE
        ):
            action = "SELL"
        elif current_weight * target_weight >= 0:
            action = (
                "INCREASE" if abs(target_weight) > abs(current_weight) else "DECREASE"
            )
        else:
            action = "SELL"
        exec_px = _compute_execution_price(price_map, ticker)
        shares = None
        notional = None
        if exec_px and model_equity > 0:
            notional = abs(delta_weight) * model_equity
            shares = round(notional / exec_px, 2) if exec_px > 0 else None
        proposed.append(
            {
                "ticker": ticker,
                "action": action,
                "sleeve": row.get("sleeve_name", ""),
                "current_weight": current_weight,
                "target_weight": target_weight,
                "delta_weight": delta_weight,
                "est_shares": shares,
                "est_notional": notional,
            }
        )
    return proposed
def derive_actual_sleeve_allocations(
    alloc_result: AllocationResult,
) -> dict[str, float]:
    allocations = {name: 0.0 for name in alloc_result.sleeve_allocations.keys()}
    weights_df = _safe_df(alloc_result.combined_weights)
    if weights_df.empty:
        return allocations
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    for _, row in weights_df.iterrows():
        sleeve_names = str(row.get("sleeve_name", "")).split(",")
        sleeve_names = [name.strip() for name in sleeve_names if name.strip()]
        if not sleeve_names:
            continue
        share = float(row.get("target_weight", 0.0)) / len(sleeve_names)
        for name in sleeve_names:
            allocations[name] = allocations.get(name, 0.0) + share
    return allocations
# ============================================================
# Daily snapshot builder
# ============================================================
def build_daily_snapshot(
    report_date: pd.Timestamp,
    alloc_result: AllocationResult,
    portfolio_stats: dict,
    st_equity: pd.DataFrame,
    s2_equity: pd.DataFrame,
    st_signals: pd.DataFrame,
    s2_details: dict,
    cm_details: dict | None = None,
) -> dict:
    stop_atr_mult = _snapshot_risk_value("STOP_ATR_MULT", STOP_ATR_MULT_DEFAULT)
    take_profit_atr_mult = _snapshot_risk_value(
        "TAKE_PROFIT_ATR_MULT", TAKE_PROFIT_ATR_MULT_DEFAULT
    )
    stop_pct = _snapshot_risk_value("STOP_PCT", STOP_PCT_DEFAULT)
    take_profit_pct = _snapshot_risk_value("TAKE_PROFIT_PCT", TAKE_PROFIT_PCT_DEFAULT)
    target_cash_weight_today = float(max(0.0, min(1.0, getattr(alloc_result, "cash_weight", 0.0))))
    breaker_cfg = get_breaker_config()
    exposure_today = float(breaker_cfg.get("exposure_multiplier", 1.0))
    exposure_label = str(breaker_cfg.get("label", "UNKNOWN"))
    invested_before_overlay = 0.0
    invested_after_overlay = max(0.0, min(1.0, 1.0 - target_cash_weight_today))

    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    weights_df = weights_df[weights_df["target_weight"].abs() > WEIGHT_TOLERANCE]
    tickers = (
        sorted(weights_df["ticker"].unique().tolist()) if not weights_df.empty else []
    )
    prices = pd.DataFrame()
    if tickers:
        prices = download_prices(tickers, period="6mo", interval="1d")
        if prices.empty or prices[["open", "high", "low", "close"]].isna().all().all():
            logger.error(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
            raise RuntimeError(
                "Downloaded price data is empty or all-NaN; aborting snapshot generation."
            )
    # --- Paper trading signals snapshot (daily immutable file) ---
    # Prefer a YYYY-MM-DD string you already use in the report.
    # If you already have something like report_date_str / asof_date_str / today_str, use that here.
    signals_path = None
    if "sleeve" not in weights_df.columns and "sleeve_name" in weights_df.columns:
        weights_df["sleeve"] = weights_df["sleeve_name"]
    if weights_df.empty:
        fallback_rows: list[dict[str, object]] = []
        for sleeve_name, details in (("sleeve_2", s2_details), ("charlie_munger", cm_details or {})):
            target_weights = (details or {}).get("target_weights")
            if not isinstance(target_weights, pd.DataFrame) or target_weights.empty:
                continue
            latest_weights = target_weights.iloc[-1]
            for ticker, target_weight in latest_weights.items():
                try:
                    weight_value = float(target_weight)
                except Exception:
                    continue
                if abs(weight_value) <= WEIGHT_TOLERANCE:
                    continue
                fallback_rows.append(
                    {
                        "ticker": str(ticker).upper(),
                        "target_weight": weight_value,
                        "sleeve": sleeve_name,
                    }
                )
        if fallback_rows:
            weights_df = pd.DataFrame(fallback_rows)
    if weights_df.empty:
        logger.warning(
            "[PAPER] No target weights available; skipping signals snapshot."
        )
    else:
        run_date_str = report_date.strftime("%Y-%m-%d")
        cutoff_date = prev_trading_day(run_date_str)

        invested_before_overlay = float(weights_df["target_weight"].sum()) if not weights_df.empty else 0.0
        weights_df_overlay = apply_portfolio_exposure_overlay(weights_df, exposure_today, cash_ticker="CASH")
        invested_after_overlay = 0.0
        if isinstance(weights_df_overlay, pd.DataFrame) and not weights_df_overlay.empty:
            non_cash = weights_df_overlay[weights_df_overlay["ticker"].astype(str) != CASH_TICKER].copy()
            invested_after_overlay = float(non_cash["target_weight"].sum()) if not non_cash.empty else 0.0
            weights_df = non_cash[non_cash["target_weight"].abs() > WEIGHT_TOLERANCE].copy()
        target_cash_weight_today = max(0.0, min(1.0, 1.0 - invested_after_overlay))
        logger.info(
            "[BREAKER] overlay applied multiplier=%.4f invested_before=%.4f invested_after=%.4f cash_target=%.4f",
            float(exposure_today),
            invested_before_overlay,
            invested_after_overlay,
            target_cash_weight_today,
        )

        breaker_block = {
            "breaker": {
                "mode": breaker_cfg.get("mode", "partial"),
                "exposure_multiplier_today": exposure_today,
                "exposure_label_today": exposure_label,
                "invested_before_overlay": invested_before_overlay,
                "invested_after_overlay": invested_after_overlay,
                "cash_target_weight_today": target_cash_weight_today,
            }
        }

        # Persist breaker-aware targets for execution/audit.
        signals_path = write_signals_snapshot(
            df_targets=weights_df,
            run_date=run_date_str,
            asof_date=cutoff_date,
            out_dir="signals",
            cash_target_weight=float(target_cash_weight_today),
            sleeve_col="sleeve",  # if column exists; otherwise writer will default to "core"
            extra=breaker_block,
        )
        logger.info("[PAPER] Wrote signals snapshot: %s", signals_path)
        signal_store_df = weights_df.rename(columns={"target_weight": "final_target_weight", "sleeve": "sleeve_source"}).copy()
        signal_store_df["ticker"] = signal_store_df["ticker"].astype(str)
        persist_signal_snapshot(signal_store_df, report_date.strftime("%Y-%m-%d"))
    price_map = _build_price_map(prices, report_date)
    atr_map = _build_atr_map(prices, report_date)
    entry_map = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        weights_history = s2_details.get("weights_df")
        prices_wide = s2_details.get("prices_wide")
        entries = infer_latest_entries(weights_history)
        entries = attach_entry_prices(entries, prices_wide)
        for _, row in entries.iterrows():
            entry_map[row["ticker"]] = {
                "entry_date": _fmt_date(pd.to_datetime(row["entry_date"])),
                "entry_price": row.get("entry_price"),
            }
    # Holdings
    holdings = []
    risk_levels = []
    model_equity = portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY)
    for _, row in weights_df.iterrows():
        ticker = row["ticker"]
        weight = float(row["target_weight"])
        direction = "LONG" if weight > 0 else "SHORT"
        price_info = price_map.get(ticker, {})
        last_px = price_info.get("last_close")
        entry_info = entry_map.get(ticker)
        entry_date = (
            entry_info.get("entry_date")
            if entry_info
            else report_date.strftime("%Y-%m-%d")
        )
        entry_date = (
            entry_info.get("entry_date") if entry_info else _fmt_date(report_date)
        )
        entry_px = (
            entry_info.get("entry_price")
            if entry_info and entry_info.get("entry_price")
            else last_px
        )
        if last_px is None or entry_px is None:
            pnl_dollars = None
            pnl_pct = None
        else:
            shares = abs(weight) * model_equity / last_px if last_px > 0 else 0.0
            if weight > 0:
                pnl_dollars = shares * (last_px - entry_px)
                pnl_pct = (last_px / entry_px) - 1.0 if entry_px > 0 else None
            else:
                pnl_dollars = shares * (entry_px - last_px)
                pnl_pct = (entry_px / last_px) - 1.0 if last_px > 0 else None
        days_held = (
            (report_date - pd.to_datetime(entry_date)).days if entry_date else None
        )
        days_held = (
            (report_date - pd.to_datetime(entry_date)).days
            if entry_date and entry_date != "n/a"
            else None
        )
        current_shares = abs(weight) * model_equity / last_px if (last_px and last_px > 0) else None
        holdings.append(
            {
                "ticker": ticker,
                "direction": direction,
                "shares": current_shares,
                "entry_date": entry_date,
                "entry_price": entry_px,
                "last_price": last_px,
                "pnl_dollars": pnl_dollars,
                "pnl_pct": pnl_pct,
                "days_held": days_held,
            }
        )
        atr = atr_map.get(ticker)
        try:
            if entry_px is None:
                stop_loss = None
                take_profit = None
            elif atr is not None:
                if weight > 0:
                    stop_loss = entry_px - stop_atr_mult * atr
                    take_profit = entry_px + take_profit_atr_mult * atr
                else:
                    stop_loss = entry_px + stop_atr_mult * atr
                    take_profit = entry_px - take_profit_atr_mult * atr
            else:
                if weight > 0:
                    stop_loss = entry_px * (1 - stop_pct)
                    take_profit = entry_px * (1 + take_profit_pct)
                else:
                    stop_loss = entry_px * (1 + stop_pct)
                    take_profit = entry_px * (1 - take_profit_pct)
        except Exception as err:
            logger.error(
                "[SNAPSHOT] stop calc failed for %s on %s: %s",
                ticker,
                report_date.strftime("%Y-%m-%d"),
                err,
            )
            stop_loss = None
            take_profit = None
            atr = None
        risk_levels.append(
            {
                "ticker": ticker,
                "entry_price": entry_px,
                "atr": atr,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }
        )
    # Trades for today
    prev_weights = {}
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        hist = s2_details.get("weights_df")
        if len(hist) > 1:
            prev_row = hist.iloc[-2]
            prev_weights = prev_row.to_dict()
    new_weights = {
        row["ticker"]: float(row["target_weight"]) for _, row in weights_df.iterrows()
    }
    all_tickers = sorted(set(prev_weights.keys()) | set(new_weights.keys()))
    orders = []
    for ticker in all_tickers:
        if ticker == CASH_TICKER:
            continue
        prev_w = float(prev_weights.get(ticker, 0.0))
        new_w = float(new_weights.get(ticker, 0.0))
        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            continue
        def _append_order(action, weight):
            exec_px = _compute_execution_price(price_map, ticker)
            shares = None
            notional = None
            if exec_px and model_equity > 0:
                notional = abs(weight) * model_equity
                shares = round(notional / exec_px, 2) if exec_px > 0 else None
            orders.append(
                {
                    "action": action,
                    "ticker": ticker,
                    "target_weight": weight,
                    "execution_price": exec_px,
                    "shares": shares,
                    "notional": notional,
                }
            )
        if abs(prev_w) <= WEIGHT_TOLERANCE and abs(new_w) > WEIGHT_TOLERANCE:
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)
        elif abs(prev_w) > WEIGHT_TOLERANCE and abs(new_w) <= WEIGHT_TOLERANCE:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
        elif prev_w * new_w < 0:
            _append_order("SELL" if prev_w > 0 else "COVER", 0.0)
            _append_order("BUY" if new_w > 0 else "SHORT", new_w)
    # Watchlist
    watchlist = []
    held = {h["ticker"] for h in holdings}
    if st_signals is not None and not st_signals.empty:
        st_latest = st_signals[st_signals["date"] == st_signals["date"].max()].copy()
        st_latest = st_latest[~st_latest["ticker"].isin(held)]
        st_latest = st_latest[st_latest["passes_liquidity"]]
        st_latest["delta_long"] = trend_cfg.LONG_THRESHOLD - st_latest["final_signal"]
        st_latest["delta_short"] = trend_cfg.SHORT_THRESHOLD - st_latest["final_signal"]
        near_long = st_latest[
            (st_latest["signal_long"])
            & (st_latest["delta_long"] >= 0)
            & (st_latest["delta_long"] <= 5)
        ]
        near_short = st_latest[
            (st_latest["signal_short"])
            & (st_latest["delta_short"] >= 0)
            & (st_latest["delta_short"] <= 5)
        ]
        for _, row in pd.concat([near_long, near_short]).head(5).iterrows():
            threshold = (
                trend_cfg.LONG_THRESHOLD
                if row["signal_long"]
                else trend_cfg.SHORT_THRESHOLD
            )
            reason = f"trend score={row['final_signal']:.1f} vs {threshold} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    s2_signals = s2_details.get("signals") if s2_details else pd.DataFrame()
    if s2_signals is not None and not s2_signals.empty:
        s2_latest = s2_signals[s2_signals["date"] == s2_signals["date"].max()].copy()
        s2_latest = s2_latest[~s2_latest["ticker"].isin(held)]
        s2_latest["delta_score"] = S2_LONG_THRESHOLD - s2_latest["score_long"]
        near_long = s2_latest[
            (s2_latest["delta_score"] >= 0) & (s2_latest["delta_score"] <= 5)
        ]
        near_short = s2_latest[s2_latest["z_pe"] >= (Z_EXTREME_SHORT - 0.25)]
        for _, row in near_long.head(5).iterrows():
            reason = f"score={row['score_long']:.1f} vs {S2_LONG_THRESHOLD} threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
        for _, row in near_short.head(5).iterrows():
            reason = f"z_pe={row['z_pe']:.2f} vs {Z_EXTREME_SHORT} short threshold"
            watchlist.append({"ticker": row["ticker"], "reason": reason})
    watchlist = watchlist[:10]
    broker_equity = os.environ.get("BROKER_EQUITY")
    broker_equity_val = (
        float(broker_equity) if broker_equity not in (None, "") else None
    )
    reconciliation = {
        "model_start_equity": DEFAULT_PORTFOLIO_BASE_EQUITY,
        "model_current_equity": portfolio_stats.get("equity"),
        "broker_equity": broker_equity_val,
        "difference": (
            (portfolio_stats.get("equity") - broker_equity_val)
            if broker_equity_val
            else None
        ),
        "note": (
            "slippage/fees/timing"
            if broker_equity_val
            else "broker equity placeholder (set BROKER_EQUITY)"
        ),
    }
    base_sleeves = {
        str(name): max(0.0, float(weight))
        for name, weight in (alloc_result.sleeve_allocations or {}).items()
    }
    base_sleeves_total = float(sum(base_sleeves.values()))
    target_risk_on = max(0.0, min(1.0, 1.0 - float(target_cash_weight_today)))
    if base_sleeves_total > WEIGHT_TOLERANCE:
        sleeve_scale = target_risk_on / base_sleeves_total
        display_sleeves = {name: float(weight * sleeve_scale) for name, weight in base_sleeves.items()}
    else:
        display_sleeves = {name: 0.0 for name in base_sleeves}
    display_cash = max(0.0, min(1.0, 1.0 - float(sum(display_sleeves.values()))))
    allocations = {
        "risk_on": 1.0 - display_cash,
        "cash": display_cash,
        "sleeves": display_sleeves,
        "cash_reason": getattr(alloc_result, "cash_reason", None),
    }
    trend_target_weights = pd.DataFrame(columns=["ticker", "target_weight"])
    if not weights_df.empty:
        if "sleeve" in weights_df.columns:
            trend_target_weights = weights_df[
                weights_df["sleeve"].astype(str) == "sleeve_trend"
            ][["ticker", "target_weight"]].copy()
        elif "sleeve_name" in weights_df.columns:
            trend_target_weights = weights_df[
                weights_df["sleeve_name"].astype(str) == "sleeve_trend"
            ][["ticker", "target_weight"]].copy()
    if trend_target_weights.empty:
        combined = _safe_df(getattr(alloc_result, "combined_weights", pd.DataFrame()))
        if not combined.empty and "sleeve_name" in combined.columns:
            trend_target_weights = combined[
                combined["sleeve_name"].astype(str) == "sleeve_trend"
            ][["ticker", "target_weight"]].copy()

    sleeve_states = {
        "sleeve_trend": determine_sleeve_state(
            {
                "equity_df": _safe_df(st_equity),
                "target_weights": _safe_df(trend_target_weights),
            },
            allocation_weight=float(display_sleeves.get("sleeve_trend", 0.0)),
            weight_tolerance=WEIGHT_TOLERANCE,
        ),
        "sleeve_2": determine_sleeve_state(
            {
                "equity_df": _safe_df(s2_equity),
                "target_weights": _safe_df((s2_details or {}).get("target_weights")),
            },
            allocation_weight=float(display_sleeves.get("sleeve_2", 0.0)),
            weight_tolerance=WEIGHT_TOLERANCE,
        ),
        "charlie_munger": determine_sleeve_state(
            {
                "equity_df": _safe_df((cm_details or {}).get("equity_df")),
                "target_weights": _safe_df((cm_details or {}).get("target_weights")),
            },
            allocation_weight=float(display_sleeves.get("charlie_munger", 0.0)),
            weight_tolerance=WEIGHT_TOLERANCE,
        ),
    }
    performance_diagnostics = {
        "current_equity": portfolio_stats.get("equity"),
        "day_return": portfolio_stats.get("day_return"),
        "cumulative_return": portfolio_stats.get("cumulative_return"),
    }
    previous_weights_df = None
    if (
        s2_details
        and s2_details.get("weights_df") is not None
        and not s2_details.get("weights_df").empty
    ):
        s2_weights_hist = s2_details.get("weights_df")
        prev_row = s2_weights_hist.iloc[-1]
        previous_weights_df = pd.DataFrame(
            {
                "ticker": prev_row.index,
                "target_weight": prev_row.values,
            }
        )
    s2_no_picks = False
    if (
        s2_details
        and s2_details.get("target_weights") is not None
        and not s2_details.get("target_weights").empty
    ):
        s2_last_weights = s2_details.get("target_weights").iloc[-1]
        s2_no_picks = s2_last_weights.abs().sum() <= WEIGHT_TOLERANCE
    proposed_trades = build_proposed_trades(
        alloc_result=alloc_result,
        previous_weights=previous_weights_df,
        price_map=price_map,
        model_equity=model_equity,
    )
    performance_summary = compute_performance_summary(
        report_date=report_date,
        sleeve_equity_map={
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        },
        sleeve_allocations=alloc_result.sleeve_allocations,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )
    return {
        "asof": report_date,
        "cash_target": target_cash_weight_today,
        "target_cash_weight": target_cash_weight_today,
        "breaker": {
            "mode": breaker_cfg.get("mode", "partial"),
            "exposure_multiplier_today": exposure_today,
            "exposure_label_today": exposure_label,
            "invested_before_overlay": invested_before_overlay,
            "invested_after_overlay": invested_after_overlay,
            "cash_target_weight_today": target_cash_weight_today,
        },
        "sleeve_allocations": allocations.get("sleeves", {}),
        "allocations": allocations,
        "sleeve_states": sleeve_states,
        "performance_summary": performance_summary,
        "orders": orders,
        "risk_levels": risk_levels,
        "holdings": holdings,
        "watchlist": watchlist,
        "reconciliation": reconciliation,
        "proposed_trades": proposed_trades,
        "performance_summary": performance_summary,
        "performance_diagnostics": performance_diagnostics,
        "skipped_trades": alloc_result.skipped_trades if alloc_result else [],
        "s2_no_picks": s2_no_picks,
        "signals_snapshot_path": signals_path,
        "charlie_munger": (cm_details or {}).get("signals", {}),
        "charlie_munger_benchmark": {**((cm_details or {}).get("benchmark", {}) or {}), "sleeve_cumulative_return": ((cm_details or {}).get("sleeve_stats", {}) or {}).get("cumulative_return")},
    }
# ============================================================
# Report builder (FIXED)
# ============================================================
def build_html_report(
    report_date: pd.Timestamp | str,
    st_equity,
    st_trades,
    s2_equity,
    s2_trades,
    alloc_result: AllocationResult = None,
    alpha_stats: dict | None = None,
    proposed_trades: list[dict] | None = None,
    performance_summary: dict | None = None,
    s2_no_picks: bool = False,
    cm_details: dict | None = None,
    inception_metrics: dict | None = None,
    allocation_diagnostics: dict | None = None,
) -> str:
    """
    Build HTML report with CORRECT portfolio math.
    Portfolio Snapshot now shows:
    - Sleeve rows: allocated notional only (attribution), NOT additive equity
    - CASH row: allocated notional with 0 return
    - TOTAL row: TRUE portfolio equity computed from weighted sleeve returns
    The TOTAL is the ONLY authoritative equity figure.
    """
    BASE_EQUITY = DEFAULT_PORTFOLIO_BASE_EQUITY
    report_date_fmt = _fmt_date(report_date)
    inception_metrics = inception_metrics or {}
    allocation_diagnostics = allocation_diagnostics or {}
    sleeve1_diag = allocation_diagnostics.get("sleeve_1", {}) or {}
    # Build summary with dynamic allocation
    if alloc_result is not None:
        trend_alloc = alloc_result.sleeve_allocations.get("sleeve_trend", 0.0)
        val_alloc = alloc_result.sleeve_allocations.get("sleeve_2", 0.0)
        cash_alloc = alloc_result.cash_weight
        # Build sleeve equity map for portfolio computation
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        }
        # Compute TRUE portfolio equity (the only correct total)
        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=alloc_result.sleeve_allocations,
            cash_weight=cash_alloc,
            base_equity=BASE_EQUITY,
        )
        # Build sleeve rows - show ALLOCATED NOTIONAL only (attribution)
        # Do NOT show sleeve backtest equity as it's not additive
        rows = []
        if trend_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * trend_alloc
            # Compute sleeve-level return for display (attribution only)
            st_df = _safe_df(st_equity)
            if not st_df.empty and "equity" in st_df.columns:
                st_start = float(st_df["equity"].iloc[0])
                st_last = float(st_df["equity"].iloc[-1])
                st_prev = float(st_df["equity"].iloc[-2]) if len(st_df) > 1 else st_last
                if st_start > 0:
                    st_ret = (st_last / st_start) - 1.0
                    st_day_ret = (st_last - st_prev) / st_prev if st_prev > 0 else 0.0
                    # Attribution: this sleeve's contribution to portfolio
                    contrib_equity = BASE_EQUITY * trend_alloc * (1.0 + st_ret)
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(st_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve Trend — Momentum ({trend_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve Trend — Momentum (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
        if val_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * val_alloc
            s2_df = _safe_df(s2_equity)
            if not s2_df.empty and "equity" in s2_df.columns:
                s2_start = float(s2_df["equity"].iloc[0])
                s2_last = float(s2_df["equity"].iloc[-1])
                s2_prev = float(s2_df["equity"].iloc[-2]) if len(s2_df) > 1 else s2_last
                if s2_start > 0:
                    s2_ret = (s2_last / s2_start) - 1.0
                    s2_day_ret = (s2_last - s2_prev) / s2_prev if s2_prev > 0 else 0.0
                    contrib_equity = BASE_EQUITY * val_alloc * (1.0 + s2_ret)
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(s2_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Sleeve 2 — Valuation ({val_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
                        "Equity": "—",
                        "Day Return": "—",
                    }
                )
        else:
            rows.append(
                {
                    "Sleeve": "Sleeve 2 — Valuation (inactive)",
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
            sleeve2_label = (
                "Sleeve 2 — Valuation (no eligible picks)"
                if s2_no_picks
                else "Sleeve 2 — Valuation (inactive)"
            )
            rows.append(
                {
                    "Sleeve": sleeve2_label,
                    "Allocated": "—",
                    "Equity": "—",
                    "Day Return": "—",
                }
            )
        # CASH row
        if cash_alloc > WEIGHT_TOLERANCE:
            cash_notional = BASE_EQUITY * cash_alloc
            rows.append(
                {
                    "Sleeve": f"CASH ({cash_alloc:.2%})",
                    "Allocated": _fmt_money(cash_notional),
                    "Equity": _fmt_money(cash_notional),  # Cash doesn't grow
                    "Day Return": _fmt_pct(0),
                }
            )
        summary_df = pd.DataFrame(rows)
        # TOTAL row - THE AUTHORITATIVE PORTFOLIO EQUITY
        # Computed from weighted sleeve returns, NOT by summing rows above
        total_row = pd.DataFrame(
            [
                {
                    "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
                    "Allocated": _fmt_money(BASE_EQUITY),
                    "Equity": _fmt_money(portfolio_stats["equity"]),
                    "Day Return": _fmt_pct(portfolio_stats["day_return"]),
                }
            ]
        )
        summary_df = pd.concat([summary_df, total_row], ignore_index=True)
        alloc_summary = allocation_summary_df(alloc_result)
        holdings = holdings_snapshot_df(alloc_result)
        skipped_df = (
            pd.DataFrame(alloc_result.skipped_trades)
            if alloc_result.skipped_trades
            else pd.DataFrame()
        )
    else:
        # Legacy static allocation fallback
        # Still compute correctly using weighted returns
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
        }
        static_allocs = {"sleeve_trend": 0.60, "sleeve_2": 0.20, "charlie_munger": 0.20}
        static_allocs, static_cash = enforce_charlie_bounds(static_allocs, 0.0, charlie_active=True)
        portfolio_stats = compute_portfolio_equity(
            sleeve_equity_map=sleeve_equity_map,
            sleeve_allocations=static_allocs,
            cash_weight=static_cash,
            base_equity=BASE_EQUITY,
        )
        rows = [
            {
                "Sleeve": f"Sleeve Trend — Momentum ({static_allocs.get('sleeve_trend', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("sleeve_trend", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
            {
                "Sleeve": f"Sleeve 2 — Valuation ({static_allocs.get('sleeve_2', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("sleeve_2", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
            {
                "Sleeve": f"Charlie Munger — Long Hold ({static_allocs.get('charlie_munger', 0.0):.0%})",
                "Allocated": _fmt_money(BASE_EQUITY * static_allocs.get("charlie_munger", 0.0)),
                "Equity": "—",
                "Day Return": "—",
            },
        ]
        if static_cash > WEIGHT_TOLERANCE:
            rows.append(
                {
                    "Sleeve": f"CASH ({static_cash:.0%})",
                    "Allocated": _fmt_money(BASE_EQUITY * static_cash),
                    "Equity": _fmt_money(BASE_EQUITY * static_cash),
                    "Day Return": _fmt_pct(0),
                }
            )
        total_row = {
            "Sleeve": f"TOTAL — Portfolio ({_fmt_money(BASE_EQUITY)})",
            "Allocated": _fmt_money(BASE_EQUITY),
            "Equity": _fmt_money(portfolio_stats["equity"]),
            "Day Return": _fmt_pct(portfolio_stats["day_return"]),
        }
        summary_df = pd.concat(
            [pd.DataFrame(rows), pd.DataFrame([total_row])], ignore_index=True
        )
        alloc_summary, holdings, skipped_df = None, None, pd.DataFrame()
    # Build exit log
    exit_log_rows = []
    for trades_df, sleeve_name in [(st_trades, "Trend"), (s2_trades, "Valuation")]:
        filtered = (
            filter_sleeve2_cash_proxy(_safe_df(trades_df))
            if sleeve_name == "Valuation"
            else _safe_df(trades_df)
        )
        if not filtered.empty and "reason_exit" in filtered.columns:
            for _, row in filtered.tail(10).iterrows():
                exit_log_rows.append(
                    {
                        "Ticker": row.get("ticker", ""),
                        "Sleeve": sleeve_name,
                        "Exit Reason": row.get("reason_exit", ""),
                        "Days Held": row.get("hold_days", ""),
                        "P&L": _fmt_money(row.get("pnl", 0)),
                    }
                )
    # CSS
    css = base_email_css() + " h2 { margin-bottom: 4px; } h3 { margin-top: 16px; }"
    performance_rows = [
        {"Metric": "Current Equity", "Value": _fmt_money(portfolio_stats["equity"])},
        {"Metric": "Day Return", "Value": _fmt_pct(portfolio_stats["day_return"])},
        {
            "Metric": "Cumulative Return",
            "Value": _fmt_pct(portfolio_stats["cumulative_return"]),
        },
        {"Metric": "Inception Date", "Value": _fmt_date(inception_metrics.get("inception_date"))},
        {"Metric": "SPY Return (Since Inception)", "Value": _fmt_pct(inception_metrics.get("spy_return_since_inception"))},
    ]
    performance_html = html_table(
        pd.DataFrame(performance_rows), "Performance Summary (Portfolio)", 10
    )
    performance_section = f'<div class="card">{performance_html}</div>'
    def _fmt_float(value: float | None) -> str:
        try:
            return f"{float(value):.2f}"
        except Exception:
            return "n/a"
    if alpha_stats and alpha_stats.get("ok"):
        alpha_summary = alpha_stats.get("summary", {}) or {}
        alpha_rows = [
            {"Metric": "Overlap Window", "Value": f"{alpha_stats.get('overlap_start')} → {alpha_stats.get('overlap_end')} ({alpha_stats.get('overlap_days')} days)"},
            {"Metric": "Cumulative Portfolio Return", "Value": _fmt_pct(alpha_summary.get("cumulative_port_return"))},
            {"Metric": "Cumulative SPY Return", "Value": _fmt_pct(alpha_summary.get("cumulative_spy_return"))},
            {"Metric": "Cumulative Alpha", "Value": _fmt_pct(alpha_summary.get("cumulative_alpha"))},
        ]
        alpha_tbl = html_table(pd.DataFrame(alpha_rows), "Alpha Attribution vs SPY", 10)
        daily_rows = pd.DataFrame(alpha_stats.get("rows", []) or []).rename(
            columns={"date": "Date", "port_ret": "Portfolio Return", "spy_ret": "SPY Return", "spread": "Spread"}
        )
        alpha_daily_tbl = html_table(daily_rows, "Alpha Daily Spread (Last 10 Days)", 10, "No overlapping return rows.")
        alpha_section = f'<div class="card">{alpha_tbl}{alpha_daily_tbl}</div>'
    else:
        reason = (alpha_stats or {}).get("reason") or "Alpha attribution unavailable."
        alpha_section = (
            '<div class="card">'
            "<h3>Alpha Attribution vs SPY</h3>"
            f"<p><em>Pending — {reason}</em></p>"
            "</div>"
        )
    # Build sections
    perf_rows = []
    if performance_summary:
        perf_rows = [
            {
                "Metric": "Total Return (Since Inception)",
                "Return": _fmt_pct(performance_summary.get("total_return")),
            },
            {
                "Metric": "Week-to-Date",
                "Return": _fmt_pct(performance_summary.get("wtd")),
            },
            {
                "Metric": "Month-to-Date",
                "Return": _fmt_pct(performance_summary.get("mtd")),
            },
            {
                "Metric": "Year-to-Date",
                "Return": _fmt_pct(performance_summary.get("ytd")),
            },
        ]
    perf_section = (
        f'<div class="card">{html_table(pd.DataFrame(perf_rows), "Performance Summary")}</div>'
        if perf_rows
        else ""
    )
    proposed_df = pd.DataFrame(proposed_trades or [])
    if not proposed_df.empty:
        proposed_df = proposed_df.rename(
            columns={
                "ticker": "Ticker",
                "action": "Action",
                "sleeve": "Sleeve",
                "current_weight": "Current Weight",
                "target_weight": "Target Weight",
                "delta_weight": "Delta Weight",
                "est_shares": "Est. Shares",
                "est_notional": "Est. Notional",
            }
        )
    proposed_html = html_table(
        proposed_df,
        "Model Intent (Pre-Constraints)",
        25,
        "No proposed trades.",
    )
    proposed_section = f'<div class="card">{proposed_html}</div>'
    alloc_html = (
        alloc_summary.to_html(index=False, border=0, classes="tbl", justify="left")
        if alloc_summary is not None
        else ""
    )
    alloc_section = (
        f'<div class="card"><h3>Sleeve Allocation (Dynamic)</h3>{alloc_html}</div>'
        if alloc_html
        else ""
    )
    alloc_diag_items = [
        {"Metric": "Sleeve 1 desired allocation (post-breaker)", "Value": _fmt_pct(sleeve1_diag.get("desired_allocation"))},
    ]
    if sleeve1_diag.get("desired_allocation_pre_breaker") is not None:
        alloc_diag_items.append(
            {"Metric": "Sleeve 1 desired allocation (pre-breaker)", "Value": _fmt_pct(sleeve1_diag.get("desired_allocation_pre_breaker"))}
        )
    alloc_diag_items.extend(
        [
            {"Metric": "Sleeve 1 achieved invested", "Value": _fmt_pct(sleeve1_diag.get("achieved_invested"))},
            {"Metric": "Sleeve 1 forced cash", "Value": _fmt_pct(sleeve1_diag.get("forced_cash"))},
            {
                "Metric": "Sleeve 1 names selected / required",
                "Value": f"{sleeve1_diag.get('selected_names', 'n/a')} / {sleeve1_diag.get('min_required_names', 'n/a')}",
            },
            {"Metric": "Limiting constraint", "Value": sleeve1_diag.get("limiting_constraint", "n/a")},
        ]
    )
    alloc_diag_rows = pd.DataFrame(alloc_diag_items)
    alloc_diag_section = f'<div class="card">{html_table(alloc_diag_rows, "Allocation Diagnostics")}</div>'
    holdings_html = (
        html_table(holdings, "Holdings Snapshot", 20)
        if holdings is not None and not holdings.empty
        else ""
    )
    holdings_section = (
        f'<div class="card">{holdings_html}</div>' if holdings_html else ""
    )
    skipped_html = (
        html_table(skipped_df, "Skipped Trades (Constraint Hits)", 10)
        if not skipped_df.empty
        else ""
    )
    skipped_section = f'<div class="card">{skipped_html}</div>' if skipped_html else ""
    exit_html = (
        html_table(pd.DataFrame(exit_log_rows), "Exit Log", 15) if exit_log_rows else ""
    )
    exit_section = f'<div class="card">{exit_html}</div>' if exit_html else ""
    st_trades_html = html_table(
        filter_sleeve2_cash_proxy(st_trades), "Recent Trades — Sleeve Trend", 15
    )
    s2_trades_html = html_table(
        filter_sleeve2_cash_proxy(s2_trades),
        "Recent Trades — Sleeve 2",
        15,
        "No eligible picks for Sleeve 2.",
    )
    st_equity_html = html_table(
        _safe_df(st_equity).tail(10), "Equity — Sleeve Trend (last 10 days)", 10
    )
    s2_equity_html = html_table(
        _safe_df(s2_equity).tail(10), "Equity — Sleeve 2 (last 10 days)", 10
    )
    cm_trades_html = html_table(
        _safe_df((cm_details or {}).get("trades_df", pd.DataFrame())),
        "Recent Trades — Charlie Munger",
        15,
        "No Charlie Munger trades.",
    )
    cm_equity_html = html_table(
        _safe_df((cm_details or {}).get("equity_df", pd.DataFrame())).tail(10),
        "Equity — Charlie Munger (last 10 weeks)",
        10,
    )
    cm_signal = (cm_details or {}).get("signals", {}) or {}
    cm_meta = cm_signal.get("meta", {}) if isinstance(cm_signal, dict) else {}
    cm_selected = cm_signal.get("selected", []) if isinstance(cm_signal, dict) else []
    cm_rows = [
        {"Metric": "Near-200W candidates", "Value": cm_meta.get("near_ma_candidates", 0)},
        {"Metric": "New buys", "Value": len(cm_selected or [])},
        {"Metric": "Sells", "Value": len(cm_signal.get("sell", []) if isinstance(cm_signal, dict) else [])},
        {"Metric": "Benchmark", "Value": ((cm_details or {}).get("benchmark", {}) or {}).get("ticker", "SPY")},
        {"Metric": "SPY cumulative return", "Value": _fmt_pct(((cm_details or {}).get("benchmark", {}) or {}).get("cumulative_return"))},
        {"Metric": "SPY max drawdown", "Value": _fmt_pct(((cm_details or {}).get("benchmark", {}) or {}).get("max_drawdown"))},
    ]
    cm_section = f'<div class="card">{html_table(pd.DataFrame(cm_rows), "Charlie Munger Sleeve")}</div>'
    return f"""
    <html>
    <head><style>{css}</style></head>
    <body>
      <div class="wrap">
        <h2>Daily Quant Report</h2>
        <div class="muted">{report_date_fmt}</div>
        {perf_section}
        {proposed_section}
        <div class="card">{html_table(summary_df, "Portfolio Snapshot")}</div>
        {performance_section}
        {alpha_section}
        {alloc_section}
        {alloc_diag_section}
        {cm_section}
        {holdings_section}
        <div class="card">
          {st_trades_html}
          {s2_trades_html}
          {cm_trades_html}
        </div>
        {exit_section}
        {skipped_section}
        <div class="card">
          {st_equity_html}
          {s2_equity_html}
          {cm_equity_html}
        </div>
        <div class="muted">
          Automated daily report. Portfolio TOTAL computed from weighted sleeve returns
          (not sum of rows).
        </div>
      </div>
    </body>
    </html>
    """
# ============================================================
# Main
# ============================================================
def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run daily quant report workflow")
    parser.add_argument(
        "--paper-reset",
        "--paper_reset",
        dest="paper_reset",
        action="store_true",
        help="Reset paper/shadow state to a clean start before running.",
    )
    parser.add_argument(
        "--paper-start-cash",
        "--paper_start_cash",
        dest="paper_start_cash",
        type=float,
        default=10_000.0,
        help="Starting cash used with --paper-reset (default: 10000).",
    )
    parser.add_argument(
        "--reset-ledger-date",
        dest="reset_ledger_date",
        default=None,
        help="Delete shadow idempotency ledger rows matching YYYY-MM-DD before execution",
    )
    parser.add_argument(
        "--force-execution",
        "--force_execution",
        "--reset-orders-sent",
        "--reset_orders_sent",
        dest="force_execution",
        action="store_true",
        help=(
            "Force same-day execution by resetting orders_sent idempotency markers "
            "(equivalent to FORCE_EXECUTION=1)."
        ),
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Generate planning artifacts only; skip order generation even when market is open.",
    )
    parser.add_argument(
        "--exit-only",
        "--exit_only",
        dest="exit_only",
        action="store_true",
        help="Explicitly force exit-only execution (sells/reductions only).",
    )
    parser.add_argument(
        "--backtest-start",
        dest="backtest_start",
        default=None,
        help="Backtest start date YYYY-MM-DD (enables backtest mode).",
    )
    parser.add_argument(
        "--backtest-end",
        dest="backtest_end",
        default=None,
        help="Backtest end date YYYY-MM-DD (enables backtest mode).",
    )
    parser.add_argument(
        "--breaker-policy",
        dest="breaker_policy",
        default=None,
        help="Breaker policy for backtest mode: FULL|PARTIAL|LOCK.",
    )
    parser.add_argument(
        "--audit-export",
        dest="audit_export",
        default=None,
        help="Backtest mode: write audit bundle when true (1/0).",
    )
    parser.add_argument(
        "--audit-run-id",
        dest="audit_run_id",
        default=None,
        help="Backtest mode: audit run id.",
    )
    parser.add_argument(
        "--audit-outdir",
        dest="audit_outdir",
        default=None,
        help="Backtest mode: base audit output directory.",
    )
    parser.add_argument(
        "--allow-empty-sleeves",
        dest="allow_empty_sleeves",
        default=None,
        help="Backtest mode: allow empty outputs (1/0).",
    )
    return parser.parse_args(argv)


def _is_truthy(value: str | int | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_backtest_mode(args: argparse.Namespace) -> bool:
    return bool(
        getattr(args, "backtest_start", None)
        or getattr(args, "backtest_end", None)
        or os.getenv("BACKTEST_START")
        or os.getenv("BACKTEST_END")
    )


def _resolve_backtest_dates(args: argparse.Namespace) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_raw = getattr(args, "backtest_start", None) or os.getenv("BACKTEST_START")
    end_raw = getattr(args, "backtest_end", None) or os.getenv("BACKTEST_END")
    if not start_raw or not end_raw:
        raise RuntimeError(
            "Backtest mode requires BACKTEST_START and BACKTEST_END (or --backtest-start/--backtest-end)."
        )
    start = pd.Timestamp(start_raw).normalize()
    end = pd.Timestamp(end_raw).normalize()
    if end < start:
        raise RuntimeError(
            f"Backtest mode failed: BACKTEST_END ({end.date()}) < BACKTEST_START ({start.date()})."
        )
    return start, end


def _run_backtest_mode(args: argparse.Namespace) -> None:
    _ensure_audit_imports()
    start, end = _resolve_backtest_dates(args)
    policy = (
        str(getattr(args, "breaker_policy", None) or os.getenv("BREAKER_POLICY", "FULL"))
        .strip()
        .upper()
    )
    if policy not in {"FULL", "PARTIAL", "LOCK"}:
        policy = "FULL"

    # Backtest/policy runs are deterministic by default: state does not override env policy.
    os.environ.setdefault("BREAKER_STATE_CAN_OVERRIDE", "0")

    allow_empty = _is_truthy(
        getattr(args, "allow_empty_sleeves", None)
        if getattr(args, "allow_empty_sleeves", None) is not None
        else os.getenv("ALLOW_EMPTY_SLEEVES"),
        default=False,
    )
    synthetic = _is_truthy(os.getenv("BACKTEST_SYNTHETIC"), default=False)
    initial_equity = float(os.getenv("BACKTEST_INITIAL_EQUITY", "10000"))
    commission_bps = float(os.getenv("BACKTEST_COMMISSION_BPS", "0"))
    slippage_bps = float(os.getenv("BACKTEST_SLIPPAGE_BPS", "0"))
    top_n = int(os.getenv("BACKTEST_TOP_N", "5"))

    dataset = load_sleeve1_dataset(
        start=start,
        end=end,
        synthetic=synthetic,
    )
    result = run_window_backtest(
        dataset,
        start=start,
        end=end,
        breaker_policy=policy,
        top_n=top_n,
        initial_equity=initial_equity,
        commission_bps=commission_bps,
        slippage_bps=slippage_bps,
        allow_empty_sleeves=allow_empty,
    )

    errors: list[str] = []
    if result.get("target_weights_post", pd.DataFrame()).empty:
        errors.append(
            "Backtest mode failed: sleeve_1 empty target_weights (missing data). Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )
    if result.get("portfolio_daily", pd.DataFrame()).empty:
        errors.append(
            "Backtest mode failed: portfolio_daily is empty. Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )
    if result.get("holdings_daily", pd.DataFrame()).empty:
        errors.append(
            "Backtest mode failed: holdings_daily is empty. Set ALLOW_EMPTY_SLEEVES=1 to continue."
        )

    if errors and not allow_empty:
        raise RuntimeError(errors[0])

    summary = dict(result.get("summary", {}))
    if errors:
        summary["warnings"] = "; ".join(errors)
    summary["allow_empty_sleeves"] = bool(allow_empty)

    audit_export = _is_truthy(
        getattr(args, "audit_export", None)
        if getattr(args, "audit_export", None) is not None
        else os.getenv("AUDIT_EXPORT", "1"),
        default=True,
    )
    run_id = (
        str(getattr(args, "audit_run_id", None) or os.getenv("AUDIT_RUN_ID", "")).strip()
        or audit_default_run_id(start=start, end=end, policy=policy)
    )
    audit_root = Path(
        getattr(args, "audit_outdir", None) or os.getenv("AUDIT_OUTDIR", "outputs/audit")
    )
    audit_out = audit_root / run_id
    if audit_export:
        write_audit_bundle(
            run_id=run_id,
            trades_df=result.get("trades"),
            holdings_daily_df=result.get("holdings_daily"),
            portfolio_daily_df=result.get("portfolio_daily"),
            summary=summary,
            outdir=audit_out,
        )
    print(
        "[BACKTEST_MODE] "
        f"start={start.date()} end={end.date()} policy={policy} "
        f"audit_out={audit_out if audit_export else 'disabled'}"
    )
def main(argv: list[str] | None = None):
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    # Fail fast on placeholder/invalid REPORT_DATE before running sleeves/network calls.
    _parse_report_date_env(os.getenv("REPORT_DATE", ""))
    ensure_no_legacy_ledger(logger=logger, when="startup")
    if _is_backtest_mode(args):
        _run_backtest_mode(args)
        return
    _ensure_quant_report_imports()
    _ensure_paper_broker_imports()
    offline_fixture = os.getenv("OFFLINE_FIXTURE", "").lower() in {"1", "true", "yes"}
    fixture_date = os.getenv("OFFLINE_FIXTURE_DATE", "2000-01-01")
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    today = fixture_date if offline_fixture else dt.date.today().strftime("%Y-%m-%d")
    portfolio_fixture = (
        _load_portfolio_fixture("tests/fixtures/portfolio_equity.csv")
        if offline_fixture
        else pd.Series(dtype=float)
    )
    if offline_fixture and not portfolio_fixture.empty:
        today = portfolio_fixture.index.max().strftime("%Y-%m-%d")
    # ── Run sleeves ───────────────────────────────────────────────
    if offline_fixture:
        logger.warning(
            "[OFFLINE] Fixture mode enabled; skipping sleeve runs and live data fetches."
        )
        _, _ = pd.DataFrame(), pd.DataFrame()
        st_equity, st_trades, st_signals = (
            pd.DataFrame(),
            pd.DataFrame(),
            pd.DataFrame(),
        )
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
        cm_details = {}
        cm_equity, cm_trades = pd.DataFrame(), pd.DataFrame()
    else:
        try:
            _, _ = run_sleeve_1()
        except Exception as e:
            logger.warning("[WARN] Sleeve 1 failed: %s", e)
            _, _ = pd.DataFrame(), pd.DataFrame()
        try:
            st_equity, st_trades, st_signals = run_sleeve_trend()
        except Exception as e:
            logger.warning("[WARN] Sleeve Trend failed: %s", e)
            st_equity, st_trades, st_signals = (
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
            )
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
        cm_details = {}
        cm_equity, cm_trades = pd.DataFrame(), pd.DataFrame()
    # ── Sleeve health checks ─────────────────────────────────────
    # Validate each sleeve BEFORE allocation.  Invalid sleeves get
    # their weight routed to CASH, never to another sleeve.
    trend_valid, trend_reason = _sleeve_is_valid(st_equity)
    s2_valid, s2_reason = _sleeve_is_valid(s2_equity)
    cm_valid, cm_reason = _sleeve_is_valid(cm_equity)
    if not trend_valid:
        logger.warning("sleeve_trend inactive: %s -> routed to CASH", trend_reason)
    if not s2_valid:
        logger.warning(
            "sleeve_2 inactive: %s (%s) -> routed to CASH",
            s2_reason,
            _inactive_input_hint(s2_details),
        )
    if not cm_valid:
        logger.warning(
            "charlie_munger inactive: %s (%s) -> routed to CASH",
            cm_reason,
            _inactive_input_hint(cm_details),
        )
    # ── Extract sleeve outputs for dynamic allocation ─────────────
    trend_output = build_trend_sleeve_output(st_signals, st_equity, top_n=10)
    val_output = extract_sleeve_output(s2_equity, s2_trades, "sleeve_2", 1.0)
    val_output = extract_sleeve_output(
        s2_equity,
        s2_trades,
        "sleeve_2",
        1.0,
        target_weights=s2_details.get("target_weights") if s2_details else None,
    )
    cm_output = extract_sleeve_output(
        cm_equity,
        cm_trades,
        "charlie_munger",
        1.0,
        target_weights=cm_details.get("target_weights") if cm_details else None,
    )
    # ── Run dynamic allocation ────────────────────────────────────
    risk_off = os.getenv("RISK_OFF", "0").lower() in ("1", "true", "yes", "y")

    allocator = PortfolioAllocator(
        risk_off=risk_off,
        stash_sleeve_name="CASH",
        risk_off_stash_pct=0.0,
    )

    trend_output, cap_fill_diag = _expand_sleeve_holdings_for_cap(
        trend_output,
        max_position_weight=allocator.max_position_pct,
        target_cash_weight=0.0,
        ranked_candidates=_ranked_signal_tickers(st_signals),
    )
    alloc_result = allocator.allocate([trend_output])
    alloc_result.sleeve_allocations = derive_actual_sleeve_allocations(alloc_result)
    _old_allocs = dict(alloc_result.sleeve_allocations)

    if not risk_off:
        _new_allocs, _new_cash = enforce_charlie_bounds(
            alloc_result.sleeve_allocations,
            alloc_result.cash_weight,
            charlie_active=cm_valid,
        )
    else:
        _new_allocs = dict(alloc_result.sleeve_allocations)
        _new_cash = float(alloc_result.cash_weight)
    # ── SAFE ALLOCATION POLICY ────────────────────────────────────
    # If a sleeve is invalid, force its allocation to 0 and route
    # the freed weight to CASH (never to another sleeve).
    patched = False
    freed_weight = 0.0
    if (
        not trend_valid
        and alloc_result.sleeve_allocations.get("sleeve_trend", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_trend"]
        alloc_result.sleeve_allocations["sleeve_trend"] = 0.0
        patched = True
    if (
        not s2_valid
        and alloc_result.sleeve_allocations.get("sleeve_2", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["sleeve_2"]
        alloc_result.sleeve_allocations["sleeve_2"] = 0.0
        patched = True
    if (
        not cm_valid
        and alloc_result.sleeve_allocations.get("charlie_munger", 0.0) > WEIGHT_TOLERANCE
    ):
        freed_weight += alloc_result.sleeve_allocations["charlie_munger"]
        alloc_result.sleeve_allocations["charlie_munger"] = 0.0
        patched = True
    if patched:
        _old_allocs = dict(alloc_result.sleeve_allocations)
        _new_allocs, _new_cash = enforce_charlie_bounds(
            alloc_result.sleeve_allocations,
            alloc_result.cash_weight + freed_weight,
            charlie_active=cm_valid,
        )
        _apply_enforced_allocations_to_result(
            alloc_result,
            old_allocations=_old_allocs,
            new_allocations=_new_allocs,
            new_cash_weight=_new_cash,
        )
        logger.info(
            "[ALLOCATION] Freed %.1f%% from inactive sleeve(s) -> CASH",
            freed_weight * 100,
        )
    # ── Validate allocation ───────────────────────────────────────
    errors = validate_allocation_result(alloc_result)
    if errors:
        logger.warning("[WARN] Allocation validation errors: %s", errors)
    # ── Log allocation summary ────────────────────────────────────
    logger.info("\n[ALLOCATION] Sleeve allocations:")
    for sleeve, pct in alloc_result.sleeve_allocations.items():
        logger.info("  %s: %.1f%%", sleeve, pct * 100)
    logger.info("  CASH: %.1f%%", alloc_result.cash_weight * 100)
    logger.info("  Total weight: %.4f", alloc_result.total_weight)
    # ── Portfolio stats for model equity ─────────────────────────
    sleeve_equity_map = {
        "sleeve_trend": st_equity,
        "sleeve_2": s2_equity,
        "charlie_munger": cm_equity,
    }
    portfolio_stats = compute_portfolio_equity(
        sleeve_equity_map=sleeve_equity_map,
        sleeve_allocations=alloc_result.sleeve_allocations,
        cash_weight=alloc_result.cash_weight,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )
    sleeve1_desired = float(alloc_result.sleeve_allocations.get("sleeve_trend", 0.0))
    sleeve1_rows = _safe_df(alloc_result.combined_weights)
    if not sleeve1_rows.empty and "sleeve_name" in sleeve1_rows.columns:
        sleeve1_rows = sleeve1_rows[sleeve1_rows["sleeve_name"] == "sleeve_trend"]
    sleeve1_achieved = float(sleeve1_rows.get("target_weight", pd.Series(dtype=float)).sum()) if not sleeve1_rows.empty else 0.0
    allocation_diagnostics = {
        "sleeve_1": {
            "desired_allocation": sleeve1_desired,
            "achieved_invested": sleeve1_achieved,
            "forced_cash": max(0.0, sleeve1_desired - sleeve1_achieved),
            "selected_names": int(cap_fill_diag.get("selected_names", 0)),
            "min_required_names": cap_fill_diag.get("min_required_names"),
            "limiting_constraint": cap_fill_diag.get("constraint", "none"),
        }
    }
    # ── Build daily snapshot context ───────────────────────────────
    report_date = _infer_report_date(
        sleeve_details=[s2_details, cm_details],
        fallback=pd.Timestamp(fixture_date if offline_fixture else dt.date.today()),
    )
    if offline_fixture and not portfolio_fixture.empty:
        report_date = portfolio_fixture.index.max()
    else:
        _ = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    if offline_fixture and not portfolio_fixture.empty:
        portfolio_equity_for_alpha = portfolio_fixture
    else:
        portfolio_equity_for_alpha = resolve_portfolio_equity_series(
            sleeve_equity_map=sleeve_equity_map,
            alloc_result=alloc_result,
            report_date=report_date,
            portfolio_equity=portfolio_stats.get(
                "equity", DEFAULT_PORTFOLIO_BASE_EQUITY
            ),
            offline_fixture=offline_fixture,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
    _ = resolve_portfolio_equity_series(
        sleeve_equity_map=sleeve_equity_map,
        alloc_result=alloc_result,
        report_date=report_date,
        portfolio_equity=portfolio_stats.get("equity", DEFAULT_PORTFOLIO_BASE_EQUITY),
        offline_fixture=offline_fixture,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        portfolio_stats=portfolio_stats,
        st_equity=st_equity,
        s2_equity=s2_equity,
        st_signals=st_signals,
        s2_details=s2_details,
    )
    inception_start = (
        portfolio_equity_for_alpha.index.min()
        if not portfolio_equity_for_alpha.empty
        else None
    )
    inception_end = (
        portfolio_equity_for_alpha.index.max()
        if not portfolio_equity_for_alpha.empty
        else None
    )
    bench_prices_for_alpha = load_benchmark_prices(
        ticker="SPY",
        start=inception_start,
        end=inception_end,
        offline_fixture=offline_fixture,
    )
    inception_metrics = {
        "inception_date": pd.to_datetime(inception_start).strftime("%Y-%m-%d")
        if inception_start is not None
        else None
    }
    spy_stats = _benchmark_since_inception_stats(bench_prices_for_alpha)
    inception_metrics["spy_return_since_inception"] = spy_stats.get("cumulative_return")
    inception_metrics["spy_mdd_since_inception"] = spy_stats.get("max_drawdown")
    alpha_stats = None
    logger.info(
        "[ALPHA] Data readiness - portfolio_equity_for_alpha: %s rows (%s), bench_prices_for_alpha: %s rows (%s)",
        len(portfolio_equity_for_alpha),
        _series_date_range(portfolio_equity_for_alpha),
        len(bench_prices_for_alpha),
        _series_date_range(bench_prices_for_alpha),
    )
    alpha_stats = compute_alpha_attribution(
        portfolio_equity_for_alpha,
        bench_prices_for_alpha,
        min_overlap_days=_alpha_min_overlap_days(5),
        last_n=10,
    )
    try:
        daily_snapshot = build_daily_snapshot(
            report_date=report_date,
            alloc_result=alloc_result,
            portfolio_stats=portfolio_stats,
            st_equity=st_equity,
            s2_equity=s2_equity,
            st_signals=st_signals,
            s2_details=s2_details,
            cm_details=cm_details,
        )
    except RuntimeError as e:
        logger.error("[ERROR] %s", e)
        sys.exit(0)
    daily_snapshot["alpha_attribution"] = alpha_stats
    allocation_diagnostics = _apply_breaker_allocation_diagnostics(
        allocation_diagnostics,
        daily_snapshot,
    )
    daily_snapshot["allocation_diagnostics"] = allocation_diagnostics
    daily_snapshot["inception_metrics"] = {
        **(daily_snapshot.get("inception_metrics") or {}),
        **inception_metrics,
    }
    # --- Paper trading execution + report ---
    trade_date_str = report_date.strftime("%Y-%m-%d")
    signals_path_exec = daily_snapshot.get("signals_snapshot_path") or os.path.join(
        "signals", f"{trade_date_str}.json"
    )
    paper_summary = None
    paper_html = ""
    sent_ledger_removed = 0
    sent_ledger_path = "outputs/shadow_orders/orders_sent.csv"
    paper_ledger_path, paper_trades_path = ensure_paper_state_files()
    reset_info = None
    if bool(getattr(args, "paper_reset", False)):
        os.environ["PAPER_START_CASH"] = str(float(getattr(args, "paper_start_cash", 10_000.0)))
        os.environ["PAPER_INCEPTION_DATE"] = trade_date_str
        reset_info = _apply_paper_reset(
            trade_date=trade_date_str,
            paper_start_cash=float(getattr(args, "paper_start_cash", 10_000.0)),
            paper_ledger_path=paper_ledger_path,
            paper_trades_path=paper_trades_path,
        )
        logger.info(
            "[PAPER][RESET] trade_date=%s start_cash=%.2f",
            trade_date_str,
            float(getattr(args, "paper_start_cash", 10_000.0)),
        )
        daily_snapshot["inception_metrics"] = {
            **(daily_snapshot.get("inception_metrics") or {}),
            "inception_date": trade_date_str,
        }
    if os.path.exists(signals_path_exec):
        try:
            snapshot_cash_target_weight = _coerce_float_or_none((daily_snapshot or {}).get("target_cash_weight"))
            if snapshot_cash_target_weight is None:
                snapshot_cash_target_weight = float(
                    {
                        **alloc_result.sleeve_allocations,
                        "CASH": alloc_result.cash_weight,
                    }.get("CASH", 0.0)
                )
            shadow_constraints = {
                "cash_target_weight": float(snapshot_cash_target_weight)
            }
            if bool(getattr(args, "exit_only", False)) or _is_truthy(os.getenv("EXIT_ONLY"), default=False):
                shadow_constraints["exit_only"] = True
            trading_mode = str(os.getenv("TRADING_MODE", "shadow")).strip().lower()
            force_execution = bool(
                bool(getattr(args, "force_execution", False))
                or _is_truthy(os.getenv("FORCE_EXECUTION"), default=False)
            )
            if force_execution:
                if trading_mode == "shadow":
                    sent_ledger_removed += reset_orders_sent_ledger_for_date(
                        sent_ledger_path,
                        trade_date_str,
                    )
                if args.reset_ledger_date:
                    sent_ledger_removed += reset_orders_sent_ledger_for_date(
                        sent_ledger_path,
                        args.reset_ledger_date,
                    )
            else:
                logger.info(
                    "[ORDER] orders_sent guard active; not resetting (%s)",
                    "use --force-execution/--reset-orders-sent or FORCE_EXECUTION=1",
                )
                if args.reset_ledger_date:
                    logger.info(
                        "[ORDER] --reset-ledger-date ignored without force execution override"
                    )
            paper_summary = run_paper_day(
                run_date=trade_date_str,
                signals_path=signals_path_exec,
                ledger_path=paper_ledger_path,
                trades_path=paper_trades_path,
                config_path="paper/config_paper.json",
                force=force_execution,
                constraints=shadow_constraints,
                plan_only=args.plan_only,
            )
            logger.info(
                "[PAPER] Executed paper trading for %s using signals %s",
                trade_date_str,
                signals_path_exec,
            )
        except Exception as e:
            msg = repr(e)
            if "Ledger already contains run_date" in msg:
                logger.info(
                    "[PAPER] Already executed for %s; rendering report from ledger.",
                    trade_date_str,
                )
            else:
                logger.warning("[PAPER][WARN] Paper execution failed: %s", msg)
        market_guard = (paper_summary or {}).get("market_guard") if isinstance(paper_summary, dict) else None
        market_status_from_guard = None
        if isinstance(market_guard, dict):
            raw_guard_status = market_guard.get("status")
            if raw_guard_status is None and market_guard.get("is_open_now") is not None:
                market_status_from_guard = "OPEN" if bool(market_guard.get("is_open_now")) else "CLOSED"
            elif raw_guard_status is not None:
                market_status_from_guard = str(raw_guard_status).strip().upper() or None

        market_status = (
            (paper_summary or {}).get("market_status")
            or market_status_from_guard
        )

        try:
            paper_html = build_paper_report_html(
                run_date=trade_date_str,
                ledger_path=paper_ledger_path,
                trades_path=paper_trades_path,
                benchmark_ticker="SPY",
                reconciliation=paper_summary,
                shadow_status={
                    "trading_mode": (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow").upper(),
                    "market_status": market_status,
                    "market_guard": (paper_summary or {}).get("market_guard"),
                    "orders_generated": len((paper_summary or {}).get("shadow_orders", []) or []),
                    "orders_blocked": len((paper_summary or {}).get("blocked_reasons", []) or []),
                    "broker_recon_status": (paper_summary or {}).get("broker_recon_status", "UNKNOWN"),
                },
            )
        except Exception as e:
            logger.warning("[PAPER][WARN] Paper report HTML build failed: %s", repr(e))
    else:
        logger.warning(
            "[PAPER][WARN] Missing signals for execution: %s", signals_path_exec
        )
    # ── Build execution + snapshot email artifacts ──────────────────
    today_et_str = _today_et_str()
    should_execute, is_planning_run, market_is_open_for_trade_date = _should_execute_run(
        trade_date_str=trade_date_str,
        today_et_str=today_et_str,
        paper_summary=paper_summary,
    )
    if is_planning_run:
        logger.info(
            "[SCHEDULE] Planning run for future trade_date=%s -> skipping execution + ledger/nav/attribution updates",
            trade_date_str,
        )
    elif not market_is_open_for_trade_date:
        logger.info(
            "[SCHEDULE] Non-execution run for trade_date=%s (market_closed_or_not_session) -> skipping execution + ledger/nav/attribution updates",
            trade_date_str,
        )

    execution_payload = build_execution_email_payload(
        trade_date=trade_date_str,
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
    )
    if not should_execute:
        execution_payload["execution_status"] = "PLANNED"
        execution_payload["halt_reason"] = None
        execution_payload["planning_disclaimer"] = "Planning email only — no orders were sent."
        execution_payload["validation_reason"] = (
            "planning_run_future_date" if is_planning_run else "market_closed_or_not_session"
        )
    if execution_payload.get("execution_status") == "HALTED":
        logger.info(
            "[EXECUTION_EMAIL] status=HALTED reason=%s",
            execution_payload.get("halt_reason", "UNKNOWN"),
        )
    else:
        exec_trades = execution_payload.get("trades", [])
        buy_count = sum(1 for t in exec_trades if str(t.get("side", "")).upper() == "BUY")
        sell_count = sum(1 for t in exec_trades if str(t.get("side", "")).upper() != "BUY")
        logger.info(
            "[EXECUTION_EMAIL] built trades=%d buys=%d sells=%d status=%s",
            len(exec_trades),
            buy_count,
            sell_count,
            execution_payload.get("status_label") or ("NO TRADES" if not exec_trades else "TRADES READY"),
        )
    execution_payload_path, payload_preserved, preserved_path = _write_execution_email_payload(execution_payload, trade_date_str)
    integrity = {
        "trade_date": trade_date_str,
        "asof_date": str(execution_payload.get("pricing_asof") or prev_trading_day(trade_date_str)),
        "mode": str((paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow")).upper(),
        "execution_status": execution_payload.get("execution_status"),
        "halt_reason": execution_payload.get("halt_reason"),
        "payload_path_written": execution_payload_path,
        "payload_preserved": payload_preserved,
        "preserved_path": preserved_path,
        "sent_ledger_path": sent_ledger_path,
        "sent_ledger_reset_removed": int(sent_ledger_removed),
        "missing_prices": [],
        "ledger2_path": str(LEDGER_TRADES_PATH),
        "ledger2_appended_rows": 0,
        "ledger2_skipped_rows": 0,
        "nav_path": None,
        "nav_timeseries_path": None,
    }
    if should_execute:
        rows2: list[dict] = []
        appended2 = 0
        skipped2 = 0
        missing_prices: list[str] = []
        ledger2_error = None
        asof_date = integrity["asof_date"]
        ledger_run_id = str(uuid.uuid4())
        ledger_source = str((paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", "shadow")).upper()
        signal_hash = compute_signal_hash(signals_path_exec) if signals_path_exec and os.path.exists(signals_path_exec) else ""
        try:
            Path("outputs/ledger").mkdir(parents=True, exist_ok=True)
            Path("outputs/perf").mkdir(parents=True, exist_ok=True)
            Path("outputs/daily").mkdir(parents=True, exist_ok=True)
            def _ledger_price_fn(ticker: str, req_asof_date: str):
                px = fetch_prev_closes_yfinance([ticker], asof_date=req_asof_date)
                if px.empty:
                    return None
                return float(px.iloc[0]["prev_close"])
            rows2, missing_prices = ledger2_payload_to_rows(
                execution_payload=execution_payload,
                trade_date=trade_date_str,
                asof_date=asof_date,
                source=ledger_source,
                run_id=ledger_run_id,
                signal_hash=signal_hash,
                get_price_fn=_ledger_price_fn,
            )
            appended2, skipped2 = append_ledger2_rows(str(LEDGER_TRADES_PATH), rows2)
            ensure_no_legacy_ledger(logger=logger, when="post_ledger_write")
            nav_result = update_nav(
                asof_date=asof_date,
                trade_date=trade_date_str,
                get_price_fn=_ledger_price_fn,
                source=ledger_source,
                run_id=ledger_run_id,
            )
            nav_ts_path = nav_result.get("nav_timeseries_path")
            if nav_ts_path and os.path.exists(str(nav_ts_path)):
                nav_ts = pd.read_csv(str(nav_ts_path))
                _merge_nav_metrics_into_snapshot(
                    daily_snapshot,
                    nav_ts,
                    asof_date=asof_date,
                )
                nav_ts["date"] = pd.to_datetime(nav_ts["date"])
                prev_dates = nav_ts[nav_ts["date"] < pd.to_datetime(asof_date)]["date"]
                if not prev_dates.empty:
                    prev_date = prev_dates.max().strftime("%Y-%m-%d")
                    attr = compute_daily_attribution(asof_date, prev_date)
                    write_attribution_outputs(asof_date, attr["tickers"], attr["sleeves"])
            inception_ts = update_inception_nav_series(asof_date=asof_date, model_nav=float(nav_result.get("equity", 0.0)))
            if not inception_ts.empty:
                last = inception_ts.iloc[-1].to_dict()
                daily_snapshot["inception_metrics"] = {
                    "inception_date": INCEPTION_DATE,
                    "model_nav": float(last.get("model_nav", 0.0)),
                    "spy_nav": float(last.get("spy_nav", 0.0)),
                    "model_return_since_inception": float(last.get("model_return_since_inception", 0.0)),
                    "spy_return_since_inception": float(last.get("spy_return_since_inception", 0.0)),
                    "alpha_since_inception": float(last.get("alpha_since_inception", 0.0)),
                    "model_mdd_since_inception": float(last.get("model_mdd_since_inception", 0.0)),
                    "spy_mdd_since_inception": float(last.get("spy_mdd_since_inception", 0.0)),
                }
            integrity.update(
                {
                    "ledger2_path": str(LEDGER_TRADES_PATH),
                    "ledger2_appended_rows": int(appended2),
                    "ledger2_skipped_rows": int(skipped2),
                    "nav_path": nav_result.get("nav_path"),
                    "nav_timeseries_path": nav_result.get("nav_timeseries_path"),
                }
            )
            integrity["missing_prices"] = sorted(set((missing_prices or []) + (nav_result.get("missing_prices") or [])))
        except Exception as e:
            ledger2_error = str(e)
            logger.warning("[LEDGER2][WARN] ledger/nav2 pipeline failed: %s", e)
        try:
            ledger_write_path = Path("outputs/ledger") / f"ledger_write_{asof_date}.json"
            ledger_write_payload = {
                "run_id": ledger_run_id,
                "trade_date": trade_date_str,
                "asof_date": asof_date,
                "rows_input": int(len(rows2)),
                "rows_appended": int(appended2),
                "rows_skipped": int(skipped2),
                "ledger_path": str(LEDGER_TRADES_PATH),
                "execution_payload_path": execution_payload_path,
            }
            if ledger2_error:
                ledger_write_payload["error"] = ledger2_error
            with ledger_write_path.open("w", encoding="utf-8") as f:
                json.dump(ledger_write_payload, f, indent=2)
                f.write("\n")
        except Exception as e:
            logger.warning("[LEDGER2][WARN] failed writing ledger metadata: %s", e)
    if not daily_snapshot.get("nav_metrics"):
        nav_ts_path_fallback = Path("outputs/perf/nav_timeseries.csv")
        if nav_ts_path_fallback.exists() and nav_ts_path_fallback.stat().st_size > 0:
            try:
                nav_ts_fallback = pd.read_csv(nav_ts_path_fallback)
                _merge_nav_metrics_into_snapshot(
                    daily_snapshot,
                    nav_ts_fallback,
                    asof_date=integrity.get("asof_date"),
                )
            except Exception as e:
                logger.warning("[NAV][WARN] unable to hydrate snapshot nav metrics: %s", e)
    health_payload: dict
    try:
        health_payload = _build_health_payload(
            trade_date=trade_date_str,
            paper_summary=paper_summary,
            execution_payload=execution_payload,
            nav_ts_path=integrity.get("nav_timeseries_path") or "outputs/perf/nav_timeseries.csv",
            ledger_path=str(LEDGER_TRADES_PATH),
            should_execute=bool(should_execute),
            leverage_enabled=str(os.getenv("ALLOW_LEVERAGE", "0")).strip().lower() in {"1", "true", "yes", "y", "on"},
        )
    except Exception as e:
        health_payload = {
            "status": "FAIL",
            "error": f"health_payload_build_failed: {e}",
            "trade_date": trade_date_str,
            "run_id": str((paper_summary or {}).get("run_id") or (execution_payload or {}).get("run_id") or ""),
            "market_guard_status": str((((paper_summary or {}).get("market_guard") or {}).get("status") or (paper_summary or {}).get("market_status") or "UNKNOWN").upper()),
            "model_equity_recon": _coerce_float_or_none((((paper_summary or {}).get("broker_reconciliation") or {}).get("model_equity"))),
            "broker_equity": _coerce_float_or_none((paper_summary or {}).get("total_equity")),
            "recon_delta": _coerce_float_or_none((((paper_summary or {}).get("broker_reconciliation") or {}).get("broker_minus_model_equity_delta"))),
            "broker_cash": _coerce_float_or_none((paper_summary or {}).get("cash")),
            "execution_basis_equity": None,
            "mark_basis_equity": None,
            "nav_last_equity": None,
            "ledger_path_used": str(LEDGER_TRADES_PATH),
            "ledger_rows": 0,
            "ledger_dupe_rows": 0,
            "turnover_dollars": 0.0,
            "turnover_pct": 0.0,
            "warnings": [f"health_payload_exception:{e}"],
        }
    if reset_info:
        health_payload["paper_reset"] = True
        health_payload["paper_start_cash"] = float(reset_info.get("start_cash", 0.0))
    try:
        _finalize_health_payload(trade_date_str, health_payload)
    except Exception as e:
        if isinstance(e, AssertionError):
            raise
        logger.warning("[HEALTH][WARN] failed writing health artifact: %s", e)
    try:
        write_integrity_artifact(integrity["asof_date"], integrity)
    except Exception as e:
        logger.warning("[INTEGRITY][WARN] failed writing integrity artifact: %s", e)
    exec_subject, exec_body = build_execution_email_text(execution_payload)
    _, exec_body_html = build_execution_email_html(execution_payload)
    execution_path = os.path.join(OUTPUT_DIR, f"trade_execution_{trade_date_str}.txt")
    with open(execution_path, "w", encoding="utf-8") as f:
        f.write(exec_body.rstrip() + "\n")
    logger.info("[OK] Execution trade email written: %s", execution_path)
    snapshot_subject, snapshot_body = create_snapshot_email(
        daily_snapshot,
        execution_payload=execution_payload,
    )
    snapshot_path = os.path.join(OUTPUT_DIR, f"trade_snapshot_{trade_date_str}.txt")
    with open(snapshot_path, "w", encoding="utf-8") as f:
        f.write(snapshot_body.rstrip() + "\n")
    logger.info("[OK] Model snapshot email written: %s", snapshot_path)
    # Backward-compatibility alias (deprecated)
    rundown_path = os.path.join(OUTPUT_DIR, f"trade_rundown_{trade_date_str}.txt")
    with open(rundown_path, "w", encoding="utf-8") as f:
        f.write(snapshot_body.rstrip() + "\n")
    logger.info("[OK] Legacy trade rundown alias written: %s", rundown_path)
    # ── Build report ──────────────────────────────────────────────
    html = build_html_report(
        report_date=report_date,
        st_equity=st_equity,
        st_trades=st_trades,
        s2_equity=s2_equity,
        s2_trades=s2_trades,
        alloc_result=alloc_result,
        alpha_stats=alpha_stats,
        proposed_trades=daily_snapshot.get("proposed_trades"),
        performance_summary=daily_snapshot.get("performance_summary"),
        s2_no_picks=daily_snapshot.get("s2_no_picks", False),
        cm_details=cm_details,
        inception_metrics=daily_snapshot.get("inception_metrics"),
        allocation_diagnostics=daily_snapshot.get("allocation_diagnostics"),
    )
    # Append paper trading HTML section (if available)
    if paper_html:
        html = html + "<hr/>" + paper_html
    out_path = os.path.join(OUTPUT_DIR, f"quant_report_{trade_date_str}.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info("[OK] HTML report written: %s", out_path)
    logger.info("\n[EMAIL PREVIEW]\n")
    logger.info("%s", snapshot_body)
    if send_email:
        try:
            send_email(subject=exec_subject, body_html=exec_body_html, body_text=exec_body)
            send_email(subject=snapshot_subject, body_html=html, body_text=snapshot_body)
            logger.info("[OK] Emails sent (execution + snapshot)")
        except Exception as e:
            logger.warning("[WARN] Email not sent: %s", e)
    else:
        logger.warning("[WARN] send_email not found — HTML generated only")

    if os.getenv("RUN_ROBUSTNESS_BACKTEST", "0").lower() in {"1", "true", "yes", "y"}:
        try:
            from backtests.sleeve1_robustness import main as run_robustness

            run_robustness([])
            logger.info("[OK] Ran sleeve1 robustness backtest")
        except Exception as e:
            logger.warning("[WARN] Robustness backtest failed: %s", e)
if __name__ == "__main__":
    main()
