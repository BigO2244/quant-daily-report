from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_LOCAL_PANEL_PATHS = (
    Path("outputs/research/ma_vol_hypothesis/price_panel.parquet"),
)
DEFAULT_TICKER_EXCEPTIONS_PATH = Path("data/ticker_exceptions.json")


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
) -> tuple[pd.DataFrame, dict]:
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

    fetched_frames: list[pd.DataFrame] = []
    fetched = pd.DataFrame()
    download_start_by_symbol: dict[str, str] = {}
    download_start_date: str | None = None
    download_failed_symbols: list[str] = []
    download_errors: dict[str, str] = {}
    download_symbols = sorted(set(missing_symbols + incomplete_symbols))
    needs_download = allow_download and bool(download_symbols)
    if needs_download:
        # Build per-symbol start dates: missing symbols need full history;
        # incomplete symbols can tail from their own max_date + 1 day.
        download_start_by_symbol = {
            sym: str(pd.Timestamp(start_date).date()) if sym in missing_symbols
            else str(max(pd.Timestamp(start_date), coverage[sym]["max_date"] + pd.Timedelta(days=1)).date())
            for sym in download_symbols
        }
        download_start_date = min(download_start_by_symbol.values()) if download_start_by_symbol else None

        def fetch_group(group_symbols: Sequence[str], group_start_date: str) -> pd.DataFrame:
            group_aliases = {sym: aliased_tickers[sym] for sym in group_symbols if sym in aliased_tickers}
            provider_symbols = [group_aliases.get(sym, sym) for sym in group_symbols]
            try:
                frame = download_price_panel(
                    symbols=provider_symbols,
                    start_date=group_start_date,
                    end_date=end_date,
                    chunk_size=chunk_size,
                )
                frame = apply_provider_aliases(frame, group_aliases)
            except Exception as exc:
                logger.warning(
                    "[FLOW] Download group failed; retrying symbols individually: symbols=%s error=%s",
                    ",".join(group_symbols),
                    exc,
                )
                frames: list[pd.DataFrame] = []
                for sym in group_symbols:
                    provider_sym = group_aliases.get(sym, sym)
                    try:
                        single = download_price_panel(
                            symbols=[provider_sym],
                            start_date=group_start_date,
                            end_date=end_date,
                            chunk_size=1,
                        )
                        single = apply_provider_aliases(single, {sym: provider_sym} if provider_sym != sym else {})
                    except Exception as single_exc:
                        download_failed_symbols.append(str(sym).upper())
                        download_errors[str(sym).upper()] = str(single_exc)
                        logger.warning("[FLOW] Download failed for symbol=%s: %s", sym, single_exc)
                        continue
                    if single.empty:
                        download_failed_symbols.append(str(sym).upper())
                        download_errors[str(sym).upper()] = "empty_download"
                    else:
                        frames.append(single)
                return (
                    pd.concat(frames, ignore_index=True)
                    if frames
                    else pd.DataFrame(columns=["date", "ticker", "open", "high", "low", "close", "volume"])
                )
            present = {str(sym).upper() for sym in frame["ticker"].unique()} if not frame.empty and "ticker" in frame.columns else set()
            for sym in group_symbols:
                sym_upper = str(sym).upper()
                if sym_upper not in present:
                    download_failed_symbols.append(sym_upper)
                    download_errors[sym_upper] = "empty_download"
            return frame

        if missing_symbols:
            fetched_frames.append(fetch_group(missing_symbols, start_date))
        # Group incomplete symbols by their individual tail-start date to avoid
        # leaving gaps when stale symbols have differing max_dates.
        if incomplete_symbols:
            groups: dict[str, list[str]] = {}
            for sym in incomplete_symbols:
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
        panel = pd.concat([panel, fetched], ignore_index=True) if not panel.empty else fetched
        panel = panel.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)
        if cache_path_obj is not None:
            cache_path_obj.parent.mkdir(parents=True, exist_ok=True)
            cache_write = pd.concat([raw_cache_panel, fetched], ignore_index=True) if not raw_cache_panel.empty else fetched
            cache_write = cache_write.sort_values(["ticker", "date"]).drop_duplicates(["ticker", "date"], keep="last").reset_index(drop=True)
            cache_write.to_parquet(cache_path_obj, index=False)
        panel = filter_panel_window(panel, start_date=start_date, end_date=end_date)

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


def pivot_close_matrix(panel: pd.DataFrame) -> pd.DataFrame:
    wide = panel.pivot(index="date", columns="ticker", values="close").sort_index()
    return wide


def available_window_years(panel: pd.DataFrame) -> float:
    if panel.empty:
        return 0.0
    dates = pd.to_datetime(panel["date"]).sort_values()
    span_days = (dates.max() - dates.min()).days
    return round(span_days / 365.25, 2)
