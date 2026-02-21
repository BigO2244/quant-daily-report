#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from audit.policy_backtest import (
    evaluate_windows,
    load_sleeve1_dataset,
    sample_random_windows,
    select_worst_window,
)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Monte Carlo windows and pick worst window.")
    parser.add_argument("--policy", default=os.getenv("BREAKER_POLICY", "FULL"))
    parser.add_argument("--n", type=int, default=_env_int("MC_N", 200))
    parser.add_argument("--years", type=int, default=_env_int("MC_WINDOW_YEARS", 3))
    parser.add_argument("--metric", default=os.getenv("MC_METRIC", "MAX_DD"))
    parser.add_argument("--seed", type=int, default=_env_int("MC_SEED", 42))
    parser.add_argument("--outdir", default="outputs/research")
    parser.add_argument("--start-min", default="2008-01-01")
    parser.add_argument("--end", default=os.getenv("BACKTEST_END", pd.Timestamp.today().date().isoformat()))
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    return parser.parse_args()


def _summary_table(df: pd.DataFrame, worst: dict, *, policy: str, years: int, n: int, seed: int, metric: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "policy": policy,
                "years": years,
                "n_windows": n,
                "seed": seed,
                "selection_metric": metric,
                "worst_start_date": worst["start_date"],
                "worst_end_date": worst["end_date"],
                "worst_max_drawdown": float(worst["max_drawdown"]),
                "worst_cagr": float(worst["cagr"]),
                "worst_ulcer_index": float(worst["ulcer_index"]),
                "median_cagr": float(df["cagr"].median()),
                "median_max_drawdown": float(df["max_drawdown"].median()),
                "median_ulcer_index": float(df["ulcer_index"].median()),
                "mean_trade_count": float(df["trade_count"].mean()),
            }
        ]
    )


def main() -> None:
    args = parse_args()
    os.environ.setdefault("BREAKER_STATE_CAN_OVERRIDE", "0")
    policy = str(args.policy).strip().upper()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    dataset = load_sleeve1_dataset(
        start=args.start_min,
        end=args.end,
        synthetic=bool(args.synthetic),
    )
    windows = sample_random_windows(
        trading_dates=dataset.prices_wide.index,
        n_windows=int(args.n),
        years=int(args.years),
        seed=int(args.seed),
        sample_start_min=args.start_min,
    )
    windows_df = evaluate_windows(
        dataset,
        windows=windows,
        breaker_policy=policy,
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )
    worst = select_worst_window(windows_df, metric=args.metric)
    summary_df = _summary_table(
        windows_df,
        worst,
        policy=policy,
        years=int(args.years),
        n=int(args.n),
        seed=int(args.seed),
        metric=str(args.metric).strip().upper(),
    )

    windows_path = outdir / f"random_windows_{args.years}y_{policy.lower()}.csv"
    summary_path = outdir / f"random_windows_summary_{policy.lower()}.csv"
    worst_path = outdir / f"worst_window_{policy.lower()}.json"

    windows_df.to_csv(windows_path, index=False)
    summary_df.to_csv(summary_path, index=False)
    worst_path.write_text(json.dumps(worst, indent=2) + "\n", encoding="utf-8")

    print(f"[MC] windows_csv={windows_path}")
    print(f"[MC] summary_csv={summary_path}")
    print(f"[MC] worst_json={worst_path}")
    print(
        "[MC] worst_window="
        f"{worst['start_date']}..{worst['end_date']} "
        f"max_dd={float(worst['max_drawdown']):.4f} "
        f"cagr={float(worst['cagr']):.4f} "
        f"ulcer={float(worst['ulcer_index']):.4f}"
    )


if __name__ == "__main__":
    main()
