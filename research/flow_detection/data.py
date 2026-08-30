from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
from paper.trading_calendar import is_trading_day, prev_trading_day

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_PANEL_PATHS = (
    Path("outputs/research/ma_vol_hypothesis/price_panel.parquet"),
)
DEFAULT_TICKER_EXCEPTIONS_PATH = Path("data/ticker_exceptions.json")


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _trading_sessions(start_date: str, end_date: str) -> list[str]:
    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()
    if end < start:
        return []
    return [
        str(value.date())
        for value in pd.date_range(start, end, freq="D")
        if is_trading_day(str(value.date()))
    ]


def _validate_catchup_sessions(
    panel: pd.DataFrame,
    *,
    download_start_by_symbol: dict[str, str],
    tail_symbols: Sequence[str],
    end_date: str,
) -> dict:
    dates_by_symbol = {
        str(ticker).upper(): {str(pd.Timestamp(value).date()) for value in group["date"]}
        for ticker, group in panel.groupby("ticker")
    } if not panel.empty else {}
    missing: dict[str, list[str]] = {}
    expected_count = 0
    for symbol in sorted({str(value).upper() for value in tail_symbols}):
        expected = _trading_sessions(download_start_by_symbol[symbol], end_date)
        expected_count += len(expected)
        absent = [date for date in expected if date not in dates_by_symbol.get(symbol, set())]
        if absent:
            missing[symbol] = absent
    return {
        "status": "OK" if not missing else "INCOMPLETE",
        "downloaded_symbols": sorted({str(value).upper() for value in tail_symbols}),
        "tail_symbols": sorted({str(value).upper() for value in tail_symbols}),
        "expected_symbol_sessions": expected_count,
        "missing_sessions_by_symbol": missing,
        "missing_symbol_sessions": sum(len(values) for values in missing.values()),
    }


def _atomic_write_price_cache(frame: pd.DataFrame, path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    before_hash = _file_sha256(path)
    descriptor, staged_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".parquet", dir=path.parent)
    os.close(descriptor)
    staged_path = Path(staged_name)
    backup_path: Path | None = None
    had_prior = path.is_file()
    try:
        canonical_frame = standardize_panel(frame)
        canonical_frame.to_parquet(staged_path, index=False)
        staged_frame = standardize_panel(pd.read_parquet(staged_path))
        try:
            pd.testing.assert_frame_equal(staged_frame, canonical_frame, check_dtype=False)
        except AssertionError as exc:
            raise RuntimeError("staged price cache changed during parquet round trip") from exc
        staged_hash = _file_sha256(staged_path)
        if not staged_hash:
            raise RuntimeError("staged price cache has no verifiable hash")
        if had_prior:
            backup_descriptor, backup_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".prior", dir=path.parent
            )
            os.close(backup_descriptor)
            backup_path = Path(backup_name)
            shutil.copyfile(path, backup_path)
            if _file_sha256(backup_path) != before_hash:
                raise RuntimeError("recoverable prior price cache hash mismatch")
        os.replace(staged_path, path)
        try:
            canonical_hash = _file_sha256(path)
            if canonical_hash != staged_hash:
                raise RuntimeError(
                    "published price cache hash does not match validated staged artifact"
                )
        except Exception:
            try:
                if backup_path is not None:
                    os.replace(backup_path, path)
                    backup_path = None
                elif not had_prior and path.exists():
                    path.unlink()
            except Exception as rollback_exc:
                raise RuntimeError(
                    "price cache publication verification and rollback both failed"
                ) from rollback_exc
            raise
        return {
            "status": "PUBLISHED",
            "before_sha256": before_hash,
            "staged_sha256": staged_hash,
            "canonical_sha256": canonical_hash,
            "rows": len(canonical_frame),
        }
    finally:
        if staged_path.exists():
            staged_path.unlink()
        if backup_path is not None and backup_path.exists():
            backup_path.unlink()


@dataclass(frozen=True)
class PricePanelCoverage:
    symbols: int
    start_date: str | None
    end_date: str | None


