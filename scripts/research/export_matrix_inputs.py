"""
export_matrix_inputs.py — Convert cached parquets to CSV for sandbox use
=========================================================================
Run this ONCE on your Mac (with .venv active).  Takes ~30-60 seconds.
The two output CSVs are then read by scripts/run_value_backtest_csv.py
which runs entirely in the sandbox without yfinance or pyarrow.

Usage:
    cd /Users/brettolson/Documents/Caerus/quant-daily-report-main
    source .venv/bin/activate
    python scripts/export_matrix_inputs.py

Outputs (both written to alpha_stack_cache/csv_export/):
    prices_matrix.csv     — daily Close prices, wide format (date × ticker)
    edgar_facts.csv       — all EDGAR fundamental facts combined

Uses pyarrow directly (fast startup) to avoid slow pandas import.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alpha_stack_cache" / "csv_export"
OUT.mkdir(parents=True, exist_ok=True)

print("=" * 65)
print("Alpha Stack — Export Matrix Inputs to CSV")
print("=" * 65)


def _read_parquet_to_pandas(path: Path):
    """Read a parquet file, preferring pyarrow then falling back to pandas."""
    try:
        import pyarrow.parquet as pq
        return pq.read_table(str(path)).to_pandas()
    except ImportError:
        import pandas as pd
        return pd.read_parquet(path)


# ── 1. Price data ─────────────────────────────────────────────────────────────
price_parquet = ROOT / "alpha_stack_cache" / "prices" / "_matrix_prices_2007_2026.parquet"

if price_parquet.exists():
    size_mb = price_parquet.stat().st_size / 1e6
    print(f"\n[1/2] Reading price parquet ({size_mb:.1f} MB)...")
    prices = _read_parquet_to_pandas(price_parquet)
    print(f"      Shape: {prices.shape}")
    price_out = OUT / "prices_matrix.csv"
    prices.to_csv(price_out)
    print(f"      ✓ Saved: {price_out.relative_to(ROOT)}  ({price_out.stat().st_size/1e6:.1f} MB)")
else:
    print(f"\n[1/2] ✗ Price parquet not found: {price_parquet}")
    print("      Will attempt yfinance download as fallback...")
    try:
        import pandas as pd
        import yfinance as yf
        # Load universe
        uni_path = ROOT / "data" / "universe.csv"
        if not uni_path.exists():
            uni_path = ROOT / "universe.csv"
        universe = pd.read_csv(uni_path)
        tickers = universe["ticker"].dropna().tolist() + ["SPY"]
        print(f"      Downloading {len(tickers)} tickers 2007-2026 via yfinance...")
        raw = yf.download(tickers, start="2007-01-01", end="2026-03-08",
                          progress=True, auto_adjust=True)
        import pandas as pd2
        if isinstance(raw.columns, pd2.MultiIndex):
            close = raw["Close"]
        else:
            close = raw[["Close"]].rename(columns={"Close": tickers[0]})
        close.index.name = "date"
        price_out = OUT / "prices_matrix.csv"
        close.to_csv(price_out)
        print(f"      ✓ Downloaded and saved: {price_out.relative_to(ROOT)}")
    except Exception as e:
        print(f"      ✗ yfinance fallback failed: {e}")
        print("      Cannot continue without price data.")
        sys.exit(1)


# ── 2. EDGAR facts ────────────────────────────────────────────────────────────
edgar_dir = ROOT / "data" / "alpha_stack_cache" / "edgar"
parquet_files = sorted(edgar_dir.glob("facts_*.parquet"))
print(f"\n[2/2] Reading {len(parquet_files)} EDGAR fact parquets...")

frames = []
ok = 0
fail = 0
for i, p in enumerate(parquet_files, 1):
    try:
        df = _read_parquet_to_pandas(p)
        frames.append(df)
        ok += 1
        if i % 25 == 0:
            print(f"      ... {i}/{len(parquet_files)} files read")
    except Exception as e:
        print(f"      ✗ {p.name}: {e}")
        fail += 1

if frames:
    import pandas as pd
    combined = pd.concat(frames, ignore_index=True)
    # Ensure date columns are strings for CSV portability
    for col in ("filed", "end"):
        if col in combined.columns:
            combined[col] = combined[col].astype(str)
    edgar_out = OUT / "edgar_facts.csv"
    combined.to_csv(edgar_out, index=False)
    print(f"      ✓ {ok} files merged → {len(combined):,} rows")
    print(f"      ✓ Saved: {edgar_out.relative_to(ROOT)}  ({edgar_out.stat().st_size/1e6:.1f} MB)")
    if fail:
        print(f"      ⚠ {fail} files failed (see above)")
else:
    print("      ✗ No EDGAR parquets could be read")
    sys.exit(1)

# ── 3. Summary ────────────────────────────────────────────────────────────────
print(f"\n{'='*65}")
print("Export complete.")
print("The sandbox can now run:  python scripts/run_value_backtest_csv.py")
print("="*65)
