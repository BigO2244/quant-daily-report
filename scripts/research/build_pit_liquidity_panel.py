#!/usr/bin/env python3
"""Build deterministic PIT liquidity artifacts from the Sharadar SEP OHLCV cache.

Research-only. Reads repo-local generated OHLCV cache files and writes panel
artifacts under outputs/research/pit_liquidity/. It does not fetch data, alter
live trading behavior, or activate any sleeve.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCHEMA_VERSION = "caerus_pit_liquidity_panel_v1"
DEFAULT_CACHE_DIR = "data/research_cache/sharadar_sep_ohlcv"
DEFAULT_OUTPUT_DIR = "outputs/research/pit_liquidity"
REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "closeadj", "volume")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _load_ticker_file(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    ticker = path.stem.replace("_", "/")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    missing = [field for field in REQUIRED_COLUMNS if field not in columns]
    if missing:
        return pd.DataFrame(), {"ticker": ticker, "rows": 0, "status": "missing_columns", "missing_columns": missing}

    normalized: list[dict[str, Any]] = []
    for row in rows:
        date = str(row.get("date") or "")[:10]
        if len(date) != 10:
            continue
        item = {"ticker": ticker, "date": date}
        for field in ("open", "high", "low", "close", "closeadj", "volume"):
            item[field] = _safe_float(row.get(field))
        normalized.append(item)
    if not normalized:
        return pd.DataFrame(), {"ticker": ticker, "rows": 0, "status": "empty"}

    df = pd.DataFrame(normalized).sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    df["dollar_volume"] = df["closeadj"] * df["volume"]
    df["ADV_20"] = df["volume"].rolling(window=20, min_periods=1).mean()
    df["ADV_60"] = df["volume"].rolling(window=60, min_periods=1).mean()
    df["dollar_ADV_20"] = df["dollar_volume"].rolling(window=20, min_periods=1).mean()
    df["dollar_ADV_60"] = df["dollar_volume"].rolling(window=60, min_periods=1).mean()
    null_counts = {field: int(df[field].isna().sum()) for field in REQUIRED_COLUMNS if field != "date"}
    status = "ok" if null_counts.get("volume", 0) == 0 and null_counts.get("closeadj", 0) == 0 else "has_nulls"
    diagnostics = {
        "ticker": ticker,
        "rows": int(len(df)),
        "first_date": str(df["date"].min()),
        "last_date": str(df["date"].max()),
        "status": status,
        "null_counts": null_counts,
        "sha256": _sha256(path),
    }
    return df, diagnostics


def build_panel(*, repo_root: Path, cache_dir: Path, output_dir: Path) -> dict[str, Any]:
    resolved_cache = cache_dir if cache_dir.is_absolute() else repo_root / cache_dir
    resolved_output = output_dir if output_dir.is_absolute() else repo_root / output_dir
    files = sorted(path for path in resolved_cache.glob("*.csv") if path.name != "manifest.json")
    frames: list[pd.DataFrame] = []
    per_ticker: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for path in files:
        frame, diag = _load_ticker_file(path)
        ticker = str(diag.get("ticker") or path.stem)
        per_ticker[ticker] = diag
        if frame.empty:
            failed.append(ticker)
        else:
            frames.append(frame)

    if frames:
        panel = pd.concat(frames, ignore_index=True).sort_values(["ticker", "date"])
    else:
        panel = pd.DataFrame(columns=[
            "ticker", "date", "open", "high", "low", "close", "closeadj", "volume",
            "dollar_volume", "ADV_20", "ADV_60", "dollar_ADV_20", "dollar_ADV_60",
        ])

    resolved_output.mkdir(parents=True, exist_ok=True)
    panel_path = resolved_output / "pit_liquidity_panel.csv"
    diagnostics_path = resolved_output / "pit_liquidity_diagnostics.json"
    manifest_path = resolved_output / "manifest.json"
    panel.to_csv(panel_path, index=False)

    null_counts = {
        column: int(panel[column].isna().sum())
        for column in panel.columns
        if column not in {"ticker", "date"}
    }
    coverage = {
        "first_date": str(panel["date"].min()) if len(panel) else None,
        "last_date": str(panel["date"].max()) if len(panel) else None,
        "ticker_count": int(panel["ticker"].nunique()) if len(panel) else 0,
        "row_count": int(len(panel)),
    }
    diagnostics = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_cache_dir": str(resolved_cache),
        "panel_path": str(panel_path),
        "volume_null_diagnostics": {
            "tickers_with_volume_nulls": sorted(
                tk for tk, diag in per_ticker.items()
                if int((diag.get("null_counts") or {}).get("volume") or 0) > 0
            ),
            "null_counts": null_counts,
        },
        "coverage_diagnostics": {
            "failed_or_empty_tickers": failed,
            "per_ticker": per_ticker,
        },
    }
    diagnostics_path.write_text(json.dumps(diagnostics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "governance_label": "RESEARCH_ONLY",
        "execution_impact": "NON_EXECUTIONAL",
        "source": "SHARADAR/SEP OHLCV cache",
        "source_cache_dir": str(resolved_cache),
        "generated_at": diagnostics["generated_at"],
        "panel_path": str(panel_path),
        "diagnostics_path": str(diagnostics_path),
        "columns": list(panel.columns),
        "coverage": coverage,
        "null_counts": null_counts,
        "failed_or_empty_tickers": failed,
        "panel_sha256": _sha256(panel_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--cache-dir", type=Path, default=Path(DEFAULT_CACHE_DIR))
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args(argv)
    manifest = build_panel(repo_root=args.repo_root.resolve(), cache_dir=args.cache_dir, output_dir=args.output_dir)
    print(json.dumps({
        "status": "OK",
        "panel_path": manifest["panel_path"],
        "ticker_count": manifest["coverage"]["ticker_count"],
        "row_count": manifest["coverage"]["row_count"],
        "failed_or_empty_count": len(manifest["failed_or_empty_tickers"]),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