def load_universe(universe_path: str | Path = "data/universe.csv") -> list[str]:
    df = pd.read_csv(universe_path)
    if "ticker" not in df.columns:
        raise ValueError(f"{universe_path} must contain a ticker column")
    tickers = sorted({str(t).strip().upper() for t in df["ticker"].dropna() if str(t).strip()})
    return tickers


def load_ticker_exceptions(path: str | Path = DEFAULT_TICKER_EXCEPTIONS_PATH) -> dict[str, dict | list]:
    path_obj = Path(path)
    if not path_obj.exists():
        return {"ignore": [], "aliases": {}, "notes": {}}
    try:
        payload = json.loads(path_obj.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("[FLOW] Ticker exceptions unreadable: %s", path_obj)
        return {"ignore": [], "aliases": {}, "notes": {}}
    if not isinstance(payload, dict):
        return {"ignore": [], "aliases": {}, "notes": {}}
    ignore = sorted({str(item).strip().upper() for item in payload.get("ignore", []) if str(item).strip()})
    aliases = {
        str(key).strip().upper(): str(value).strip().upper()
        for key, value in (payload.get("aliases") or {}).items()
        if str(key).strip() and str(value).strip()
    }
    notes = {
        str(key).strip().upper(): str(value)
        for key, value in (payload.get("notes") or {}).items()
        if str(key).strip()
    }
    return {"ignore": ignore, "aliases": aliases, "notes": notes}


def standardize_panel(df: pd.DataFrame) -> pd.DataFrame:
    col_map = {str(col).lower(): col for col in df.columns}
    rename_map = {}
    for canonical in ("date", "ticker", "open", "high", "low", "close", "volume", "sector"):
        if canonical in col_map and col_map[canonical] != canonical:
            rename_map[col_map[canonical]] = canonical
    if rename_map:
        df = df.rename(columns=rename_map)
    required = {"date", "ticker", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"price panel missing required columns: {sorted(missing)}")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    out["ticker"] = out["ticker"].astype(str).str.upper()
    for col in ("open", "high", "low", "close", "volume"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    keep = ["date", "ticker", "open", "high", "low", "close", "volume"]
    if "sector" in out.columns:
        keep.append("sector")
    out = out[keep].dropna(subset=["date", "ticker", "close"])
    out = out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    return out.reset_index(drop=True)


def filter_panel_window(df: pd.DataFrame, *, start_date: str, end_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    return df[
        (df["date"] >= pd.Timestamp(start_date))
        & (df["date"] <= pd.Timestamp(end_date))
    ].copy().reset_index(drop=True)


def load_local_price_panel(
    *,
    symbols: Sequence[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    paths: Sequence[Path] = DEFAULT_LOCAL_PANEL_PATHS,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    symbol_set = {s.upper() for s in symbols} if symbols else None
    for path in paths:
        if not path.exists():
            continue
        df = pd.read_parquet(path)
        df = standardize_panel(df)
        if symbol_set is not None:
            df = df[df["ticker"].isin(symbol_set)]
        if start_date:
            df = df[df["date"] >= pd.Timestamp(start_date)]
        if end_date:
            df = df[df["date"] <= pd.Timestamp(end_date)]
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    return out.reset_index(drop=True)


def panel_coverage(panel: pd.DataFrame) -> PricePanelCoverage:
    if panel.empty:
        return PricePanelCoverage(symbols=0, start_date=None, end_date=None)
    return PricePanelCoverage(
        symbols=int(panel["ticker"].nunique()),
        start_date=str(panel["date"].min().date()),
        end_date=str(panel["date"].max().date()),
    )


def _normalize_yfinance_download(raw: pd.DataFrame, symbols: Sequence[str]) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])

    if isinstance(raw.columns, pd.MultiIndex):
        level_names = [str(name).lower() if name is not None else "" for name in raw.columns.names]
        if "ticker" in level_names and len(raw.columns.levels) >= 2:
            ticker_level = level_names.index("ticker")
            price_level = 1 - ticker_level if len(raw.columns.names) == 2 else 0
            frames: list[pd.DataFrame] = []
            for ticker in symbols:
                ticker_upper = ticker.upper()
                if ticker_upper not in {str(v).upper() for v in raw.columns.get_level_values(ticker_level)}:
                    continue
                block = raw.xs(ticker_upper, axis=1, level=ticker_level, drop_level=True).copy()
                block.columns = [str(col).lower() for col in block.columns]
                block = block.reset_index().rename(columns={block.index.name or "index": "date"})
                block["ticker"] = ticker_upper
                frames.append(block)
            if frames:
                return standardize_panel(pd.concat(frames, ignore_index=True))
        flattened = raw.copy()
        flattened.columns = [str(levels[0]).lower() for levels in raw.columns]
        raw = flattened
    else:
        raw = raw.copy()
        raw.columns = [str(col).lower() for col in raw.columns]

    date_col = raw.index.name or "index"
    out = raw.reset_index().rename(columns={date_col: "date"})
    ticker = symbols[0].upper() if symbols else "UNKNOWN"
    out["ticker"] = ticker
    return standardize_panel(out)


def download_price_panel(
    *,
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    chunk_size: int = 25,
    pause_seconds: float = 0.0,
) -> pd.DataFrame:
    import yfinance as yf

    end_ts = pd.Timestamp(end_date) + pd.Timedelta(days=1)
    chunks: list[pd.DataFrame] = []
    symbol_list = [str(sym).upper() for sym in symbols]
    for idx in range(0, len(symbol_list), max(1, chunk_size)):
        chunk = symbol_list[idx: idx + max(1, chunk_size)]
        logger.info("[FLOW] Downloading OHLCV chunk %d-%d of %d", idx + 1, idx + len(chunk), len(symbol_list))
        raw = yf.download(
            chunk,
            start=str(start_date),
            end=end_ts.strftime("%Y-%m-%d"),
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )
        normalized = _normalize_yfinance_download(raw, chunk)
        if not normalized.empty:
            chunks.append(normalized)
        if pause_seconds > 0:
            time.sleep(pause_seconds)
    if not chunks:
        return pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
    out = pd.concat(chunks, ignore_index=True)
    out = out.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
    return out.reset_index(drop=True)


def ensure_price_panel(
    *,
    symbols: Sequence[str],
    start_date: str,
    end_date: str,
    cache_path: str | Path | None = None,
    prefer_local: bool = True,
    allow_download: bool = True,
    chunk_size: int = 25,
    ticker_exceptions_path: str | Path = DEFAULT_TICKER_EXCEPTIONS_PATH,
    required_anchor_dates: Sequence[str] = (),
    required_history_offsets: Sequence[int] = (),
    provider_group_attempts: int = 3,
    provider_retry_backoff_seconds: float = 1.0,
) -> tuple[pd.DataFrame, dict]:
    if provider_group_attempts < 1:
        raise ValueError("provider_group_attempts must be at least 1")
    if provider_retry_backoff_seconds < 0:
        raise ValueError("provider_retry_backoff_seconds cannot be negative")
    symbol_set = {str(sym).upper() for sym in symbols}
    exceptions = load_ticker_exceptions(ticker_exceptions_path)
    ignored_tickers = sorted(symbol_set & set(exceptions.get("ignore") or []))
    aliases_all = dict(exceptions.get("aliases") or {})
    aliased_tickers = {
        sym: aliases_all[sym]
        for sym in sorted(symbol_set)
        if sym in aliases_all and sym not in ignored_tickers
    }
    for ticker in ignored_tickers:
        logger.info("Skipping ignored ticker: %s", ticker)
    active_symbol_set = symbol_set - set(ignored_tickers)

    def apply_provider_aliases(frame: pd.DataFrame, requested_to_provider: dict[str, str]) -> pd.DataFrame:
        if frame.empty or "ticker" not in frame.columns or not requested_to_provider:
            return frame
        provider_to_requested = {provider: requested for requested, provider in requested_to_provider.items()}
        out = frame.copy()
        out["ticker"] = out["ticker"].replace(provider_to_requested)
        return out

    raw_cache_panel = pd.DataFrame()
    cache_panel = pd.DataFrame()
    cache_source = None
    cache_path_obj = Path(cache_path) if cache_path else None
    cache_before_sha256 = _file_sha256(cache_path_obj) if cache_path_obj else None
    cache_publish: dict[str, Any] = {
        "status": "NOT_NEEDED",
        "before_sha256": cache_before_sha256,
        "canonical_sha256": cache_before_sha256,
    }
    if cache_path_obj and cache_path_obj.exists():
        raw_cache_panel = standardize_panel(pd.read_parquet(cache_path_obj))
        raw_cache_panel = apply_provider_aliases(raw_cache_panel, aliased_tickers)
        cache_panel = filter_panel_window(raw_cache_panel, start_date=start_date, end_date=end_date)
        cache_panel = cache_panel[cache_panel["ticker"].isin(active_symbol_set)].copy()
        cache_source = str(cache_path_obj)

    local_panel = pd.DataFrame()
    if prefer_local:
        local_panel = load_local_price_panel(symbols=symbols, start_date=start_date, end_date=end_date)
        local_panel = apply_provider_aliases(local_panel, aliased_tickers)

    if cache_panel.empty:
        panel = local_panel.copy()
    elif local_panel.empty:
        panel = cache_panel.copy()
    else:
        panel = pd.concat([cache_panel, local_panel], ignore_index=True)
    if not panel.empty:
        panel = panel.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last")
        panel = filter_panel_window(panel, start_date=start_date, end_date=end_date)
        panel = panel.reset_index(drop=True)

    coverage = _coverage_by_symbol(panel)
    missing_symbols = sorted(active_symbol_set - set(coverage))
    incomplete_symbols = sorted(
        sym for sym, meta in coverage.items()
        if sym in active_symbol_set and meta["max_date"] < pd.Timestamp(end_date)
    )
    initial_validation = validate_symbol_coverage(
        panel,
        symbols=sorted(active_symbol_set),
        current_session=end_date,
        required_anchor_dates=required_anchor_dates,
        required_history_offsets=required_history_offsets,
    )
    missing_anchor_dates_by_symbol: dict[str, list[str]] = {}
    for anchor_date, missing_at_anchor in (initial_validation.get("missing_required_anchor_symbols") or {}).items():
        for symbol in missing_at_anchor:
            missing_anchor_dates_by_symbol.setdefault(symbol, []).append(anchor_date)
    anchor_incomplete_symbols = sorted(set(missing_anchor_dates_by_symbol) - set(missing_symbols))

    fetched_frames: list[pd.DataFrame] = []
    fetched = pd.DataFrame()
    download_start_by_symbol: dict[str, str] = {}
    download_start_date: str | None = None
    download_failed_symbols: list[str] = []
    download_errors: dict[str, str] = {}
    download_attempts: list[dict[str, Any]] = []
    catchup_validation: dict[str, Any] = {
        "status": "NOT_REQUIRED",
        "downloaded_symbols": [],
        "tail_symbols": [],
        "expected_symbol_sessions": 0,
        "missing_sessions_by_symbol": {},
        "missing_symbol_sessions": 0,
    }
    download_symbols = sorted(set(missing_symbols + incomplete_symbols + anchor_incomplete_symbols))
    needs_download = allow_download and bool(download_symbols)
    if needs_download:
        # Build per-symbol start dates: missing symbols need full history;
        # incomplete symbols can tail from their own max_date + 1 day.
        download_start_by_symbol = {
            sym: str(pd.Timestamp(start_date).date()) if sym in missing_symbols
            else str(max(pd.Timestamp(start_date), pd.Timestamp(min(missing_anchor_dates_by_symbol[sym]))).date())
            if sym in missing_anchor_dates_by_symbol
            else str(max(pd.Timestamp(start_date), coverage[sym]["max_date"] + pd.Timedelta(days=1)).date())
            for sym in download_symbols
        }
        download_start_date = min(download_start_by_symbol.values()) if download_start_by_symbol else None

        empty_panel = pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])

        def fetch_single(symbol: str, group_start_date: str, alias: str | None) -> pd.DataFrame:
            symbol = str(symbol).upper()
            provider_symbol = alias or symbol
            attempt_record: dict[str, Any] = {
                "scope": "SYMBOL",
                "attempt": 1,
                "symbols": [symbol],
                "provider_symbols": [provider_symbol],
                "start_date": group_start_date,
                "end_date": end_date,
            }
            try:
                single = download_price_panel(
                    symbols=[provider_symbol],
                    start_date=group_start_date,
                    end_date=end_date,
                    chunk_size=1,
                )
                single = apply_provider_aliases(
                    single,
                    {symbol: provider_symbol} if provider_symbol != symbol else {},
                )
            except Exception as exc:
                attempt_record.update({"result": "ERROR", "error": str(exc)})
                download_attempts.append(attempt_record)
                download_failed_symbols.append(symbol)
                download_errors[symbol] = str(exc)
                return empty_panel.copy()
            present = set(single.get("ticker", pd.Series(dtype=str)).astype(str).str.upper())
            if single.empty or symbol not in present:
                attempt_record.update({"result": "EMPTY", "returned_symbol_count": 0})
                download_attempts.append(attempt_record)
                download_failed_symbols.append(symbol)
                download_errors[symbol] = "empty_download_after_individual_retry"
                return empty_panel.copy()
            attempt_record.update({"result": "COMPLETE", "returned_symbol_count": 1})
            download_attempts.append(attempt_record)
            return single

        def fetch_group(group_symbols: Sequence[str], group_start_date: str) -> pd.DataFrame:
            normalized_symbols = [str(value).upper() for value in group_symbols]
            group_aliases = {sym: aliased_tickers[sym] for sym in normalized_symbols if sym in aliased_tickers}
            provider_symbols = [group_aliases.get(sym, sym) for sym in normalized_symbols]
            frame = empty_panel.copy()
            group_results: list[str] = []
            for attempt in range(1, provider_group_attempts + 1):
                attempt_record: dict[str, Any] = {
                    "scope": "GROUP",
                    "attempt": attempt,
                    "symbols": normalized_symbols,
                    "provider_symbols": provider_symbols,
                    "start_date": group_start_date,
                    "end_date": end_date,
                }
                try:
                    candidate = download_price_panel(
                        symbols=provider_symbols,
                        start_date=group_start_date,
                        end_date=end_date,
                        chunk_size=chunk_size,
                    )
                    candidate = apply_provider_aliases(candidate, group_aliases)
                    present_count = int(candidate["ticker"].nunique()) if not candidate.empty else 0
                    result = "EMPTY" if candidate.empty else (
                        "COMPLETE" if present_count == len(normalized_symbols) else "PARTIAL"
                    )
                    attempt_record.update({"result": result, "returned_symbol_count": present_count})
                    group_results.append(result)
                    download_attempts.append(attempt_record)
                    if not candidate.empty:
                        frame = candidate
                        break
                except Exception as exc:
                    attempt_record.update({"result": "ERROR", "error": str(exc)})
                    group_results.append("ERROR")
                    download_attempts.append(attempt_record)
                if attempt < provider_group_attempts and provider_retry_backoff_seconds:
                    time.sleep(provider_retry_backoff_seconds * attempt)

            systemic_empty = (
                frame.empty
                and "EMPTY" in group_results
                and set(group_results) <= {"EMPTY", "ERROR"}
                and len(normalized_symbols) > 1
            )
            if systemic_empty:
                failure_reason = (
                    "provider_systemic_empty_after_group_retries"
                    if set(group_results) == {"EMPTY"}
                    else "provider_systemic_mixed_empty_error_after_group_retries"
                )
                for symbol in normalized_symbols:
                    download_failed_symbols.append(symbol)
                    download_errors[symbol] = failure_reason
                logger.warning(
                    "[FLOW] Provider-wide empty/error response persisted for %d bounded attempts; "
                    "individual fanout suppressed: symbols=%d",
                    provider_group_attempts,
                    len(normalized_symbols),
                )
                return frame

            present = set(frame.get("ticker", pd.Series(dtype=str)).astype(str).str.upper())
            missing_after_group = [symbol for symbol in normalized_symbols if symbol not in present]
            retry_frames = [
                fetch_single(symbol, group_start_date, group_aliases.get(symbol))
                for symbol in missing_after_group
            ]
            successful_retries = [single for single in retry_frames if not single.empty]
            if successful_retries:
                frames_to_merge = successful_retries if frame.empty else [frame, *successful_retries]
                frame = pd.concat(frames_to_merge, ignore_index=True)
            return frame

        if missing_symbols:
            fetched_frames.append(fetch_group(missing_symbols, start_date))
        # Group stale or anchor-incomplete symbols by their individual start
        # date so historical holes are repaired instead of hidden by max_date.
        tail_or_anchor_symbols = sorted(set(download_symbols) - set(missing_symbols))
        if tail_or_anchor_symbols:
            groups: dict[str, list[str]] = {}
            for sym in tail_or_anchor_symbols:
                tail_start = download_start_by_symbol[sym]
                groups.setdefault(tail_start, []).append(sym)
            for tail_start, group_syms in groups.items():
                fetched_frames.append(fetch_group(group_syms, tail_start))
        download_failed_symbols = sorted(set(download_failed_symbols))
        fetched = (
            pd.concat([frame for frame in fetched_frames if not frame.empty], ignore_index=True)
            if any(not frame.empty for frame in fetched_frames)
            else pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
        )
        if panel.empty:
            panel = fetched
        elif not fetched.empty:
            panel = pd.concat([panel, fetched], ignore_index=True)
        panel = panel.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)
        panel = filter_panel_window(panel, start_date=start_date, end_date=end_date)
        if cache_path_obj is not None:
            if raw_cache_panel.empty:
                cache_write = fetched
            elif fetched.empty:
                cache_write = raw_cache_panel.copy()
            else:
                cache_write = pd.concat([raw_cache_panel, fetched], ignore_index=True)
            cache_write = cache_write.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)
            candidate_coverage = validate_symbol_coverage(
                panel,
                symbols=sorted(active_symbol_set),
                current_session=end_date,
                required_anchor_dates=required_anchor_dates,
                required_history_offsets=required_history_offsets,
            )
            catchup_validation = _validate_catchup_sessions(
                panel,
                download_start_by_symbol=download_start_by_symbol,
                tail_symbols=download_symbols,
                end_date=end_date,
            )
            publication_blocks: list[str] = []
            if candidate_coverage.get("status") != "OK":
                publication_blocks.append("required_current_or_anchor_coverage_incomplete")
            if catchup_validation.get("status") != "OK":
                publication_blocks.append("catchup_session_coverage_incomplete")
            if download_failed_symbols:
                publication_blocks.append("download_failures_present")
            if publication_blocks:
                cache_publish = {
                    "status": "BLOCKED_UNCHANGED",
                    "before_sha256": cache_before_sha256,
                    "canonical_sha256": _file_sha256(cache_path_obj),
                    "reason_codes": publication_blocks,
                }
            else:
                cache_publish = _atomic_write_price_cache(cache_write, cache_path_obj)

    final_coverage = validate_symbol_coverage(
        panel,
        symbols=sorted(active_symbol_set),
        current_session=end_date,
        required_anchor_dates=required_anchor_dates,
        required_history_offsets=required_history_offsets,
    )
    meta = {
        "requested_start_date": start_date,
        "requested_end_date": end_date,
        "symbols_requested": len(symbol_set),
        "coverage": panel_coverage(panel).__dict__,
        "download_performed": bool(needs_download),
        "download_start_date": str(download_start_date) if needs_download else None,
        "download_start_by_symbol": download_start_by_symbol if needs_download else {},
        "download_failed_symbols": download_failed_symbols,
        "download_errors": download_errors,
        "download_attempts": download_attempts,
        "ignored_tickers": ignored_tickers,
        "aliased_tickers": aliased_tickers,
        "ticker_exception_notes": {
            ticker: str((exceptions.get("notes") or {}).get(ticker) or "")
            for ticker in sorted(set(ignored_tickers) | set(aliased_tickers))
            if (exceptions.get("notes") or {}).get(ticker)
        },
        "cache_path": str(cache_path_obj) if cache_path_obj else None,
        "local_sources": [str(path) for path in DEFAULT_LOCAL_PANEL_PATHS if path.exists()],
        "cache_source": cache_source,
        "coverage_validation": final_coverage,
        "catchup_validation": catchup_validation,
        "cache_publish": cache_publish,
    }
    return panel, meta


