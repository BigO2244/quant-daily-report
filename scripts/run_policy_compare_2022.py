#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.policy_backtest import load_sleeve1_dataset, run_window_backtest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Policy compare for 2022 window.")
    parser.add_argument("--start", default=os.getenv("BACKTEST_START", "2022-01-01"))
    parser.add_argument("--end", default=os.getenv("BACKTEST_END", "2022-12-31"))
    parser.add_argument("--policies", default="FULL,PARTIAL,LOCK")
    parser.add_argument("--outdir", default="outputs/research")
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    return parser.parse_args()


def _normalize_policies(raw: str) -> list[str]:
    values = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
    allowed = {"FULL", "PARTIAL", "LOCK"}
    deduped: list[str] = []
    for value in values:
        if value in allowed and value not in deduped:
            deduped.append(value)
    return deduped or ["FULL", "PARTIAL", "LOCK"]


def main() -> None:
    args = parse_args()
    os.environ.setdefault("BREAKER_STATE_CAN_OVERRIDE", "0")
    policies = _normalize_policies(args.policies)
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    try:
        dataset = load_sleeve1_dataset(
            start=args.start,
            end=args.end,
            synthetic=bool(args.synthetic),
        )
    except Exception as exc:
        if bool(args.synthetic):
            raise
        print(
            f"[POLICY_COMPARE][WARN] live data load failed ({exc}); retrying with synthetic data."
        )
        dataset = load_sleeve1_dataset(
            start=args.start,
            end=args.end,
            synthetic=True,
        )

    summary_rows: list[dict] = []
    curve_rows: list[pd.DataFrame] = []
    for policy in policies:
        result = run_window_backtest(
            dataset,
            start=args.start,
            end=args.end,
            breaker_policy=policy,
            top_n=int(args.top_n),
            initial_equity=float(args.initial_equity),
            commission_bps=float(args.commission_bps),
            slippage_bps=float(args.slippage_bps),
        )
        summary_rows.append(dict(result["summary"]))
        curve = result["portfolio_daily"][["date", "total_equity"]].copy()
        curve["policy"] = policy
        curve_rows.append(curve)

    summary_df = pd.DataFrame(summary_rows)
    curves_df = pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame()

    summary_path = outdir / "policy_compare_2022.csv"
    curves_path = outdir / "policy_compare_2022_equity_curves.csv"
    summary_df.to_csv(summary_path, index=False)
    curves_df.to_csv(curves_path, index=False)

    print(f"[POLICY_COMPARE] summary_csv={summary_path}")
    print(f"[POLICY_COMPARE] curves_csv={curves_path}")


if __name__ == "__main__":
    main()
