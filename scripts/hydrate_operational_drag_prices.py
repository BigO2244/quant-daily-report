#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.flow_detection.data import download_price_panel  # noqa: E402
from research.operational_drag import (  # noqa: E402
    build_actual_nav,
    discover_plan_snapshots,
)

SCHEMA_VERSION = "caerus_operational_drag_price_hydration_v1"
BENCHMARK_SYMBOL = "SPY"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text.rstrip() + "\n", encoding="utf-8")
    tmp.replace(path)


def _write_json_atomic(path: Path, payload: Any) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True))


def _is_date(value: Any) -> bool:
    try:
        from datetime import date

        date.fromisoformat(str(value))
    except Exception:
        return False
    return True


def _date_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)[:10]
    return text if _is_date(text) else None


def _safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except Exception:
        return None
    if out != out:
        return None
    return out


def _dedupe(values: Sequence[str]) -> list[str]:
    return sorted({str(value).upper().strip() for value in values if str(value).strip()})


def _append_source(sources: dict[str, list[str]], symbol: str, source: str) -> None:
    symbol = symbol.upper().strip()
    if not symbol:
        return
    values = sources.setdefault(symbol, [])
    if source not in values:
        values.append(source)


def discover_required_symbols(*, repo_root: Path | str, trade_date: str) -> dict[str, Any]:
    repo = Path(repo_root)
    symbols: set[str] = {BENCHMARK_SYMBOL}
    sources: dict[str, list[str]] = {BENCHMARK_SYMBOL: ["benchmark"]}

    for snapshot in discover_plan_snapshots(repo, trade_date):
        for position in snapshot.positions:
            symbol = position.symbol.upper().strip()
            if symbol:
                symbols.add(symbol)
                _append_source(sources, symbol, f"plan:{snapshot.plan_source}")

    precompute_root = repo / "outputs" / "precompute"
    if precompute_root.exists():
        for day_dir in sorted(path for path in precompute_root.iterdir() if path.is_dir() and _is_date(path.name)):
            if day_dir.name > trade_date:
                continue
            payload = _read_json(day_dir / "planned_execution_payload.json")
            trades = payload.get("trades") if isinstance(payload, dict) else None
            if not isinstance(trades, list):
                continue
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                symbol = str(trade.get("ticker") or trade.get("symbol") or "").upper().strip()
                if symbol and symbol != "CASH":
                    symbols.add(symbol)
                    _append_source(sources, symbol, "planned_execution_payload")

    actual = build_actual_nav(trade_date=trade_date, repo_root=repo)
    for position in actual.get("actual_positions") or []:
        if not isinstance(position, dict):
            continue
        symbol = str(position.get("symbol") or position.get("ticker") or "").upper().strip()
        if symbol and symbol != "CASH":
            symbols.add(symbol)
            _append_source(sources, symbol, "actual_positions")

    return {
        "symbols": _dedupe(sorted(symbols)),
        "symbol_sources": {symbol: sorted(values) for symbol, values in sorted(sources.items())},
        "actual_nav_reason_codes": actual.get("reason_codes") or [],
        "actual_position_count": len(actual.get("actual_positions") or []),
    }


def _default_fetch_price_panel(*, symbols: Sequence[str], trade_date: str, chunk_size: int) -> Any:
    return download_price_panel(
        symbols=symbols,
        start_date=trade_date,
        end_date=trade_date,
        chunk_size=chunk_size,
    )