def _coverage_by_symbol(panel: pd.DataFrame) -> dict[str, dict[str, pd.Timestamp]]:
    if panel.empty:
        return {}
    grouped = panel.groupby("ticker")["date"].agg(["min", "max"])
    return {
        str(ticker): {"min_date": row["min"], "max_date": row["max"]}
        for ticker, row in grouped.iterrows()
    }


def validate_symbol_coverage(
    panel: pd.DataFrame,
    *,
    symbols: Sequence[str],
    current_session: str,
    required_anchor_dates: Sequence[str] = (),
    required_history_offsets: Sequence[int] = (),
) -> dict:
    requested = sorted({str(symbol).upper() for symbol in symbols})
    normalized = standardize_panel(panel) if not panel.empty else panel
    dates_by_symbol = {
        str(ticker).upper(): {str(pd.Timestamp(value).date()) for value in group["date"]}
        for ticker, group in normalized.groupby("ticker")
    } if not normalized.empty else {}
    current = str(pd.Timestamp(current_session).date())
    anchors = {str(pd.Timestamp(value).date()) for value in required_anchor_dates}
    offsets = sorted({int(offset) for offset in required_history_offsets if int(offset) > 0})
    offset_anchors: dict[str, str | None] = {}
    session = current
    anchors_by_offset: dict[int, str] = {}
    for step in range(1, max(offsets, default=0) + 1):
        session = prev_trading_day(session)
        anchors_by_offset[step] = session
    for offset in offsets:
        anchor = anchors_by_offset.get(offset)
        offset_anchors[str(offset)] = anchor
        anchors.add(anchor)
    anchors = sorted(anchors)
    missing_current = [symbol for symbol in requested if current not in dates_by_symbol.get(symbol, set())]
    missing_anchors = {
        anchor: [symbol for symbol in requested if anchor not in dates_by_symbol.get(symbol, set())]
        for anchor in anchors
    }
    missing_anchors = {anchor: missing for anchor, missing in missing_anchors.items() if missing}
    unresolved_offsets = [int(offset) for offset, anchor in offset_anchors.items() if anchor is None]
    return {
        "status": "OK" if not missing_current and not missing_anchors and not unresolved_offsets else "INCOMPLETE",
        "current_session": current,
        "required_anchor_dates": anchors,
        "required_history_offsets": offsets,
        "history_offset_anchor_dates": offset_anchors,
        "symbols_required": requested,
        "symbols_required_count": len(requested),
        "symbols_current_count": len(requested) - len(missing_current),
        "missing_current_session_symbols": missing_current,
        "missing_required_anchor_symbols": missing_anchors,
        "unresolved_history_offsets": unresolved_offsets,
        "per_symbol": {
            symbol: {
                "min_date": min(dates_by_symbol[symbol]) if dates_by_symbol.get(symbol) else None,
                "max_date": max(dates_by_symbol[symbol]) if dates_by_symbol.get(symbol) else None,
                "has_current_session": current in dates_by_symbol.get(symbol, set()),
                "anchors_present": [anchor for anchor in anchors if anchor in dates_by_symbol.get(symbol, set())],
            }
            for symbol in requested
        },
    }


def pivot_close_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    wide = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    return wide


def available_window_years(panel: pd.DataFrame) -> float:
    if panel.empty:
        return 0.0
    dates = pd.to_datetime(panel["date"]).sort_values()
    span_days = (dates.max() - dates.min()).days
    return round(span_days / 365.25, 2)
