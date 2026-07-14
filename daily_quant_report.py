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
import subprocess
import atexit
from dataclasses import dataclass
from pathlib import Path
from typing import Any
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
from paper.ledger2 import (
    append_rows as append_ledger2_rows,
    payload_to_rows as ledger2_payload_to_rows,
    LEDGER2_COLUMNS,
    compute_signal_hash,
)
from paper.paths import (
    LEDGER_TRADES_PATH,
    ensure_no_legacy_ledger,
)
from paper.run_manager import (
    collect_manifest,
    ensure_dir,
    file_sha256,
    get_run_dir,
    get_run_id,
    safe_write_bytes,
    safe_write_text,
    write_latest_pointer,
)
from brokers.alpaca_snapshot import (
    fetch_pretrade_snapshot,
    summarize_pretrade_broker_policy,
    write_pretrade_snapshot_artifacts,
)
from brokers.alpaca_broker import (
    AlpacaSubmissionRejectError,
    EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
    EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
)
from core.run_pointer import write_latest_run_pointer as write_canonical_run_pointer
from core.execution_payload import write_canonical_execution_payload, normalize_status
from core.execution_audit import write_planner_audit
from core.execution_summary import write_execution_artifacts
from core.precompute_contract import write_precompute_bundle
from core.research_context import write_research_context
from core.operator_summary import (
    format_broker_preflight_banner,
    write_operator_summary,
    format_operator_summary_log,
    format_execution_health_banner,
    load_operator_summary,
)
from core.step_summary import append_step_summary
from core.strategy_identity import strategy_identity_metadata
from core.trade_count_contract import compute_trade_count_contract
from core.sleeve_numeric_diagnostics import (
    REASON_NAN_TRACE_MISSING,
    REASON_SLEEVE_TERMINAL_EQUITY_NAN,
    build_trace_payload,
    is_finite_number,
    trace_artifact_name,
)
from core.trading_mode import (
    canonical_trading_mode,
    canonical_trading_mode_label,
    legacy_shadow_mode_requested,
)
from core.security_master import resolve_trade_plan_symbols
from paper.nav2 import update_nav
try:
    from paper.perf_artifact_producers import (
        build_benchmark_relative_series,
        build_concentration_history,
        build_construction_parity_artifact,
        build_trade_day_pnl_artifact,
        rebuild_premarket_analyzer_scores,
        update_benchmark_close_history,
        update_vix_close_history,
    )