def _normalize_price_rows(frame: Any, *, trade_date: str, requested_symbols: set[str]) -> list[dict[str, Any]]:
    try:
        import pandas as pd
    except Exception as exc:  # pragma: no cover - covered by runtime diagnostics
        raise RuntimeError("pandas_unavailable") from exc

    if frame is None or getattr(frame, "empty", True):
        return []
    df = frame.copy()
    columns = {str(col).lower(): col for col in df.columns}
    date_col = columns.get("date") or columns.get("as_of") or columns.get("timestamp")
    symbol_col = columns.get("ticker") or columns.get("symbol") or columns.get("asset")
    close_col = columns.get("close") or columns.get("adj_close") or columns.get("price") or columns.get("last")
    if date_col is None or symbol_col is None or close_col is None:
        raise ValueError("price_frame_missing_required_columns")
    df["_date"] = pd.to_datetime(df[date_col], errors="coerce").dt.strftime("%Y-%m-%d")
    df["_symbol"] = df[symbol_col].astype(str).str.upper().str.strip()
    df["_close"] = pd.to_numeric(df[close_col], errors="coerce")
    df = df[
        (df["_date"] == trade_date)
        & df["_symbol"].isin(requested_symbols)
        & df["_close"].notna()
    ].copy()
    if df.empty:
        return []
    df = df.sort_values(["_symbol", "_date"]).drop_duplicates(["_symbol", "_date"], keep="last")
    return [
        {
            "date": str(row["_date"]),
            "symbol": str(row["_symbol"]),
            "close": round(float(row["_close"]), 10),
            "source": "yfinance",
        }
        for _, row in df.iterrows()
    ]


