from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Callable

import pandas as pd

from sleeves.sleeve_trend.config import (
    VIX_ELEVATED_THRESHOLD,
    VIX_FETCH_FALLBACK,
    VIX_HIGH_THRESHOLD,
    VIX_LOW_THRESHOLD,
    VIX_MAX_POSITIONS_CRISIS,
    VIX_MAX_POSITIONS_ELEVATED,
    VIX_MAX_POSITIONS_HIGH,
    VIX_MAX_POSITIONS_LOW,
    VIX_SCALE_CRISIS,
    VIX_SCALE_ELEVATED,
    VIX_SCALE_HIGH,
    VIX_SCALE_LOW,
)

logger = logging.getLogger(__name__)


def classify_vix_regime(vix_level: float) -> dict[str, float | int | str]:
    vix = float(vix_level)
    if vix < VIX_LOW_THRESHOLD:
        return {
            "regime": "LOW",
            "position_scale": float(VIX_SCALE_LOW),
            "max_positions": int(VIX_MAX_POSITIONS_LOW),
        }
    if vix < VIX_ELEVATED_THRESHOLD:
        return {
            "regime": "ELEVATED",
            "position_scale": float(VIX_SCALE_ELEVATED),
            "max_positions": int(VIX_MAX_POSITIONS_ELEVATED),
        }
    if vix < VIX_HIGH_THRESHOLD:
        return {
            "regime": "HIGH",
            "position_scale": float(VIX_SCALE_HIGH),
            "max_positions": int(VIX_MAX_POSITIONS_HIGH),
        }
    return {
        "regime": "CRISIS",
        "position_scale": float(VIX_SCALE_CRISIS),
        "max_positions": int(VIX_MAX_POSITIONS_CRISIS),
    }


def get_current_regime(
    vix_override: float | None = None,
    *,
    as_of_date: str | None = None,
    output_dir: str | Path = "outputs/vix_regime",
    fetch_vix_fn: Callable[[], float] | None = None,
) -> dict[str, float | int | str]:
    report_date = str(as_of_date or date.today().isoformat())
    fallback_used = False

    if vix_override is not None:
        vix_value = float(vix_override)
        source = "override"
    else:
        fetch_fn = fetch_vix_fn or _default_fetch_vix_close
        try:
            vix_value = float(fetch_fn())
            source = "yfinance"
        except Exception as exc:
            fallback_used = True
            vix_value = float(VIX_FETCH_FALLBACK)
            source = "fallback"
            logger.warning("[VIX_REGIME] fetch failed, using fallback %.2f: %s", vix_value, exc)

    payload = {
        "date": report_date,
        "vix": float(vix_value),
        **classify_vix_regime(vix_value),
        "source": source,
        "fallback_used": bool(fallback_used),
    }
    _persist_payload(payload=payload, output_dir=Path(output_dir))
    return payload


def _default_fetch_vix_close() -> float:
    try:
        import yfinance as yf
    except Exception as exc:
        raise RuntimeError("yfinance import failed") from exc

    downloaded = yf.download("^VIX", period="5d", interval="1d", progress=False, auto_adjust=False)
    if downloaded is None or getattr(downloaded, "empty", True):
        raise RuntimeError("empty VIX download")
    if "Close" not in downloaded:
        raise RuntimeError("download missing Close column")

    close_series = downloaded["Close"]
    if isinstance(close_series, pd.DataFrame):
        close_series = close_series.iloc[:, 0]
    close_series = pd.to_numeric(close_series, errors="coerce").dropna()
    if close_series.empty:
        raise RuntimeError("no valid VIX closes returned")
    return float(close_series.iloc[-1])


def _persist_payload(*, payload: dict[str, float | int | str | bool], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    current_path = output_dir / "regime_current.json"
    history_path = output_dir / "regime_history.csv"

    current_path.write_text(json.dumps(payload, indent=2, sort_keys=True))

    row = {
        "date": payload["date"],
        "vix": payload["vix"],
        "regime": payload["regime"],
        "position_scale": payload["position_scale"],
        "max_positions": payload["max_positions"],
        "source": payload["source"],
        "fallback_used": payload["fallback_used"],
    }
    if history_path.exists():
        history = pd.read_csv(history_path)
    else:
        history = pd.DataFrame(columns=list(row.keys()))
    if not history.empty and "date" not in history.columns and "as_of" in history.columns:
        history = history.rename(columns={"as_of": "date"})
    for column in row:
        if column not in history.columns:
            history[column] = pd.NA
    history = history[history["date"].astype(str) != str(payload["date"])].copy() if not history.empty else history
    if history.empty:
        history = pd.DataFrame([row])
    else:
        history = pd.concat([history, pd.DataFrame([row])], ignore_index=True)
    history = history[list(row.keys())].sort_values("date").reset_index(drop=True)
    history.to_csv(history_path, index=False)


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Compute and persist the current VIX regime.")
    parser.add_argument("vix_override", nargs="?", type=float, help="Optional explicit VIX level override")
    parser.add_argument("--as-of-date", dest="as_of_date", help="Trade date to stamp into artifacts")
    parser.add_argument("--output-dir", default="outputs/vix_regime", help="Artifact output directory")
    args = parser.parse_args(argv)

    payload = get_current_regime(
        vix_override=args.vix_override,
        as_of_date=args.as_of_date,
        output_dir=args.output_dir,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
