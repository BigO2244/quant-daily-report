"""
merge_local_results.py — Merge local run results into Alpha Stack Matrix v2
============================================================================
Run this AFTER completing the local run with regime_aware_backtest_matrix.py.

Usage:
    python scripts/merge_local_results.py \
        --local outputs/regime_matrix/local_run/master_results.csv
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
import pandas as pd

ROOT   = Path(__file__).resolve().parent.parent
OUTDIR = ROOT / "outputs" / "regime_matrix"
PENDING = "PENDING"


def merge(local_csv: Path, base_csv: Path) -> pd.DataFrame:
    base  = pd.read_csv(base_csv)
    local = pd.read_csv(local_csv)

    # Keep only value_only and combined_static from local run
    local_filt = local[local["config"].isin(["value_only", "combined_static"])].copy()
    print(f"Local rows to merge: {len(local_filt)}")

    # Drop PENDING rows for those configs in base
    base_clean = base[~(
        base["config"].isin(["value_only", "combined_static"]) &
        (base["cagr"] == PENDING)
    )].copy()
    print(f"Base rows after removing PENDING: {len(base_clean)}")

    merged = pd.concat([base_clean, local_filt], ignore_index=True)
    print(f"Merged total rows: {len(merged)}")
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", required=True,
                    help="Path to local run master_results.csv")
    args = ap.parse_args()

    local_csv = Path(args.local)
    base_csv  = OUTDIR / "master_results.csv"

    if not local_csv.exists():
        print(f"ERROR: local results not found: {local_csv}")
        sys.exit(1)
    if not base_csv.exists():
        print(f"ERROR: base results not found: {base_csv}")
        sys.exit(1)

    merged = merge(local_csv, base_csv)
    out = OUTDIR / "master_results.csv"
    merged.to_csv(out, index=False)
    print(f"Saved merged results: {out}")

    # Rebuild Excel
    import importlib.util, os
    os.chdir(str(ROOT))
    spec = importlib.util.spec_from_file_location("build_complete_matrix",
        ROOT / "scripts" / "build_complete_matrix.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)

    data = m.load_data()
    regime_df = m.classify_regimes(data["spy_nav"])
    # Re-run analysis with merged data (will overwrite equity curves too)
    print("Rebuilding Excel workbook with merged data...")
    m.build_excel(merged, regime_df)
    print("Done.")


if __name__ == "__main__":
    main()