def _load_fixture_prices(path: Path, *, trade_date: str, requested_symbols: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            date = _date_text(raw.get("date") or raw.get("Date"))
            symbol = str(raw.get("symbol") or raw.get("ticker") or raw.get("Ticker") or "").upper().strip()
            close = _safe_float(raw.get("close") or raw.get("price") or raw.get("Close"))
            if date == trade_date and symbol in requested_symbols and close is not None:
                rows.append({"date": date, "symbol": symbol, "close": round(float(close), 10), "source": str(path)})
    return sorted(rows, key=lambda row: row["symbol"])


def build_price_hydration(
    *,
    trade_date: str,
    repo_root: Path | str = Path("."),
    output_root: Path | str | None = None,
    allow_download: bool = True,
    chunk_size: int = 25,
    fixture_prices: Path | None = None,
    fetch_price_panel_fn: Callable[..., Any] | None = None,
    write: bool = True,
) -> dict[str, Any]:
    if not _is_date(trade_date):
        raise ValueError(f"trade_date must be YYYY-MM-DD, got {trade_date!r}")
    repo = Path(repo_root)
    discovery = discover_required_symbols(repo_root=repo, trade_date=trade_date)
    requested = discovery["symbols"]
    requested_set = set(requested)
    reason_codes: list[str] = []
    prices: list[dict[str, Any]] = []
    download_error: str | None = None
    download_attempted = False

    if fixture_prices is not None:
        prices = _load_fixture_prices(fixture_prices, trade_date=trade_date, requested_symbols=requested_set)
        reason_codes.append("price_source_fixture_csv")
    elif allow_download and requested:
        download_attempted = True
        fetch_fn = fetch_price_panel_fn or _default_fetch_price_panel
        try:
            frame = fetch_fn(symbols=requested, trade_date=trade_date, chunk_size=chunk_size)
            prices = _normalize_price_rows(frame, trade_date=trade_date, requested_symbols=requested_set)
        except Exception as exc:
            download_error = f"{type(exc).__name__}:{exc}"
            reason_codes.append("price_download_failed")
    elif not allow_download:
        reason_codes.append("price_download_disabled")

    hydrated = sorted({row["symbol"] for row in prices})
    missing = sorted(requested_set - set(hydrated))
    reason_codes.extend(f"missing_price:{symbol}" for symbol in missing)
    if not prices and "price_hydration_empty" not in reason_codes:
        reason_codes.append("price_hydration_empty")
    if not reason_codes:
        reason_codes.append("ok")

    out_dir = (Path(output_root) if output_root is not None else repo / "outputs" / "operational_drag") / trade_date
    payload = {
        "schema_version": SCHEMA_VERSION,
        "date": trade_date,
        "generated_at": f"{trade_date}T00:00:00Z",
        "available": bool(prices),
        "confidence": "HIGH" if not missing and prices else ("MEDIUM" if prices else "LOW"),
        "price_source": "fixture_csv" if fixture_prices else "yfinance",
        "download_attempted": bool(download_attempted),
        "download_error": download_error,
        "symbols_requested": requested,
        "symbols_hydrated": hydrated,
        "missing_symbols": missing,
        "symbol_sources": discovery["symbol_sources"],
        "actual_position_count": discovery["actual_position_count"],
        "actual_nav_reason_codes": discovery["actual_nav_reason_codes"],
        "date_range": {
            "start": min((row["date"] for row in prices), default=None),
            "end": max((row["date"] for row in prices), default=None),
        },
        "prices": prices,
        "reason_codes": sorted(set(reason_codes)),
        "source_diagnostics": {
            "requested_trade_date": trade_date,
            "fixture_prices": str(fixture_prices) if fixture_prices else None,
            "output_path": str(out_dir / "price_hydration.json"),
            "no_forward_fill": True,
            "lookahead_guard": "only rows exactly matching requested trade_date are emitted",
        },
    }
    if write:
        _write_json_atomic(out_dir / "price_hydration.json", payload)
        _write_text_atomic(out_dir / "price_hydration.md", render_price_hydration_markdown(payload))
    return payload


def render_price_hydration_markdown(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"# Operational Drag Price Hydration - {payload.get('date')}",
            "",
            f"- Available: {payload.get('available')}",
            f"- Confidence: {payload.get('confidence')}",
            f"- Price source: {payload.get('price_source')}",
            f"- Download attempted: {payload.get('download_attempted')}",
            f"- Symbols requested: {len(payload.get('symbols_requested') or [])}",
            f"- Symbols hydrated: {len(payload.get('symbols_hydrated') or [])}",
            f"- Missing symbols: {', '.join(payload.get('missing_symbols') or []) or 'none'}",
            f"- Date range: {(payload.get('date_range') or {}).get('start')} to {(payload.get('date_range') or {}).get('end')}",
            f"- Reason codes: {', '.join(payload.get('reason_codes') or [])}",
            "",
            "No current-day forward-fill is applied; only prices dated exactly to the requested trade date are emitted.",
        ]
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Hydrate date-scoped prices for operational drag analysis.")
    parser.add_argument("--date", required=True, help="Trade date to hydrate, YYYY-MM-DD.")
    parser.add_argument("--repo-root", default=".", help="Repository root. Defaults to current directory.")
    parser.add_argument("--output-root", help="Optional output root; defaults to outputs/operational_drag.")
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--no-download", action="store_true", help="Discover symbols and write missing-data metadata without downloading.")
    parser.add_argument("--fixture-prices", help="Optional CSV fixture with date,symbol,close columns; intended for tests/offline validation.")
    parser.add_argument("--no-write", action="store_true", help="Build in memory without writing artifacts.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    payload = build_price_hydration(
        trade_date=args.date,
        repo_root=Path(args.repo_root),
        output_root=Path(args.output_root) if args.output_root else None,
        allow_download=not bool(args.no_download),
        chunk_size=int(args.chunk_size),
        fixture_prices=Path(args.fixture_prices) if args.fixture_prices else None,
        write=not bool(args.no_write),
    )
    print(json.dumps({
        "date": payload["date"],
        "available": payload["available"],
        "confidence": payload["confidence"],
        "symbols_requested": len(payload["symbols_requested"]),
        "symbols_hydrated": len(payload["symbols_hydrated"]),
        "missing_symbols": payload["missing_symbols"],
        "reason_codes": payload["reason_codes"],
        "artifact_paths": {
            "price_hydration": str((Path(args.output_root) if args.output_root else Path(args.repo_root) / "outputs" / "operational_drag") / args.date / "price_hydration.json"),
            "price_hydration_md": str((Path(args.output_root) if args.output_root else Path(args.repo_root) / "outputs" / "operational_drag") / args.date / "price_hydration.md"),
        },
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
