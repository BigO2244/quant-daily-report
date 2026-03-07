from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


DEFAULT_BENCHMARK_PATH = Path("outputs/perf/benchmark_close_history.csv")
DEFAULT_ANALYZER_PATH = Path("outputs/perf/premarket_analyzer_scores.csv")
DEFAULT_VIX_PATH = Path("outputs/perf/vix_close_history.csv")
DEFAULT_SIGNALS_DIR = Path("signals")
DEFAULT_EXECUTION_EMAIL_DIR = Path("outputs/execution_email")


def _effective_inception_date() -> str:
    return str(os.getenv("PAPER_INCEPTION_DATE", "2026-02-23")).strip() or "2026-02-23"


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

    close_index = pd.DatetimeIndex(pd.to_datetime(closes.index, errors="coerce"))
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
    parser = argparse.ArgumentParser(description="Build benchmark, VIX, and premarket analyzer producer artifacts")
    parser.add_argument("--asof-date", required=True, help="As-of date for benchmark close history update")
    parser.add_argument("--benchmark-out", default=str(DEFAULT_BENCHMARK_PATH), help="Benchmark output CSV path")
    parser.add_argument("--vix-out", default=str(DEFAULT_VIX_PATH), help="VIX output CSV path")
    parser.add_argument("--analyzer-out", default=str(DEFAULT_ANALYZER_PATH), help="Analyzer output CSV path")
    parser.add_argument("--signals-dir", default=str(DEFAULT_SIGNALS_DIR), help="Signals JSON directory")
    parser.add_argument("--execution-email-dir", default=str(DEFAULT_EXECUTION_EMAIL_DIR), help="Execution email JSON directory")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
