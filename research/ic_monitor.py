"""
Rolling Information Coefficient monitor for sleeve signals.

This monitor rebuilds daily IC artifacts from immutable signal snapshots in
signals/<date>.json and writes production audit outputs under outputs/ic_monitor/:

* ic_daily.csv   - one row per (date, sleeve, horizon)
* ic_rolling.csv - rolling 20d/60d mean IC per (sleeve, horizon)
* ic_summary.json - latest values plus alerts
* last_run.json  - status/error/duration for the last invocation
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from core.quant_report import download_prices

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("outputs/ic_monitor")
SIGNALS_DIR = Path("signals")
IC_DAILY_CSV = OUTPUT_DIR / "ic_daily.csv"
IC_ROLL_CSV = OUTPUT_DIR / "ic_rolling.csv"
IC_ROLL_CSV_60D = OUTPUT_DIR / "ic_rolling_60d.csv"
IC_SUMMARY = OUTPUT_DIR / "ic_summary.json"
IC_LAST_RUN = OUTPUT_DIR / "last_run.json"

IC_WARN_THRESHOLD = 0.0
IC_WARN_CONSECUTIVE_DAYS = 20
IC_ROLLING_WINDOWS = [20, 60]
IC_HORIZONS = [1, 5, 10, 21]


@dataclass(slots=True)
class SignalSnapshot:
    path: Path
    snapshot_date: pd.Timestamp
    df: pd.DataFrame


def _parse_date(value: Any) -> pd.Timestamp | None:
    if value in (None, ""):
        return None
    try:
        return pd.Timestamp(value).tz_localize(None) if getattr(pd.Timestamp(value), "tzinfo", None) else pd.Timestamp(value)
    except Exception:
        return None


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return None


def _infer_raw_score(row: pd.Series) -> float:
    for col in (
        "raw_score",
        "signal_strength",
        "score",
        "final_signal",
        "composite_score",
        "trend_score",
        "quality_score",
        "target_weight",
    ):
        if col not in row.index:
            continue
        value = _coerce_float(row.get(col))
        if value is not None:
            return float(value)
    return 0.0


def _normalize_signal_frame(raw: Any) -> pd.DataFrame:
    if isinstance(raw, list):
        df = pd.DataFrame(raw)
    elif isinstance(raw, dict) and "signals" in raw:
        df = pd.DataFrame(raw.get("signals") or [])
    elif isinstance(raw, dict) and "weights" in raw:
        df = pd.DataFrame(raw.get("weights") or [])
    elif isinstance(raw, dict):
        rows = []
        for key, value in raw.items():
            numeric = _coerce_float(value)
            if numeric is not None:
                rows.append(
                    {
                        "ticker": str(key).upper(),
                        "target_weight": numeric,
                        "raw_score": numeric,
                        "sleeve": "core",
                    }
                )
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame()

    if df.empty:
        return pd.DataFrame(columns=["ticker", "sleeve", "target_weight", "raw_score"])

    df = df.copy()
    if "ticker" not in df.columns:
        return pd.DataFrame(columns=["ticker", "sleeve", "target_weight", "raw_score"])

    if "sleeve" not in df.columns:
        if "sleeve_name" in df.columns:
            df["sleeve"] = df["sleeve_name"]
        else:
            df["sleeve"] = "core"

    if "target_weight" not in df.columns:
        for alias in ("weight", "final_target_weight", "target"):
            if alias in df.columns:
                df["target_weight"] = df[alias]
                break
        else:
            df["target_weight"] = pd.NA

    if "raw_score" not in df.columns:
        df["raw_score"] = df.apply(_infer_raw_score, axis=1)
    else:
        df["raw_score"] = df["raw_score"].apply(lambda value: _coerce_float(value))
        missing_mask = df["raw_score"].isna()
        if missing_mask.any():
            df.loc[missing_mask, "raw_score"] = df.loc[missing_mask].apply(_infer_raw_score, axis=1)

    df["ticker"] = df["ticker"].astype(str).str.upper().str.strip()
    df["sleeve"] = df["sleeve"].astype(str).str.strip()
    df["target_weight"] = pd.to_numeric(df["target_weight"], errors="coerce")
    df["raw_score"] = pd.to_numeric(df["raw_score"], errors="coerce")
    df = df[df["ticker"].str.len() > 0].copy()
    if "sleeve" not in df.columns:
        df["sleeve"] = "core"
    df["sleeve"] = df["sleeve"].replace("", "core").fillna("core")
    return df[["ticker", "sleeve", "target_weight", "raw_score"] + [c for c in df.columns if c not in {"ticker", "sleeve", "target_weight", "raw_score"}]]


def _load_snapshot(path: Path) -> SignalSnapshot | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[IC_MONITOR] Could not parse %s: %s", path, exc)
        return None

    df = _normalize_signal_frame(raw)
    snapshot_date = None
    if isinstance(raw, dict):
        snapshot_date = _parse_date(raw.get("snapshot_date"))
        if snapshot_date is None:
            meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
            snapshot_date = _parse_date((meta or {}).get("trade_date")) or _parse_date((meta or {}).get("asof_date"))
    if snapshot_date is None:
        snapshot_date = _parse_date(path.stem)
    if snapshot_date is None:
        logger.warning("[IC_MONITOR] Could not determine snapshot date for %s", path)
        return None
    return SignalSnapshot(path=path, snapshot_date=snapshot_date.normalize(), df=df)


def _load_snapshots(signals_dir: Path) -> list[SignalSnapshot]:
    snapshots: list[SignalSnapshot] = []
    for path in sorted(signals_dir.glob("*.json")):
        snapshot = _load_snapshot(path)
        if snapshot is not None:
            snapshots.append(snapshot)
    return sorted(snapshots, key=lambda item: (item.snapshot_date, item.path.name))


def _log_missing_snapshots(signals_dir: Path, snapshots: list[SignalSnapshot]) -> None:
    if len(snapshots) < 2:
        return
    present = {snap.snapshot_date.normalize().strftime("%Y-%m-%d") for snap in snapshots}
    start = snapshots[0].snapshot_date.normalize()
    end = snapshots[-1].snapshot_date.normalize()
    for bday in pd.bdate_range(start=start, end=end):
        date_str = bday.normalize().strftime("%Y-%m-%d")
        if date_str not in present:
            logger.info("[IC_BACKFILL] SKIP %s: snapshot missing", date_str)


def _download_price_history(tickers: Iterable[str]) -> pd.DataFrame:
    tickers = sorted({str(t).upper().strip() for t in tickers if str(t).strip()})
    if not tickers:
        return pd.DataFrame()
    return download_prices(tickers, period="1y", interval="1d")


def _price_wide(prices: pd.DataFrame) -> pd.DataFrame:
    if prices is None or prices.empty:
        return pd.DataFrame()
    if "date" not in prices.columns or "ticker" not in prices.columns or "close" not in prices.columns:
        return pd.DataFrame()
    wide = prices.copy()
    wide["date"] = pd.to_datetime(wide["date"]).dt.tz_localize(None)
    wide["ticker"] = wide["ticker"].astype(str).str.upper().str.strip()
    wide = wide.dropna(subset=["date", "ticker", "close"])
    pivot = wide.pivot(index="date", columns="ticker", values="close").sort_index()
    pivot.index = pd.to_datetime(pivot.index).tz_localize(None)
    return pivot


def _forward_return_vector(price_wide: pd.DataFrame, signal_date: pd.Timestamp, horizon: int, as_of_date: pd.Timestamp) -> pd.Series | None:
    if price_wide.empty:
        return None
    idx = price_wide.index
    start_pos = idx.searchsorted(signal_date.normalize(), side="left")
    if start_pos >= len(idx):
        return None
    end_pos = start_pos + horizon
    if end_pos >= len(idx):
        return None
    start_date = idx[start_pos]
    end_date = idx[end_pos]
    if end_date > as_of_date.normalize():
        return None
    start_px = price_wide.iloc[start_pos]
    end_px = price_wide.iloc[end_pos]
    fwd = end_px / start_px - 1.0
    fwd.name = end_date.strftime("%Y-%m-%d")
    return fwd.replace([np.inf, -np.inf], np.nan)


def _pearson_ic(signals_df: pd.DataFrame, forward_returns: pd.Series) -> tuple[float | None, int]:
    if signals_df.empty or forward_returns is None or forward_returns.empty:
        return None, 0
    merged = (
        signals_df[["ticker", "raw_score"]]
        .dropna(subset=["ticker", "raw_score"])
        .assign(ticker=lambda frame: frame["ticker"].astype(str).str.upper().str.strip())
        .set_index("ticker")
        .join(forward_returns.rename("fwd_return"), how="inner")
    )
    merged = merged.dropna(subset=["raw_score", "fwd_return"])
    n = len(merged)
    if n < 3:
        return None, n
    ic = merged["raw_score"].corr(merged["fwd_return"])
    return (float(ic) if ic is not None and not np.isnan(ic) else None), n


def _build_daily_rows(
    snapshots: list[SignalSnapshot],
    price_wide: pd.DataFrame,
    as_of_date: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not snapshots or price_wide.empty:
        return pd.DataFrame(columns=["date", "sleeve", "horizon", "ic", "n", "universe_size"])

    for snapshot in snapshots:
        if snapshot.snapshot_date >= as_of_date.normalize():
            continue
        signals = snapshot.df.copy()
        if signals.empty:
            continue
        for horizon in IC_HORIZONS:
            forward_returns = _forward_return_vector(price_wide, snapshot.snapshot_date, horizon, as_of_date)
            if forward_returns is None or forward_returns.empty:
                continue
            for sleeve, sleeve_df in signals.groupby("sleeve", dropna=False):
                sleeve_name = str(sleeve or "core").strip() or "core"
                ic, n = _pearson_ic(sleeve_df, forward_returns)
                if n == 0:
                    continue
                rows.append(
                    {
                        "date": snapshot.snapshot_date.strftime("%Y-%m-%d"),
                        "sleeve": sleeve_name,
                        "horizon": int(horizon),
                        "ic": ic,
                        "n": int(n),
                        "universe_size": int(len(sleeve_df)),
                    }
                )
    daily_df = pd.DataFrame(rows, columns=["date", "sleeve", "horizon", "ic", "n", "universe_size"])
    if not daily_df.empty:
        daily_df["date"] = pd.to_datetime(daily_df["date"]).dt.strftime("%Y-%m-%d")
        daily_df["ic"] = pd.to_numeric(daily_df["ic"], errors="coerce")
        daily_df["n"] = pd.to_numeric(daily_df["n"], errors="coerce").fillna(0).astype(int)
        daily_df["universe_size"] = pd.to_numeric(daily_df["universe_size"], errors="coerce").fillna(0).astype(int)
        daily_df = daily_df.sort_values(["date", "sleeve", "horizon"]).reset_index(drop=True)
    return daily_df


def _build_rolling_rows(daily_df: pd.DataFrame) -> pd.DataFrame:
    if daily_df.empty:
        return pd.DataFrame(columns=["date", "sleeve", "horizon", "window", "rolling_ic", "n", "universe_size"])
    rows: list[dict[str, Any]] = []
    working = daily_df.copy()
    working["date"] = pd.to_datetime(working["date"])
    working = working.sort_values(["sleeve", "horizon", "date"]).reset_index(drop=True)
    for (sleeve, horizon), group in working.groupby(["sleeve", "horizon"], sort=True):
        group = group.sort_values("date").reset_index(drop=True)
        for window in IC_ROLLING_WINDOWS:
            rolling = group["ic"].rolling(window=window, min_periods=window).mean()
            for idx, value in enumerate(rolling):
                rows.append(
                    {
                        "date": group.loc[idx, "date"].strftime("%Y-%m-%d"),
                        "sleeve": sleeve,
                        "horizon": int(horizon),
                        "window": int(window),
                        "rolling_ic": None if pd.isna(value) else float(value),
                        "n": int(group.loc[idx, "n"]),
                        "universe_size": int(group.loc[idx, "universe_size"]),
                    }
                )
    rolling_df = pd.DataFrame(rows, columns=["date", "sleeve", "horizon", "window", "rolling_ic", "n", "universe_size"])
    if not rolling_df.empty:
        rolling_df = rolling_df.sort_values(["sleeve", "horizon", "window", "date"]).reset_index(drop=True)
    return rolling_df


def _latest_non_null(series: pd.Series) -> float | None:
    if series is None or series.empty:
        return None
    valid = series.dropna()
    if valid.empty:
        return None
    return float(valid.iloc[-1])


def _consecutive_nonpositive(values: pd.Series) -> int:
    count = 0
    for value in reversed(list(values.dropna())):
        if float(value) <= IC_WARN_THRESHOLD:
            count += 1
        else:
            break
    return count


def _sign_flip_alert(series: pd.Series) -> str | None:
    values = [float(v) for v in series.dropna().tolist()]
    if len(values) < 2:
        return None
    latest = values[-1]
    latest_sign = int(np.sign(latest))
    if latest_sign == 0:
        return None
    prior_window = values[-5:-1] if len(values) > 1 else []
    for prior in prior_window:
        prior_sign = int(np.sign(prior))
        if prior_sign == 0:
            continue
        if prior_sign != latest_sign:
            return (
                f"1d IC sign flipped within last 5 observations "
                f"(latest={latest:+.4f}, prior={prior:+.4f})"
            )
    return None


def _build_summary(daily_df: pd.DataFrame, rolling_df: pd.DataFrame, *, as_of_date: str) -> dict[str, Any]:
    if daily_df.empty:
        return {
            "status": "no_data",
            "as_of_date": as_of_date,
            "generated_at": pd.Timestamp.utcnow().isoformat(),
            "alerts": [],
            "sleeves": {},
        }

    sleeves: dict[str, Any] = {}
    alerts: list[str] = []
    for sleeve in sorted(daily_df["sleeve"].dropna().astype(str).unique().tolist()):
        sleeve_daily = daily_df[daily_df["sleeve"] == sleeve].copy()
        sleeve_daily = sleeve_daily.sort_values(["horizon", "date"])
        latest_ic_by_horizon: dict[str, float | None] = {}
        latest_rolling_by_horizon: dict[str, dict[str, float | None]] = {}
        sleeve_alerts: list[str] = []

        for horizon in IC_HORIZONS:
            daily_h = sleeve_daily[sleeve_daily["horizon"] == horizon].copy().sort_values("date")
            latest_ic_by_horizon[str(horizon)] = _latest_non_null(daily_h["ic"])
            latest_rolling_by_horizon[str(horizon)] = {}
            for window in IC_ROLLING_WINDOWS:
                rolling_h = rolling_df[
                    (rolling_df["sleeve"] == sleeve)
                    & (rolling_df["horizon"] == horizon)
                    & (rolling_df["window"] == window)
                ].copy().sort_values("date")
                latest_rolling_by_horizon[str(horizon)][str(window)] = _latest_non_null(rolling_h["rolling_ic"])
                if horizon == 1 and window == 20 and not rolling_h.empty:
                    if _consecutive_nonpositive(rolling_h["rolling_ic"]) >= 10:
                        message = (
                            f"{sleeve}: 20d rolling IC has been <= 0 for "
                            f"{_consecutive_nonpositive(rolling_h['rolling_ic'])} consecutive days"
                        )
                        sleeve_alerts.append(message)
                        alerts.append(message)

            if horizon == 1:
                sign_flip = _sign_flip_alert(daily_h["ic"])
                if sign_flip:
                    message = f"{sleeve}: {sign_flip}"
                    sleeve_alerts.append(message)
                    alerts.append(message)

        sleeve_rows = daily_df[daily_df["sleeve"] == sleeve].sort_values(["date", "horizon"])
        sleeves[sleeve] = {
            "latest_date": str(pd.to_datetime(sleeve_rows["date"]).max().date()),
            "latest_ic_by_horizon": latest_ic_by_horizon,
            "latest_rolling_ic_by_horizon": latest_rolling_by_horizon,
            "alerts": sleeve_alerts,
        }

    status = "warning" if alerts else "ok"
    return {
        "status": status,
        "as_of_date": as_of_date,
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "alerts": alerts,
        "sleeves": sleeves,
        "latest_date": str(pd.to_datetime(daily_df["date"]).max().date()),
        "daily_rows": int(len(daily_df)),
        "rolling_rows": int(len(rolling_df)),
    }


def _write_last_run(*, run_date: str, status: str, error: str | None, duration_sec: float) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_date": run_date,
        "status": status,
        "error": error,
        "duration_sec": round(float(duration_sec), 6),
    }
    IC_LAST_RUN.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _run_ic_monitor(
    *,
    report_date: str | None = None,
    signal_date: str | None = None,
    signals_dir: str | Path | None = None,
    backfill: bool = False,
) -> dict[str, Any]:
    signals_path = Path(signals_dir or SIGNALS_DIR)
    report_ts = pd.Timestamp(report_date or date.today().isoformat()).normalize()
    snapshots = _load_snapshots(signals_path)
    if backfill:
        _log_missing_snapshots(signals_path, snapshots)
    if not snapshots:
        daily_df = pd.DataFrame(columns=["date", "sleeve", "horizon", "ic", "n", "universe_size"])
        rolling_df = pd.DataFrame(columns=["date", "sleeve", "horizon", "window", "rolling_ic", "n", "universe_size"])
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        daily_df.to_csv(IC_DAILY_CSV, index=False)
        rolling_df.to_csv(IC_ROLL_CSV, index=False)
        rolling_df.to_csv(IC_ROLL_CSV_60D, index=False)
        summary = _build_summary(daily_df, rolling_df, as_of_date=report_ts.strftime("%Y-%m-%d"))
        IC_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    if signal_date:
        selected = [snap for snap in snapshots if snap.snapshot_date.strftime("%Y-%m-%d") == signal_date]
    else:
        selected = [snap for snap in snapshots if snap.snapshot_date < report_ts]

    if not selected:
        daily_df = pd.DataFrame(columns=["date", "sleeve", "horizon", "ic", "n", "universe_size"])
        rolling_df = pd.DataFrame(columns=["date", "sleeve", "horizon", "window", "rolling_ic", "n", "universe_size"])
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        daily_df.to_csv(IC_DAILY_CSV, index=False)
        rolling_df.to_csv(IC_ROLL_CSV, index=False)
        rolling_df.to_csv(IC_ROLL_CSV_60D, index=False)
        summary = _build_summary(daily_df, rolling_df, as_of_date=report_ts.strftime("%Y-%m-%d"))
        IC_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        return summary

    if signal_date:
        selected = [selected[-1]]

    tickers = sorted(
        {
            str(ticker).upper().strip()
            for snapshot in selected
            for ticker in snapshot.df.get("ticker", pd.Series(dtype=str)).dropna().astype(str).tolist()
            if str(ticker).strip()
        }
    )
    prices = _download_price_history(tickers)
    price_wide = _price_wide(prices)
    daily_df = _build_daily_rows(selected, price_wide, report_ts)
    rolling_df = _build_rolling_rows(daily_df)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    daily_df.to_csv(IC_DAILY_CSV, index=False)
    rolling_df.to_csv(IC_ROLL_CSV, index=False)
    rolling_df.to_csv(IC_ROLL_CSV_60D, index=False)
    summary = _build_summary(daily_df, rolling_df, as_of_date=report_ts.strftime("%Y-%m-%d"))
    IC_SUMMARY.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def compute_and_log_ic(
    report_date: str | None = None,
    signal_date: str | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    run_date = (report_date or date.today().isoformat())
    error: str | None = None
    summary: dict[str, Any] = {}
    try:
        summary = _run_ic_monitor(report_date=report_date, signal_date=signal_date, signals_dir=SIGNALS_DIR, backfill=False)
        return summary
    except Exception as exc:
        error = str(exc)
        logger.exception("[IC_MONITOR] failed: %s", exc)
        raise
    finally:
        _write_last_run(run_date=run_date, status="ok" if error is None else "error", error=error, duration_sec=time.perf_counter() - start)


def backfill_ic(
    *,
    signals_dir: str | Path = SIGNALS_DIR,
    report_date: str | None = None,
) -> dict[str, Any]:
    start = time.perf_counter()
    run_date = (report_date or date.today().isoformat())
    error: str | None = None
    summary: dict[str, Any] = {}
    try:
        summary = _run_ic_monitor(report_date=report_date, signal_date=None, signals_dir=signals_dir, backfill=True)
        return summary
    except Exception as exc:
        error = str(exc)
        logger.exception("[IC_MONITOR] failed: %s", exc)
        raise
    finally:
        _write_last_run(run_date=run_date, status="ok" if error is None else "error", error=error, duration_sec=time.perf_counter() - start)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Compute rolling signal IC by sleeve and horizon")
    parser.add_argument("--report-date", default=None, help="As-of date for the monitor (YYYY-MM-DD)")
    parser.add_argument("--signal-date", default=None, help="Restrict computation to one signal snapshot (YYYY-MM-DD)")
    parser.add_argument("--signals-dir", default=str(SIGNALS_DIR), help="Directory containing signal JSON files")
    parser.add_argument("--backfill", action="store_true", help="Rebuild IC artifacts from all available snapshots")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = _parse_args(argv)
    global SIGNALS_DIR
    SIGNALS_DIR = Path(args.signals_dir)
    if args.backfill:
        result = backfill_ic(signals_dir=SIGNALS_DIR, report_date=args.report_date)
    else:
        result = compute_and_log_ic(report_date=args.report_date, signal_date=args.signal_date)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
