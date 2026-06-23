"""FR-068 market-cap reconstruction utilities (RESEARCH_ONLY).

This module is intentionally isolated from production trading paths.  It audits
whether the local Caerus research cache can replace the current
`scalemarketcap` large-cap family with a PIT numeric market-cap family, and it
provides a deterministic builder for a real daily market-cap panel when that
source exists.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from research.pit_large_cap_family import DEFAULT_MIN_MARKETCAP, US_EQUITY_EXCHANGES, normalize_ticker

SCHEMA_VERSION = "fr068_marketcap_reconstruction_v1"
COMMON_SHARES_TAG = "CommonStockSharesOutstanding"
DAILY_MARKETCAP_SOURCE = "sharadar_daily_marketcap"
DATE_CHECKPOINTS = ("2014-01-02", "2020-01-02", "2026-01-02")


@dataclass(frozen=True)
class SourceInventory:
    rows: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.rows)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stable_digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str)


def _is_common_stock(category: Any) -> bool:
    return "common stock" in str(category or "").lower()


def _active_on(row: pd.Series, as_of: pd.Timestamp) -> bool:
    first = pd.to_datetime(row.get("firstpricedate"), errors="coerce")
    last = pd.to_datetime(row.get("lastpricedate"), errors="coerce")
    if pd.isna(first) or as_of < first:
        return False
    if str(row.get("isdelisted") or "").upper().startswith("Y"):
        if pd.isna(last) or as_of > last:
            return False
    return True


def discover_source_inventory(repo_root: Path | str = Path(".")) -> SourceInventory:
    """Inventory local sources relevant to PIT market-cap reconstruction."""
    root = Path(repo_root)
    pit_dir = root / "data" / "pit_universe"
    master_path = pit_dir / "security_master.csv"
    membership_path = pit_dir / "membership_universe_large_cap.csv"
    fundamental_dir = root / "data" / "fundamental"
    edgar_dir = root / "data" / "alpha_stack_cache" / "edgar"
    sep_dir = root / "data" / "research_cache" / "sharadar_sep"
    sep_ohlcv_dir = root / "data" / "research_cache" / "sharadar_sep_ohlcv"

    master = _read_csv(master_path) if master_path.exists() else pd.DataFrame()
    membership = _read_csv(membership_path) if membership_path.exists() else pd.DataFrame()
    fundamental_files = sorted(fundamental_dir.glob("*.parquet")) if fundamental_dir.exists() else []
    edgar_files = sorted(edgar_dir.glob("facts_*.parquet")) if edgar_dir.exists() else []
    sep_files = sorted(sep_dir.glob("*.csv")) if sep_dir.exists() else []
    sep_ohlcv_files = sorted(sep_ohlcv_dir.glob("*.csv")) if sep_ohlcv_dir.exists() else []

    share_tag_files = 0
    for path in fundamental_files:
        try:
            tags = pd.read_parquet(path, columns=["tag"])["tag"].astype(str)
        except Exception:
            continue
        if (tags == COMMON_SHARES_TAG).any():
            share_tag_files += 1

    edgar_common_shares_files = 0
    for path in edgar_files:
        try:
            df = pd.read_parquet(path, columns=["concept", "field_name"])
        except Exception:
            continue
        concepts = df["concept"].astype(str).eq(COMMON_SHARES_TAG)
        fields = df["field_name"].astype(str).eq("shares_outstanding")
        if bool((concepts | fields).any()):
            edgar_common_shares_files += 1

    daily_candidates = []
    for pattern in (
        "data/**/sharadar_daily*",
        "data/**/*daily*marketcap*",
        "data/**/*marketcap*daily*",
        "data/**/*mktcap*daily*",
    ):
        daily_candidates.extend(root.glob(pattern))
    daily_cache_dir = root / "data" / "research_cache" / "sharadar_daily_marketcap"
    if daily_cache_dir.exists():
        daily_candidates.extend(daily_cache_dir.glob("*.csv"))
    daily_files = sorted({p for p in daily_candidates if p.is_file()})

    large_tickers = set(membership.get("ticker", pd.Series(dtype=str)).astype(str).map(normalize_ticker))
    fundamental_tickers = {normalize_ticker(p.stem) for p in fundamental_files}

    rows = {
        "schema_version": SCHEMA_VERSION,
        "security_master_path": str(master_path),
        "security_master_rows": int(len(master)),
        "security_master_has_numeric_marketcap": bool(
            any(str(c).lower() in {"marketcap", "market_cap", "mktcap"} for c in master.columns)
        ),
        "security_master_has_scalemarketcap": "scalemarketcap" in set(master.columns),
        "current_large_cap_membership_path": str(membership_path),
        "current_large_cap_rows": int(len(membership)),
        "current_large_cap_scale_source_counts": (
            membership.get("scale_source", pd.Series(dtype=str)).fillna("").value_counts().to_dict()
            if not membership.empty else {}
        ),
        "sep_price_file_count": len(sep_files),
        "sep_ohlcv_file_count": len(sep_ohlcv_files),
        "fundamental_file_count": len(fundamental_files),
        "fundamental_overlap_with_current_large_cap": len(large_tickers & fundamental_tickers),
        "fundamental_common_shares_file_count": share_tag_files,
        "edgar_fact_file_count": len(edgar_files),
        "edgar_common_shares_file_count": edgar_common_shares_files,
        "daily_marketcap_candidate_file_count": len(daily_files),
        "daily_marketcap_candidate_sample": [str(p) for p in daily_files[:25]],
        "nasdaq_data_link_api_key_present": bool(
            os.environ.get("NASDAQ_DATA_LINK_API_KEY") or os.environ.get("QUANDL_API_KEY")
        ),
    }
    rows["digest"] = stable_digest(rows)
    return SourceInventory(rows=rows)


def latest_reported_shares_as_of(fundamental_path: Path, as_of: pd.Timestamp) -> float | None:
    """Return the latest known common shares outstanding as of `as_of`.

    The timestamp gate is `filed_date <= as_of`; period end alone is not enough
    because it was not known until the filing date.
    """
    if not fundamental_path.exists():
        return None
    try:
        df = pd.read_parquet(
            fundamental_path,
            columns=["tag", "unit", "period_end", "filed_date", "form", "value"],
        )
    except Exception:
        return None
    df = df[df["tag"].astype(str).eq(COMMON_SHARES_TAG)].copy()
    if df.empty:
        return None
    df["filed_date"] = pd.to_datetime(df["filed_date"], errors="coerce")
    df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df[(df["filed_date"].notna()) & (df["filed_date"] <= as_of) & (df["value"] > 0)]
    if df.empty:
        return None
    df = df.sort_values(["filed_date", "period_end"], na_position="first")
    return float(df.iloc[-1]["value"])


def close_on_or_before(price_path: Path, as_of: pd.Timestamp, *, max_calendar_lag_days: int = 7) -> float | None:
    if not price_path.exists():
        return None
    try:
        df = pd.read_csv(price_path, usecols=lambda c: c in {"date", "close"})
    except Exception:
        return None
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df = df[(df["date"].notna()) & (df["close"] > 0) & (df["date"] <= as_of)]
    if df.empty:
        return None
    row = df.sort_values("date").iloc[-1]
    if (as_of - row["date"]).days > max_calendar_lag_days:
        return None
    return float(row["close"])


def evaluate_filing_based_marketcap_coverage(
    *,
    repo_root: Path | str = Path("."),
    as_of_dates: tuple[str, ...] = DATE_CHECKPOINTS,
    min_marketcap: float = DEFAULT_MIN_MARKETCAP,
) -> pd.DataFrame:
    """Measure whether local filings + SEP close can reconstruct market cap.

    This intentionally measures the current approximate large-cap family only.
    If this subset cannot be reconstructed, the full security-master universe
    needed for unbiased large-cap selection cannot be reconstructed either.
    """
    root = Path(repo_root)
    membership = _read_csv(root / "data" / "pit_universe" / "membership_universe_large_cap.csv")
    fundamental_dir = root / "data" / "fundamental"
    sep_ohlcv_dir = root / "data" / "research_cache" / "sharadar_sep_ohlcv"
    rows: list[dict[str, Any]] = []

    membership["membership_start_date"] = pd.to_datetime(membership["membership_start_date"], errors="coerce")
    membership["membership_end_date"] = pd.to_datetime(membership["membership_end_date"], errors="coerce")
    membership["ticker_norm"] = membership["ticker"].astype(str).map(normalize_ticker)

    for date_text in as_of_dates:
        as_of = pd.Timestamp(date_text)
        active = membership[
            (membership["membership_start_date"] <= as_of)
            & (membership["membership_end_date"].isna() | (membership["membership_end_date"] >= as_of))
        ].copy()
        share_count = 0
        close_count = 0
        both_count = 0
        reconstructed_large_count = 0
        missing_share_sample: list[str] = []
        missing_price_sample: list[str] = []
        for ticker in active["ticker_norm"]:
            fundamental_path = fundamental_dir / f"{ticker.replace('.', '-')}.parquet"
            price_path = sep_ohlcv_dir / f"{ticker.replace('/', '_')}.csv"
            shares = latest_reported_shares_as_of(fundamental_path, as_of)
            close = close_on_or_before(price_path, as_of)
            if shares is not None:
                share_count += 1
            elif len(missing_share_sample) < 20:
                missing_share_sample.append(ticker)
            if close is not None:
                close_count += 1
            elif len(missing_price_sample) < 20:
                missing_price_sample.append(ticker)
            if shares is not None and close is not None:
                both_count += 1
                if shares * close >= min_marketcap:
                    reconstructed_large_count += 1

        rows.append({
            "as_of_date": date_text,
            "active_current_large_cap_members": int(len(active)),
            "members_with_sep_close": int(close_count),
            "members_with_reported_shares": int(share_count),
            "members_with_reconstructable_marketcap": int(both_count),
            "reconstructable_marketcap_coverage_pct": round(both_count / len(active), 6) if len(active) else None,
            "reconstructed_above_min_marketcap": int(reconstructed_large_count),
            "missing_share_sample": " ".join(missing_share_sample),
            "missing_price_sample": " ".join(missing_price_sample),
        })
    return pd.DataFrame(rows)


def load_daily_marketcap_panel(path: Path | str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix.lower() == ".parquet":
        df = pd.read_parquet(p)
    else:
        df = pd.read_csv(p)
    lower = {str(c).lower(): c for c in df.columns}
    required_marketcap = lower.get("marketcap") or lower.get("market_cap") or lower.get("mktcap")
    if not required_marketcap:
        raise ValueError("daily market-cap panel requires marketcap/market_cap/mktcap column")
    if "date" not in lower:
        raise ValueError("daily market-cap panel requires date column")
    if "security_id" not in lower and "ticker" not in lower:
        raise ValueError("daily market-cap panel requires security_id or ticker column")
    out = pd.DataFrame({
        "date": pd.to_datetime(df[lower["date"]], errors="coerce"),
        "marketcap": pd.to_numeric(df[required_marketcap], errors="coerce"),
    })
    if "security_id" in lower:
        out["security_id"] = df[lower["security_id"]].astype(str).str.strip()
    if "ticker" in lower:
        out["ticker"] = df[lower["ticker"]].astype(str).map(normalize_ticker)
    out = out.dropna(subset=["date", "marketcap"])
    out = out[out["marketcap"] > 0].copy()
    out["date"] = out["date"].dt.normalize()
    return out


def load_daily_marketcap_cache(cache_dir: Path | str, security_master: pd.DataFrame) -> pd.DataFrame:
    """Load per-ticker SHARADAR/DAILY market-cap cache files into one panel."""
    cache = Path(cache_dir)
    master = security_master.copy()
    master["ticker_norm"] = master["ticker"].astype(str).map(normalize_ticker)
    ticker_to_sid = dict(zip(master["ticker_norm"], master["security_id"].astype(str)))
    frames: list[pd.DataFrame] = []
    for path in sorted(cache.glob("*.csv")):
        if path.name == "manifest.json":
            continue
        try:
            piece = pd.read_csv(path)
        except Exception:
            continue
        lower = {str(c).lower(): c for c in piece.columns}
        if "date" not in lower:
            continue
        cap_col = lower.get("marketcap") or lower.get("market_cap") or lower.get("mktcap")
        if cap_col is None:
            continue
        ticker = normalize_ticker(path.stem.replace("_", "/"))
        frame = pd.DataFrame({
            "date": pd.to_datetime(piece[lower["date"]], errors="coerce"),
            "ticker": ticker,
            "security_id": ticker_to_sid.get(ticker),
            "marketcap": pd.to_numeric(piece[cap_col], errors="coerce"),
        })
        frame = frame.dropna(subset=["date", "security_id", "marketcap"])
        frame = frame[frame["marketcap"] > 0]
        if not frame.empty:
            frame["date"] = frame["date"].dt.normalize()
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=["date", "ticker", "security_id", "marketcap"])
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(["date", "security_id"], keep="last").sort_values(["date", "security_id"])


def build_daily_marketcap_membership(
    *,
    security_master: pd.DataFrame,
    daily_marketcap: pd.DataFrame,
    min_marketcap: float = DEFAULT_MIN_MARKETCAP,
) -> pd.DataFrame:
    """Build date-effective `caerus_large_cap` membership from DAILY market cap."""
    master = security_master.copy()
    master["ticker_norm"] = master["ticker"].astype(str).map(normalize_ticker)
    ticker_to_sid = dict(zip(master["ticker_norm"], master["security_id"].astype(str)))

    panel = daily_marketcap.copy()
    if "security_id" not in panel.columns:
        panel["security_id"] = panel["ticker"].map(ticker_to_sid)
    panel = panel[panel["security_id"].notna()].copy()
    panel["security_id"] = panel["security_id"].astype(str)
    panel = panel.merge(
        master,
        on="security_id",
        how="inner",
        suffixes=("", "_master"),
    )
    if panel.empty:
        return pd.DataFrame(columns=[
            "security_id", "ticker", "membership_family", "membership_start_date",
            "membership_end_date", "scale_source", "source", "confidence",
        ])
    common_stock = panel["category"].map(_is_common_stock).fillna(False).astype(bool)
    us_exchange = panel["exchange"].astype(str).str.upper().isin(US_EQUITY_EXCHANGES).fillna(False).astype(bool)
    above_min_marketcap = (panel["marketcap"] >= float(min_marketcap)).fillna(False).astype(bool)
    panel = panel[common_stock & us_exchange & above_min_marketcap].copy()
    if panel.empty:
        return pd.DataFrame(columns=[
            "security_id", "ticker", "membership_family", "membership_start_date",
            "membership_end_date", "scale_source", "source", "confidence",
        ])

    panel = panel[panel.apply(lambda r: _active_on(r, r["date"]), axis=1)].copy()
    all_dates = sorted(d for d in daily_marketcap["date"].dropna().unique())
    date_index = {pd.Timestamp(d): i for i, d in enumerate(all_dates)}
    rows: list[dict[str, Any]] = []
    for security_id, group in panel.sort_values(["security_id", "date"]).groupby("security_id"):
        dates = [pd.Timestamp(d) for d in group["date"].drop_duplicates().sort_values()]
        if not dates:
            continue
        ticker = str(group.iloc[-1].get("ticker") or group.iloc[-1].get("ticker_master") or "")
        start = prev = dates[0]
        for current in dates[1:]:
            if date_index[current] != date_index[prev] + 1:
                rows.append(_membership_row(security_id, ticker, start, prev))
                start = current
            prev = current
        rows.append(_membership_row(security_id, ticker, start, prev))
    return pd.DataFrame(rows).sort_values(["security_id", "membership_start_date"]).reset_index(drop=True)


def _membership_row(security_id: str, ticker: str, start: pd.Timestamp, end: pd.Timestamp) -> dict[str, Any]:
    return {
        "security_id": security_id,
        "ticker": ticker,
        "membership_family": "caerus_large_cap",
        "membership_start_date": start.strftime("%Y-%m-%d"),
        "membership_end_date": end.strftime("%Y-%m-%d"),
        "scale_source": "marketcap",
        "source": DAILY_MARKETCAP_SOURCE,
        "confidence": "HIGH",
    }