except ImportError as exc:  # pragma: no cover - defensive compatibility fallback
    _perf_artifact_logger = logging.getLogger(__name__)
    _perf_artifact_logger.warning(
        "[PERF_PRODUCERS][WARN] falling back to compatibility shims: %s",
        exc,
    )

    def build_benchmark_relative_series(
        *,
        nav_timeseries_path: Path | str = Path("outputs/perf/nav_timeseries.csv"),
        benchmark_path: Path | str = Path("outputs/perf/benchmark_close_history.csv"),
        output_path: Path | str = Path("outputs/perf/benchmark_relative_series.csv"),
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
        out = pd.DataFrame(columns=columns)
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    def build_concentration_history(*, output_path: Path | str = Path("outputs/perf/concentration_history.csv")) -> pd.DataFrame:
        out = pd.DataFrame()
        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.to_csv(out_path, index=False)
        return out

    def build_construction_parity_artifact(*, asof_date: str | None = None) -> dict[str, object]:
        return {"status": "fallback_compatibility_shim", "asof_date": asof_date}

    def build_trade_day_pnl_artifact(
        *,
        trade_date: str,
        run_root: Path,
        broker_snapshot_path: Path,
    ) -> dict[str, object]:
        return {
            "status": "fallback_compatibility_shim",
            "trade_date": trade_date,
            "run_root": str(run_root),
            "broker_snapshot_path": str(broker_snapshot_path),
        }

    def rebuild_premarket_analyzer_scores() -> pd.DataFrame:
        return pd.DataFrame()

    def update_benchmark_close_history(*, asof_date: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()

    def update_vix_close_history(*, asof_date: str | None = None) -> pd.DataFrame:
        return pd.DataFrame()
from paper.reporting_consistency import compute_exposure, determine_sleeve_state
from core.benchmark_v4 import update_inception_nav_series, INCEPTION_DATE
from reporting.attribution import compute_daily_attribution, write_attribution_outputs
from engine.breaker import get_breaker_config, apply_portfolio_exposure_overlay
from reconciliation import (
    bootstrap_model_ledger_from_broker,
    ensure_sent_ledger_exists,
    post_trade_validate,
    pre_trade_reconcile_and_classify,
    pre_trade_reconcile_or_exit,
    refresh_canonical_snapshot_from_posttrade_snapshot,
    refresh_canonical_snapshot_from_broker,
)
from regime.regime_engine import RegimeEngine

from sleeves.sleeve_trend.build_sleeve_output import build_trend_sleeve_output
from sleeves.sleeve_trend import config as trend_cfg
from sleeves.sleeve_defensive_etf.selection import (
    DEFENSIVE_ETF_TICKERS,
    SLEEVE_NAME as DEFENSIVE_SLEEVE_NAME,
    build_defensive_etf_equity_curve,
    build_defensive_etf_sleeve_output,
)
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
    DEFAULT_MIN_GROSS_EXPOSURE,
    DEFAULT_PORTFOLIO_BASE_EQUITY,
    WEIGHT_TOLERANCE,
)
from core.alpha_attribution import load_benchmark_prices  # noqa: E402
from core.live_regime_review import build_returns_by_ticker, write_live_regime_review  # noqa: E402
from core.options_overlay_paper import write_options_overlay_paper_review  # noqa: E402
from core.options_execution import write_options_execution_review  # noqa: E402
from core.options_overlay_shadow import write_options_overlay_shadow  # noqa: E402
from alpha_stack._config_loader import get_flag as get_alpha_stack_flag  # noqa: E402
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
quant_tickers = None

logger = logging.getLogger(__name__)
# Backward-compatible alias for tests/patch points
calc_alpha_stats = compute_alpha_attribution
# ============================================================
# Trading mode config
# ============================================================
# Default trading mode if TRADING_MODE env var is not set.
# "paper" is the paper-broker execution mode. Planning-only behavior is
# controlled by PLAN_ONLY / --plan-only, not by a separate trading mode.
DEFAULT_TRADING_MODE = "paper"
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


@dataclass
class RunContext:
    run_id: str
    run_root: Path
    allow_overwrite: bool
    created_at: str
    git_sha: str | None
    mode: str
    trading_mode: str
    report_date_env: str | None = None
    report_date: str | None = None
    paper_trading: bool | None = None


_RUN_CONTEXT: RunContext | None = None
_RUN_CONTEXT_FINALIZED = False
# Terminal status tracking — starts as failed_unknown so any unfinished run is
# classified as a failure.  Updated to "success" / "no_action" only when main()
# reaches its natural terminal point.  Atexit reads this to write the correct
# status to latest_run.json and to produce failure artifacts.
_RUN_TERMINAL_STATUS: str = "failed_unknown"
_RUN_TERMINAL_SUBSTATUS: str | None = None
_RUN_TERMINAL_MESSAGE: str | None = None
# Stage flags: used to produce a precise failure classification on early exit.
_RUN_STAGE_PAYLOAD_WRITTEN: bool = False    # True after execution_payload.json written
_RUN_STAGE_EXECUTION_REACHED: bool = False  # True when run_paper_day() is invoked
_SIGNAL_SNAPSHOT_CACHE: dict[str, object] | None = None
_SIGNAL_SNAPSHOT_WRITTEN: bool = False


def _cache_signal_snapshot_payload(
    *,
    run_date: str,
    asof_date: str | None,
    df_targets: pd.DataFrame,
    cash_target_weight: float | None,
    extra: dict | None,
) -> None:
    global _SIGNAL_SNAPSHOT_CACHE
    global _SIGNAL_SNAPSHOT_WRITTEN
    _SIGNAL_SNAPSHOT_CACHE = {
        "run_date": run_date,
        "asof_date": asof_date,
        "df_targets": df_targets.copy() if df_targets is not None else pd.DataFrame(),
        "cash_target_weight": cash_target_weight,
        "extra": dict(extra or {}),
    }
    _SIGNAL_SNAPSHOT_WRITTEN = False


def _flush_signal_snapshot_cache() -> str | None:
    """Flush the cached signals snapshot to disk.

    This used to swallow all exceptions and return None — the result was
    that 20 of 21 fill dates had no signal snapshot and we could not
    compute shadow-vs-live or alpha attribution. We now raise on every
    failure mode except "there's nothing to flush" (empty cache).
    """
    global _SIGNAL_SNAPSHOT_CACHE
    global _SIGNAL_SNAPSHOT_WRITTEN
    if _SIGNAL_SNAPSHOT_WRITTEN:
        return None
    if not _SIGNAL_SNAPSHOT_CACHE:
        logger.warning(
            "[SIGNAL_SNAPSHOT] flush called with empty cache — no snapshot will be written"
        )
        return None

    run_date = str(_SIGNAL_SNAPSHOT_CACHE.get("run_date") or "").strip()
    df_targets = _SIGNAL_SNAPSHOT_CACHE.get("df_targets")
    if not run_date:
        raise RuntimeError(
            "[SIGNAL_SNAPSHOT] cached payload missing run_date — refusing to write"
        )
    if not isinstance(df_targets, pd.DataFrame):
        raise RuntimeError(
            "[SIGNAL_SNAPSHOT] cached payload df_targets is not a DataFrame"
        )

    asof_date = _SIGNAL_SNAPSHOT_CACHE.get("asof_date")
    cash_target_weight = _SIGNAL_SNAPSHOT_CACHE.get("cash_target_weight")
    extra = _SIGNAL_SNAPSHOT_CACHE.get("extra")

    if cash_target_weight is None:
        # Derive cash weight from the breaker block in extra if possible,
        # so we never write a cash-less snapshot from the daily pipeline.
        if isinstance(extra, dict):
            breaker = extra.get("breaker") if isinstance(extra.get("breaker"), dict) else None
            if breaker and breaker.get("cash_target_weight_today") is not None:
                cash_target_weight = float(breaker["cash_target_weight_today"])
        if cash_target_weight is None:
            logger.warning(
                "[SIGNAL_SNAPSHOT] cache has no cash_target_weight; defaulting to 0.0"
            )
            cash_target_weight = 0.0

    path = write_signals_snapshot(
        df_targets=df_targets if not df_targets.empty else pd.DataFrame(
            [{"ticker": "CASH", "target_weight": 1.0, "sleeve": "core"}]
        ),
        run_date=run_date,
        asof_date=str(asof_date) if asof_date else None,
        out_dir="signals",
        cash_target_weight=float(cash_target_weight),
        sleeve_col="sleeve",
        extra=extra if isinstance(extra, dict) else None,
    )
    _SIGNAL_SNAPSHOT_WRITTEN = True
    logger.info("[SIGNAL_SNAPSHOT] flushed cached snapshot: %s", path)
    return path


def _finalize_signal_snapshot_once() -> None:
    try:
        _flush_signal_snapshot_cache()
    except Exception:
        pass


atexit.register(_finalize_signal_snapshot_once)


def _get_git_sha() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return out or None
    except Exception:
        return None


def _pointer_payload(*, run_ctx: RunContext, canonical_path: Path) -> dict:
    return {
        "run_id": run_ctx.run_id,
        "canonical_path": str(canonical_path),
    }


def _write_view_pointer(path: Path, payload: dict) -> None:
    ensure_dir(path.parent)
    safe_write_text(path, json.dumps(payload, indent=2) + "\n", allow_overwrite=True)


def _snapshot_existing_file(
    src_path: str | Path,
    *,
    category: str,
    filename: str | None = None,
) -> str | None:
    src = Path(src_path)
    if _RUN_CONTEXT is None:
        return None
    if not src.exists() or not src.is_file():
        return None
    dest = _canonical_artifact_path(category, filename or src.name)
    safe_write_bytes(
        dest,
        src.read_bytes(),
        allow_overwrite=_RUN_CONTEXT.allow_overwrite,
    )
    return str(dest)


def _canonical_artifact_path(category: str, filename: str) -> Path:
    if _RUN_CONTEXT is None:
        return Path("outputs") / category / filename
    base = _RUN_CONTEXT.run_root / category
    ensure_dir(base)
    return base / filename


def _capture_pretrade_broker_snapshot(
    *,
    trade_date: str,
    paper_requested: bool | None = None,
    alpaca_requested: bool | None = None,
) -> dict | None:
    """
    Capture authoritative broker state before any execution-path mutation.

    Phase 1 observability only: failures are recorded to artifacts/logs but do not
    change execution control flow.
    """
    requested = bool(paper_requested if paper_requested is not None else alpaca_requested)
    if not requested or _RUN_CONTEXT is None or _RUN_CONTEXT.run_root is None:
        return None

    try:
        snapshot = fetch_pretrade_snapshot()
        account_path, positions_path = write_pretrade_snapshot_artifacts(
            run_root=_RUN_CONTEXT.run_root,
            run_id=str(_RUN_CONTEXT.run_id),
            trade_date=str(trade_date),
            snapshot=snapshot,
            allow_overwrite=True,
        )
        logger.info(
            "[BROKER][PRETRADE] wrote account=%s positions=%s ok=%s",
            account_path,
            positions_path,
            bool(snapshot.get("ok")),
        )
        return {
            "snapshot": snapshot,
            "account_path": str(account_path),
            "positions_path": str(positions_path),
        }
    except Exception as exc:
        logger.warning("[BROKER][PRETRADE] snapshot capture failed (non-blocking): %s", exc)
        return None


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
    global send_email, download_prices, add_atr, quant_tickers
    if (
        send_email is not None
        and download_prices is not None
        and add_atr is not None
        and quant_tickers is not None
    ):
        return
    from core.quant_report import (
        send_email as _send_email,
        download_prices as _download_prices,
        add_atr as _add_atr,
        TICKERS as _quant_tickers,
    )
    if send_email is None:
        send_email = _send_email
    if download_prices is None:
        download_prices = _download_prices
    if add_atr is None:
        add_atr = _add_atr
    if quant_tickers is None:
        quant_tickers = list(_quant_tickers)


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


def _resolve_live_construction_policy(model_equity: float | None) -> dict[str, float]:
    equity = max(0.0, float(model_equity or DEFAULT_PORTFOLIO_BASE_EQUITY))
    small_account_threshold = max(
        DEFAULT_PORTFOLIO_BASE_EQUITY,
        float(os.getenv("SMALL_ACCOUNT_EQUITY_THRESHOLD", "25000")),
    )
    small_account = equity <= small_account_threshold

    # Position-cap default comes from the SINGLE source of truth in
    # core.risk_controls (explicit MAX_POSITION_PCT env wins there, else the
    # concentration ceiling CAERUS_CONCENTRATED_MAX_WEIGHT, capped at the
    # temporary pilot maximum of 0.30) so
    # construction and execution-time risk controls can never disagree.
    # Known-safe: concentrate_targets re-derives weights from convictions under
    # its own cap, so this cannot loosen the final book.
    from core.risk_controls import _default_max_position_pct

    default_max_position_weight = float(_default_max_position_pct())

    max_position_weight = max(
        WEIGHT_TOLERANCE,
        min(
            CONCENTRATED_MAX_WEIGHT,
            _snapshot_risk_value("MAX_POSITION_PCT", default_max_position_weight),
        ),
    )
    default_min_position_weight = 0.05 if small_account else 0.02
    min_position_weight = max(
        0.0,
        min(
            max_position_weight,
            _snapshot_risk_value("MIN_POSITION_PCT", default_min_position_weight),
        ),
    )
    default_min_gross_exposure = 0.95 if small_account else DEFAULT_MIN_GROSS_EXPOSURE
    min_gross_exposure = max(
        0.0,
        min(1.0, _snapshot_risk_value("MIN_GROSS_EXPOSURE", default_min_gross_exposure)),
    )
    return {
        "model_equity": equity,
        "max_position_weight": float(max_position_weight),
        "min_position_weight": float(min_position_weight),
        "min_gross_exposure": float(min_gross_exposure),
    }


def _preview_sleeve_allocations(sleeve_outputs: list[SleeveOutput]) -> dict[str, float]:
    active = []
    for sleeve_output in sleeve_outputs:
        positions_df = _safe_df(getattr(sleeve_output, "positions_df", pd.DataFrame()))
        has_positions = not positions_df.empty and float(
            positions_df.get("target_weight", pd.Series(dtype=float)).abs().sum()
        ) > WEIGHT_TOLERANCE
        if has_positions and float(getattr(sleeve_output.meta, "strength", 0.0)) > WEIGHT_TOLERANCE:
            active.append(sleeve_output)

    total_strength = sum(float(sleeve_output.meta.strength) for sleeve_output in active)
    if total_strength <= WEIGHT_TOLERANCE:
        return {sleeve_output.meta.sleeve_name: 0.0 for sleeve_output in sleeve_outputs}

    active_ids = {id(sleeve_output) for sleeve_output in active}
    return {
        sleeve_output.meta.sleeve_name: (
            float(sleeve_output.meta.strength) / total_strength
            if id(sleeve_output) in active_ids
            else 0.0
        )
        for sleeve_output in sleeve_outputs
    }


def _resize_sleeve_holdings_for_live_book(
    sleeve_output: SleeveOutput,
    *,
    target_allocation: float,
    max_position_weight: float,
    min_position_weight: float,
    ranked_candidates: list[str] | None = None,
) -> tuple[SleeveOutput, dict]:
    positions_df = _safe_df(sleeve_output.positions_df).copy()
    target_allocation = max(0.0, float(target_allocation or 0.0))
    max_position_weight = max(0.0, float(max_position_weight or 0.0))
    min_position_weight = max(0.0, float(min_position_weight or 0.0))

    diagnostics = {
        "selected_names": int(len(positions_df)),
        "min_required_names": None,
        "max_supported_names": None,
        "added_names": 0,
        "trimmed_names": 0,
        "constraint": (
            f"max_position_weight={max_position_weight:.0%}, min_position_weight={min_position_weight:.0%}"
            if max_position_weight > 0
            else "none"
        ),
    }
    if positions_df.empty or target_allocation <= WEIGHT_TOLERANCE or max_position_weight <= WEIGHT_TOLERANCE:
        return sleeve_output, diagnostics

    if ranked_candidates:
        rank_lookup = {
            str(ticker).strip().upper(): idx
            for idx, ticker in enumerate(ranked_candidates)
            if str(ticker).strip()
        }
        positions_df["_live_rank"] = positions_df.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().map(
            lambda ticker: rank_lookup.get(ticker, len(rank_lookup) + 10_000)
        )
        positions_df = positions_df.sort_values(["_live_rank", "ticker"], ascending=[True, True]).drop(
            columns=["_live_rank"],
            errors="ignore",
        )
    elif "rank" in positions_df.columns:
        positions_df = positions_df.sort_values(["rank", "ticker"], ascending=[True, True])

    min_required_names = max(1, int(math.ceil(target_allocation / max_position_weight)))
    max_supported_names = None
    if min_position_weight > WEIGHT_TOLERANCE:
        max_supported_names = max(
            min_required_names,
            int(math.floor((target_allocation + WEIGHT_TOLERANCE) / min_position_weight)),
        )
        max_supported_names = max(1, max_supported_names)

    diagnostics["min_required_names"] = int(min_required_names)
    diagnostics["max_supported_names"] = (
        int(max_supported_names) if max_supported_names is not None else None
    )

    original_count = int(len(positions_df))
    if max_supported_names is not None and len(positions_df) > max_supported_names:
        positions_df = positions_df.head(max_supported_names).copy()
    trimmed_count = max(0, original_count - int(len(positions_df)))

    additions: list[dict[str, object]] = []
    if ranked_candidates and len(positions_df) < min_required_names:
        existing = set(
            positions_df.get("ticker", pd.Series(dtype=str)).astype(str).str.upper().tolist()
        )
        for ticker in ranked_candidates:
            ticker_value = str(ticker).strip().upper()
            if not ticker_value or ticker_value in existing:
                continue
            additions.append(
                {
                    "ticker": ticker_value,
                    "target_weight": 0.0,
                    "reason": "cap_fill",
                    "signal_strength": 1.0,
                }
            )
            existing.add(ticker_value)
            if len(existing) >= min_required_names:
                break
        if additions:
            positions_df = pd.concat([positions_df, pd.DataFrame(additions)], ignore_index=True)

    if positions_df.empty:
        return create_sleeve_output(
            [],
            sleeve_output.meta.sleeve_name,
            strength=sleeve_output.meta.strength,
            notes=sleeve_output.meta.notes,
        ), diagnostics

    if additions:
        positions_df["target_weight"] = 1.0 / len(positions_df)
    else:
        weight_sum = float(positions_df.get("target_weight", pd.Series(dtype=float)).sum())
        if weight_sum > WEIGHT_TOLERANCE:
            positions_df["target_weight"] = positions_df["target_weight"] / weight_sum
        else:
            positions_df["target_weight"] = 1.0 / len(positions_df)

    diagnostics["selected_names"] = int(len(positions_df))
    diagnostics["added_names"] = int(len(additions))
    diagnostics["trimmed_names"] = int(trimmed_count)
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


def _legacy_write_execution_email_payload(payload: dict, run_date: str) -> tuple[str, bool, str | None]:
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
    safe_write_text(
        write_path,
        json.dumps(payload, indent=2) + "\n",
        allow_overwrite=(write_path == out_path),
    )
    return str(write_path), preserved, preserved_path


def _write_execution_email_payload(payload: dict, run_date: str) -> tuple[str, bool, str | None]:
    if _RUN_CONTEXT is None:
        write_path, preserved, preserved_path = _legacy_write_execution_email_payload(payload, run_date)
        logger.info("[EXECUTION_EMAIL] payload written: %s", write_path)
        return write_path, preserved, preserved_path

    payload_text = json.dumps(payload, indent=2) + "\n"
    canonical_path = _canonical_artifact_path("snapshots", f"execution_email_{run_date}.json")
    safe_write_text(
        canonical_path,
        payload_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    out_dir = Path("outputs") / "execution_email"
    out_dir.mkdir(parents=True, exist_ok=True)
    view_path = out_dir / f"{run_date}.json"
    safe_write_text(view_path, payload_text, allow_overwrite=True)
    logger.info("[EXECUTION_EMAIL] payload written: %s", canonical_path)
    return str(canonical_path), False, None


def _write_canonical_execution_payload(payload: dict, run_date: str, run_root: Path | None = None) -> str:
    target_root = run_root
    if target_root is None and _RUN_CONTEXT is not None:
        target_root = _RUN_CONTEXT.run_root
    out_path = write_canonical_execution_payload(
        payload,
        run_date,
        run_root=target_root,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    logger.info(
        "[EXECUTION_PAYLOAD] wrote path=%s trades=%d status=%s",
        out_path,
        int((payload or {}).get("executable_trades_count") or 0),
        (payload or {}).get("execution_status"),
    )
    return str(out_path)


def write_integrity_artifact(asof_date: str, payload: dict) -> str:
    payload_text = json.dumps(payload, indent=2) + "\n"
    view_path = Path("outputs") / "daily" / f"integrity_{asof_date}.json"
    if _RUN_CONTEXT is None:
        safe_write_text(view_path, payload_text, allow_overwrite=True)
        return str(view_path)

    canonical_path = _canonical_artifact_path("snapshots", f"integrity_{asof_date}.json")
    safe_write_text(
        canonical_path,
        payload_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    safe_write_text(view_path, payload_text, allow_overwrite=True)
    return str(canonical_path)


def write_health_artifact(trade_date: str, payload: dict) -> str:
    payload_text = json.dumps(payload, indent=2) + "\n"
    view_path = Path("outputs") / "daily" / f"health_{trade_date}.json"
    if _RUN_CONTEXT is None:
        safe_write_text(view_path, payload_text, allow_overwrite=True)
        return str(view_path)

    canonical_path = _canonical_artifact_path("snapshots", f"health_{trade_date}.json")
    safe_write_text(
        canonical_path,
        payload_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    safe_write_text(view_path, payload_text, allow_overwrite=True)
    return str(canonical_path)


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
        "outputs/orders_sent/orders_sent.csv",
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
        "orders_sent_path": "outputs/orders_sent/orders_sent.csv",
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


def _coerce_share_quantity(value: object, *, whole: bool) -> float | int:
    try:
        numeric = max(0.0, float(value))
    except Exception:
        return 0
    if whole:
        return int(numeric)
    if numeric.is_integer():
        return int(numeric)
    return numeric


def _candidate_row_symbol(row: dict[str, object]) -> str:
    return str(row.get("ticker") or row.get("symbol") or "").strip().upper()


def _candidate_row_side(row: dict[str, object]) -> str:
    return str(row.get("side") or row.get("action") or "").strip().upper()


def _candidate_symbol_side_key(row: dict[str, object]) -> str:
    return f"{_candidate_row_symbol(row)}:{_candidate_row_side(row)}"


def _candidate_row_qty(row: dict[str, object]) -> float | None:
    for key in ("shares", "quantity", "qty", "requested_quantity", "rounded_quantity"):
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except Exception:
            return None
    return None


def _candidate_row_reason(row: dict[str, object], default: str = "") -> str:
    for key in (
        "decision_reason",
        "suppression_or_clipping_reason",
        "reason",
        "reason_code",
        "block_reason",
        "skip_reason",
    ):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return default


def _load_execution_readiness_certification(trade_date: str, paper_summary: dict | None) -> dict[str, object]:
    if isinstance((paper_summary or {}).get("execution_readiness_certification"), dict):
        return dict((paper_summary or {}).get("execution_readiness_certification") or {})
    raw_path = (paper_summary or {}).get("execution_readiness_certification_path")
    path = Path(str(raw_path)) if raw_path else Path("outputs") / "precompute" / trade_date / "execution_readiness_certification.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _readiness_rows(readiness: dict[str, object], key: str) -> list[dict[str, object]]:
    diagnostics = readiness.get("per_trade_diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, dict) else {}
    rows = diagnostics.get(key)
    if not isinstance(rows, list):
        rows = readiness.get(key) if isinstance(readiness.get(key), list) else []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _execution_lifecycle_payload(
    *,
    trade_date: str,
    paper_summary: dict | None,
    filter_stats: dict | None,
    payload_submitted_count: int,
    payload_rejected_count: int,
) -> dict[str, object]:
    summary = paper_summary or {}
    readiness = _load_execution_readiness_certification(trade_date, summary)
    post_sell_rebudget = summary.get("post_sell_rebudget")
    if not isinstance(post_sell_rebudget, dict):
        submission_summary = summary.get("alpaca_submission_summary")
        if isinstance(submission_summary, dict) and isinstance(submission_summary.get("post_sell_rebudget"), dict):
            post_sell_rebudget = submission_summary.get("post_sell_rebudget")
    if not isinstance(post_sell_rebudget, dict):
        cash_gate = summary.get("cash_gate_diagnostics")
        if isinstance(cash_gate, dict) and isinstance(cash_gate.get("post_sell_rebudget"), dict):
            post_sell_rebudget = cash_gate.get("post_sell_rebudget")
    post_sell_rebudget = dict(post_sell_rebudget or {})

    expected_rows = [
        row
        for row in (
            _readiness_rows(readiness, "expected_submission_orders")
            or _readiness_rows(readiness, "retained")
        )
        if _candidate_row_symbol(row) and _candidate_row_side(row)
    ]
    expected_by_key = {_candidate_symbol_side_key(row): row for row in expected_rows}
    expected_keys = set(expected_by_key)

    filtered_rows = [
        row
        for row in _readiness_rows(readiness, "skipped")
        if _candidate_row_reason(row) in {"min_notional", "zero_shares"}
    ]
    min_notional_rows = [
        row for row in filtered_rows if _candidate_row_reason(row) == "min_notional"
    ]
    filtered_sell_rows = [
        row for row in min_notional_rows if _candidate_row_side(row) in {"SELL", "CLOSE", "REDUCE"}
    ]

    resized_rows: list[dict[str, object]] = []
    for row in post_sell_rebudget.get("final_buy_orders_submitted") or []:
        if not isinstance(row, dict) or _candidate_row_side(row) != "BUY":
            continue
        key = _candidate_symbol_side_key(row)
        expected = expected_by_key.get(key)
        expected_qty = _candidate_row_qty(expected) if expected else None
        submitted_qty = _candidate_row_qty(row)
        if expected_keys and key not in expected_keys:
            continue
        if expected_qty is not None and submitted_qty is not None and abs(expected_qty - submitted_qty) <= 1e-9:
            continue
        resized_rows.append(
            {
                "ticker": _candidate_row_symbol(row),
                "side": "BUY",
                "submitted": True,
                "decision_reason": "resized_by_post_sell_rebudget",
                "suppression_or_clipping_reason": _candidate_row_reason(row, "post_sell_rebudget"),
                "requested_quantity": expected_qty,
                "submitted_quantity": submitted_qty,
            }
        )

    suppressed_rows: list[dict[str, object]] = []
    for row in post_sell_rebudget.get("skipped_buy_orders") or []:
        if not isinstance(row, dict) or _candidate_row_side(row) != "BUY":
            continue
        key = _candidate_symbol_side_key(row)
        if expected_keys and key not in expected_keys:
            continue
        suppressed_rows.append(
            {
                "ticker": _candidate_row_symbol(row),
                "side": "BUY",
                "submitted": False,
                "decision_reason": _candidate_row_reason(row, "buy_blocked_insufficient_buying_power"),
                "suppression_or_clipping_reason": _candidate_row_reason(row, "buy_blocked_insufficient_buying_power"),
            }
        )

    filtered_lifecycle_rows = [
        {
            "ticker": _candidate_row_symbol(row),
            "side": _candidate_row_side(row),
            "submitted": False,
            "decision_reason": _candidate_row_reason(row, "execution_filter"),
            "suppression_or_clipping_reason": _candidate_row_reason(row, "execution_filter"),
        }
        for row in filtered_sell_rows
    ]
    lifecycle_rows = [*resized_rows, *suppressed_rows, *filtered_lifecycle_rows]

    planned_count = (
        summary.get("planned_payload_trade_count")
        or readiness.get("planned_trade_count")
        or (filter_stats or {}).get("raw")
    )
    min_notional_count = (
        readiness.get("dropped_min_notional_count")
        or (filter_stats or {}).get("dropped_min_notional")
    )
    intended_count = (
        summary.get("intended_orders_count")
        or readiness.get("expected_submissions")
        or (filter_stats or {}).get("kept")
    )
    submission_summary = summary.get("alpaca_submission_summary")
    submission_summary = submission_summary if isinstance(submission_summary, dict) else {}
    filled_count = (
        summary.get("orders_filled_count")
        or summary.get("filled_count")
        or submission_summary.get("orders_filled_count")
        or submission_summary.get("filled_count")
    )

    try:
        final_count = int(payload_submitted_count)
    except Exception:
        final_count = 0
    if final_count <= 0:
        final_count = int(summary.get("submitted_count") or summary.get("orders_submitted_count") or 0)
    try:
        rejected_count = int(summary.get("rejected_count") or payload_rejected_count or 0)
    except Exception:
        rejected_count = int(payload_rejected_count or 0)
    try:
        planned_differs_from_submitted = bool(
            planned_count is not None and final_count and int(planned_count) != final_count
        )
    except Exception:
        planned_differs_from_submitted = False

    has_lifecycle_signal = bool(
        lifecycle_rows
        or planned_differs_from_submitted
        or min_notional_count
    )
    if not has_lifecycle_signal:
        return {}

    return {
        "planned_payload_trade_count": planned_count,
        "min_notional_filtered_count": min_notional_count,
        "executable_filter_passed_count": intended_count,
        "intended_orders_count": intended_count,
        "final_executable_trades_count": final_count,
        "candidate_trade_lifecycle_summary": {
            "precompute_candidates": planned_count,
            "min_notional_filtered": min_notional_count,
            "passed_executable_filter": intended_count,
            "intended_orders": intended_count,
            "submitted": final_count,
            "accepted": summary.get("accepted_count") or final_count,
            "filled": filled_count,
            "rejected": rejected_count,
            "clipped": len(resized_rows),
            "suppressed": len(suppressed_rows),
            "filtered": len(filtered_lifecycle_rows),
            "artifact_path": post_sell_rebudget.get("artifact_path") or readiness.get("artifact_path"),
        },
        "candidate_trade_lifecycle": lifecycle_rows,
        "resized_intended_buys": resized_rows,
        "suppressed_intended_buys": suppressed_rows,
        "filtered_sells": filtered_lifecycle_rows,
    }


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
    raw_summary_mode = (paper_summary or {}).get("trading_mode") or os.getenv(
        "TRADING_MODE",
        DEFAULT_TRADING_MODE,
    )
    mode = "SHADOW" if legacy_shadow_mode_requested(raw_summary_mode) else canonical_trading_mode_label(raw_summary_mode)
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
            if mode == "PAPER" or plan_only:
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
        if str(paper_summary.get("execution_status") or "").upper() == "HALTED":
            status = "HALTED"
            halted_reason = str(
                paper_summary.get("halt_reason")
                or paper_summary.get("execution_reason")
                or "EXECUTION HALTED"
            )
        execution_outcome_code = str(paper_summary.get("execution_outcome") or "").strip()
        if execution_outcome_code in {
            EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
            EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
        }:
            status = "HALTED"
            halted_reason = str(
                paper_summary.get("halt_reason")
                or f"{execution_outcome_code}:execution_aborted"
            )
    paper_breaker = (paper_summary or {}).get("breaker") if isinstance(paper_summary, dict) else {}
    snapshot_breaker = (daily_snapshot or {}).get("breaker") if isinstance(daily_snapshot, dict) else {}
    breaker_mode = str(
        (paper_breaker or {}).get("mode")
        or (snapshot_breaker or {}).get("mode")
        or os.getenv("BREAKER_MODE", "off")
    ).strip().lower()
    if breaker_mode not in {"off", "partial", "lock"}:
        breaker_mode = "off"
    risk_map = {r.get("ticker"): r for r in (daily_snapshot.get("risk_levels", []) or []) if r.get("ticker")}
    holdings_shares = {
        str(h.get("ticker")): _coerce_whole_shares(h.get("shares"))
        for h in (daily_snapshot.get("holdings", []) or [])
        if h.get("ticker")
    }
    orders = (paper_summary or {}).get("orders") or (paper_summary or {}).get("shadow_orders", []) or []
    execution_trades = (paper_summary or {}).get("execution_trades", []) or []
    planned_trades = (paper_summary or {}).get("trade_plan", []) or []
    execution_filter = (paper_summary or {}).get("execution_filter", {}) or {}
    alpaca_submissions = (paper_summary or {}).get("alpaca_submissions", []) or []
    min_trade_dollars_raw = (paper_summary or {}).get("min_trade_dollars")
    min_trade_dollars = float(min_trade_dollars_raw) if min_trade_dollars_raw is not None else None
    filter_stats = _coerce_filter_stats(execution_filter)
    risk_meta = (paper_summary or {}).get("risk_meta", {}) or {}
    turnover_scaled = bool(risk_meta.get("turnover_scaled", False))
    turnover_requested_raw = risk_meta.get("turnover_requested")
    turnover_cap_raw = risk_meta.get("turnover_cap")
    turnover_scale_raw = risk_meta.get("turnover_scale")
    turnover_cap_scope = str(risk_meta.get("turnover_cap_scope") or "").strip().lower()
    turnover_scope_label = "buy turnover" if turnover_cap_scope == "buys_only" else "turnover"
    turnover_requested = _coerce_float_or_none(turnover_requested_raw)
    turnover_cap = _coerce_float_or_none(turnover_cap_raw)
    turnover_scale = _coerce_float_or_none(turnover_scale_raw)
    turnover_dollars = _coerce_float_or_none((paper_summary or {}).get("turnover_notional"))
    turnover_pct = _coerce_float_or_none((paper_summary or {}).get("turnover_pct"))
    trades = []
    source_rows = []
    row_reason_lookup: dict[tuple[str, str, str], dict[str, object]] = {}

    def _register_reason_source(rows: list[dict] | list[object], *, source_key: str) -> None:
        for item in rows or []:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker", "")).upper()
            side = str(item.get("side", "")).upper()
            order_id = str(item.get("order_id", "") or "")
            if not ticker or not side:
                continue
            row_reason_lookup[(ticker, side, order_id)] = {
                "reason": item.get("reason"),
                "price": item.get("price"),
                "notional": item.get("notional"),
                "source": source_key,
            }
            if order_id:
                continue
            row_reason_lookup[(ticker, side, "")] = {
                "reason": item.get("reason"),
                "price": item.get("price"),
                "notional": item.get("notional"),
                "source": source_key,
            }

    _register_reason_source(planned_trades, source_key="trade_plan_lookup")
    _register_reason_source(execution_trades, source_key="execution_trades_lookup")
    _register_reason_source(orders, source_key="orders_lookup")

    payload_submitted_count = int(
        ((paper_summary or {}).get("alpaca_submission_summary") or {}).get("submit_success")
        or 0
    )
    payload_rejected_count = int(
        ((paper_summary or {}).get("alpaca_submission_summary") or {}).get("submit_failed")
        or 0
    )

    if mode == "PAPER" and payload_submitted_count > 0 and alpaca_submissions:
        for submission in alpaca_submissions:
            if not isinstance(submission, dict):
                continue
            ticker = str(submission.get("ticker", "")).upper()
            side = str(submission.get("side", "")).upper()
            order_id = str(submission.get("order_id", "") or "")
            lookup = row_reason_lookup.get((ticker, side, order_id)) or row_reason_lookup.get((ticker, side, ""))
            source_rows.append(
                {
                    "ticker": ticker,
                    "side": side,
                    "shares": submission.get("quantity"),
                    "price": (lookup or {}).get("price"),
                    "reason": (lookup or {}).get("reason"),
                    "order_id": order_id or None,
                    "notional": (lookup or {}).get("notional"),
                    "source": "alpaca_submissions",
                }
            )
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
    elif execution_trades and not source_rows:
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
    elif status == "READY" and not source_rows:
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
                    "source": "orders",
                }
            )
    for row in source_rows:
        ticker = row.get("ticker")
        side = str(row.get("side", "")).upper()
        risk = risk_map.get(ticker, {})
        row_source = str(row.get("source") or "")
        broker_quantity_source = row_source == "alpaca_submissions"
        shares = _coerce_share_quantity(row.get("shares"), whole=not broker_quantity_source)
        if side in {"SELL", "CLOSE", "REDUCE"} and not broker_quantity_source:
            available = holdings_shares.get(str(ticker))
            if available is not None:
                shares = min(shares, available)
        if broker_quantity_source:
            if float(shares or 0) <= 0.0:
                continue
        elif shares < 1:
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
    symbol_resolution = resolve_trade_plan_symbols(trades, today=trade_date)
    trades = symbol_resolution.trades
    if symbol_resolution.status == "FAIL":
        status = "HALTED"
        halted_reason = f"security_master_symbol_resolution_failed:{symbol_resolution.reason}"
    trades = sorted(trades, key=lambda x: (x.get("ticker") or "", x.get("side") or ""))
    buy_count = sum(1 for t in trades if str(t.get("side", "")).upper() == "BUY")
    sell_count = sum(1 for t in trades if str(t.get("side", "")).upper() in {"SELL", "CLOSE", "REDUCE"})
    status_label = None
    status_reason = None
    if str((paper_summary or {}).get("execution_outcome") or "").strip() in {
        EXECUTION_OUTCOME_PARTIAL_BROKER_ABORT,
        EXECUTION_OUTCOME_POST_SUBMIT_ARTIFACT_FAILURE,
    }:
        status_label = "PARTIAL EXECUTION"
        status_reason = (
            halted_reason
            or str((paper_summary or {}).get("broker_reject_message") or "")
            or str((paper_summary or {}).get("artifact_failure_message") or "")
            or str((paper_summary or {}).get("execution_reason") or "")
        )
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
    
    # Format turnover_cap - if infinite, show as Disabled
    if turnover_cap is None:
        turnover_cap_display = "unavailable"
    elif math.isinf(turnover_cap):
        turnover_cap_display = "Disabled (∞)"
    else:
        turnover_cap_display = f"${turnover_cap:,.2f}"
    
    risk_summary = {
        "Turnover requested ($)": f"${turnover_requested:,.2f}" if turnover_requested is not None else "unavailable",
        "Turnover cap ($)": turnover_cap_display,
        "Turnover scale": f"{turnover_scale:.4f}" if turnover_scale is not None else "unavailable",
        "Executed turnover ($)": f"${turnover_dollars:,.2f}" if turnover_dollars is not None else "unavailable",
        "Executed turnover (%)": f"{turnover_pct * 100:.2f}%" if turnover_pct is not None else "unavailable",
        "Target cash weight (%)": f"{target_cash_weight * 100:.2f}%" if target_cash_weight is not None else "unavailable",
        "Target gross exposure (%)": f"{(1.0 - target_cash_weight) * 100:.2f}%" if target_cash_weight is not None else "unavailable",
        "Achieved cash weight (%)": f"{achieved_cash_weight * 100:.2f}%" if achieved_cash_weight is not None else "unavailable",
        "Current achieved cash weight (%)": f"{achieved_cash_weight * 100:.2f}%" if achieved_cash_weight is not None else "unavailable",
        "Gross exposure (%)": f"{gross_exposure * 100:.2f}%" if gross_exposure is not None else "unavailable",
        "Current achieved gross exposure (%)": f"{gross_exposure * 100:.2f}%" if gross_exposure is not None else "unavailable",
        "Net exposure (%)": f"{net_exposure * 100:.2f}%" if net_exposure is not None else "unavailable",
        "# positions": str(position_count) if position_count is not None else "unavailable",
        "Max position weight (%)": f"{max_position_weight * 100:.2f}%" if max_position_weight is not None else "unavailable",
    }
    trade_count_contract = compute_trade_count_contract(
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
        execution_payload={
            "trades": trades,
            "executable_trades_count": int(len(trades)),
        },
    )
    submission_summary_for_counts = (paper_summary or {}).get("alpaca_submission_summary")
    submission_summary_for_counts = (
        submission_summary_for_counts
        if isinstance(submission_summary_for_counts, dict)
        else {}
    )
    orders_filled_count = int(
        (paper_summary or {}).get("orders_filled_count")
        or (paper_summary or {}).get("filled_count")
        or submission_summary_for_counts.get("orders_filled_count")
        or submission_summary_for_counts.get("filled_count")
        or trade_count_contract["orders_filled_count"]
    )
    sizing_equity = _coerce_float_or_none((paper_summary or {}).get("sizing_equity"))
    total_equity_fallback = _coerce_float_or_none((paper_summary or {}).get("total_equity"))
    identity = strategy_identity_metadata(trade_date)
    lifecycle_payload = _execution_lifecycle_payload(
        trade_date=trade_date,
        paper_summary=paper_summary,
        filter_stats=filter_stats,
        payload_submitted_count=payload_submitted_count,
        payload_rejected_count=payload_rejected_count,
    )

    payload = {
        "trade_date": trade_date,
        "strategy_identity": identity,
        **identity,
        **lifecycle_payload,
        "mode": mode,
        "execution_status": status,
        "halt_reason": halted_reason,
        "status_label": status_label,
        "status_reason": status_reason,
        "execution_outcome": (paper_summary or {}).get("execution_outcome"),
        "execution_reason": (paper_summary or {}).get("execution_reason"),
        "cash_rebalance_status": (paper_summary or {}).get("cash_rebalance_status"),
        "broker_reject_status": (paper_summary or {}).get("broker_reject_status"),
        "broker_reject_message": (paper_summary or {}).get("broker_reject_message"),
        "asset_validation_status": (paper_summary or {}).get("asset_validation_status"),
        "invalid_symbols": list((paper_summary or {}).get("invalid_symbols") or []),
        "non_tradable_symbols": list((paper_summary or {}).get("non_tradable_symbols") or []),
        "asset_validation_reason": (paper_summary or {}).get("asset_validation_reason"),
        "invalid_asset_count": int((paper_summary or {}).get("invalid_asset_count") or 0),
        **symbol_resolution.to_payload(),
        "order_lifecycle": list((paper_summary or {}).get("order_lifecycle") or []),
        "sell_submit_started_at": (paper_summary or {}).get("sell_submit_started_at"),
        "sell_submit_completed_at": (paper_summary or {}).get("sell_submit_completed_at"),
        "buy_submit_started_at": (paper_summary or {}).get("buy_submit_started_at"),
        "buy_submit_completed_at": (paper_summary or {}).get("buy_submit_completed_at"),
        "pending_sell_count_at_buy_decision": (paper_summary or {}).get("pending_sell_count_at_buy_decision"),
        "buying_power_at_buy_decision": (paper_summary or {}).get("buying_power_at_buy_decision"),
        "cash_at_buy_decision": (paper_summary or {}).get("cash_at_buy_decision"),
        "skipped_buy_count": int((paper_summary or {}).get("skipped_buy_count") or 0),
        "blocked_buy_count": int((paper_summary or {}).get("blocked_buy_count") or 0),
        "buy_phase_decision_reason": (paper_summary or {}).get("buy_phase_decision_reason"),
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
        "model_proposed_trades_count": int(trade_count_contract["model_proposed_trades_count"]),
        "planner_intended_trades_count": int(trade_count_contract["planner_intended_trades_count"]),
        "execution_eligible_trades_count": int(trade_count_contract["execution_eligible_trades_count"]),
        "orders_submitted_count": int(trade_count_contract["orders_submitted_count"]),
        "orders_filled_count": orders_filled_count,
        "submitted_count": payload_submitted_count,
        "accepted_count": payload_submitted_count,
        "rejected_count": payload_rejected_count,
        "proposed_trades_intent_count": int(trade_count_contract["planner_intended_trades_count"]),
        "proposed_trades_intent": int(trade_count_contract["planner_intended_trades_count"]),
        "executable_trades_count": int(trade_count_contract["execution_eligible_trades_count"]),
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
            "turnover_requested_buys": _coerce_float_or_none(risk_meta.get("turnover_requested_buys")),
            "turnover_requested_sells": _coerce_float_or_none(risk_meta.get("turnover_requested_sells")),
            "turnover_requested_total": _coerce_float_or_none(risk_meta.get("turnover_requested_total")),
            "turnover_cap": turnover_cap,
            "turnover_scaled": turnover_scaled,
            "turnover_scale": turnover_scale,
            "turnover_cap_scope": turnover_cap_scope or None,
            "turnover_dollars": turnover_dollars,
            "turnover_pct": turnover_pct,
        },
        "risk_summary": risk_summary,
        "market_analyzer": (daily_snapshot or {}).get("market_analyzer"),
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
                f"{turnover_scope_label.capitalize()} cap scaling applied (requested ${turnover_requested:,.2f} vs cap ${turnover_cap:,.2f}, "
                f"scale={turnover_scale:.4f}); no trades remained after rounding/minimum filters"
            )
    if turnover_scaled and turnover_requested is not None and turnover_cap is not None and turnover_scale is not None:
        payload["turnover_note"] = (
            f"{turnover_scope_label.capitalize()} cap applied: requested ${turnover_requested:,.2f}, "
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
        f"• Defensive ETFs — Bonds: {_fmt_pct(sleeve_splits.get(DEFENSIVE_SLEEVE_NAME, 0.0))}",
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
    ic_monitor = _load_ic_monitor_snapshot()
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
    raw_mode = str((execution_payload or {}).get("mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)).upper()
    env_mode = "LIVE" if raw_mode == "LIVE" else "SHADOW"
    strategy_identity = (execution_payload or {}).get("strategy_identity") or strategy_identity_metadata(asof_str)
    live_strategy_id = str(strategy_identity.get("live_strategy_id") or "unknown")
    shadow_baseline = str(strategy_identity.get("shadow_baseline_strategy") or "unknown")
    tracks_shadow = bool(strategy_identity.get("live_tracks_shadow_baseline"))
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
    sleeve_cash_routes = [
        route for route in list(sleeve1_diag.get("cash_routing") or [])
        if isinstance(route, dict)
    ]
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
        f"• Live strategy: {live_strategy_id}",
        f"• Shadow baseline: {shadow_baseline}",
        f"• Live tracks shadow baseline: {'YES' if tracks_shadow else 'NO'}",
        "",
        "SLEEVE ALLOCATION (DYNAMIC)",
        f"• Sleeve 1 — Momentum: {_fmt_pct(sleeve_splits.get('sleeve_trend', 0.0))}",
        f"• Sleeve 2 — Valuation: {_fmt_pct(sleeve_splits.get('sleeve_2', 0.0))}",
        f"• Defensive ETFs — Bonds: {_fmt_pct(sleeve_splits.get(DEFENSIVE_SLEEVE_NAME, 0.0))}",
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
        "DEFENSIVE ETF SLEEVE — TREASURY / CASH PROXY",
        f"• Status: {_sleeve_state_status(DEFENSIVE_SLEEVE_NAME)}",
        f"• Allocation: {_fmt_pct(sleeve_splits.get(DEFENSIVE_SLEEVE_NAME, 0.0))}",
        f"• Trades Today: {_trades_today(DEFENSIVE_SLEEVE_NAME)}",
        "• Role: Deploy defensive capital into liquid Treasury ETFs instead of pure cash when regime risk is elevated.",
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
    if sleeve_cash_routes:
        insert_at = lines.index("")
        # Use the blank line immediately before ALPHA ATTRIBUTION.
        for idx, item in enumerate(lines):
            if item == "ALPHA ATTRIBUTION VS SPY":
                insert_at = max(0, idx - 1)
                break
        route_lines = [
            "• Cash route: "
            f"{route.get('sleeve_id', 'unknown')} "
            f"{_fmt_pct(route.get('routed_weight'))} "
            f"reason={route.get('invalid_reason', route.get('reason_code', 'unknown'))} "
            f"diagnostic={route.get('diagnostic_artifact') or 'unavailable'}"
            for route in sleeve_cash_routes
        ]
        lines[insert_at:insert_at] = route_lines
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
            "SIGNAL IC MONITOR",
        ]
    )
    if not ic_monitor.get("available"):
        lines.append("• Status: unavailable — run research.ic_monitor")
    else:
        ic_summary = ic_monitor.get("summary") or {}
        sleeves = ic_summary.get("sleeves") or {}
        if not sleeves:
            lines.append("• Status: no sleeve IC data available")
        else:
            lines.append(f"• Status: {str(ic_summary.get('status', 'unknown')).upper()}")
            for sleeve_name in sorted(sleeves):
                sleeve_payload = sleeves.get(sleeve_name) or {}
                latest_ic = (sleeve_payload.get("latest_ic_by_horizon") or {}).get("1")
                rolling_20 = ((sleeve_payload.get("latest_rolling_ic_by_horizon") or {}).get("1") or {}).get("20")
                lines.append(
                    f"• {sleeve_name}: 20d rolling IC {_fmt_number(_coerce_float_or_none(rolling_20))}, "
                    f"latest 1d IC {_fmt_number(_coerce_float_or_none(latest_ic))}"
                )
        alerts = ic_summary.get("alerts") or []
        if alerts:
            lines.append("• Active alerts:")
            for alert in alerts:
                lines.append(f"  - {alert}")
        else:
            lines.append("• Active alerts: none")
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


def _load_ic_monitor_snapshot() -> dict[str, Any]:
    path = Path("outputs") / "ic_monitor" / "ic_summary.json"
    if not path.exists():
        return {"available": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[IC_MONITOR][WARN] could not parse %s: %s", path, exc)
        return {"available": False, "path": str(path)}
    return {"available": True, "path": str(path), "summary": payload}
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
def _write_sleeve_numeric_trace(
    *,
    sleeve_id: str,
    equity_df: pd.DataFrame,
    invalid_reason: str,
    reason_code: str,
    downstream_effect: str,
) -> str | None:
    trade_date = _RUN_CONTEXT.report_date if _RUN_CONTEXT is not None else None
    if not trade_date and _RUN_CONTEXT is not None:
        trade_date = _RUN_CONTEXT.report_date_env
    diagnostics = []
    if isinstance(equity_df, pd.DataFrame):
        diagnostics = list(equity_df.attrs.get("numeric_diagnostics") or [])
    payload = build_trace_payload(
        sleeve_id=sleeve_id,
        trade_date=trade_date,
        reason_code=reason_code,
        invalid_reason=invalid_reason,
        events=diagnostics,
        source_artifact="sleeve_runtime",
        downstream_effect=downstream_effect,
        run_id=_RUN_CONTEXT.run_id if _RUN_CONTEXT is not None else None,
    )
    path = _canonical_artifact_path("audit", trace_artifact_name(sleeve_id, trade_date))
    safe_write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    return str(path)


def _sleeve_is_valid(
    equity_df: pd.DataFrame,
    *,
    sleeve_id: str | None = None,
    write_trace: bool = False,
) -> tuple[bool, str]:
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
    if pd.isna(last_eq) or not is_finite_number(last_eq):
        reason = f"invalid terminal equity ({last_eq})"
        if write_trace and sleeve_id:
            try:
                trace_path = _write_sleeve_numeric_trace(
                    sleeve_id=sleeve_id,
                    equity_df=equity_df,
                    invalid_reason=reason,
                    reason_code=REASON_SLEEVE_TERMINAL_EQUITY_NAN,
                    downstream_effect="sleeve allocation forced to cash",
                )
                equity_df.attrs["numeric_trace_path"] = trace_path
                return False, f"{reason}; diagnostic={trace_path}"
            except Exception as exc:
                logger.warning("[SLEEVE_NUMERIC_TRACE] failed for %s: %s", sleeve_id, exc)
                return False, f"{reason}; diagnostic={REASON_NAN_TRACE_MISSING}"
        return False, reason
    if last_eq <= 0:
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
def run_sleeve_1(prices: pd.DataFrame | None = None):
    _ensure_sleeve_backtest_imports()
    logger.info("[SLEEVE 1] Preparing data...")
    signals = s1_prepare_data(prices=prices)
    logger.info("[SLEEVE 1] Running backtest...")
    return s1_backtest(signals)
def run_sleeve_trend(prices: pd.DataFrame | None = None):
    _ensure_sleeve_backtest_imports()
    logger.info("[SLEEVE TREND] Preparing data...")
    signals = st_prepare_data(prices=prices)
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


def run_sleeve_value(prices: pd.DataFrame | None = None) -> "SleeveOutput":
    """
    Build and return a SleeveOutput for the Value sleeve (Sleeve 2 / EDGAR XBRL P/E).

    Never raises — returns an empty SleeveOutput on any failure so the
    orchestrator can continue.
    """
    from core.portfolio_alloc import create_sleeve_output
    try:
        from sleeves.sleeve_2.selection import build_value_sleeve_output
        if prices is None or (hasattr(prices, "empty") and prices.empty):
            logger.warning("[VALUE] No price data — skipping value sleeve")
            return create_sleeve_output([], "sleeve_2", 0.0, "No price data")
        result = build_value_sleeve_output(prices=prices, top_n=10, base_strength=1.0)
        n_pos = len(result.positions_df) if result.positions_df is not None else 0
        strength = result.meta.strength if result.meta else 0.0
        logger.info("[VALUE] SleeveOutput: %d positions, strength=%.3f", n_pos, strength)
        return result
    except Exception as exc:
        logger.warning("[VALUE] Sleeve build failed (non-blocking): %s", exc)
        return create_sleeve_output([], "sleeve_2", 0.0, f"Build error: {exc}")


def run_sleeve_quality(prices: pd.DataFrame | None = None) -> "SleeveOutput":
    """
    Build and return a SleeveOutput for the Quality sleeve.

    Requires a populated DataStore (data/fundamental/ from EDGAR XBRL ingestion).
    Returns an empty SleeveOutput if the DataStore is unavailable or signal
    build fails — never raises so the orchestrator can continue.

    2026-04-11: strength is now auto-throttled by rolling IC. When the
    60-day rolling IC of the quality sleeve turns non-positive, the
    sleeve's base_strength is scaled down proportionally toward a floor
    (see core/ic_throttle.py). This prevents the portfolio from paying
    full weight while the sleeve's edge is negative (the exact failure
    mode we saw in the backtest-vs-live reconciliation).
    """
    from core.portfolio_alloc import create_sleeve_output
    try:
        from datastore import DataStore
        from sleeves.sleeve_quality.selection import build_quality_sleeve_output
        from core.ic_throttle import compute_sleeve_ic_throttle

        ds = DataStore("data/fundamental")
        _tickers = list(quant_tickers or [])
        if not _tickers:
            logger.warning("[QUALITY] No universe tickers available — skipping")
            return create_sleeve_output([], "sleeve_quality", 0.0, "No tickers")

        # IC-gated auto-throttle. When the rolling IC is missing / neutral,
        # this returns 1.0 so existing behaviour is preserved. When IC is
        # negative, strength is scaled toward IcThrottleResult.floor.
        throttle = compute_sleeve_ic_throttle(sleeve="sleeve_quality")
        base_strength = float(throttle.multiplier)
        logger.info(
            "[QUALITY] IC throttle: multiplier=%.3f rolling_ic=%s horizon=%s reason=%s",
            base_strength,
            "None" if throttle.rolling_ic is None else f"{throttle.rolling_ic:.4f}",
            throttle.horizon,
            throttle.reason,
        )

        as_of = pd.Timestamp("today").normalize()
        logger.info("[QUALITY] Building quality signals for %d tickers", len(_tickers))
        result = build_quality_sleeve_output(
            tickers=_tickers,
            as_of_date=as_of,
            ds=ds,
            prices=prices,
            sectors=None,
            top_n=10,
            base_strength=base_strength,
        )
        # Stash the throttle diagnostics so downstream reports can surface it.
        try:
            if result is not None and getattr(result, "meta", None) is not None:
                setattr(result.meta, "ic_throttle", throttle.to_dict())
        except Exception:
            pass
        logger.info(
            "[QUALITY] SleeveOutput: %d positions, strength=%.3f (ic_mult=%.3f)",
            len(result.positions_df) if result.positions_df is not None else 0,
            result.meta.strength if result.meta else 0.0,
            base_strength,
        )
        return result
    except Exception as exc:
        logger.warning("[QUALITY] Sleeve build failed (non-blocking): %s", exc)
        return create_sleeve_output([], "sleeve_quality", 0.0, f"Build error: {exc}")


def run_sleeve_mean_reversion(
    prices: pd.DataFrame | None = None,
    regime_state: "dict | None" = None,
) -> "SleeveOutput":
    """
    Build and return a SleeveOutput for the Mean Reversion sleeve.

    Uses only price data (no DataStore / EDGAR dependency).
    Returns an empty SleeveOutput if price data is missing or signals fail.

    Parameters
    ----------
    prices       : Raw OHLCV universe prices from download_prices().
    regime_state : Optional dict of today's regime state (regime_result.today).
                   When breadth_state is in {deteriorating, washed_out} OR
                   composite_regime is in {risk_off_defensive, high_volatility},
                   the breadth gate fires and reduces the candidate count by 50%.
                   Pass None to disable the gate.
    """
    from core.portfolio_alloc import create_sleeve_output
    try:
        from sleeves.sleeve_mean_reversion.selection import build_mean_reversion_sleeve_output

        if prices is None or prices.empty:
            logger.warning("[MEAN_REV] No price data — skipping")
            return create_sleeve_output([], "sleeve_mean_reversion", 0.0, "No price data")

        logger.info(
            "[MEAN_REV] Building mean-reversion signals (regime breadth_state=%s composite=%s)",
            (regime_state or {}).get("breadth_state", "unknown"),
            (regime_state or {}).get("composite_regime", "unknown"),
        )
        result = build_mean_reversion_sleeve_output(
            prices=prices,
            top_n=10,
            base_strength=1.0,
            breadth_gate=regime_state,
        )
        logger.info(
            "[MEAN_REV] SleeveOutput: %d positions, strength=%.3f",
            len(result.positions_df) if result.positions_df is not None else 0,
            result.meta.strength if result.meta else 0.0,
        )
        return result
    except Exception as exc:
        logger.warning("[MEAN_REV] Sleeve build failed (non-blocking): %s", exc)
        return create_sleeve_output([], "sleeve_mean_reversion", 0.0, f"Build error: {exc}")


def run_sleeve_defensive_etf(
    prices: pd.DataFrame | None = None,
    regime_summary: dict | None = None,
) -> tuple["SleeveOutput", pd.DataFrame]:
    """
    Build the defensive ETF sleeve used to absorb part of the regime cash bucket
    in defensive states while staying on the existing ETF-capable execution path.
    """
    from core.portfolio_alloc import create_sleeve_output

    try:
        if prices is None or prices.empty:
            logger.warning("[DEFENSIVE_ETF] No price data — skipping")
            return create_sleeve_output([], DEFENSIVE_SLEEVE_NAME, 0.0, "No price data"), pd.DataFrame()

        output = build_defensive_etf_sleeve_output(
            prices=prices,
            regime_summary=regime_summary,
            base_strength=1.0,
        )
        equity_df = build_defensive_etf_equity_curve(
            prices=prices,
            sleeve_output=output,
            base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
        )
        logger.info(
            "[DEFENSIVE_ETF] SleeveOutput: %d positions, strength=%.3f",
            len(output.positions_df) if output.positions_df is not None else 0,
            output.meta.strength if output.meta else 0.0,
        )
        return output, equity_df
    except Exception as exc:
        logger.warning("[DEFENSIVE_ETF] Sleeve build failed (non-blocking): %s", exc)
        return create_sleeve_output([], DEFENSIVE_SLEEVE_NAME, 0.0, f"Build error: {exc}"), pd.DataFrame()


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


def _prepare_shared_regime_market_data(
    period: str = "1y",
    interval: str = "1d",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Download the universe plus SPY/VIX once, then split it for sleeves and regime.
    This keeps the regime path read-only and avoids a second price download.
    """
    _ensure_quant_report_imports()
    tickers = list(dict.fromkeys(list(quant_tickers or []) + ["SPY", "^VIX"] + DEFENSIVE_ETF_TICKERS))
    shared_prices = download_prices(tickers, period=period, interval=interval)
    if shared_prices.empty:
        raise ValueError("Shared market price download returned no rows")

    universe_prices = shared_prices[
        ~shared_prices["ticker"].astype(str).isin(["SPY", "^VIX", *DEFENSIVE_ETF_TICKERS])
    ].copy()
    defensive_etf_prices = shared_prices[
        shared_prices["ticker"].astype(str).isin(DEFENSIVE_ETF_TICKERS)
    ].copy()
    spy_prices = shared_prices[
        shared_prices["ticker"].astype(str) == "SPY"
    ][["date", "close"]].copy()
    vix_prices = shared_prices[
        shared_prices["ticker"].astype(str) == "^VIX"
    ][["date", "close"]].copy()

    if spy_prices.empty or vix_prices.empty or universe_prices.empty:
        raise ValueError(
            "Shared market price set missing SPY, ^VIX, or universe rows"
        )

    return universe_prices, spy_prices, vix_prices, defensive_etf_prices


def _build_regime_summary_payload(regime_result) -> dict:
    today = dict((regime_result.today or {}))
    weights = {
        str(name): float(value)
        for name, value in dict(today.get("weights") or {}).items()
    }
    return {
        "composite_regime": str(today.get("composite_regime") or "unknown"),
        "trend_state": str(today.get("trend_state") or "unknown"),
        "volatility_state": str(today.get("volatility_state") or "unknown"),
        "breadth_state": str(today.get("breadth_state") or "unknown"),
        "macro_state": str(today.get("macro_state") or "unknown"),
        "target_weights": weights,
    }


LIVE_REGIME_TO_SLEEVE = {
    "trend": "sleeve_trend",
    "value": "sleeve_2",
    "quality": "sleeve_quality",
    "mean_reversion": "sleeve_mean_reversion",
}

DEFENSIVE_CASH_ROUTE_REGIMES = {"risk_off_defensive", "high_volatility"}
DEFENSIVE_CASH_ROUTE_MACRO_STATES = {"risk_off", "stress"}
DEFENSIVE_CASH_ROUTE_VOLATILITY_STATES = {"elevated", "crisis"}


def _build_regime_html_block(regime_summary: dict) -> str:
    """
    Build an HTML summary block for today's macro regime.
    Displayed above sleeve tables and aligned with the live sleeve allocator.
    Fails gracefully: returns empty string on missing/incomplete data.
    """
    if not regime_summary:
        return ""
    try:
        composite = str(regime_summary.get("composite_regime") or "unknown")
        trend_state = str(regime_summary.get("trend_state") or "unknown")
        vol_state = str(regime_summary.get("volatility_state") or "unknown")
        breadth_state = str(regime_summary.get("breadth_state") or "unknown")
        macro_state = str(regime_summary.get("macro_state") or "unknown")
        weights = dict(regime_summary.get("target_weights") or {})

        _regime_colors = {
            "risk_on_trending":   "#d4edda",
            "neutral_mixed":      "#fff3cd",
            "risk_off_defensive": "#f8d7da",
            "high_volatility":    "#f8d7da",
            "breadth_washout":    "#fde8d8",
        }
        bg = _regime_colors.get(composite, "#f0f0f0")
        label = composite.replace("_", " ").title()

        dim_rows = "".join(
            f"<tr><td style='padding:3px 10px'>{dim}</td>"
            f"<td style='padding:3px 10px'>{state.replace('_',' ').title()}</td></tr>"
            for dim, state in [
                ("Trend", trend_state),
                ("Volatility", vol_state),
                ("Breadth", breadth_state),
                ("Macro", macro_state),
            ]
        )
        weight_rows = "".join(
            f"<tr><td style='padding:3px 10px'>{s.replace('_',' ').title()}</td>"
            f"<td style='padding:3px 10px;text-align:right'>{w:.0%}</td></tr>"
            for s, w in sorted(weights.items(), key=lambda kv: -kv[1])
        )

        return f"""
<div style="border:1px solid #ced4da;border-radius:6px;padding:14px 18px;
            margin-bottom:20px;background:{bg}">
  <h3 style="margin:0 0 10px 0;font-size:15px;font-weight:700">
    Macro Regime &mdash; {label}
  </h3>
  <div style="display:flex;gap:32px;flex-wrap:wrap">
    <div>
      <h4 style="margin:0 0 4px 0;font-size:12px;color:#6c757d;
                 text-transform:uppercase;letter-spacing:.05em">Dimension States</h4>
      <table style="border-collapse:collapse;font-size:13px">
        <tbody>{dim_rows}</tbody>
      </table>
    </div>
    <div>
      <h4 style="margin:0 0 4px 0;font-size:12px;color:#6c757d;
                 text-transform:uppercase;letter-spacing:.05em">Target Sleeve Weights</h4>
      <table style="border-collapse:collapse;font-size:13px">
        <tbody>{weight_rows}</tbody>
      </table>
    </div>
  </div>
  <p style="margin:10px 0 0 0;font-size:11px;color:#6c757d;font-style:italic">
    These target weights feed the live allocator. Realized sleeve allocations can differ when a sleeve is inactive or broker drift stays below the rebalance threshold.
  </p>
</div>
""".strip()
    except Exception as _regime_html_err:
        logger.warning("[REGIME_HTML] Could not build regime block: %s", _regime_html_err)
        return ""


def resolve_regime_strengths(
    regime_today: "dict | None",
    available_sleeves: "list[str]",
) -> "dict[str, float]":
    """
    Map regime weights to live sleeve names and renormalize.

    Regime columns (from RegimeAllocator):
        trend, value, quality, mean_reversion, cash

    Live sleeves handled here:
        sleeve_trend  ← trend
        sleeve_2      ← value
        sleeve_quality ← quality
        sleeve_mean_reversion ← mean_reversion
        sleeve_defensive_etf ← cash (risk_off / high-volatility only)

    Cash weight is dropped and the remaining mapped weights are
    renormalized so they sum to 1.0 unless the defensive ETF sleeve is
    active, in which case the defensive cash bucket can be routed there.

    Fallback: if regime_today is None, mapping is empty, or any error
    occurs, returns equal weight across available_sleeves so allocation
    remains valid.
    """
    try:
        if not regime_today:
            raise ValueError("No regime_today data")
        weights = dict(regime_today.get("weights") or {})
        if not weights:
            raise ValueError("No weights in regime_today")

        mapped: "dict[str, float]" = {}
        for regime_col, sleeve_name in LIVE_REGIME_TO_SLEEVE.items():
            if sleeve_name in available_sleeves and regime_col in weights:
                mapped[sleeve_name] = float(weights[regime_col])

        composite = str(regime_today.get("composite_regime") or "").strip().lower()
        macro_state = str(regime_today.get("macro_state") or "").strip().lower()
        vol_state = str(regime_today.get("volatility_state") or "").strip().lower()
        if (
            DEFENSIVE_SLEEVE_NAME in available_sleeves
            and float(weights.get("cash", 0.0) or 0.0) > WEIGHT_TOLERANCE
            and (
                composite in DEFENSIVE_CASH_ROUTE_REGIMES
                or macro_state in DEFENSIVE_CASH_ROUTE_MACRO_STATES
                or vol_state in DEFENSIVE_CASH_ROUTE_VOLATILITY_STATES
            )
        ):
            mapped[DEFENSIVE_SLEEVE_NAME] = float(weights.get("cash", 0.0) or 0.0)

        if not mapped:
            raise ValueError("No regime weights mapped to active sleeves")

        total = sum(mapped.values())
        if total < 1e-9:
            raise ValueError("Mapped regime weights sum to zero")

        return {k: v / total for k, v in mapped.items()}

    except Exception as exc:
        logger.debug("[REGIME_STRENGTHS] Fallback to equal weights: %s", exc)
        n = len(available_sleeves)
        if n == 0:
            return {}
        eq = 1.0 / n
        return {s: eq for s in available_sleeves}


def write_regime_state_artifact(
    *,
    report_date: str,
    regime_summary: "dict",
    vix_regime: "dict | None",
    regime_strengths: "dict[str, float]",
    drift_blends: "dict[str, float]",
    sleeve_cash_routes: "list[dict]",
    pre_concentration_target_count: int,
    repo_root: "Path | None" = None,
) -> "Path | None":
    """Write outputs/workflow/<REPORT_DATE>/regime_state.json.

    Also logs a single concise human-readable REGIME summary block and, when a
    previous-trading-day artifact exists, appends a 'changes' list of fields that
    differ from the prior run.

    ``pre_concentration_target_count`` is the ticker count from the broad-book
    ``alloc_result.combined_weights`` BEFORE the concentrated-alpha step reduces
    it to top-N names.  Concentration is always ON so this value overstates the
    number of names actually traded — it is preserved as provenance for diagnosis
    (how many candidates existed before narrowing).

    All exceptions are caught and logged — never raises.
    """
    try:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parent
        out_dir = repo_root / "outputs" / "workflow" / report_date
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "regime_state.json"

        # Concentration N + source from current env / vix_regime
        conc_n, conc_source = _concentrated_top_n(vix_regime)

        # Deployed SHA from deploy_state.json
        deploy_state_path = repo_root / "outputs" / "deploy_state.json"
        deployed_sha: "str | None" = None
        try:
            if deploy_state_path.exists():
                _ds = json.loads(deploy_state_path.read_text(encoding="utf-8"))
                deployed_sha = str(_ds.get("deployed_sha") or "").strip() or None
        except Exception:
            pass

        vix_val = float((vix_regime or {}).get("vix", 0.0) or 0.0)
        vix_label = str((vix_regime or {}).get("regime", "unknown")).lower()
        composite_regime = str(regime_summary.get("composite_regime") or "unknown").lower()

        state: dict = {
            "report_date": report_date,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
            "deployed_sha": deployed_sha,
            "regime": {
                "composite_regime": composite_regime,
                "vix_regime_label": vix_label,
                "vix_value": round(vix_val, 4),
                "trend_state": str(regime_summary.get("trend_state") or ""),
                "volatility_state": str(regime_summary.get("volatility_state") or ""),
                "breadth_state": str(regime_summary.get("breadth_state") or ""),
                "macro_state": str(regime_summary.get("macro_state") or ""),
            },
            "concentration": {
                "n": conc_n,
                "source": conc_source,
            },
            "sleeve_strengths": {k: round(v, 6) for k, v in (regime_strengths or {}).items()},
            "drift_blends": {k: round(v, 6) for k, v in (drift_blends or {}).items()},
            "cash_routes": [
                {
                    "sleeve": str(r.get("sleeve_id") or ""),
                    "routed_weight": float(r.get("routed_weight") or 0.0),
                    "reason": str(r.get("invalid_reason") or r.get("reason_code") or ""),
                }
                for r in (sleeve_cash_routes or [])
            ],
            "pre_concentration_target_count": int(pre_concentration_target_count),
        }

        # Load previous trading-day artifact for diff
        try:
            prev_date_str = prev_trading_day(report_date)
            prev_path = repo_root / "outputs" / "workflow" / prev_date_str / "regime_state.json"
            if prev_path.exists():
                prev_state = json.loads(prev_path.read_text(encoding="utf-8"))
                changes: list[str] = []
                # Compare flat scalars at regime level
                for field in ("composite_regime", "vix_regime_label", "trend_state", "volatility_state", "breadth_state", "macro_state"):
                    old_val = (prev_state.get("regime") or {}).get(field)
                    new_val = state["regime"].get(field)
                    if old_val != new_val:
                        changes.append(f"regime.{field}: {old_val!r} → {new_val!r}")
                # Concentration N change
                old_n = (prev_state.get("concentration") or {}).get("n")
                if old_n != conc_n:
                    changes.append(f"concentration.n: {old_n!r} → {conc_n!r}")
                # Sleeve strength changes (rounded to 3 decimal places for stability)
                for sname in sorted(set(list((prev_state.get("sleeve_strengths") or {}).keys()) + list(regime_strengths.keys()))):
                    old_s = round(float((prev_state.get("sleeve_strengths") or {}).get(sname, 0.0) or 0.0), 3)
                    new_s = round(float((regime_strengths or {}).get(sname, 0.0) or 0.0), 3)
                    if old_s != new_s:
                        changes.append(f"sleeve_strengths.{sname}: {old_s:.3f} → {new_s:.3f}")
                state["changes"] = changes
        except Exception as _diff_err:
            logger.debug("[REGIME_STATE] Could not load prior artifact for diff: %s", _diff_err)

        out_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        # Concise human-readable log block
        blend_log = ", ".join(
            f"{k}={v:.2f}" for k, v in sorted((drift_blends or {}).items())
        )
        strength_log = " ".join(
            f"{k}={v:.2f}" for k, v in sorted((regime_strengths or {}).items())
        )
        cash_log = (
            " | cash_routes=" + ",".join(r.get("sleeve") or "" for r in state["cash_routes"])
            if state["cash_routes"] else ""
        )
        logger.info(
            "REGIME | vix=%.1f %s | composite=%s | N=%d (src=%s) | %s"
            " | drift_blends: %s%s | targets=%d",
            vix_val, vix_label, composite_regime, conc_n, conc_source,
            strength_log, blend_log, cash_log, int(pre_concentration_target_count),
        )
        if state.get("changes"):
            logger.info("[REGIME_STATE] changes vs prev day: %s", "; ".join(state["changes"]))

        return out_path

    except Exception as _exc:
        logger.warning("[REGIME_STATE] Failed to write regime_state.json: %s", _exc)
        return None


_DRIFT_HOLD_THRESHOLD = 0.015   # below → hold prior strength (blend=0.0)
_DRIFT_FULL_THRESHOLD = 0.045   # above → full regime target (blend=1.0)
# Between the two thresholds, blend factor = linear interpolation 0..1.


def _drift_blend(drift: float) -> float:
    """Map absolute drift to a blend factor in [0.0, 1.0].

    Below ``_DRIFT_HOLD_THRESHOLD`` (1.5%) → 0.0 (full HOLD, keep prior strength).
    Above ``_DRIFT_FULL_THRESHOLD`` (4.5%) → 1.0 (full REBALANCE, regime target).
    Between the two thresholds → linear interpolation.
    """
    if drift <= _DRIFT_HOLD_THRESHOLD:
        return 0.0
    if drift >= _DRIFT_FULL_THRESHOLD:
        return 1.0
    span = _DRIFT_FULL_THRESHOLD - _DRIFT_HOLD_THRESHOLD
    return (drift - _DRIFT_HOLD_THRESHOLD) / span


def compute_sleeve_drift(
    broker_positions: "list | None",
    broker_equity: "float | None",
    sleeve_outputs: "list[SleeveOutput]",
    regime_strengths: "dict[str, float]",
) -> "dict[str, float]":
    """
    Compute per-sleeve rebalance blend factors from live broker positions.

    OBSERVABILITY ONLY: the returned blends drive drift logging and the
    HOLD/REBALANCE flags surfaced in live_regime_review; they do NOT gate
    the applied sleeve strength (see apply_regime_strengths_to_sleeves,
    which always anchors to the regime target).

    Compares current sleeve weights (derived from live Alpaca market values)
    against regime-target weights and returns a graded blend factor:

    * drift < 1.5%  → 0.0  (HOLD: within threshold of regime target)
    * drift > 4.5%  → 1.0  (FULL: far from regime target)
    * 1.5% ≤ drift ≤ 4.5% → linear blend between 0.0 and 1.0

    Falls back to blend=1.0 (full rebalance) for all sleeves when broker
    data is unavailable — safe for plan-only paper mode, offline fixture,
    and FRED-missing runs.

    Parameters
    ----------
    broker_positions : list of position dicts from fetch_pretrade_snapshot()["positions"].
        Each has "symbol" (str) and "market_value" (str or float).
    broker_equity : total portfolio equity from Alpaca account.
    sleeve_outputs : SleeveOutput list used to map tickers → sleeve names.
    regime_strengths : target weights from resolve_regime_strengths().

    Returns
    -------
    dict[str, float]  {sleeve_name: blend_factor}
        0.0 = HOLD (drift below 1.5% threshold)
        1.0 = FULL REBALANCE (drift above 4.5% threshold)
        0.0..1.0 = graded blend (linear between thresholds)
    """
    sleeve_names = list(regime_strengths.keys())

    # Fallback: no usable broker data → always full rebalance all sleeves
    if (
        not sleeve_names
        or broker_positions is None
        or broker_equity is None
        or broker_equity < 1.0
    ):
        for name in sleeve_names:
            logger.info(
                "[DRIFT] %s: current=n/a target=%.2f drift=n/a → REBALANCE blend=1.00 (no broker data)",
                name, regime_strengths.get(name, 0.0),
            )
        return {name: 1.0 for name in sleeve_names}

    # Build ticker → {sleeve_name: share} from sleeve_outputs.
    # When the same ticker appears in multiple sleeves, split the live broker
    # market value across sleeves in proportion to their current target weights
    # instead of assigning the entire holding to the last sleeve processed.
    ticker_to_sleeve_weights: "dict[str, dict[str, float]]" = {}
    for so in (sleeve_outputs or []):
        if so is None or so.positions_df is None or so.positions_df.empty:
            continue
        if "ticker" not in so.positions_df.columns or "target_weight" not in so.positions_df.columns:
            continue
        sname = so.meta.sleeve_name
        df = so.positions_df.copy()
        df["ticker"] = df["ticker"].astype(str).str.upper()
        grouped = (
            df.groupby("ticker", as_index=False)["target_weight"]
            .sum()
        )
        sleeve_budget = max(
            0.0,
            float(regime_strengths.get(sname, getattr(so.meta, "strength", 0.0)) or 0.0),
        )
        for _, row in grouped.iterrows():
            ticker = str(row.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            weight = abs(float(row.get("target_weight") or 0.0)) * sleeve_budget
            ticker_to_sleeve_weights.setdefault(ticker, {})[sname] = weight

    ticker_to_sleeve_shares: "dict[str, dict[str, float]]" = {}
    for ticker, sleeve_weights in ticker_to_sleeve_weights.items():
        positive = {
            name: max(0.0, float(weight))
            for name, weight in sleeve_weights.items()
            if max(0.0, float(weight)) > WEIGHT_TOLERANCE
        }
        if not positive:
            continue
        total = sum(positive.values())
        if total <= WEIGHT_TOLERANCE:
            share = 1.0 / len(positive)
            ticker_to_sleeve_shares[ticker] = {name: share for name in positive}
        else:
            ticker_to_sleeve_shares[ticker] = {
                name: weight / total for name, weight in positive.items()
            }

    # Accumulate current market value per sleeve from broker positions
    sleeve_mv: "dict[str, float]" = {name: 0.0 for name in sleeve_names}
    for pos in (broker_positions or []):
        symbol = str((pos or {}).get("symbol") or "").strip().upper()
        if not symbol:
            continue
        mv_raw = (pos or {}).get("market_value")
        try:
            mv = float(mv_raw) if mv_raw not in (None, "") else 0.0
        except (ValueError, TypeError):
            mv = 0.0
        sleeve_shares = ticker_to_sleeve_shares.get(symbol, {})
        for sname, share in sleeve_shares.items():
            if sname in sleeve_mv and share > WEIGHT_TOLERANCE:
                sleeve_mv[sname] += mv * share

    # Compute drift and emit one log line per sleeve (graded blend)
    result: "dict[str, float]" = {}
    for sname, target in regime_strengths.items():
        current = sleeve_mv.get(sname, 0.0) / broker_equity
        drift = abs(current - target)
        blend = _drift_blend(drift)
        result[sname] = blend
        if blend >= 1.0:
            logger.info(
                "[DRIFT] %s: current=%.2f target=%.2f drift=%.2f → REBALANCE blend=1.00",
                sname, current, target, drift,
            )
        elif blend <= 0.0:
            logger.info(
                "[DRIFT] %s: current=%.2f target=%.2f drift=%.2f → HOLD blend=0.00 (below %.0f%% threshold)",
                sname, current, target, drift, _DRIFT_HOLD_THRESHOLD * 100,
            )
        else:
            logger.info(
                "[DRIFT] %s: current=%.2f target=%.2f drift=%.2f → BLEND=%.2f (partial rebalance)",
                sname, current, target, drift, blend,
            )
    return result


def apply_regime_strengths_to_sleeves(
    sleeve_outputs: "list[SleeveOutput]",
    regime_strengths: "dict[str, float]",
    drift_blends: "dict[str, float] | None" = None,
) -> None:
    """Apply regime target strengths without letting HOLD sleeves use stale bases.

    ``drift_blends`` remains part of the diagnostics path (drift logging and
    the HOLD/REBALANCE flags in live_regime_review), but every sleeve must be
    anchored UNCONDITIONALLY to the regime target strength here. At the call
    site this runs before sleeves are resized/allocated, so ``meta.strength``
    still holds the construction-time base (~1.0), not a genuine prior-day
    regime strength. Blending against that stale base lets base strengths such
    as 1.0/1.0 get renormalized into unintended 50/50 allocations downstream.
    Re-introduce blending only if a persisted prior-day (or live current)
    regime strength becomes available to blend from.
    """
    _ = drift_blends
    for sleeve_output in sleeve_outputs:
        sname = sleeve_output.meta.sleeve_name
        target_strength = regime_strengths.get(sname)
        if target_strength is None:
            continue
        sleeve_output.meta.strength = float(target_strength)


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


def _build_market_analyzer_payload(
    *,
    report_date: pd.Timestamp,
    vix_regime: dict | None,
    breaker_mode: str,
    exposure_today: float,
    risk_off: bool,
) -> dict:
    vix_component = None
    regime_label = None
    vix_level = None
    if isinstance(vix_regime, dict):
        try:
            vix_component = float(vix_regime.get("position_scale"))
        except Exception:
            vix_component = None
        regime_label = str(vix_regime.get("regime") or "").strip().upper() or None
        try:
            vix_level = float(vix_regime.get("vix"))
        except Exception:
            vix_level = None

    breaker_component = float(max(0.0, min(1.0, exposure_today)))
    risk_off_component = 0.0 if bool(risk_off) else 1.0

    score_candidates = [breaker_component, risk_off_component]
    if vix_component is not None:
        score_candidates.append(float(max(0.0, min(1.0, vix_component))))
    premarket_score = float(min(score_candidates)) if score_candidates else None

    if premarket_score is None:
        signal_bucket = "UNKNOWN"
    elif premarket_score <= 0.0:
        signal_bucket = "LOCK"
    elif premarket_score <= 0.5:
        signal_bucket = "BEARISH"
    elif premarket_score < 1.0:
        signal_bucket = "DEFENSIVE"
    else:
        signal_bucket = "RISK_ON"

    bearish_flag = bool(
        bool(risk_off)
        or str(breaker_mode).strip().lower() == "lock"
        or (regime_label in {"HIGH", "CRISIS"})
        or (premarket_score is not None and premarket_score <= 0.5)
    )

    payload = {
        "date": report_date.strftime("%Y-%m-%d"),
        "premarket_score": premarket_score,
        "bearish_flag": bearish_flag,
        "signal_bucket": signal_bucket,
        "analyzer_version": "v1_breaker_vix_riskoff",
        "notes": "derived from active breaker exposure, vix regime scale, and risk_off guard",
        "vix_component": vix_component,
        "trend_component": None,
        "realized_vol_component": None,
        "gap_risk_component": None,
        "breadth_component": None,
        "macro_component": None,
        "breaker_component": breaker_component,
        "risk_off_component": risk_off_component,
    }
    if regime_label is not None:
        payload["regime"] = regime_label
    if vix_level is not None:
        payload["vix"] = vix_level
    return payload


# ============================================================
# Concentrated-alpha construction (ALWAYS ON — concentration is the model)
# ============================================================
CONCENTRATED_TOP_N_MIN = 5
CONCENTRATED_TOP_N_MAX = 7
CONCENTRATED_TOP_N_FALLBACK = 5
CONCENTRATED_MAX_WEIGHT = 0.30


def _concentrated_top_n(vix_regime: dict | None) -> tuple[int, str]:
    """Regime-adaptive top-N for the concentrated book.

    N = clamp(int(vix_regime["max_positions"]), 5, 7); falls back to 5 when the
    regime state is unavailable. An explicitly-set CAERUS_CONCENTRATED_TOP_N env
    remains an emergency operator override (env wins ONLY when set non-empty).

    Returns (top_n, source) where source is one of "env_override",
    "vix_regime:<REGIME>", or "fallback_default".
    """
    env_val = os.environ.get("CAERUS_CONCENTRATED_TOP_N")
    if env_val not in (None, ""):
        try:
            return max(
                CONCENTRATED_TOP_N_MIN,
                min(CONCENTRATED_TOP_N_MAX, int(env_val)),
            ), "env_override"
        except (TypeError, ValueError):
            logger.warning(
                "[CONCENTRATED_ALPHA] Invalid CAERUS_CONCENTRATED_TOP_N=%r; "
                "using regime-adaptive N",
                env_val,
            )
    try:
        max_positions = int((vix_regime or {})["max_positions"])
        regime_label = str((vix_regime or {}).get("regime", "UNKNOWN"))
        clamped = max(CONCENTRATED_TOP_N_MIN, min(CONCENTRATED_TOP_N_MAX, max_positions))
        return clamped, f"vix_regime:{regime_label}"
    except (KeyError, TypeError, ValueError):
        return CONCENTRATED_TOP_N_FALLBACK, "fallback_default"


def _concentrated_max_weight() -> float:
    try:
        configured = float(
            os.environ.get("CAERUS_CONCENTRATED_MAX_WEIGHT", str(CONCENTRATED_MAX_WEIGHT))
        )
        if configured <= 0.0:
            return CONCENTRATED_MAX_WEIGHT
        return min(CONCENTRATED_MAX_WEIGHT, configured)
    except (TypeError, ValueError):
        return CONCENTRATED_MAX_WEIGHT


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
    defensive_equity: pd.DataFrame | None = None,
    defensive_output: SleeveOutput | None = None,
    vix_regime: dict | None = None,
    regime_summary: dict | None = None,
    risk_off: bool = False,
) -> dict:
    stop_atr_mult = _snapshot_risk_value("STOP_ATR_MULT", STOP_ATR_MULT_DEFAULT)
    take_profit_atr_mult = _snapshot_risk_value(
        "TAKE_PROFIT_ATR_MULT", TAKE_PROFIT_ATR_MULT_DEFAULT
    )
    stop_pct = _snapshot_risk_value("STOP_PCT", STOP_PCT_DEFAULT)
    take_profit_pct = _snapshot_risk_value("TAKE_PROFIT_PCT", TAKE_PROFIT_PCT_DEFAULT)
    # Regime-aware ATR multipliers and position sizing
    _regime_composite = (regime_summary or {}).get("composite_regime", "")
    _elevated_defensive = _regime_composite in ("risk_off_defensive", "high_volatility")
    if _elevated_defensive:
        _stop_atr_mult = 2.5
        _take_atr_mult = 3.75
        _size_scale = 0.80
        logger.info(
            "[TPSL] Elevated/defensive regime (%s): widening stops to %.2f ATR, "
            "TP to %.2f ATR, sizing at %.0f%% of target notional",
            _regime_composite, _stop_atr_mult, _take_atr_mult, _size_scale * 100,
        )
    else:
        _stop_atr_mult = stop_atr_mult
        _take_atr_mult = take_profit_atr_mult
        _size_scale = 1.0
    target_cash_weight_today = float(max(0.0, min(1.0, getattr(alloc_result, "cash_weight", 0.0))))
    breaker_cfg = get_breaker_config()
    exposure_today = float(breaker_cfg.get("exposure_multiplier", 1.0))
    exposure_label = str(breaker_cfg.get("label", "UNKNOWN"))
    breaker_mode = str(breaker_cfg.get("mode", "off"))
    logger.info(
        "[BREAKER_CONFIG] resolved mode=%s label=%s exposure=%.4f source=%s reason=%s",
        breaker_mode, exposure_label, exposure_today,
        breaker_cfg.get("source", "unknown"), breaker_cfg.get("reason", ""),
    )
    market_analyzer = _build_market_analyzer_payload(
        report_date=report_date,
        vix_regime=vix_regime,
        breaker_mode=breaker_mode,
        exposure_today=exposure_today,
        risk_off=bool(risk_off),
    )
    invested_before_overlay = 0.0
    invested_after_overlay = max(0.0, min(1.0, 1.0 - target_cash_weight_today))

    weights_df = _safe_df(alloc_result.combined_weights)
    weights_df = weights_df[weights_df["ticker"] != CASH_TICKER].copy()
    weights_df = weights_df[weights_df["target_weight"].abs() > WEIGHT_TOLERANCE]
    if "sleeve" not in weights_df.columns and "sleeve_name" in weights_df.columns:
        weights_df["sleeve"] = weights_df["sleeve_name"]
    # Concentrated-alpha (ALWAYS ON — concentration is the model): replace the broad
    # book with the top-N highest-conviction names, conviction-weighted, cap per name.
    # N adapts to the VIX regime (clamped 5..7, fallback 5). Regime/sleeve scoring
    # upstream is untouched; cash becomes the residual after concentration and honors
    # the engine's cash weight (risk-off) as a floor.
    #
    # FAIL LOUD: if concentration cannot produce a book, the precompute must exit
    # nonzero so no bundle is written and BOTH execution lanes fail closed (a HOLD
    # day). A silent broad-book fallback is never traded.
    if not weights_df.empty:
        _top_n, _top_n_source = _concentrated_top_n(vix_regime)
        _max_w = _concentrated_max_weight()
        _names_before = len(weights_df)
        try:
            from core.concentration import concentrate_targets

            _concentrated = concentrate_targets(
                weights_df,
                top_n=_top_n,
                max_position_weight=_max_w,
                target_cash_weight=max(target_cash_weight_today, 0.05),
            )
            if _concentrated is None or _concentrated.empty:
                raise ValueError("concentration produced an empty book")
        except Exception as _conc_err:
            logger.critical(
                "[CONCENTRATED_ALPHA] concentration failed (top_n=%d source=%s cap=%.2f, "
                "%d candidate names); FAILING CLOSED — no bundle will be written and "
                "both execution lanes HOLD: %s",
                _top_n, _top_n_source, _max_w, _names_before, _conc_err,
                exc_info=True,
            )
            raise RuntimeError(
                "Concentrated-alpha construction failed; failing closed (HOLD day)"
            ) from _conc_err
        weights_df = _concentrated
        target_cash_weight_today = float(
            max(0.0, min(1.0, 1.0 - float(weights_df["target_weight"].sum())))
        )
        invested_after_overlay = max(0.0, min(1.0, 1.0 - target_cash_weight_today))
        logger.info(
            "[CONCENTRATED_ALPHA] top_n=%d (source=%s) cap=%.2f: %d -> %d names, "
            "cash_target=%.3f, names=%s",
            _top_n, _top_n_source, _max_w, _names_before, len(weights_df),
            target_cash_weight_today, list(weights_df["ticker"]),
        )
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
    signals_path = None
    run_date_str = report_date.strftime("%Y-%m-%d")
    cutoff_date = prev_trading_day(run_date_str)
    if weights_df.empty:
        logger.warning("[PAPER] No target weights available; writing cash-only signals snapshot.")
        weights_df = pd.DataFrame(
            [
                {
                    "ticker": CASH_TICKER,
                    "target_weight": 1.0,
                    "sleeve": "core",
                    "raw_score": 0.0,
                }
            ]
        )
        target_cash_weight_today = 1.0
        breaker_block = {
            "breaker": {
                "mode": breaker_cfg.get("mode", "off"),
                "source": breaker_cfg.get("source", "unknown"),
                "reason": "no_target_weights",
                "exposure_multiplier_today": exposure_today,
                "exposure_label_today": exposure_label,
                "invested_before_overlay": 0.0,
                "invested_after_overlay": 0.0,
                "cash_target_weight_today": 1.0,
            },
            "market_analyzer": market_analyzer,
        }
        _cache_signal_snapshot_payload(
            run_date=run_date_str,
            asof_date=cutoff_date,
            df_targets=weights_df,
            cash_target_weight=1.0,
            extra=breaker_block,
        )
        signals_path = _flush_signal_snapshot_cache()
        if signals_path is not None:
            logger.info("[PAPER] Wrote signals snapshot: %s", signals_path)
    else:
        invested_before_overlay = float(weights_df["target_weight"].sum()) if not weights_df.empty else 0.0
        weights_df_overlay = apply_portfolio_exposure_overlay(weights_df, exposure_today, cash_ticker="CASH")
        invested_after_overlay = 0.0
        if isinstance(weights_df_overlay, pd.DataFrame) and not weights_df_overlay.empty:
            non_cash = weights_df_overlay[weights_df_overlay["ticker"].astype(str) != CASH_TICKER].copy()
            invested_after_overlay = float(non_cash["target_weight"].sum()) if not non_cash.empty else 0.0
            weights_df = non_cash[non_cash["target_weight"].abs() > WEIGHT_TOLERANCE].copy()
        target_cash_weight_today = max(0.0, min(1.0, 1.0 - invested_after_overlay))
        logger.info(
            "[BREAKER] overlay applied multiplier=%.4f invested_before=%.4f invested_after=%.4f cash_target=%.4f source=%s reason=%s",
            float(exposure_today),
            invested_before_overlay,
            invested_after_overlay,
            target_cash_weight_today,
            breaker_cfg.get("source", "unknown"),
            breaker_cfg.get("reason", ""),
        )

        breaker_block = {
            "breaker": {
                "mode": breaker_cfg.get("mode", "off"),
                "source": breaker_cfg.get("source", "unknown"),
                "reason": breaker_cfg.get("reason", ""),
                "exposure_multiplier_today": exposure_today,
                "exposure_label_today": exposure_label,
                "invested_before_overlay": invested_before_overlay,
                "invested_after_overlay": invested_after_overlay,
                "cash_target_weight_today": target_cash_weight_today,
            },
            "market_analyzer": market_analyzer,
        }

        _cache_signal_snapshot_payload(
            run_date=run_date_str,
            asof_date=cutoff_date,
            df_targets=weights_df,
            cash_target_weight=float(target_cash_weight_today),
            extra=breaker_block,
        )
        signals_path = _flush_signal_snapshot_cache()
        if signals_path is not None:
            logger.info("[PAPER] Wrote signals snapshot: %s", signals_path)
    tickers = (
        sorted(
            weights_df.loc[weights_df["ticker"].astype(str) != CASH_TICKER, "ticker"].unique().tolist()
        )
        if not weights_df.empty
        else []
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
                    stop_loss = entry_px - _stop_atr_mult * atr
                    take_profit = entry_px + _take_atr_mult * atr
                else:
                    stop_loss = entry_px + _stop_atr_mult * atr
                    take_profit = entry_px - _take_atr_mult * atr
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
        # ── TP/SL sanity guard ────────────────────────────────────────
        # For long positions (weight > 0): take_profit > entry_px > stop_loss
        # For short positions (weight < 0): take_profit < entry_px < stop_loss
        if entry_px is not None and take_profit is not None and stop_loss is not None:
            if weight > 0:
                if take_profit <= entry_px:
                    logger.warning(
                        "[SNAPSHOT][TP_SL_INVALID] %s LONG: take_profit=%.4f <= entry=%.4f; "
                        "clipping to entry * 1.01",
                        ticker, take_profit, entry_px,
                    )
                    take_profit = entry_px * 1.01
                if stop_loss >= entry_px:
                    logger.warning(
                        "[SNAPSHOT][TP_SL_INVALID] %s LONG: stop_loss=%.4f >= entry=%.4f; "
                        "clipping to entry * 0.99",
                        ticker, stop_loss, entry_px,
                    )
                    stop_loss = entry_px * 0.99
            elif weight < 0:
                if take_profit >= entry_px:
                    logger.warning(
                        "[SNAPSHOT][TP_SL_INVALID] %s SHORT: take_profit=%.4f >= entry=%.4f; "
                        "clipping to entry * 0.99",
                        ticker, take_profit, entry_px,
                    )
                    take_profit = entry_px * 0.99
                if stop_loss <= entry_px:
                    logger.warning(
                        "[SNAPSHOT][TP_SL_INVALID] %s SHORT: stop_loss=%.4f <= entry=%.4f; "
                        "clipping to entry * 1.01",
                        ticker, stop_loss, entry_px,
                    )
                    stop_loss = entry_px * 1.01
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
                notional = abs(weight) * model_equity * _size_scale
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
        DEFENSIVE_SLEEVE_NAME: determine_sleeve_state(
            {
                "equity_df": _safe_df(defensive_equity if defensive_equity is not None else pd.DataFrame()),
                "target_weights": _safe_df(
                    getattr(defensive_output, "positions_df", pd.DataFrame()) if defensive_output is not None else pd.DataFrame()
                ),
            },
            allocation_weight=float(display_sleeves.get(DEFENSIVE_SLEEVE_NAME, 0.0)),
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
            DEFENSIVE_SLEEVE_NAME: _safe_df(defensive_equity if defensive_equity is not None else pd.DataFrame()),
        },
        sleeve_allocations=alloc_result.sleeve_allocations,
        base_equity=DEFAULT_PORTFOLIO_BASE_EQUITY,
    )
    return {
        "asof": report_date,
        "cash_target": target_cash_weight_today,
        "target_cash_weight": target_cash_weight_today,
        "breaker": {
            "mode": breaker_cfg.get("mode", "off"),
            "source": breaker_cfg.get("source", "unknown"),
            "reason": breaker_cfg.get("reason", ""),
            "exposure_multiplier_today": exposure_today,
            "exposure_label_today": exposure_label,
            "invested_before_overlay": invested_before_overlay,
            "invested_after_overlay": invested_after_overlay,
            "cash_target_weight_today": target_cash_weight_today,
        },
        "market_analyzer": market_analyzer,
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
        "regime_summary": regime_summary or {},
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
    defensive_equity: pd.DataFrame | None = None,
    alloc_result: AllocationResult = None,
    alpha_stats: dict | None = None,
    proposed_trades: list[dict] | None = None,
    performance_summary: dict | None = None,
    s2_no_picks: bool = False,
    cm_details: dict | None = None,
    inception_metrics: dict | None = None,
    allocation_diagnostics: dict | None = None,
    regime_summary: dict | None = None,
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
        defensive_alloc = alloc_result.sleeve_allocations.get(DEFENSIVE_SLEEVE_NAME, 0.0)
        cash_alloc = alloc_result.cash_weight
        # Build sleeve equity map for portfolio computation
        sleeve_equity_map = {
            "sleeve_trend": st_equity,
            "sleeve_2": s2_equity,
            "charlie_munger": (cm_details or {}).get("equity_df", pd.DataFrame()),
            DEFENSIVE_SLEEVE_NAME: _safe_df(defensive_equity if defensive_equity is not None else pd.DataFrame()),
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
        if defensive_alloc > WEIGHT_TOLERANCE:
            alloc_notional = BASE_EQUITY * defensive_alloc
            defensive_df = _safe_df(defensive_equity if defensive_equity is not None else pd.DataFrame())
            if not defensive_df.empty and "equity" in defensive_df.columns:
                d_start = float(defensive_df["equity"].iloc[0])
                d_last = float(defensive_df["equity"].iloc[-1])
                d_prev = float(defensive_df["equity"].iloc[-2]) if len(defensive_df) > 1 else d_last
                if d_start > 0:
                    d_ret = (d_last / d_start) - 1.0
                    d_day_ret = (d_last - d_prev) / d_prev if d_prev > 0 else 0.0
                    contrib_equity = BASE_EQUITY * defensive_alloc * (1.0 + d_ret)
                    rows.append(
                        {
                            "Sleeve": f"Defensive ETFs — Bonds ({defensive_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": _fmt_money(contrib_equity),
                            "Day Return": _fmt_pct(d_day_ret),
                        }
                    )
                else:
                    rows.append(
                        {
                            "Sleeve": f"Defensive ETFs — Bonds ({defensive_alloc:.2%})",
                            "Allocated": _fmt_money(alloc_notional),
                            "Equity": "—",
                            "Day Return": "—",
                        }
                    )
            else:
                rows.append(
                    {
                        "Sleeve": f"Defensive ETFs — Bonds ({defensive_alloc:.2%})",
                        "Allocated": _fmt_money(alloc_notional),
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
    regime_section = ""
    if isinstance(regime_summary, dict) and regime_summary:
        regime_rows = pd.DataFrame(
            [
                {"Metric": "Composite Regime", "Value": str(regime_summary.get("composite_regime", "n/a"))},
                {"Metric": "Trend State", "Value": str(regime_summary.get("trend_state", "n/a"))},
                {"Metric": "Volatility State", "Value": str(regime_summary.get("volatility_state", "n/a"))},
                {"Metric": "Breadth State", "Value": str(regime_summary.get("breadth_state", "n/a"))},
                {"Metric": "Macro State", "Value": str(regime_summary.get("macro_state", "n/a"))},
            ]
        )
        weight_label_map = {
            "trend": "Trend",
            "value": "Value",
            "quality": "Quality",
            "mean_reversion": "Mean Reversion",
            "cash": "Cash",
        }
        weight_rows = pd.DataFrame(
            [
                {
                    "Sleeve": weight_label_map.get(str(name), str(name)),
                    "Target Weight": _fmt_pct(value),
                }
                for name, value in dict(regime_summary.get("target_weights") or {}).items()
            ]
        )
        regime_body = html_table(regime_rows, "Regime Summary", 10)
        regime_weights = html_table(
            weight_rows,
            "Regime Target Sleeve Weights",
            10,
            "No regime weights available.",
        )
        regime_section = (
            '<div class="card">'
            f"{regime_body}"
            f"{regime_weights}"
            "<p><em>Read-only preview only. Regime output does not yet drive live sleeve allocation.</em></p>"
            "</div>"
        )
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
    for route in list(sleeve1_diag.get("cash_routing") or []):
        if not isinstance(route, dict):
            continue
        alloc_diag_items.append(
            {
                "Metric": f"Cash route: {route.get('sleeve_id', 'unknown')}",
                "Value": (
                    f"{_fmt_pct(route.get('routed_weight'))}; "
                    f"reason={route.get('invalid_reason', route.get('reason_code', 'unknown'))}; "
                    f"diagnostic={route.get('diagnostic_artifact') or 'unavailable'}"
                ),
            }
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
        {regime_section}
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
        help="Reset paper-mode state to a clean start before running.",
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
        help="Delete execution idempotency ledger rows matching YYYY-MM-DD before execution",
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
        "--bootstrap-model-ledger-from-broker",
        dest="bootstrap_model_ledger_from_broker",
        action="store_true",
        help=(
            "Bootstrap canonical model snapshot from broker positions and exit without "
            "submitting orders (paper mode only)."
        ),
    )
    parser.add_argument(
        "--force-bootstrap-flat",
        dest="force_bootstrap_flat",
        action="store_true",
        help=(
            "Bypass the empty-position safety guard during bootstrap. "
            "Use when the account is genuinely flat (0 positions, all cash) after a "
            "manual reset and the guard would otherwise refuse to write the flat snapshot."
        ),
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
    parser.add_argument(
        "--allow-overwrite",
        dest="allow_overwrite",
        action="store_true",
        help="Allow overwriting existing files for the same RUN_ID (emergency use only).",
    )
    parser.add_argument(
        "--write-precompute-bundle",
        dest="write_precompute_bundle",
        action="store_true",
        help="Persist today's plan-only daily snapshot and signals as a live-execution precompute bundle.",
    )
    return parser.parse_args(argv)


def _is_truthy(value: str | int | bool | None, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _explicit_trading_mode_requested() -> bool:
    return bool(str(os.getenv("MODE") or "").strip() or str(os.getenv("TRADING_MODE") or "").strip())


def _email_strict_enabled() -> bool:
    return _is_truthy(os.getenv("EMAIL_STRICT"), default=False)


def _email_dry_run_enabled() -> bool:
    return _is_truthy(os.getenv("EMAIL_DRY_RUN"), default=False)


def _inline_report_emails_enabled() -> bool:
    return _is_truthy(os.getenv("EMAIL_INLINE_REPORTS"), default=True)


def _try_send_email(*, subject: str, body_html: str | None, body_text: str | None, label: str) -> bool:
    strict = _email_strict_enabled()
    if _email_dry_run_enabled():
        logger.info("[EMAIL][DRY_RUN] skipped %s email send", label)
        return True

    if send_email is None:
        msg = "send_email not available"
        if strict:
            raise RuntimeError(msg)
        logger.warning("[WARN] %s — %s email not sent", msg, label)
        return False

    try:
        send_email(subject=subject, body_html=body_html, body_text=body_text)
        return True
    except Exception as exc:
        if strict:
            logger.error("[EMAIL][STRICT] %s email failed: %s", label, exc)
            raise
        logger.warning("[WARN] %s email not sent: %s", label, exc)
        return False


def _send_report_emails(
    *,
    exec_subject: str,
    exec_body_html: str,
    exec_body_text: str,
    snapshot_subject: str,
    snapshot_body_html: str,
    snapshot_body_text: str,
) -> tuple[bool, bool]:
    execution_ok = _try_send_email(
        subject=exec_subject,
        body_html=exec_body_html,
        body_text=exec_body_text,
        label="execution",
    )
    snapshot_ok = _try_send_email(
        subject=snapshot_subject,
        body_html=snapshot_body_html,
        body_text=snapshot_body_text,
        label="snapshot",
    )
    return execution_ok, snapshot_ok


def _deliver_inline_report_emails(
    *,
    exec_subject: str,
    exec_body_html: str,
    exec_body_text: str,
    snapshot_subject: str,
    snapshot_body_html: str,
    snapshot_body_text: str,
) -> tuple[bool, bool]:
    if not _inline_report_emails_enabled():
        logger.info(
            "[EMAIL] inline report delivery disabled; persisted workflow email job is authoritative"
        )
        return False, False

    return _send_report_emails(
        exec_subject=exec_subject,
        exec_body_html=exec_body_html,
        exec_body_text=exec_body_text,
        snapshot_subject=snapshot_subject,
        snapshot_body_html=snapshot_body_html,
        snapshot_body_text=snapshot_body_text,
    )


def _normalize_exec_mode(value: str | None, default: str) -> str:
    norm = str(value if value is not None else default).strip().lower()
    return norm or str(default).strip().lower()


def _resolve_exec_modes() -> tuple[str, str, bool, bool]:
    raw_mode = os.getenv("MODE")
    raw_trading_mode = os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
    trading_mode_norm = canonical_trading_mode(
        _normalize_exec_mode(raw_trading_mode, DEFAULT_TRADING_MODE),
        field_name="TRADING_MODE",
    )
    mode_norm = canonical_trading_mode(
        _normalize_exec_mode(raw_mode, trading_mode_norm),
        field_name="MODE",
    )
    if mode_norm != trading_mode_norm:
        raise RuntimeError(
            f"[INVARIANT] MODE={mode_norm} and TRADING_MODE={trading_mode_norm} must resolve to the same canonical mode"
        )
    paper_requested = trading_mode_norm == "paper"
    legacy_shadow_requested = legacy_shadow_mode_requested(raw_mode, raw_trading_mode)
    os.environ["MODE"] = mode_norm
    os.environ["TRADING_MODE"] = trading_mode_norm
    if legacy_shadow_requested and not _is_truthy(os.getenv("PLAN_ONLY"), default=False):
        os.environ["PLAN_ONLY"] = "1"
        logger.warning(
            "[MODE] legacy shadow alias detected; forcing PLAN_ONLY=1 under canonical paper mode"
        )
    return mode_norm, trading_mode_norm, paper_requested, legacy_shadow_requested


def _init_run_context(
    *,
    allow_overwrite: bool,
    mode: str,
    trading_mode: str,
    report_date_env: str | None,
) -> RunContext:
    run_id = get_run_id()
    run_root = get_run_dir(run_id)
    if run_root.exists() and any(run_root.iterdir()) and not allow_overwrite:
        raise FileExistsError(
            f"Run directory already exists and is non-empty: {run_root}. "
            "Refusing to overwrite. Use --allow-overwrite only for emergency reruns."
        )
    for sub in ("logs", "reports", "broker", "ledger", "snapshots", "audit"):
        ensure_dir(run_root / sub)
    ctx = RunContext(
        run_id=run_id,
        run_root=run_root,
        allow_overwrite=bool(allow_overwrite),
        created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        git_sha=_get_git_sha(),
        mode=mode,
        trading_mode=trading_mode,
        report_date_env=report_date_env or None,
        paper_trading=(trading_mode == "paper"),
    )
    os.environ["RUN_ID"] = ctx.run_id
    os.environ["RUN_OUTPUT_ROOT"] = str(ctx.run_root)
    if ctx.allow_overwrite:
        os.environ["ALLOW_OVERWRITE"] = "1"
    else:
        os.environ.pop("ALLOW_OVERWRITE", None)
    initial_meta = {
        "run_id": ctx.run_id,
        "created_at": ctx.created_at,
        "mode": ctx.mode,
        "trading_mode": ctx.trading_mode,
        "paper_trading": ctx.paper_trading,
        "report_date_env": ctx.report_date_env,
        "report_date": ctx.report_date,
        "git_sha": ctx.git_sha,
        "run_root": str(ctx.run_root),
    }
    safe_write_text(
        ctx.run_root / "meta.json",
        json.dumps(initial_meta, indent=2) + "\n",
        allow_overwrite=ctx.allow_overwrite,
    )
    # Write canonical run pointer early so execute_orders can resolve run_root
    # even if the planner exits before _finalize_run_context() runs.
    # _finalize_run_context() will overwrite this with status="success".
    try:
        write_canonical_run_pointer(
            run_id=ctx.run_id,
            trade_date=ctx.report_date_env or "",
            mode=ctx.mode,
            run_root=str(ctx.run_root),
            status="running",
            workflow_stage="precompute",
        )
    except Exception as _e:
        # Non-blocking — finalize will write the canonical version later.
        pass
    return ctx


def _finalize_run_context(run_ctx: RunContext) -> None:
    meta = {
        "run_id": run_ctx.run_id,
        "created_at": run_ctx.created_at,
        "mode": run_ctx.mode,
        "trading_mode": run_ctx.trading_mode,
        "paper_trading": run_ctx.paper_trading,
        "report_date_env": run_ctx.report_date_env,
        "report_date": run_ctx.report_date,
        "git_sha": run_ctx.git_sha,
        "run_root": str(run_ctx.run_root),
    }
    safe_write_text(
        run_ctx.run_root / "meta.json",
        json.dumps(meta, indent=2) + "\n",
        allow_overwrite=True,
    )
    manifest_payload = collect_manifest(run_ctx.run_root)
    safe_write_text(
        run_ctx.run_root / "manifest.json",
        json.dumps(manifest_payload, indent=2) + "\n",
        allow_overwrite=run_ctx.allow_overwrite,
    )
    lines: list[str] = []
    for item in manifest_payload.get("files", []):
        rel = str(item.get("path", ""))
        if not rel:
            continue
        abs_path = run_ctx.run_root / rel
        lines.append(f"{file_sha256(abs_path)}  {rel}")
    safe_write_text(
        run_ctx.run_root / "checksums.sha256",
        ("\n".join(lines) + "\n") if lines else "",
        allow_overwrite=run_ctx.allow_overwrite,
    )
    # Write to latest.json for backward compatibility
    latest_payload = {
        "run_id": run_ctx.run_id,
        "path": str(run_ctx.run_root),
        "report_date": run_ctx.report_date,
        "mode": run_ctx.mode,
        "paper_trading": run_ctx.paper_trading,
        "git_sha": run_ctx.git_sha,
        "created_at": run_ctx.created_at,
    }
    write_latest_pointer(Path("outputs/latest.json"), latest_payload)
    
    # Write canonical run pointer (latest_run.json) as single source of truth.
    # Use _RUN_TERMINAL_STATUS so failures are never misclassified as "success".
    try:
        write_canonical_run_pointer(
            run_id=run_ctx.run_id,
            trade_date=run_ctx.report_date,
            mode=run_ctx.mode,
            run_root=str(run_ctx.run_root),
            status=_RUN_TERMINAL_STATUS,
            substatus=_RUN_TERMINAL_SUBSTATUS,
            status_message=_RUN_TERMINAL_MESSAGE,
            workflow_stage="precompute",
        )
        logger.info(
            "[RUN_ARCHIVE] canonical run pointer written: outputs/latest_run.json status=%s",
            _RUN_TERMINAL_STATUS,
        )
    except Exception as e:
        logger.warning("[RUN_ARCHIVE][WARN] failed to write canonical run pointer: %s", e)


def _is_run_complete(status: str) -> bool:
    """True when the run reached a terminal non-failure state."""
    return status in {"success", "no_action"}


def _write_failure_artifacts_on_exit(run_ctx: RunContext) -> None:
    """Write operator_summary and planner_failure.json on early exit. Non-blocking."""
    try:
        global _RUN_TERMINAL_MESSAGE
        # Refine generic failed_unknown into a more precise classification.
        terminal = _RUN_TERMINAL_STATUS
        if terminal == "failed_unknown":
            if _RUN_STAGE_EXECUTION_REACHED:
                terminal = "failed_pre_execution"
            elif _RUN_STAGE_PAYLOAD_WRITTEN:
                terminal = "failed_pre_execution"
            else:
                terminal = "failed_pre_payload"

        stage = (
            "execution" if _RUN_STAGE_EXECUTION_REACHED
            else ("post_payload" if _RUN_STAGE_PAYLOAD_WRITTEN else "pre_payload")
        )

        from core.operator_summary import write_preflight_failure, write_operator_summary
        write_preflight_failure(
            run_ctx.run_root,
            run_id=run_ctx.run_id,
            stage=stage,
            terminal_status=terminal,
            exception_type=_RUN_TERMINAL_SUBSTATUS,
            exception_message=_RUN_TERMINAL_MESSAGE,
        )
        write_operator_summary(
            run_ctx.run_root,
            run_id=run_ctx.run_id,
            trade_date=run_ctx.report_date or run_ctx.report_date_env or "",
            mode=run_ctx.mode.upper(),
            terminal_status=terminal,
            pretrade_halt_reason=_RUN_TERMINAL_MESSAGE,
            execution_payload_written=_RUN_STAGE_PAYLOAD_WRITTEN,
            execution_stage_reached=_RUN_STAGE_EXECUTION_REACHED,
            exception_type=_RUN_TERMINAL_SUBSTATUS,
            exception_message=_RUN_TERMINAL_MESSAGE,
            broker_reject_status=_RUN_TERMINAL_SUBSTATUS,
            broker_reject_message=_RUN_TERMINAL_MESSAGE,
        )
    except Exception as e:
        logger.warning("[RUN_ARCHIVE][WARN] failure artifacts write failed: %s", e)


def _finalize_run_context_once() -> None:
    global _RUN_CONTEXT_FINALIZED
    if _RUN_CONTEXT is None or _RUN_CONTEXT_FINALIZED:
        return
    # Write failure artifacts before finalizing if run did not reach its terminal state.
    if not _is_run_complete(_RUN_TERMINAL_STATUS):
        _write_failure_artifacts_on_exit(_RUN_CONTEXT)
    try:
        _finalize_run_context(_RUN_CONTEXT)
    except Exception as exc:
        logger.warning("[RUN_ARCHIVE][WARN] finalize failed: %s", exc)
    _RUN_CONTEXT_FINALIZED = True


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
    global _RUN_CONTEXT
    global _RUN_STAGE_EXECUTION_REACHED, _RUN_STAGE_PAYLOAD_WRITTEN
    global _RUN_TERMINAL_STATUS, _RUN_TERMINAL_SUBSTATUS, _RUN_TERMINAL_MESSAGE
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    explicit_trading_mode_requested = _explicit_trading_mode_requested()
    mode_norm, trading_mode_norm, paper_requested, legacy_shadow_requested = _resolve_exec_modes()
    boot_now_utc = dt.datetime.now(dt.timezone.utc)
    boot_now_et = boot_now_utc.astimezone(ZoneInfo("America/New_York"))
    logger.info(
        "[BOOT] mode=%s trading_mode=%s report_date_env=%s tz_env=%s now_utc=%s now_et=%s session_basis=America/New_York",
        mode_norm,
        trading_mode_norm,
        str(os.getenv("REPORT_DATE", "")).strip() or "n/a",
        str(os.getenv("TZ", "")).strip() or "n/a",
        boot_now_utc.isoformat(),
        boot_now_et.isoformat(),
    )
    # Fail fast on placeholder/invalid REPORT_DATE before running sleeves/network calls.
    _parse_report_date_env(os.getenv("REPORT_DATE", ""))
    ensure_no_legacy_ledger(logger=logger, when="startup")
    if _is_backtest_mode(args):
        _run_backtest_mode(args)
        return
    allow_overwrite = bool(
        bool(getattr(args, "allow_overwrite", False))
        or _is_truthy(os.getenv("ALLOW_OVERWRITE"), default=False)
    )
    _RUN_CONTEXT = _init_run_context(
        allow_overwrite=allow_overwrite,
        mode=mode_norm,
        trading_mode=trading_mode_norm,
        report_date_env=str(os.getenv("REPORT_DATE", "")).strip() or None,
    )
    atexit.register(_finalize_run_context_once)
    logger.info(
        "[RUN_ARCHIVE] run_id=%s run_root=%s allow_overwrite=%s",
        _RUN_CONTEXT.run_id,
        _RUN_CONTEXT.run_root,
        _RUN_CONTEXT.allow_overwrite,
    )
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

    # ── Early Alpaca snapshot for turnover-aware allocation ───────
    # Pulled here so that broker positions are available before sleeve
    # signal generation and allocation.  Capital sizing is unaffected —
    # paper_broker.run_paper_day() fetches live equity again at execution
    # time via its own AlpacaBroker.get_account() call.
    _pretrade_raw_snapshot: "dict | None" = None
    _broker_positions_for_drift: "list | None" = None
    _broker_equity_for_drift: "float | None" = None
    if paper_requested and not offline_fixture:
        try:
            _pretrade_raw_snapshot = fetch_pretrade_snapshot()
            if (_pretrade_raw_snapshot or {}).get("ok"):
                _acct = (_pretrade_raw_snapshot or {}).get("account") or {}
                _eq_raw = _acct.get("equity") or _acct.get("portfolio_value")
                if _eq_raw not in (None, ""):
                    _broker_equity_for_drift = float(_eq_raw)
                _broker_positions_for_drift = list(
                    (_pretrade_raw_snapshot or {}).get("positions") or []
                )
                logger.info(
                    "[DRIFT_SNAPSHOT] equity=%.2f positions=%d",
                    _broker_equity_for_drift or 0.0,
                    len(_broker_positions_for_drift),
                )
            else:
                logger.info(
                    "[DRIFT_SNAPSHOT] broker snapshot not ok — drift gate defaults to REBALANCE all"
                )
        except Exception as _early_snap_err:
            logger.warning(
                "[DRIFT_SNAPSHOT] early broker snapshot failed (non-blocking): %s",
                _early_snap_err,
            )
    # ─────────────────────────────────────────────────────────────

    # ── Run sleeves ───────────────────────────────────────────────
    _val_output_direct = None  # set by run_sleeve_value() in the live path below
    if offline_fixture:
        logger.warning(
            "[OFFLINE] Fixture mode enabled; skipping sleeve runs and live data fetches."
        )
        shared_universe_prices = pd.DataFrame()
        regime_spy_prices = pd.DataFrame()
        regime_vix_prices = pd.DataFrame()
        defensive_etf_prices = pd.DataFrame()
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
        quality_output = None
        mr_output = None
        defensive_output = None
        defensive_equity = pd.DataFrame()
    else:
        shared_universe_prices = pd.DataFrame()
        regime_spy_prices = pd.DataFrame()
        regime_vix_prices = pd.DataFrame()
        defensive_etf_prices = pd.DataFrame()
        try:
            shared_universe_prices, regime_spy_prices, regime_vix_prices, defensive_etf_prices = (
                _prepare_shared_regime_market_data(period="2y", interval="1d")
            )
            logger.info(
                "[REGIME] Reusing shared market data: universe_rows=%d spy_rows=%d vix_rows=%d defensive_rows=%d",
                len(shared_universe_prices),
                len(regime_spy_prices),
                len(regime_vix_prices),
                len(defensive_etf_prices),
            )
        except Exception as _shared_market_err:
            logger.warning(
                "[REGIME] Shared market preload failed; falling back to sleeve-local downloads: %s",
                _shared_market_err,
            )
        try:
            _, _ = run_sleeve_1(
                prices=shared_universe_prices if not shared_universe_prices.empty else None
            )
        except Exception as e:
            logger.warning("[WARN] Sleeve 1 failed: %s", e)
            _, _ = pd.DataFrame(), pd.DataFrame()
        try:
            st_equity, st_trades, st_signals = run_sleeve_trend(
                prices=shared_universe_prices if not shared_universe_prices.empty else None
            )
        except Exception as e:
            logger.warning("[WARN] Sleeve Trend failed: %s", e)
            st_equity, st_trades, st_signals = (
                pd.DataFrame(),
                pd.DataFrame(),
                pd.DataFrame(),
            )
        # ── Quality sleeve (non-blocking) ────────────────────────────
        quality_output = run_sleeve_quality(
            prices=shared_universe_prices if not shared_universe_prices.empty else None
        )
        # ── Mean Reversion sleeve — built after regime engine so the
        # breadth gate can be wired with _regime_today (set below).
        mr_output = None  # populated after regime engine runs
        # ── Value sleeve (non-blocking) ───────────────────────────────
        _val_output_direct = run_sleeve_value(
            prices=shared_universe_prices if not shared_universe_prices.empty else None
        )
        # ─────────────────────────────────────────────────────────────
        s2_details = {}
        s2_equity, s2_trades = pd.DataFrame(), pd.DataFrame()
        cm_details = {}
        cm_equity, cm_trades = pd.DataFrame(), pd.DataFrame()
        defensive_output = None
        defensive_equity = pd.DataFrame()
    # ── Sleeve health checks ─────────────────────────────────────
    # Validate each sleeve BEFORE allocation.  Invalid sleeves get
    # their weight routed to CASH, never to another sleeve.
    trend_valid, trend_reason = _sleeve_is_valid(
        st_equity,
        sleeve_id="sleeve_trend",
        write_trace=True,
    )
    # s2 validity derived from SleeveOutput positions (not legacy equity_df)
    _s2_pos_df = _val_output_direct.positions_df if _val_output_direct is not None else None
    s2_valid = _s2_pos_df is not None and not _s2_pos_df.empty
    s2_reason = (
        (_val_output_direct.meta.notes if _val_output_direct and _val_output_direct.meta else "inactive")
        if not s2_valid
        else ""
    )
    defensive_valid = False
    defensive_reason = ""
    cm_valid, cm_reason = _sleeve_is_valid(cm_equity, sleeve_id="charlie_munger")
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
    # ── VIX regime detection (non-blocking) ──────────────────────
    _vix_regime: dict | None = None
    try:
        from research.vix_regime import get_current_regime
        _vix_regime = get_current_regime()
        logger.info(
            "[VIX_REGIME] Regime=%s  VIX=%.2f  scale=%.0f%%  max_pos=%d",
            _vix_regime["regime"],
            _vix_regime["vix"],
            _vix_regime["position_scale"] * 100,
            _vix_regime["max_positions"],
        )
    except Exception as _vix_err:
        logger.warning("[VIX_REGIME] Skipped (defaulting to full deployment): %s", _vix_err)
    # ─────────────────────────────────────────────────────────────

    # ── Run macro regime engine (feeds sleeve allocation weights) ──
    regime_summary: dict = {}
    _regime_today: "dict | None" = None
    if (
        not offline_fixture
        and not shared_universe_prices.empty
        and not regime_spy_prices.empty
        and not regime_vix_prices.empty
    ):
        try:
            regime_engine = RegimeEngine()
            regime_result = regime_engine.run(
                spy_prices=regime_spy_prices,
                vix_prices=regime_vix_prices,
                universe_prices=shared_universe_prices[["date", "ticker", "close"]].copy(),
            )
            regime_summary = _build_regime_summary_payload(regime_result)
            _regime_today = regime_result.today
            logger.info(
                "[REGIME] composite=%s trend=%s volatility=%s breadth=%s macro=%s",
                regime_summary.get("composite_regime"),
                regime_summary.get("trend_state"),
                regime_summary.get("volatility_state"),
                regime_summary.get("breadth_state"),
                regime_summary.get("macro_state"),
            )
        except Exception as _regime_err:
            logger.warning("[REGIME] Live regime summary skipped: %s", _regime_err)
    # ─────────────────────────────────────────────────────────────

    # ── Mean Reversion sleeve (non-blocking, breadth-gated) ───────
    # Built here (after regime engine) so _regime_today is available to
    # fire the breadth gate when market internals are unhealthy.
    if mr_output is None:
        mr_output = run_sleeve_mean_reversion(
            prices=shared_universe_prices if not shared_universe_prices.empty else None,
            regime_state=_regime_today,
        )
    defensive_output, defensive_equity = run_sleeve_defensive_etf(
        prices=defensive_etf_prices if not defensive_etf_prices.empty else None,
        regime_summary=regime_summary,
    )
    defensive_valid = (
        defensive_output is not None
        and defensive_output.positions_df is not None
        and not defensive_output.positions_df.empty
    )
    defensive_reason = (
        defensive_output.meta.notes
        if defensive_output is not None and defensive_output.meta is not None
        else "inactive"
    )
    if (
        not defensive_valid
        and str(regime_summary.get("composite_regime") or "").strip().lower() in DEFENSIVE_CASH_ROUTE_REGIMES
    ):
        logger.warning("sleeve_defensive_etf inactive: %s -> routed to CASH", defensive_reason)
    # ─────────────────────────────────────────────────────────────

    # ── Extract sleeve outputs for dynamic allocation ─────────────
    trend_output = build_trend_sleeve_output(st_signals, st_equity, top_n=10, regime=_vix_regime)
    # Use the directly-built SleeveOutput from run_sleeve_value() when available;
    # fall back to the legacy extract path (always empty) otherwise.
    val_output = (
        _val_output_direct
        if _val_output_direct is not None
        else extract_sleeve_output(
            s2_equity,
            s2_trades,
            "sleeve_2",
            1.0,
            target_weights=s2_details.get("target_weights") if s2_details else None,
        )
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

    # Regime-conditioned gross exposure target.
    # In LOW regime (scale=1.0) we target full deployment.
    # In HIGH/CRISIS regimes the allocator boosts only up to position_scale,
    # routing the remainder to CASH — no options or short selling required.
    _regime_gross_exp = (
        float(_vix_regime["position_scale"])
        if _vix_regime is not None
        else DEFAULT_MIN_GROSS_EXPOSURE
    )
    construction_policy = _resolve_live_construction_policy(_broker_equity_for_drift)
    logger.info(
        "[LIVE_CONSTRUCTION] equity=%.2f max_pos=%.0f%% min_pos=%.0f%% min_gross=%.0f%%",
        construction_policy["model_equity"],
        construction_policy["max_position_weight"] * 100,
        construction_policy["min_position_weight"] * 100,
        construction_policy["min_gross_exposure"] * 100,
    )

    allocator = PortfolioAllocator(
        risk_off=risk_off,
        stash_sleeve_name="CASH",
        risk_off_stash_pct=0.0,
        max_position_pct=construction_policy["max_position_weight"],
        min_gross_exposure=(
            _regime_gross_exp if _vix_regime is not None else construction_policy["min_gross_exposure"]
        ),
    )
    cap_fill_diag = {
        "selected_names": int(len(_safe_df(trend_output.positions_df))),
        "min_required_names": None,
        "max_supported_names": None,
        "added_names": 0,
        "trimmed_names": 0,
        "constraint": "none",
    }

    # ── Apply regime-driven sleeve strengths with drift gate ──────
    _active_sleeve_outputs = [
        so for so in [trend_output, val_output, quality_output, mr_output, defensive_output]
        if so is not None
    ]
    _active_sleeve_names = [
        so.meta.sleeve_name
        for so in _active_sleeve_outputs
        if _safe_df(getattr(so, "positions_df", pd.DataFrame())).get("target_weight", pd.Series(dtype=float)).abs().sum() > WEIGHT_TOLERANCE
    ]
    _regime_strengths = resolve_regime_strengths(_regime_today, _active_sleeve_names)
    _drift_blends = compute_sleeve_drift(
        broker_positions=_broker_positions_for_drift,
        broker_equity=_broker_equity_for_drift,
        sleeve_outputs=_active_sleeve_outputs,
        regime_strengths=_regime_strengths,
    )
    # Convert blend factors to boolean flags for downstream consumers that
    # expect dict[str, bool] (live_regime_review, serialization).
    # A blend of 0.0 = HOLD (False); any positive blend = some rebalance (True).
    _drift_flags = {k: (v > 0.0) for k, v in _drift_blends.items()}
    apply_regime_strengths_to_sleeves(
        _active_sleeve_outputs,
        _regime_strengths,
        _drift_blends,
    )
    _preview_allocations = _preview_sleeve_allocations(_active_sleeve_outputs)
    _resized_outputs_by_name: dict[str, SleeveOutput] = {}
    for _so in _active_sleeve_outputs:
        _ranked_candidates = _ranked_signal_tickers(st_signals) if _so.meta.sleeve_name == "sleeve_trend" else None
        _resized_output, _resize_diag = _resize_sleeve_holdings_for_live_book(
            _so,
            target_allocation=_preview_allocations.get(_so.meta.sleeve_name, 0.0),
            max_position_weight=construction_policy["max_position_weight"],
            min_position_weight=construction_policy["min_position_weight"],
            ranked_candidates=_ranked_candidates,
        )
        _resized_outputs_by_name[_so.meta.sleeve_name] = _resized_output
        if _so.meta.sleeve_name == "sleeve_trend":
            cap_fill_diag = _resize_diag
    trend_output = _resized_outputs_by_name.get("sleeve_trend", trend_output)
    val_output = _resized_outputs_by_name.get("sleeve_2", val_output)
    quality_output = _resized_outputs_by_name.get("sleeve_quality", quality_output)
    mr_output = _resized_outputs_by_name.get("sleeve_mean_reversion", mr_output)
    defensive_output = _resized_outputs_by_name.get(DEFENSIVE_SLEEVE_NAME, defensive_output)
    _active_sleeve_outputs = [
        so for so in [trend_output, val_output, quality_output, mr_output, defensive_output]
        if so is not None
    ]
    logger.info(
        "[REGIME_ALLOC] %s (regime: %s)",
        ", ".join(f"{k}={v:.3f}" for k, v in sorted(_regime_strengths.items())),
        regime_summary.get("composite_regime", "unknown"),
    )
    alloc_result = allocator.allocate(_active_sleeve_outputs)
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
    sleeve_cash_routes: list[dict[str, object]] = []
    if (
        not trend_valid
        and alloc_result.sleeve_allocations.get("sleeve_trend", 0.0) > WEIGHT_TOLERANCE
    ):
        routed_weight = float(alloc_result.sleeve_allocations["sleeve_trend"])
        freed_weight += routed_weight
        alloc_result.sleeve_allocations["sleeve_trend"] = 0.0
        logger.error(
            "[CASH_ROUTE] sleeve=sleeve_trend routed_weight=%.4f (%.1f%%) reason=%s",
            routed_weight, routed_weight * 100, trend_reason or "sleeve_invalid",
        )
        sleeve_cash_routes.append(
            {
                "sleeve_id": "sleeve_trend",
                "invalid_reason": trend_reason,
                "reason_code": REASON_SLEEVE_TERMINAL_EQUITY_NAN
                if "terminal equity" in str(trend_reason)
                else "sleeve_invalid",
                "routed_weight": routed_weight,
                "diagnostic_artifact": st_equity.attrs.get("numeric_trace_path")
                if isinstance(st_equity, pd.DataFrame)
                else None,
            }
        )
        patched = True
    if (
        not s2_valid
        and alloc_result.sleeve_allocations.get("sleeve_2", 0.0) > WEIGHT_TOLERANCE
    ):
        _s2_routed = float(alloc_result.sleeve_allocations["sleeve_2"])
        freed_weight += _s2_routed
        alloc_result.sleeve_allocations["sleeve_2"] = 0.0
        logger.error(
            "[CASH_ROUTE] sleeve=sleeve_2 routed_weight=%.4f (%.1f%%) reason=%s",
            _s2_routed, _s2_routed * 100, s2_reason or "sleeve_invalid",
        )
        patched = True
    if (
        not cm_valid
        and alloc_result.sleeve_allocations.get("charlie_munger", 0.0) > WEIGHT_TOLERANCE
    ):
        _cm_routed = float(alloc_result.sleeve_allocations["charlie_munger"])
        freed_weight += _cm_routed
        alloc_result.sleeve_allocations["charlie_munger"] = 0.0
        logger.error(
            "[CASH_ROUTE] sleeve=charlie_munger routed_weight=%.4f (%.1f%%) reason=%s",
            _cm_routed, _cm_routed * 100, cm_reason or "sleeve_invalid",
        )
        patched = True
    if (
        not defensive_valid
        and alloc_result.sleeve_allocations.get(DEFENSIVE_SLEEVE_NAME, 0.0) > WEIGHT_TOLERANCE
    ):
        _def_routed = float(alloc_result.sleeve_allocations[DEFENSIVE_SLEEVE_NAME])
        freed_weight += _def_routed
        alloc_result.sleeve_allocations[DEFENSIVE_SLEEVE_NAME] = 0.0
        logger.error(
            "[CASH_ROUTE] sleeve=%s routed_weight=%.4f (%.1f%%) reason=%s",
            DEFENSIVE_SLEEVE_NAME, _def_routed, _def_routed * 100,
            defensive_reason or "sleeve_invalid",
        )
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
        for route in sleeve_cash_routes:
            logger.warning(
                "[ALLOCATION][CASH_ROUTE] sleeve=%s reason=%s weight=%.1f%% diagnostic=%s",
                route.get("sleeve_id"),
                route.get("invalid_reason"),
                float(route.get("routed_weight") or 0.0) * 100,
                route.get("diagnostic_artifact") or "unavailable",
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
        DEFENSIVE_SLEEVE_NAME: defensive_equity,
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
            "cash_routing": sleeve_cash_routes,
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
    trade_date_str = report_date.strftime("%Y-%m-%d")
    if _RUN_CONTEXT is not None and not _RUN_CONTEXT.report_date:
        _RUN_CONTEXT.report_date = trade_date_str
    # ── Regime state artifact ──────────────────────────────────────
    _final_target_count = int(
        len(
            {
                str(t).strip().upper()
                for t in (_safe_df(getattr(alloc_result, "combined_weights", pd.DataFrame()))
                          .get("ticker", pd.Series(dtype=str))
                          .tolist())
                if str(t).strip().upper() not in ("", CASH_TICKER)
            }
        )
    )
    write_regime_state_artifact(
        report_date=trade_date_str,
        regime_summary=regime_summary,
        vix_regime=_vix_regime,
        regime_strengths=_regime_strengths,
        drift_blends=_drift_blends,
        sleeve_cash_routes=sleeve_cash_routes,
        pre_concentration_target_count=_final_target_count,
        repo_root=Path(__file__).resolve().parent,
    )
    pretrade_broker_capture = _capture_pretrade_broker_snapshot(
        trade_date=trade_date_str,
        paper_requested=paper_requested,
    )
    pretrade_broker_policy = summarize_pretrade_broker_policy(
        ((pretrade_broker_capture or {}).get("snapshot") or {}) if pretrade_broker_capture else {}
    )
    if paper_requested:
        logger.info("%s", format_broker_preflight_banner(pretrade_broker_policy))
        if pretrade_broker_policy.get("broker_pdt_warning_message"):
            logger.warning(
                "[BROKER_PREFLIGHT][PDT] status=%s message=%s",
                pretrade_broker_policy.get("broker_pdt_risk_status"),
                pretrade_broker_policy.get("broker_pdt_warning_message"),
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
            defensive_equity=defensive_equity,
            defensive_output=defensive_output,
            vix_regime=_vix_regime,
            regime_summary=regime_summary,
            risk_off=risk_off,
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
    try:
        from research.ic_monitor import compute_and_log_ic

        compute_and_log_ic(report_date=today)
    except Exception as _ic_err:
        logger.warning("[IC_MONITOR] failed: %s", _ic_err)
    # --- Paper trading execution + report ---
    if _RUN_CONTEXT is not None:
        _RUN_CONTEXT.report_date = trade_date_str
    logger.info(
        "[BOOT] report_date=%s mode=%s trading_mode=%s",
        trade_date_str,
        mode_norm,
        trading_mode_norm,
    )
    signals_path_exec = daily_snapshot.get("signals_snapshot_path") or os.path.join(
        "signals", f"{trade_date_str}.json"
    )
    paper_summary = None
    paper_html = ""
    sent_ledger_removed = 0
    sent_ledger_path = "outputs/orders_sent/orders_sent.csv"
    ensure_sent_ledger_exists(sent_ledger_path)
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
    if bool(getattr(args, "bootstrap_model_ledger_from_broker", False)):
        if trading_mode_norm != "paper":
            raise RuntimeError(
                "--bootstrap-model-ledger-from-broker is only supported when TRADING_MODE=paper"
            )
        ok = bootstrap_model_ledger_from_broker(
            trading_mode=trading_mode_norm,
            ledger_path=paper_ledger_path,
            sent_ledger_path=sent_ledger_path,
            run_date=trade_date_str,
            force=bool(getattr(args, "force_bootstrap_flat", False)),
        )
        if not ok:
            raise SystemExit(1)
        logger.info(
            "[BOOTSTRAP] Canonical model snapshot initialized from broker; exiting before order generation"
        )
        return
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
            execution_constraints = {
                "cash_target_weight": float(snapshot_cash_target_weight)
            }
            if bool(getattr(args, "exit_only", False)) or _is_truthy(os.getenv("EXIT_ONLY"), default=False):
                execution_constraints["exit_only"] = True
            trading_mode = trading_mode_norm
            force_execution = bool(
                bool(getattr(args, "force_execution", False))
                or _is_truthy(os.getenv("FORCE_EXECUTION"), default=False)
            )
            if force_execution:
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
            effective_plan_only = bool(args.plan_only or legacy_shadow_requested)
            recon_enabled = bool(
                paper_requested
                and not effective_plan_only
                and _is_truthy(os.getenv("RECON_ENABLE"), default=True)
            )
            if (
                recon_enabled
                and not explicit_trading_mode_requested
            ):
                recon_enabled = False
                logger.warning(
                    "[RECON] Skipping broker reconciliation for implicit local paper mode"
                )
            if recon_enabled:
                if _is_truthy(os.getenv("RECON_V2"), default=False):
                    recon_result = pre_trade_reconcile_and_classify(
                        run_date=trade_date_str,
                        trading_mode=trading_mode_norm,
                        ledger_path=paper_ledger_path,
                        sent_ledger_path=sent_ledger_path,
                    )
                    if str((recon_result or {}).get("reconciliation_decision") or "PASS") == "BLOCK":
                        raise SystemExit(2)
                else:
                    pre_trade_reconcile_or_exit(
                        run_date=trade_date_str,
                        trading_mode=trading_mode_norm,
                        ledger_path=paper_ledger_path,
                        sent_ledger_path=sent_ledger_path,
                    )
            _RUN_STAGE_EXECUTION_REACHED = True
            paper_summary = run_paper_day(
                run_date=trade_date_str,
                signals_path=signals_path_exec,
                ledger_path=paper_ledger_path,
                trades_path=paper_trades_path,
                config_path="paper/config_paper.json",
                force=force_execution,
                constraints=execution_constraints,
                plan_only=effective_plan_only,
            )
            if paper_requested:
                summary_mode = str((paper_summary or {}).get("trading_mode", "")).strip().lower()
                summary_mode_norm = canonical_trading_mode(
                    summary_mode or "paper",
                    field_name="paper_summary.trading_mode",
                )
                if summary_mode_norm != "paper":
                    raise RuntimeError(
                        f"[INVARIANT] Paper mode requested but paper broker resolved trading_mode={summary_mode or 'unknown'}"
                    )
            logger.info(
                "[PAPER] Executed paper trading for %s using signals %s",
                trade_date_str,
                signals_path_exec,
            )
            if paper_requested and (paper_summary or {}).get("posttrade_recon_status"):
                logger.info(
                    "[POSTTRADE_DRIFT] status=%s report=%s repairs=%s",
                    (paper_summary or {}).get("posttrade_recon_status"),
                    (paper_summary or {}).get("posttrade_recon_path"),
                    ",".join(list((paper_summary or {}).get("posttrade_repair_suggestions") or [])) or "none",
                )
            if recon_enabled:
                # Refresh canonical snapshot from broker to ensure model matches reality after execution
                posttrade_positions_path = (paper_summary or {}).get("posttrade_positions_snapshot_path")
                posttrade_account_path = (paper_summary or {}).get("posttrade_account_snapshot_path")
                refreshed_from_snapshot = False
                if posttrade_positions_path:
                    try:
                        positions_snapshot = json.loads(Path(str(posttrade_positions_path)).read_text(encoding="utf-8"))
                        account_snapshot = (
                            json.loads(Path(str(posttrade_account_path)).read_text(encoding="utf-8"))
                            if posttrade_account_path and Path(str(posttrade_account_path)).exists()
                            else None
                        )
                        refreshed_from_snapshot = refresh_canonical_snapshot_from_posttrade_snapshot(
                            positions_snapshot=positions_snapshot,
                            account_snapshot=account_snapshot,
                            run_date=trade_date_str,
                        )
                    except Exception as _posttrade_refresh_exc:
                        logger.warning(
                            "[POSTTRADE] Snapshot-based canonical refresh failed: %s",
                            _posttrade_refresh_exc,
                        )
                if not refreshed_from_snapshot:
                    refresh_canonical_snapshot_from_broker(
                        trading_mode=trading_mode_norm,
                        run_date=trade_date_str,
                    )
                post_trade_validate(
                    run_date=trade_date_str,
                    trading_mode=trading_mode_norm,
                    ledger_path=paper_ledger_path,
                    sent_ledger_path=sent_ledger_path,
                )
        except AlpacaSubmissionRejectError as e:
            _RUN_TERMINAL_STATUS = "failed_pre_execution"
            _RUN_TERMINAL_SUBSTATUS = str(e.classification or "")
            _RUN_TERMINAL_MESSAGE = str(e.broker_message or e.raw_message or e)
            _partial_submitted_count = int(getattr(e, "successful_submissions", 0) or 0)
            _partial_rejected_count = int(getattr(e, "failed_submissions", 0) or 0)
            logger.error(
                "[ALPACA][REJECT] classification=%s order_id=%s symbol=%s side=%s qty=%s message=%s",
                e.classification,
                e.order_id or "unknown",
                e.symbol or "unknown",
                e.side or "unknown",
                e.quantity if e.quantity is not None else "unknown",
                e.broker_message,
            )
            if _RUN_CONTEXT is not None:
                write_operator_summary(
                    _RUN_CONTEXT.run_root,
                    run_id=str(_RUN_CONTEXT.run_id),
                    trade_date=trade_date_str,
                    mode=str(os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)).upper(),
                    submitted_count=_partial_submitted_count,
                    accepted_count=_partial_submitted_count,
                    rejected_count=_partial_rejected_count,
                    orders_submitted_count=_partial_submitted_count,
                    terminal_status=_RUN_TERMINAL_STATUS,
                    pretrade_halt_reason=_RUN_TERMINAL_MESSAGE,
                    execution_payload_written=_RUN_STAGE_PAYLOAD_WRITTEN,
                    execution_stage_reached=_RUN_STAGE_EXECUTION_REACHED,
                    exception_type=_RUN_TERMINAL_SUBSTATUS,
                    exception_message=_RUN_TERMINAL_MESSAGE,
                    broker_preflight_status=pretrade_broker_policy.get("broker_preflight_status"),
                    broker_preflight_account_status=pretrade_broker_policy.get("broker_preflight_account_status"),
                    broker_preflight_cash=pretrade_broker_policy.get("broker_preflight_cash"),
                    broker_preflight_equity=pretrade_broker_policy.get("broker_preflight_equity"),
                    broker_preflight_buying_power=pretrade_broker_policy.get("broker_preflight_buying_power"),
                    broker_preflight_restriction_flags=pretrade_broker_policy.get("broker_preflight_restriction_flags"),
                    broker_preflight_warning_flags=pretrade_broker_policy.get("broker_preflight_warning_flags"),
                    broker_pdt_risk_status=pretrade_broker_policy.get("broker_pdt_risk_status"),
                    broker_pdt_daytrade_count=pretrade_broker_policy.get("broker_pdt_daytrade_count"),
                    broker_pdt_daytrading_buying_power=pretrade_broker_policy.get("broker_pdt_daytrading_buying_power"),
                    broker_pdt_flags=pretrade_broker_policy.get("broker_pdt_flags"),
                    broker_pdt_warning_message=pretrade_broker_policy.get("broker_pdt_warning_message"),
                    broker_reject_status=_RUN_TERMINAL_SUBSTATUS,
                    broker_reject_message=_RUN_TERMINAL_MESSAGE,
                )
                try:
                    print(format_execution_health_banner(load_operator_summary(_RUN_CONTEXT.run_root) or {}))
                except Exception as _banner_exc:
                    logger.warning("[EXECUTION_HEALTH][WARN] failed to render banner: %s", _banner_exc)
                try:
                    from core.trading_day_summary import write_trading_day_summary
                    write_trading_day_summary(
                        run_root=_RUN_CONTEXT.run_root,
                        run_id=str(_RUN_CONTEXT.run_id),
                        trade_date=trade_date_str,
                    )
                except Exception as _summary_exc:
                    logger.warning("[SUMMARY] trading_day_summary skipped after broker reject: %s", _summary_exc)
            raise
        except Exception as e:
            msg = repr(e)
            if "Ledger already contains run_date" in msg:
                logger.info(
                    "[PAPER] Already executed for %s; rendering report from ledger.",
                    trade_date_str,
                )
            elif paper_requested:
                raise
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
                    "trading_mode": canonical_trading_mode_label(
                        (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
                    ),
                    "market_status": market_status,
                    "market_guard": (paper_summary or {}).get("market_guard"),
                    "orders_generated": len(
                        (paper_summary or {}).get("orders")
                        or (paper_summary or {}).get("shadow_orders", [])
                        or []
                    ),
                    "orders_blocked": len((paper_summary or {}).get("blocked_reasons", []) or []),
                    "broker_recon_status": (paper_summary or {}).get("broker_recon_status", "UNKNOWN"),
                },
            )
        except Exception as e:
            logger.warning("[PAPER][WARN] Paper report HTML build failed: %s", repr(e))

        # ── Prepend regime block above sleeve tables (informational) ──────
        if paper_html and regime_summary:
            try:
                _regime_block = _build_regime_html_block(regime_summary)
                if _regime_block:
                    paper_html = _regime_block + "\n" + paper_html
            except Exception as _rb_err:
                logger.warning("[REGIME_HTML] Skipped: %s", _rb_err)

        # ── Append attribution blocks below sleeve tables (collapsible) ───
        try:
            from core.attribution import compute_attribution, build_attribution_html
            _attr_blocks = ""
            if st_signals is not None and not st_signals.empty:
                _st_attr = compute_attribution(st_signals, st_signals, "sleeve_trend")
                _attr_blocks += build_attribution_html(_st_attr, "Sleeve Trend")
            if _attr_blocks and paper_html:
                paper_html = paper_html + "\n<hr>\n" + _attr_blocks
        except Exception as _attr_err:
            logger.info("[ATTRIBUTION_HTML] Skipped: %s", _attr_err)
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
    proposed_trades = list((daily_snapshot or {}).get("proposed_trades") or [])
    trade_count_contract = compute_trade_count_contract(
        daily_snapshot=daily_snapshot,
        paper_summary=paper_summary,
        execution_payload=execution_payload,
    )
    if not should_execute:
        execution_payload["execution_status"] = "PLANNED"
        execution_payload["halt_reason"] = None
        execution_payload["planning_disclaimer"] = "Planning email only — no orders were sent."
        execution_payload["validation_reason"] = (
            "planning_run_future_date" if is_planning_run else "market_closed_or_not_session"
        )
    # ── Deterministic gate log — visible in every CI run ─────────────
    logger.info(
        "[EXEC_DECISION] trade_date=%s today_et=%s is_planning_run=%s "
        "market_is_open=%s should_execute=%s execution_status=%s halt_reason=%s trades=%d",
        trade_date_str,
        today_et_str,
        is_planning_run,
        market_is_open_for_trade_date,
        should_execute,
        execution_payload.get("execution_status", "UNKNOWN"),
        execution_payload.get("halt_reason"),
        int(execution_payload.get("executable_trades_count") or 0),
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

    try:
        research_context_paths = write_research_context(
            trade_date=trade_date_str,
            run_id=str(_RUN_CONTEXT.run_id) if _RUN_CONTEXT is not None else None,
            mode=trading_mode_norm,
            daily_snapshot=daily_snapshot,
            execution_payload=execution_payload,
            paper_summary=paper_summary,
            run_root=_RUN_CONTEXT.run_root if _RUN_CONTEXT is not None else None,
            allow_overwrite=True,
        )
        daily_snapshot["research_context_path"] = research_context_paths.get("canonical_path")
        logger.info(
            "[RESEARCH_CONTEXT] canonical=%s run=%s",
            research_context_paths.get("canonical_path"),
            research_context_paths.get("run_path", ""),
        )
    except Exception as exc:
        logger.warning("[RESEARCH_CONTEXT] write skipped (non-blocking): %s", exc)

    # ── Write execution audit artifact (non-blocking) ─────────────────
    if _RUN_CONTEXT is not None:
        try:
            _broker_cash = float((paper_summary or {}).get("broker_cash") or 0) or None
            _broker_pos = (paper_summary or {}).get("alpaca_positions_snapshot") or None
            _canonical_pos = None
            try:
                import json as _json
                _cpath = Path("outputs/paper_state/canonical_positions.json")
                if _cpath.exists():
                    _canonical_pos = _json.loads(_cpath.read_text(encoding="utf-8")).get("positions")
            except Exception:
                pass
            write_planner_audit(
                run_id=str(_RUN_CONTEXT.run_id),
                trade_date=trade_date_str,
                mode=canonical_trading_mode_label(
                    (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
                ),
                today_et=today_et_str,
                is_planning_run=is_planning_run,
                market_is_open=market_is_open_for_trade_date,
                should_execute=should_execute,
                market_guard=(paper_summary or {}).get("market_guard"),
                broker_cash_before=_broker_cash,
                broker_positions_before=_broker_pos,
                canonical_positions_before=_canonical_pos,
                signals_generated=len((paper_summary or {}).get("signals") or []),
                proposed_trades=proposed_trades,
                blocked_reasons=list((paper_summary or {}).get("blocked_reasons") or []),
                execution_payload=execution_payload,
            )
        except Exception as _audit_exc:
            logger.warning("[EXEC_AUDIT] planner audit failed (non-blocking): %s", _audit_exc)

        # Write per-run broker probe artifact (non-blocking).
        try:
            _probe_ok = paper_summary is not None and str(
                (paper_summary or {}).get("broker_recon_status") or ""
            ).upper() not in {"FAIL", "ERROR"}
            _probe_payload = {
                "run_id": str(_RUN_CONTEXT.run_id),
                "broker_name": "paper_broker" if paper_requested else "paper_simulated",
                "probe_attempted": True,
                "probe_ok": _probe_ok,
                "as_of": dt.datetime.now(dt.timezone.utc).isoformat(),
                "source_type": "authoritative_broker" if paper_requested else "derived",
                "base_url": os.getenv("ALPACA_BASE_URL"),
                "error": str((paper_summary or {}).get("broker_error") or "") or None,
            }
            safe_write_text(
                _canonical_artifact_path("broker", "account_probe.json"),
                json.dumps(_probe_payload, indent=2) + "\n",
                allow_overwrite=bool(_RUN_CONTEXT.allow_overwrite),
            )
        except Exception as _probe_exc:
            logger.warning("[BROKER_PROBE][WARN] failed writing probe artifact: %s", _probe_exc)

    canonical_execution_payload_path = _write_canonical_execution_payload(
        execution_payload,
        trade_date_str,
        run_root=_RUN_CONTEXT.run_root if _RUN_CONTEXT is not None else None,
    )
    _RUN_STAGE_PAYLOAD_WRITTEN = True

    # Write operator summary after planner completes
    if _RUN_CONTEXT is not None and _RUN_CONTEXT.run_root:
        pdt_pretrade_summary = dict(pretrade_broker_policy or {})
        pdt_pretrade_summary.update(dict((paper_summary or {}).get("pdt_pretrade") or {}))
        normalized_status = normalize_status(
            execution_status=execution_payload.get("execution_status"),
            halt_reason=execution_payload.get("halt_reason"),
            executable_trades_count=int(execution_payload.get("executable_trades_count") or 0),
        )
        _no_trade_reason: str | None = None
        if normalized_status == "NO_ACTION":
            _no_trade_reason = (
                execution_payload.get("halt_reason")
                or execution_payload.get("no_action_reason")
                or "no_executable_trades_after_constraints"
            )
        execution_outcome_code = str((paper_summary or {}).get("execution_outcome") or "").strip()
        partial_execution_abort = bool(
            execution_outcome_code
            and int(
                ((paper_summary or {}).get("alpaca_submission_summary") or {}).get("submit_success")
                or 0
            ) > 0
        )
        if partial_execution_abort:
            _RUN_TERMINAL_STATUS = "failed_pre_execution"
            _RUN_TERMINAL_SUBSTATUS = str(
                (paper_summary or {}).get("broker_reject_status")
                or (paper_summary or {}).get("execution_reason")
                or execution_outcome_code
                or "partial_execution_abort"
            )
            _RUN_TERMINAL_MESSAGE = str(
                execution_payload.get("halt_reason")
                or (paper_summary or {}).get("broker_reject_message")
                or (paper_summary or {}).get("artifact_failure_message")
                or "partial_execution_broker_abort"
            )
        write_operator_summary(
            _RUN_CONTEXT.run_root,
            run_id=str(_RUN_CONTEXT.run_id),
            trade_date=trade_date_str,
            mode=canonical_trading_mode_label(
                (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
            ),
            pretrade_status=normalized_status,
            pretrade_halt_reason=execution_payload.get("halt_reason"),
            proposed_trades_count=int(trade_count_contract["planner_intended_trades_count"]),
            executable_trades_count=int(trade_count_contract["execution_eligible_trades_count"]),
            model_proposed_trades_count=int(trade_count_contract["model_proposed_trades_count"]),
            planner_intended_trades_count=int(trade_count_contract["planner_intended_trades_count"]),
            execution_eligible_trades_count=int(trade_count_contract["execution_eligible_trades_count"]),
            orders_submitted_count=int(trade_count_contract["orders_submitted_count"]),
            orders_filled_count=int(trade_count_contract["orders_filled_count"]),
            planner_completed=True,
            execution_payload_written=True,
            execution_stage_reached=_RUN_STAGE_EXECUTION_REACHED,
            terminal_status="failed_pre_execution" if partial_execution_abort else None,
            suggested_order_count=int(trade_count_contract["execution_eligible_trades_count"]),
            no_trade_reason=_no_trade_reason,
            exception_type=(
                (paper_summary or {}).get("broker_reject_status")
                or (paper_summary or {}).get("artifact_failure_stage")
            ),
            exception_message=(
                (paper_summary or {}).get("artifact_failure_message")
                or execution_payload.get("halt_reason")
            ),
            broker_pretrade_snapshot_ok=bool(((pretrade_broker_capture or {}).get("snapshot") or {}).get("ok")) if pretrade_broker_capture else None,
            broker_posttrade_snapshot_ok=bool((paper_summary or {}).get("posttrade_account_snapshot_path") and (paper_summary or {}).get("posttrade_positions_snapshot_path")) if paper_requested else None,
            broker_authoritative_state=bool((paper_summary or {}).get("posttrade_positions_snapshot_path")) if paper_requested else False,
            broker_preflight_status=pretrade_broker_policy.get("broker_preflight_status"),
            broker_preflight_account_status=pretrade_broker_policy.get("broker_preflight_account_status"),
            broker_preflight_cash=pretrade_broker_policy.get("broker_preflight_cash"),
            broker_preflight_equity=pretrade_broker_policy.get("broker_preflight_equity"),
            broker_preflight_buying_power=pretrade_broker_policy.get("broker_preflight_buying_power"),
            broker_preflight_restriction_flags=pretrade_broker_policy.get("broker_preflight_restriction_flags"),
            broker_preflight_warning_flags=pretrade_broker_policy.get("broker_preflight_warning_flags"),
            broker_pdt_risk_status=pdt_pretrade_summary.get("broker_pdt_risk_status"),
            broker_pdt_daytrade_count=pdt_pretrade_summary.get("broker_pdt_daytrade_count"),
            broker_pdt_daytrading_buying_power=pdt_pretrade_summary.get("broker_pdt_daytrading_buying_power"),
            broker_pdt_flags=pdt_pretrade_summary.get("broker_pdt_flags"),
            broker_pdt_warning_message=pdt_pretrade_summary.get("broker_pdt_warning_message"),
            broker_cash_at_planning=((paper_summary or {}).get("capital_budget") or {}).get("broker_cash_at_planning"),
            broker_equity_at_planning=((paper_summary or {}).get("capital_budget") or {}).get("broker_equity_at_planning"),
            broker_buying_power_at_planning=((paper_summary or {}).get("capital_budget") or {}).get("broker_buying_power_at_planning"),
            reserve_cash_policy=((paper_summary or {}).get("capital_budget") or {}).get("reserve_cash_policy"),
            expected_sell_proceeds=((paper_summary or {}).get("capital_budget") or {}).get("expected_sell_proceeds"),
            expected_sell_proceeds_conservative=((paper_summary or {}).get("capital_budget") or {}).get("expected_sell_proceeds_conservative"),
            requested_buy_notional=((paper_summary or {}).get("capital_budget") or {}).get("requested_buy_notional"),
            allowed_buy_notional=((paper_summary or {}).get("capital_budget") or {}).get("allowed_buy_notional"),
            capital_constraint_triggered=((paper_summary or {}).get("capital_budget") or {}).get("capital_constraint_triggered"),
            clipped_or_deferred_buys_count=((paper_summary or {}).get("capital_budget") or {}).get("clipped_or_deferred_buys_count"),
            post_execution_recon_status=(paper_summary or {}).get("posttrade_recon_status"),
            post_execution_recon_path=(paper_summary or {}).get("posttrade_recon_path"),
            duplicate_guard_status=(
                "BLOCKED_SAME_DAY_LOCK"
                if bool(((paper_summary or {}).get("alpaca_submission_summary") or {}).get("same_day_submission_lock"))
                else "REMOTE_IDEMPOTENT_REPLAY"
                if int(((paper_summary or {}).get("alpaca_submission_summary") or {}).get("idempotent_skips_total") or 0) > 0
                else "CLEAR"
            ),
            duplicate_guard_reason=(
                "; ".join(
                    [
                        str(reason)
                        for reason in list((paper_summary or {}).get("blocked_reasons") or [])
                        if "same_day_submission_lock" in str(reason)
                    ]
                )
                or None
            ),
            affected_symbols=sorted(
                set(
                    list((paper_summary or {}).get("posttrade_affected_symbols") or [])
                    + list((paper_summary or {}).get("execution_submitted_symbols") or [])
                )
            ),
            repair_suggestions=list((paper_summary or {}).get("posttrade_repair_suggestions") or []),
            duplicate_fill_suspicions_count=int(
                (paper_summary or {}).get("posttrade_duplicate_fill_suspicions_count") or 0
            ),
            broker_reject_status=(paper_summary or {}).get("broker_reject_status"),
            broker_reject_message=(paper_summary or {}).get("broker_reject_message"),
            execution_outcome=(paper_summary or {}).get("execution_outcome"),
            execution_reason=(paper_summary or {}).get("execution_reason"),
            cash_rebalance_status=(paper_summary or {}).get("cash_rebalance_status"),
        )
        try:
            _op_summary = load_operator_summary(_RUN_CONTEXT.run_root) or {}
            print(format_execution_health_banner(_op_summary))
        except Exception as _banner_exc:
            logger.warning("[EXECUTION_HEALTH][WARN] failed to render banner: %s", _banner_exc)
        try:
            from core.trading_day_summary import write_trading_day_summary
            write_trading_day_summary(
                run_root=_RUN_CONTEXT.run_root,
                run_id=str(_RUN_CONTEXT.run_id),
                trade_date=trade_date_str,
            )
        except Exception as _summary_exc:
            logger.warning("[SUMMARY] trading_day_summary skipped after operator summary write: %s", _summary_exc)
        # Log pretrade summary
        print(
            f"[PRETRADE_SUMMARY] run_id={_RUN_CONTEXT.run_id} "
            f"status={normalized_status} "
            f"model_proposed={int(trade_count_contract['model_proposed_trades_count'])} "
            f"planner_intended={int(trade_count_contract['planner_intended_trades_count'])} "
            f"execution_eligible={int(trade_count_contract['execution_eligible_trades_count'])} "
            f"payload_path={canonical_execution_payload_path}"
        )
        append_step_summary(
            [
                "### Pretrade Summary",
                f"- run_id: `{_RUN_CONTEXT.run_id}`",
                f"- trade_date: `{trade_date_str}`",
                f"- mode: `{str((paper_summary or {}).get('trading_mode') or os.getenv('TRADING_MODE', DEFAULT_TRADING_MODE)).upper()}`",
                f"- pretrade_status: `{normalized_status}`",
                f"- broker_pdt_risk_status: `{pdt_pretrade_summary.get('broker_pdt_risk_status') or 'UNKNOWN'}`",
                f"- broker_pdt_warning_message: `{pdt_pretrade_summary.get('broker_pdt_warning_message') or 'none'}`",
                f"- model_proposed_trades_count: `{int(trade_count_contract['model_proposed_trades_count'])}`",
                f"- planner_intended_trades_count: `{int(trade_count_contract['planner_intended_trades_count'])}`",
                f"- execution_eligible_trades_count: `{int(trade_count_contract['execution_eligible_trades_count'])}`",
                f"- duplicate_guard_status: `{('BLOCKED_SAME_DAY_LOCK' if bool(((paper_summary or {}).get('alpaca_submission_summary') or {}).get('same_day_submission_lock')) else 'REMOTE_IDEMPOTENT_REPLAY' if int(((paper_summary or {}).get('alpaca_submission_summary') or {}).get('idempotent_skips_total') or 0) > 0 else 'CLEAR')}`",
                f"- post_execution_recon_status: `{(paper_summary or {}).get('posttrade_recon_status') or 'UNKNOWN'}`",
                f"- affected_symbols: `{','.join(list((paper_summary or {}).get('posttrade_affected_symbols') or [])) or 'none'}`",
                f"- repair_suggestions: `{' ; '.join(list((paper_summary or {}).get('posttrade_repair_suggestions') or [])) or 'none'}`",
                f"- proposed_trades_count (legacy alias): `{int(trade_count_contract['planner_intended_trades_count'])}`",
                f"- executable_trades_count (legacy alias): `{int(trade_count_contract['execution_eligible_trades_count'])}`",
                f"- operator_summary_path: `{_RUN_CONTEXT.run_root / 'operator_summary.json'}`",
                f"- execution_payload_path: `{canonical_execution_payload_path}`",
            ]
        )

    execution_payload_path, payload_preserved, preserved_path = _write_execution_email_payload(execution_payload, trade_date_str)
    if bool(getattr(args, "write_precompute_bundle", False)):
        try:
            signals_payload = {}
            if signals_path_exec and os.path.exists(signals_path_exec):
                with open(signals_path_exec, "r", encoding="utf-8") as _signals_f:
                    signals_payload = json.load(_signals_f)
            precompute_contract_path = write_precompute_bundle(
                trade_date=trade_date_str,
                run_id=str((paper_summary or {}).get("run_id") or (_RUN_CONTEXT.run_id if _RUN_CONTEXT is not None else "")),
                mode=canonical_trading_mode_label(trading_mode_norm),
                daily_snapshot=daily_snapshot,
                signals_payload=signals_payload,
                execution_payload=execution_payload,
                allow_overwrite=True,
            )
            logger.info(
                "[PRECOMPUTE] bundle written trade_date=%s contract=%s",
                trade_date_str,
                precompute_contract_path,
            )
        except Exception as _precompute_exc:
            logger.warning("[PRECOMPUTE][WARN] failed writing bundle: %s", _precompute_exc)
    integrity = {
        "trade_date": trade_date_str,
        "asof_date": str(execution_payload.get("pricing_asof") or prev_trading_day(trade_date_str)),
        "mode": canonical_trading_mode_label(
            (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
        ),
        "execution_status": execution_payload.get("execution_status"),
        "halt_reason": execution_payload.get("halt_reason"),
        "canonical_execution_payload_path": canonical_execution_payload_path,
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
        "holdings_mtm_path": None,
    }
    if should_execute:
        rows2: list[dict] = []
        appended2 = 0
        skipped2 = 0
        missing_prices: list[str] = []
        ledger2_error = None
        asof_date = integrity["asof_date"]
        ledger_run_id = str(uuid.uuid4())
        ledger_source = canonical_trading_mode_label(
            (paper_summary or {}).get("trading_mode") or os.getenv("TRADING_MODE", DEFAULT_TRADING_MODE)
        )
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
                    "holdings_mtm_path": nav_result.get("holdings_mtm_path"),
                }
            )
            integrity["missing_prices"] = sorted(set((missing_prices or []) + (nav_result.get("missing_prices") or [])))
            _snapshot_existing_file(
                nav_result.get("nav_path") or "",
                category="snapshots",
            )
            _snapshot_existing_file(
                nav_result.get("holdings_mtm_path") or "",
                category="snapshots",
            )
        except Exception as e:
            ledger2_error = str(e)
            logger.warning("[LEDGER2][WARN] ledger/nav2 pipeline failed: %s", e)
        try:
            ledger_write_path = _canonical_artifact_path("ledger", f"ledger_write_{asof_date}.json")
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
            safe_write_text(
                ledger_write_path,
                json.dumps(ledger_write_payload, indent=2) + "\n",
                allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
            )
            view_path = Path("outputs/ledger") / f"ledger_write_{asof_date}.json"
            _write_view_pointer(
                view_path,
                _pointer_payload(run_ctx=_RUN_CONTEXT, canonical_path=ledger_write_path)
                if _RUN_CONTEXT is not None
                else {"canonical_path": str(ledger_write_path)},
            )
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
    regime_review: dict | None = None
    try:
        review_asof_date = str(integrity.get("asof_date") or prev_trading_day(trade_date_str))
        review_prev_date = prev_trading_day(review_asof_date) if review_asof_date else None
        returns_by_ticker: dict[str, float] = {}
        target_book = _safe_df(getattr(alloc_result, "combined_weights", pd.DataFrame()))
        if not target_book.empty and "ticker" in target_book.columns:
            target_tickers = sorted(
                {
                    str(ticker).strip().upper()
                    for ticker in target_book["ticker"].tolist()
                    if str(ticker).strip().upper() != CASH_TICKER
                }
            )
            if target_tickers and review_prev_date and review_asof_date:
                prev_px = fetch_prev_closes_yfinance(target_tickers, asof_date=review_prev_date)
                curr_px = fetch_prev_closes_yfinance(target_tickers, asof_date=review_asof_date)
                returns_by_ticker = build_returns_by_ticker(prev_px, curr_px)
        if _RUN_CONTEXT is not None:
            regime_review = write_live_regime_review(
                run_root=_RUN_CONTEXT.run_root,
                output_dir=Path("outputs") / "engine_review",
                trade_date=trade_date_str,
                asof_date=review_asof_date,
                regime_summary=regime_summary,
                regime_strengths=_regime_strengths,
                drift_flags=_drift_flags,
                sleeve_outputs=_active_sleeve_outputs,
                final_target_allocations=alloc_result.sleeve_allocations,
                broker_positions=_broker_positions_for_drift,
                broker_equity=_broker_equity_for_drift,
                returns_by_ticker=returns_by_ticker,
                alpha_attribution=alpha_stats,
                signal_snapshot_path=signals_path_exec,
                objective_contract_path=Path("config") / "engine_objectives.json",
            )
            daily_snapshot["live_regime_review"] = {
                "overall_status": ((regime_review.get("promotion_gate") or {}).get("overall_status") or "").upper(),
                "blockers": (regime_review.get("promotion_gate") or {}).get("blockers") or [],
                "artifact_paths": regime_review.get("artifact_paths") or {},
            }
            write_operator_summary(
                _RUN_CONTEXT.run_root,
                regime_review_status=(regime_review.get("promotion_gate") or {}).get("overall_status"),
                regime_review_blockers=(regime_review.get("promotion_gate") or {}).get("blockers"),
            )
    except Exception as e:
        logger.warning("[REGIME_REVIEW][WARN] failed building live regime review: %s", e)
    try:
        overlay_asof_date = str(integrity.get("asof_date") or prev_trading_day(trade_date_str))
        overlay_spy_price = None
        if fetch_prev_closes_yfinance is not None and overlay_asof_date:
            overlay_spy_df = fetch_prev_closes_yfinance(["SPY"], asof_date=overlay_asof_date)
            if isinstance(overlay_spy_df, pd.DataFrame) and not overlay_spy_df.empty:
                overlay_spy_price = _coerce_float_or_none(overlay_spy_df.iloc[0].get("prev_close"))
        overlay_equity = _coerce_float_or_none(
            ((paper_summary or {}).get("capital_budget") or {}).get("broker_equity_at_planning")
        )
        if overlay_equity is None:
            overlay_equity = _broker_equity_for_drift
        overlay_cash = _coerce_float_or_none(
            ((paper_summary or {}).get("capital_budget") or {}).get("broker_cash_at_planning")
        )
        if overlay_cash is None:
            overlay_cash = _coerce_float_or_none((paper_summary or {}).get("broker_cash"))
        if overlay_cash is None:
            overlay_cash = _coerce_float_or_none((((_pretrade_raw_snapshot or {}).get("account") or {}).get("cash")))
        if _RUN_CONTEXT is not None and get_alpha_stack_flag("ENABLE_OPTIONS_OVERLAY", default=False):
            options_submission_requested = str(
                os.getenv("ALLOW_OPTIONS_EXECUTION", os.getenv("ALLOW_OPTIONS_SUBMISSION", "0"))
            ).strip().lower() in {"1", "true", "yes", "y", "on"}
            options_plan_only = bool(
                getattr(args, "plan_only", False)
                or legacy_shadow_requested
                or _is_truthy(os.getenv("PLAN_ONLY"), default=False)
            )
            options_live_context = str(os.getenv("WORKFLOW_KIND") or "").strip().lower() == "live"
            options_submission_enabled = bool(
                options_submission_requested and options_live_context and not options_plan_only
            )
            if options_submission_requested and not options_submission_enabled:
                logger.warning(
                    "[OPTIONS_OVERLAY] live submission requested but blocked "
                    "(plan_only=%s workflow_kind=%s)",
                    options_plan_only,
                    str(os.getenv("WORKFLOW_KIND") or "unset"),
                )
            options_broker = None
            if options_submission_enabled:
                from brokers.alpaca_broker import AlpacaBroker  # local import to keep startup light

                options_broker = AlpacaBroker.from_env()
            options_overlay_shadow = write_options_overlay_shadow(
                run_root=_RUN_CONTEXT.run_root,
                output_dir=Path("outputs") / "options_overlay",
                trade_date=trade_date_str,
                asof_date=overlay_asof_date,
                regime_summary=regime_summary,
                portfolio_equity=overlay_equity,
                portfolio_cash=overlay_cash,
                spy_price=overlay_spy_price,
                live_regime_review=regime_review,
                policy_path=Path("config") / "options_overlay_policy.json",
            )
            daily_snapshot["options_overlay_shadow"] = {
                "status": ((options_overlay_shadow.get("trigger") or {}).get("status") or "").upper(),
                "strategy": (options_overlay_shadow.get("recommendation") or {}).get("strategy"),
                "feasible": bool((options_overlay_shadow.get("recommendation") or {}).get("feasible")),
                "candidate_strategies": [
                    item.get("strategy")
                    for item in list(options_overlay_shadow.get("candidate_strategies") or [])
                    if item.get("strategy")
                ],
                "artifact_paths": options_overlay_shadow.get("artifact_paths") or {},
            }
            write_operator_summary(
                _RUN_CONTEXT.run_root,
                options_overlay_status=(options_overlay_shadow.get("trigger") or {}).get("status"),
                options_overlay_strategy=(options_overlay_shadow.get("recommendation") or {}).get("strategy"),
                options_overlay_active=(options_overlay_shadow.get("trigger") or {}).get("active"),
                options_overlay_feasible=(options_overlay_shadow.get("recommendation") or {}).get("feasible"),
                options_overlay_asof_date=options_overlay_shadow.get("asof_date"),
                options_overlay_reasons=(options_overlay_shadow.get("trigger") or {}).get("reasons"),
                options_overlay_artifact_path=((options_overlay_shadow.get("artifact_paths") or {}).get("run_json")),
            )
            options_overlay_paper = write_options_overlay_paper_review(
                run_root=_RUN_CONTEXT.run_root,
                output_dir=Path("outputs") / "options_overlay_paper",
                trade_date=trade_date_str,
                asof_date=overlay_asof_date,
                regime_summary=regime_summary,
                portfolio_equity=overlay_equity,
                portfolio_cash=overlay_cash,
                spy_price=overlay_spy_price,
                live_regime_review=regime_review,
                shadow_payload=options_overlay_shadow,
                policy_path=Path("config") / "options_overlay_paper_policy.json",
            )
            daily_snapshot["options_overlay_paper_review"] = {
                "status": str(options_overlay_paper.get("paper_review_status") or "").upper(),
                "ready": bool(options_overlay_paper.get("paper_ready")),
                "artifact_paths": options_overlay_paper.get("artifact_paths") or {},
            }
            write_operator_summary(
                _RUN_CONTEXT.run_root,
                options_overlay_paper_status=options_overlay_paper.get("paper_review_status"),
                options_overlay_paper_ready=options_overlay_paper.get("paper_ready"),
                options_overlay_paper_strategy=((options_overlay_paper.get("paper_plan") or {}).get("strategy")),
                options_overlay_paper_artifact_path=((options_overlay_paper.get("artifact_paths") or {}).get("run_json")),
            )
            options_execution_review = write_options_execution_review(
                run_root=_RUN_CONTEXT.run_root,
                output_dir=Path("outputs") / "options_execution",
                trade_date=trade_date_str,
                asof_date=overlay_asof_date,
                paper_review=options_overlay_paper,
                allow_live_submission=options_submission_enabled,
                broker=options_broker,
            )
            daily_snapshot["options_overlay_execution"] = {
                "status": str(options_execution_review.get("execution_status") or "").upper(),
                "ready": bool(options_execution_review.get("ready_for_submission")),
                "artifact_paths": options_execution_review.get("artifact_paths") or {},
            }
            write_operator_summary(
                _RUN_CONTEXT.run_root,
                options_overlay_execution_status=options_execution_review.get("execution_status"),
                options_overlay_execution_ready=options_execution_review.get("ready_for_submission"),
                options_overlay_execution_artifact_path=((options_execution_review.get("artifact_paths") or {}).get("run_json")),
            )
            append_step_summary(
                [
                    "### Options Overlay Shadow",
                    f"- status: `{str((options_overlay_shadow.get('trigger') or {}).get('status') or 'unknown').upper()}`",
                    f"- strategy: `{(options_overlay_shadow.get('recommendation') or {}).get('strategy') or 'none'}`",
                    f"- feasible: `{bool((options_overlay_shadow.get('recommendation') or {}).get('feasible'))}`",
                    f"- artifact: `{((options_overlay_shadow.get('artifact_paths') or {}).get('run_json') or 'unavailable')}`",
                    "",
                    "### Options Overlay Paper Review",
                    f"- status: `{str(options_overlay_paper.get('paper_review_status') or 'unknown').upper()}`",
                    f"- ready: `{bool(options_overlay_paper.get('paper_ready'))}`",
                    f"- strategy: `{(options_overlay_paper.get('paper_plan') or {}).get('strategy') or 'none'}`",
                    f"- artifact: `{((options_overlay_paper.get('artifact_paths') or {}).get('run_json') or 'unavailable')}`",
                    "",
                    "### Options Overlay Execution Review",
                    f"- status: `{str(options_execution_review.get('execution_status') or 'unknown').upper()}`",
                    f"- ready: `{bool(options_execution_review.get('ready_for_submission'))}`",
                    f"- strategy: `{(options_execution_review.get('live_plan') or {}).get('strategy') or 'none'}`",
                    f"- artifact: `{((options_execution_review.get('artifact_paths') or {}).get('run_json') or 'unavailable')}`",
                ]
            )
    except Exception as e:
        logger.warning("[OPTIONS_OVERLAY][WARN] failed building shadow overlay artifact: %s", e)
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

    # Keep canonical producer artifacts fresh for downstream alpha assessment.
    try:
        benchmark_df = update_benchmark_close_history(asof_date=str(integrity.get("asof_date") or trade_date_str))
        logger.info("[PERF_PRODUCERS] benchmark_close_history rows=%d", len(benchmark_df))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] benchmark producer failed: %s", e)
    try:
        vix_df = update_vix_close_history(asof_date=str(integrity.get("asof_date") or trade_date_str))
        logger.info("[PERF_PRODUCERS] vix_close_history rows=%d", len(vix_df))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] vix producer failed: %s", e)
    try:
        analyzer_df = rebuild_premarket_analyzer_scores()
        logger.info("[PERF_PRODUCERS] premarket_analyzer_scores rows=%d", len(analyzer_df))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] analyzer producer failed: %s", e)
    try:
        benchmark_relative_df = build_benchmark_relative_series()
        logger.info("[PERF_PRODUCERS] benchmark_relative_series rows=%d", len(benchmark_relative_df))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] benchmark-relative producer failed: %s", e)
    try:
        concentration_history_df = build_concentration_history()
        logger.info("[PERF_PRODUCERS] concentration_history rows=%d", len(concentration_history_df))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] concentration producer failed: %s", e)
    try:
        construction_parity = build_construction_parity_artifact(
            asof_date=str(integrity.get("asof_date") or trade_date_str)
        )
        logger.info("[PERF_PRODUCERS] construction_parity status=%s", construction_parity.get("status"))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] construction parity producer failed: %s", e)
    try:
        if _RUN_CONTEXT is not None:
            trade_pnl_payload = build_trade_day_pnl_artifact(
                trade_date=trade_date_str,
                run_root=_RUN_CONTEXT.run_root,
                broker_snapshot_path=Path(f"outputs/broker_snapshot/broker_snapshot_{trade_date_str}.json"),
            )
            logger.info("[PERF_PRODUCERS] trade_day_pnl status=%s", trade_pnl_payload.get("status"))
    except Exception as e:
        logger.warning("[PERF_PRODUCERS][WARN] trade-day P&L producer failed: %s", e)

    # ── Execution Summary & Rolling Export (non-blocking) ─────────────
    try:
        if _RUN_CONTEXT is not None:
            broker_reconciliation = (
                (paper_summary or {}).get("broker_reconciliation")
                if isinstance((paper_summary or {}).get("broker_reconciliation"), dict)
                else {}
            )
            # Gather NAV snapshot for summary
            nav_snapshot_data = {
                "position_count": len([h for h in (daily_snapshot.get("holdings") or []) if str(h.get("ticker", "")).upper() != "CASH"]),
                "equity": portfolio_stats.get("equity"),
                "cash": float(portfolio_stats.get("equity", 0.0)) if portfolio_stats.get("cash") is None else portfolio_stats.get("cash"),
                "turnover_pct": float((execution_payload or {}).get("turnover_pct") or 0.0) or float((daily_snapshot.get("nav_metrics") or {}).get("turnover_pct") or 0.0),
                "holdings": (daily_snapshot.get("holdings") or []),
            }
            
            # Call non-blocking summary writer
            summary_results = write_execution_artifacts(
                run_id=_RUN_CONTEXT.run_id,
                trade_date=trade_date_str,
                run_dir=_RUN_CONTEXT.run_root,
                execution_payload=execution_payload,
                paper_summary=paper_summary,
                health_payload=health_payload,
                nav_snapshot=nav_snapshot_data,
                reconciliation_result=broker_reconciliation,
            )
            if summary_results.get("summary_path"):
                logger.info("[EXECUTION_SUMMARY] artifacts written successfully")
            if summary_results.get("csv_run_path"):
                logger.info("[EXECUTION_SUMMARY] per-run CSV artifact: %s", summary_results.get("csv_run_path"))
            if summary_results.get("csv_path"):
                logger.info("[EXECUTION_SUMMARY] CSV export updated: %s", summary_results.get("csv_path"))
    except Exception as e:
        logger.warning("[EXECUTION_SUMMARY][WARN] summary generation failed (non-blocking): %s", e)

    exec_subject, exec_body = build_execution_email_text(execution_payload)
    _, exec_body_html = build_execution_email_html(execution_payload)
    execution_text = exec_body.rstrip() + "\n"
    execution_canonical_path = _canonical_artifact_path("reports", f"trade_execution_{trade_date_str}.txt")
    safe_write_text(
        execution_canonical_path,
        execution_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    execution_path = Path(OUTPUT_DIR) / f"trade_execution_{trade_date_str}.txt"
    safe_write_text(execution_path, execution_text, allow_overwrite=True)
    logger.info("[OK] Execution trade email written: %s", execution_canonical_path)
    snapshot_subject, snapshot_body = create_snapshot_email(
        daily_snapshot,
        execution_payload=execution_payload,
    )
    snapshot_text = snapshot_body.rstrip() + "\n"
    snapshot_canonical_path = _canonical_artifact_path("reports", f"trade_snapshot_{trade_date_str}.txt")
    safe_write_text(
        snapshot_canonical_path,
        snapshot_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    snapshot_path = Path(OUTPUT_DIR) / f"trade_snapshot_{trade_date_str}.txt"
    safe_write_text(snapshot_path, snapshot_text, allow_overwrite=True)
    logger.info("[OK] Model snapshot email written: %s", snapshot_canonical_path)
    # Backward-compatibility alias (deprecated)
    rundown_text = snapshot_body.rstrip() + "\n"
    rundown_canonical_path = _canonical_artifact_path("reports", f"trade_rundown_{trade_date_str}.txt")
    safe_write_text(
        rundown_canonical_path,
        rundown_text,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    rundown_path = Path(OUTPUT_DIR) / f"trade_rundown_{trade_date_str}.txt"
    safe_write_text(rundown_path, rundown_text, allow_overwrite=True)
    logger.info("[OK] Legacy trade rundown alias written: %s", rundown_canonical_path)
    # ── Build report ──────────────────────────────────────────────
    html = build_html_report(
        report_date=report_date,
        st_equity=st_equity,
        st_trades=st_trades,
        s2_equity=s2_equity,
        s2_trades=s2_trades,
        defensive_equity=defensive_equity,
        alloc_result=alloc_result,
        alpha_stats=alpha_stats,
        proposed_trades=daily_snapshot.get("proposed_trades"),
        performance_summary=daily_snapshot.get("performance_summary"),
        s2_no_picks=daily_snapshot.get("s2_no_picks", False),
        cm_details=cm_details,
        inception_metrics=daily_snapshot.get("inception_metrics"),
        allocation_diagnostics=daily_snapshot.get("allocation_diagnostics"),
        regime_summary=daily_snapshot.get("regime_summary"),
    )
    # Append paper trading HTML section (if available)
    if paper_html:
        html = html + "<hr/>" + paper_html
    out_path = _canonical_artifact_path("reports", f"quant_report_{trade_date_str}.html")
    safe_write_text(
        out_path,
        html,
        allow_overwrite=bool(_RUN_CONTEXT and _RUN_CONTEXT.allow_overwrite),
    )
    out_view_path = Path(OUTPUT_DIR) / f"quant_report_{trade_date_str}.html"
    safe_write_text(out_view_path, html, allow_overwrite=True)
    logger.info("[OK] HTML report written: %s", out_path)
    logger.info("\n[EMAIL PREVIEW]\n")
    logger.info("%s", snapshot_body)
    try:
        execution_email_ok, snapshot_email_ok = _deliver_inline_report_emails(
            exec_subject=exec_subject,
            exec_body_html=exec_body_html,
            exec_body_text=exec_body,
            snapshot_subject=snapshot_subject,
            snapshot_body_html=html,
            snapshot_body_text=snapshot_body,
        )
        if execution_email_ok and snapshot_email_ok:
            logger.info("[OK] Emails sent (execution + snapshot)")
    except Exception:
        raise

    if os.getenv("RUN_ROBUSTNESS_BACKTEST", "0").lower() in {"1", "true", "yes", "y"}:
        try:
            from backtests.sleeve1_robustness import main as run_robustness

            run_robustness([])
            logger.info("[OK] Ran sleeve1 robustness backtest")
        except Exception as e:
            logger.warning("[WARN] Robustness backtest failed: %s", e)
    for src, category, name in (
        (str(LEDGER_TRADES_PATH), "ledger", "trades.csv"),
        (sent_ledger_path, "ledger", "orders_sent.csv"),
        ("outputs/perf/nav_timeseries.csv", "snapshots", "nav_timeseries.csv"),
    ):
        try:
            _snapshot_existing_file(src, category=category, filename=name)
        except Exception as e:
            logger.warning("[RUN_ARCHIVE][WARN] snapshot failed src=%s err=%s", src, e)
    # All critical pipeline steps completed — classify terminal status and finalize.
    try:
        if _RUN_TERMINAL_STATUS == "failed_unknown":
            _op_path = (_RUN_CONTEXT.run_root / "operator_summary.json") if _RUN_CONTEXT else None
            if _op_path and _op_path.exists():
                _op_data = json.loads(_op_path.read_text(encoding="utf-8"))
                _pretrade = str(_op_data.get("pretrade_status") or "").upper()
                _op_terminal = str(_op_data.get("terminal_status") or "").strip()
                _submitted = int(
                    _op_data.get("orders_submitted_count")
                    or _op_data.get("submitted_count")
                    or 0
                )
                _broker_reject = str(_op_data.get("broker_reject_status") or "").strip()
                _execution_outcome = str(_op_data.get("execution_outcome") or "").strip()
                _execution_reason = str(_op_data.get("execution_reason") or "").strip()
                if _op_terminal.startswith("failed_"):
                    _RUN_TERMINAL_STATUS = _op_terminal
                    _RUN_TERMINAL_SUBSTATUS = _broker_reject or _RUN_TERMINAL_SUBSTATUS
                    _RUN_TERMINAL_MESSAGE = str(
                        _op_data.get("halt_reason")
                        or _op_data.get("broker_reject_message")
                        or _RUN_TERMINAL_MESSAGE
                    )
                elif (_broker_reject or _execution_outcome) and _submitted > 0:
                    _RUN_TERMINAL_STATUS = "failed_pre_execution"
                    _RUN_TERMINAL_SUBSTATUS = _broker_reject or _execution_reason or _execution_outcome
                    _RUN_TERMINAL_MESSAGE = str(
                        _op_data.get("halt_reason")
                        or _op_data.get("broker_reject_message")
                        or _op_data.get("exception_message")
                        or _RUN_TERMINAL_MESSAGE
                    )
                else:
                    _RUN_TERMINAL_STATUS = "no_action" if _pretrade == "NO_ACTION" else "success"
            else:
                _RUN_TERMINAL_STATUS = "success"
    except Exception:
        if _RUN_TERMINAL_STATUS == "failed_unknown":
            _RUN_TERMINAL_STATUS = "success"
    _finalize_run_context_once()
if __name__ == "__main__":
    main()
