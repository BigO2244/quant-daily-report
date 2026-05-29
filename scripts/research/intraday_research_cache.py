"""Research-only intraday minute-bar cache.

This script is part of the execution timing sensitivity study described in
``specs/execution_timing_sensitivity_study.md``. It is **research-only** and
**additive**: it never submits orders, never touches the live execution path,
never modifies the crontab or ``cron_execute.sh``, and never changes the
broker, reconciliation, or portfolio-construction code paths.

Phase 1 cache contract
----------------------
The Phase 1 contract is intentionally narrow so that all parquet snapshots
written under ``data/research_cache/intraday/<CACHE_KEY_VERSION>/`` are
guaranteed to share the same session window and the same data feed:

* canonical window  = 09:25–10:30 ET (covers every offset T+0..T+10m in the
  spec plus a buffer for spread baselining)
* canonical feed    = ``iex`` (paper-account entitlement; SIP requires a
  separate cache namespace)
* canonical version = ``intraday_bars_v1_iex_0925_1030``

The CLI honours these constants only — it does not expose ``--feed`` or
``--window-*`` flags. The :func:`collect_intraday_cache` *function* still
accepts overrides so unit tests can exercise other shapes, but the
``cache_key_version`` is derived from whatever values are resolved, and the
cache path embeds that version. Consequence: a programmatic caller that
chose a non-canonical window writes into a sibling directory and physically
cannot pollute the canonical cache.

Determinism
-----------
* Existing parquet snapshots are never overwritten — a rerun against a fully
  populated cache is a no-op for the on-disk minute bars (only the status
  artifact's ``generated_at`` is refreshed).
* Symbols and bar rows are sorted before persistence so the same upstream
  data produces byte-identical parquet files.
* The status artifact uses ``json.dumps(..., sort_keys=True, indent=2)`` and
  carries provenance fields (source, retrieval timestamp, symbol, trade
  date, feed, cache_key_version) on every row and at the run level so any
  look-ahead can be audited after the fact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from paper.trading_calendar import is_trading_day


logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
UTC = dt.timezone.utc

DEFAULT_CACHE_ROOT = Path("data/research_cache/intraday")
DEFAULT_STATUS_ROOT = Path("outputs/research/intraday_collection")
DEFAULT_PLAN_ROOT = Path("outputs/precompute")

# --- Phase 1 canonical contract (locked before first production write) ---
CANONICAL_WINDOW_START_ET = dt.time(9, 25)
CANONICAL_WINDOW_END_ET = dt.time(10, 30)
CANONICAL_FEED = "iex"
CACHE_KEY_VERSION = "intraday_bars_v1_iex_0925_1030"

SCHEMA_VERSION = "1.0"
SOURCE_LABEL = "alpaca:1Min"

PARQUET_COLUMNS = [
    "symbol",
    "trade_date",
    "bar_start_ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "trade_count",
    "vwap",
    "source",
    "feed",
    "retrieved_at",
]


# ---------------------------------------------------------------------------
# Cache-key derivation
# ---------------------------------------------------------------------------


def derive_cache_key_version(feed: str, window_start: dt.time, window_end: dt.time) -> str:
    """Return the cache-key version string for the given (feed, window).

    Canonical inputs return :data:`CACHE_KEY_VERSION`. Any deviation produces
    a distinct key, so non-canonical runs cannot share a directory with the
    canonical cache.
    """
    feed_norm = feed.strip().lower()
    start = f"{window_start.hour:02d}{window_start.minute:02d}"
    end = f"{window_end.hour:02d}{window_end.minute:02d}"
    return f"intraday_bars_v1_{feed_norm}_{start}_{end}"


# ---------------------------------------------------------------------------
# Plan discovery
# ---------------------------------------------------------------------------


def resolve_plan_path(trade_date: str, plan_root: Path = DEFAULT_PLAN_ROOT) -> Path:
    return plan_root / trade_date / "planned_execution_payload.json"


def load_plan_symbols(plan_path: Path) -> list[str]:
    """Return the alphabetically-sorted unique tickers from a precompute plan."""
    if not plan_path.exists():
        raise FileNotFoundError(f"plan_payload_missing: {plan_path}")
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    trades = payload.get("trades") or []
    symbols: set[str] = set()
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        ticker = trade.get("ticker")
        if not isinstance(ticker, str):
            continue
        ticker = ticker.strip().upper()
        if ticker:
            symbols.add(ticker)
    return sorted(symbols)


# ---------------------------------------------------------------------------
# Fetcher protocol + Alpaca adapter
# ---------------------------------------------------------------------------


@dataclass
class BarFetchRequest:
    symbol: str
    trade_date: str
    window_start_et: dt.datetime
    window_end_et: dt.datetime
    feed: str


BarFetcher = Callable[[BarFetchRequest], Optional[pd.DataFrame]]
"""Returns a DataFrame with the per-bar OHLCV columns, or ``None`` when the
vendor returned no data for the requested symbol/date. Implementations must
NOT raise on missing data; raise only on auth or vendor protocol failures.
"""


def _bars_to_frame(symbol: str, trade_date: str, raw_bars: Iterable[Any], feed: str, retrieved_at: str) -> Optional[pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    for bar in raw_bars:
        ts = getattr(bar, "timestamp", None) if not isinstance(bar, dict) else bar.get("timestamp")
        if ts is None:
            continue
        ts_value = pd.Timestamp(ts)
        if ts_value.tzinfo is None:
            ts_value = ts_value.tz_localize("UTC")
        bar_start_utc = ts_value.tz_convert("UTC").isoformat().replace("+00:00", "Z")

        def _get(name: str) -> Any:
            return bar.get(name) if isinstance(bar, dict) else getattr(bar, name, None)

        rows.append(
            {
                "symbol": symbol,
                "trade_date": trade_date,
                "bar_start_ts": bar_start_utc,
                "open": _coerce_float(_get("open")),
                "high": _coerce_float(_get("high")),
                "low": _coerce_float(_get("low")),
                "close": _coerce_float(_get("close")),
                "volume": _coerce_float(_get("volume")),
                "trade_count": _coerce_float(_get("trade_count")),
                "vwap": _coerce_float(_get("vwap")),
                "source": SOURCE_LABEL,
                "feed": feed,
                "retrieved_at": retrieved_at,
            }
        )
    if not rows:
        return None
    frame = pd.DataFrame(rows, columns=PARQUET_COLUMNS)
    frame = frame.sort_values("bar_start_ts", kind="mergesort").reset_index(drop=True)
    return frame


def _coerce_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def make_alpaca_fetcher(*, client: Any | None = None) -> BarFetcher:
    """Build a fetcher backed by alpaca-py's StockHistoricalDataClient.

    The client and request types are imported lazily so this module can be
    imported in test environments that do not have credentials configured.
    """

    def _fetch(req: BarFetchRequest) -> Optional[pd.DataFrame]:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        nonlocal client
        if client is None:
            from brokers.alpaca_broker import load_alpaca_env

            cfg = load_alpaca_env()
            client = StockHistoricalDataClient(cfg.key_id, cfg.secret_key)

        try:
            request = StockBarsRequest(
                symbol_or_symbols=req.symbol,
                timeframe=TimeFrame.Minute,
                start=req.window_start_et.astimezone(UTC),
                end=req.window_end_et.astimezone(UTC),
                feed=req.feed,
            )
            response = client.get_stock_bars(request)
        except Exception as exc:
            raise RuntimeError(f"alpaca_get_stock_bars_failed symbol={req.symbol}: {exc}") from exc

        # Stamp ``retrieved_at`` immediately after the response returns so the
        # provenance reflects the actual data-receipt time, not the request
        # construction time.
        retrieved_at = dt.datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

        data = getattr(response, "data", None)
        if isinstance(data, dict):
            bars = data.get(req.symbol) or []
        else:
            bars = list(response or [])
        return _bars_to_frame(req.symbol, req.trade_date, bars, req.feed, retrieved_at)

    return _fetch


# ---------------------------------------------------------------------------
# Cache write — immutable
# ---------------------------------------------------------------------------


def cache_path_for(
    symbol: str,
    trade_date: str,
    root: Path = DEFAULT_CACHE_ROOT,
    cache_key_version: str = CACHE_KEY_VERSION,
) -> Path:
    """Return the canonical parquet path for ``(symbol, trade_date)``.

    The cache key version is embedded in the path so a non-canonical run
    cannot overlay or shadow the canonical cache.
    """
    return root / cache_key_version / symbol.upper() / f"{trade_date}.parquet"


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
    try:
        frame.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Top-level collector
# ---------------------------------------------------------------------------


@dataclass
class SymbolResult:
    symbol: str
    status: str  # "fetched" | "cached" | "missing" | "error"
    rows: int = 0
    path: Optional[str] = None
    reason: Optional[str] = None
    retrieved_at: Optional[str] = None


@dataclass
class CollectionResult:
    trade_date: str
    plan_source: str
    status_path: Path
    overall_status: str
    counts: dict[str, int]
    cache_key_version: str
    symbol_results: list[SymbolResult] = field(default_factory=list)


def _window_for(trade_date: str, window_start: dt.time, window_end: dt.time) -> tuple[dt.datetime, dt.datetime]:
    day = dt.date.fromisoformat(trade_date)
    start_et = dt.datetime.combine(day, window_start, tzinfo=ET)
    end_et = dt.datetime.combine(day, window_end, tzinfo=ET)
    return start_et, end_et


def collect_intraday_cache(
    *,
    trade_date: str,
    plan_path: Optional[Path] = None,
    plan_root: Path = DEFAULT_PLAN_ROOT,
    cache_root: Path = DEFAULT_CACHE_ROOT,
    status_root: Path = DEFAULT_STATUS_ROOT,
    fetcher: Optional[BarFetcher] = None,
    feed: str = CANONICAL_FEED,
    window_start: dt.time = CANONICAL_WINDOW_START_ET,
    window_end: dt.time = CANONICAL_WINDOW_END_ET,
    now: Optional[dt.datetime] = None,
    symbols_override: Optional[list[str]] = None,
    require_trading_day: bool = True,
) -> CollectionResult:
    """Fetch + freeze minute bars for the plan's symbols on ``trade_date``.

    A symbol's parquet file is treated as immutable: if it already exists at
    the resolved cache-key-version path the fetcher is NOT called for that
    symbol and the result is recorded as ``cached``. This makes reruns safe
    and deterministic.

    The cache path embeds :func:`derive_cache_key_version`, so a non-canonical
    (feed, window) combination writes to a sibling directory and cannot
    pollute the canonical cache.
    """

    if require_trading_day and not is_trading_day(trade_date):
        raise ValueError(
            f"trade_date_is_not_trading_day: {trade_date} is not a US equities trading day; "
            "refuse to fetch intraday bars."
        )

    resolved_plan = Path(plan_path) if plan_path else resolve_plan_path(trade_date, plan_root)
    if symbols_override is not None:
        symbols = sorted({s.strip().upper() for s in symbols_override if s and s.strip()})
        plan_source = "override"
    else:
        symbols = load_plan_symbols(resolved_plan)
        plan_source = str(resolved_plan)

    if fetcher is None:
        fetcher = make_alpaca_fetcher()

    cache_key_version = derive_cache_key_version(feed, window_start, window_end)
    window_start_et, window_end_et = _window_for(trade_date, window_start, window_end)
    now_utc = (now or dt.datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    generated_at = now_utc.isoformat().replace("+00:00", "Z")

    symbol_results: list[SymbolResult] = []
    counts = {"fetched": 0, "cached": 0, "missing": 0, "errors": 0}

    for symbol in symbols:
        out_path = cache_path_for(symbol, trade_date, cache_root, cache_key_version)
        if out_path.exists():
            try:
                row_count = int(pd.read_parquet(out_path, columns=["bar_start_ts"]).shape[0])
            except Exception:
                row_count = 0
            symbol_results.append(
                SymbolResult(symbol=symbol, status="cached", rows=row_count, path=str(out_path))
            )
            counts["cached"] += 1
            continue

        req = BarFetchRequest(
            symbol=symbol,
            trade_date=trade_date,
            window_start_et=window_start_et,
            window_end_et=window_end_et,
            feed=feed,
        )
        try:
            frame = fetcher(req)
        except Exception as exc:
            logger.warning("[INTRADAY_CACHE] fetch failed symbol=%s err=%s", symbol, exc)
            symbol_results.append(
                SymbolResult(symbol=symbol, status="error", rows=0, reason=str(exc))
            )
            counts["errors"] += 1
            continue

        if frame is None or frame.empty:
            symbol_results.append(
                SymbolResult(symbol=symbol, status="missing", rows=0, reason="no_bars_returned")
            )
            counts["missing"] += 1
            continue

        _atomic_write_parquet(frame, out_path)
        retrieved_at = str(frame.iloc[0].get("retrieved_at", generated_at))
        symbol_results.append(
            SymbolResult(
                symbol=symbol,
                status="fetched",
                rows=int(frame.shape[0]),
                path=str(out_path),
                retrieved_at=retrieved_at,
            )
        )
        counts["fetched"] += 1

    if counts["errors"] > 0:
        overall_status = "FAILED"
    elif counts["missing"] > 0 and counts["fetched"] == 0 and counts["cached"] == 0:
        overall_status = "FAILED"
    elif counts["missing"] > 0:
        overall_status = "PARTIAL"
    else:
        overall_status = "OK"

    status_path = status_root / trade_date / "status.json"
    status_payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "cache_key_version": cache_key_version,
        "trade_date": trade_date,
        "generated_at": generated_at,
        "plan_source": plan_source,
        "cache_root": str(cache_root),
        "intraday_source": SOURCE_LABEL,
        "feed": feed,
        "session_window": {
            "start_et": window_start_et.isoformat(),
            "end_et": window_end_et.isoformat(),
        },
        "symbols_requested": symbols,
        "symbol_results": [
            {k: v for k, v in sorted(_result_to_dict(r).items()) if v is not None}
            for r in symbol_results
        ],
        "counts": counts,
        "overall_status": overall_status,
        "notes": (
            "Research-only intraday minute-bar cache; no orders submitted, "
            "no execution-path artifacts modified. See "
            "specs/execution_timing_sensitivity_study.md."
        ),
    }
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(
        json.dumps(status_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return CollectionResult(
        trade_date=trade_date,
        plan_source=plan_source,
        status_path=status_path,
        overall_status=overall_status,
        counts=counts,
        cache_key_version=cache_key_version,
        symbol_results=symbol_results,
    )


def _result_to_dict(result: SymbolResult) -> dict[str, Any]:
    return {
        "symbol": result.symbol,
        "status": result.status,
        "rows": result.rows,
        "path": result.path,
        "reason": result.reason,
        "retrieved_at": result.retrieved_at,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Research-only intraday minute-bar cache for the execution-timing "
            "sensitivity study. Reads a precompute plan and freezes minute "
            f"bars per symbol into an immutable parquet cache. Locked to the "
            f"Phase 1 contract: window={CANONICAL_WINDOW_START_ET.strftime('%H:%M')}"
            f"-{CANONICAL_WINDOW_END_ET.strftime('%H:%M')} ET, feed={CANONICAL_FEED}, "
            f"cache_key_version={CACHE_KEY_VERSION}. Never modifies execution-path "
            "artifacts."
        )
    )
    parser.add_argument("--trade-date", required=True, help="ISO trade date, e.g. 2026-03-24")
    parser.add_argument(
        "--plan-path",
        default=None,
        help="Override plan path; defaults to outputs/precompute/<DATE>/planned_execution_payload.json",
    )
    parser.add_argument("--plan-root", default=str(DEFAULT_PLAN_ROOT))
    parser.add_argument("--cache-root", default=str(DEFAULT_CACHE_ROOT))
    parser.add_argument("--status-root", default=str(DEFAULT_STATUS_ROOT))
    parser.add_argument(
        "--symbol",
        action="append",
        default=None,
        help="Override symbol list; may be passed multiple times. Bypasses plan discovery.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve plan + symbols and print the run summary without fetching or writing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = build_parser().parse_args(argv)

    cache_root = Path(args.cache_root)
    status_root = Path(args.status_root)
    plan_root = Path(args.plan_root)
    plan_path = Path(args.plan_path) if args.plan_path else None

    if not is_trading_day(args.trade_date):
        print(
            json.dumps(
                {
                    "trade_date": args.trade_date,
                    "overall_status": "REFUSED",
                    "reason": "trade_date_is_not_trading_day",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    if args.dry_run:
        resolved_plan = plan_path or resolve_plan_path(args.trade_date, plan_root)
        symbols = (
            sorted({s.strip().upper() for s in args.symbol if s and s.strip()})
            if args.symbol
            else load_plan_symbols(resolved_plan)
        )
        summary = {
            "trade_date": args.trade_date,
            "plan_source": "override" if args.symbol else str(resolved_plan),
            "symbols_requested": symbols,
            "feed": CANONICAL_FEED,
            "cache_key_version": CACHE_KEY_VERSION,
            "cache_root": str(cache_root),
            "status_root": str(status_root),
            "session_window_et": {
                "start": CANONICAL_WINDOW_START_ET.isoformat(),
                "end": CANONICAL_WINDOW_END_ET.isoformat(),
            },
            "dry_run": True,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    result = collect_intraday_cache(
        trade_date=args.trade_date,
        plan_path=plan_path,
        plan_root=plan_root,
        cache_root=cache_root,
        status_root=status_root,
        symbols_override=args.symbol,
    )
    print(
        json.dumps(
            {
                "trade_date": result.trade_date,
                "overall_status": result.overall_status,
                "counts": result.counts,
                "cache_key_version": result.cache_key_version,
                "status_path": str(result.status_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.overall_status != "FAILED" else 1


if __name__ == "__main__":
    sys.exit(main())
