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

from audit.export import write_audit_bundle
from audit.policy_backtest import (
    evaluate_windows,
    load_sleeve1_dataset,
    run_window_backtest,
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


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_policies(raw: str) -> list[str]:
    values = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
    allowed = {"FULL", "PARTIAL", "LOCK"}
    out: list[str] = []
    for value in values:
        if value in allowed and value not in out:
            out.append(value)
    return out or ["FULL", "PARTIAL", "LOCK"]


def _audit_run_path(base_dir: Path, run_id: str) -> Path:
    return base_dir if base_dir.name == run_id else (base_dir / run_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run 2022 FULL audit export, pick worst MC window, and export both."
    )
    parser.add_argument("--start-2022", default="2022-01-01")
    parser.add_argument("--end-2022", default="2022-12-31")
    parser.add_argument("--mc-policy", default=os.getenv("BREAKER_POLICY", "FULL"))
    parser.add_argument("--mc-n", "--mc_n", dest="mc_n", type=int, default=_env_int("MC_N", 200))
    parser.add_argument(
        "--mc-years",
        "--mc_years",
        dest="mc_years",
        type=int,
        default=_env_int("MC_WINDOW_YEARS", 3),
    )
    parser.add_argument("--mc-metric", default=os.getenv("MC_METRIC", "MAX_DD"))
    parser.add_argument(
        "--mc-seed",
        "--seed",
        dest="mc_seed",
        type=int,
        default=_env_int("MC_SEED", 42),
    )
    parser.add_argument("--mc-start-min", default="2008-01-01")
    parser.add_argument(
        "--data-end",
        default=os.getenv("BACKTEST_END", pd.Timestamp.today().date().isoformat()),
    )
    parser.add_argument("--top-n", type=int, default=5)
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--commission-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--research-outdir", default="outputs/research")
    parser.add_argument("--audit-export", type=int, default=1 if _env_bool("AUDIT_EXPORT", True) else 0)
    parser.add_argument("--audit-run-id", default=os.getenv("AUDIT_RUN_ID", "").strip())
    parser.add_argument("--audit-outdir", default=os.getenv("AUDIT_OUTDIR", "outputs/audit"))
    parser.add_argument("--compare-policies", default="FULL,PARTIAL,LOCK")
    return parser.parse_args()


def _run_policy_compare(
    dataset,
    *,
    start: str,
    end: str,
    policies: list[str],
    top_n: int,
    initial_equity: float,
    commission_bps: float,
    slippage_bps: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict] = []
    curve_rows: list[pd.DataFrame] = []
    for policy in policies:
        result = run_window_backtest(
            dataset,
            start=start,
            end=end,
            breaker_policy=policy,
            top_n=top_n,
            initial_equity=initial_equity,
            commission_bps=commission_bps,
            slippage_bps=slippage_bps,
        )
        summary_rows.append(dict(result["summary"]))
        curve = result["portfolio_daily"][["date", "total_equity"]].copy()
        curve["policy"] = policy
        curve_rows.append(curve)
    summary_df = pd.DataFrame(summary_rows)
    curves_df = pd.concat(curve_rows, ignore_index=True) if curve_rows else pd.DataFrame()
    return summary_df, curves_df


def main() -> None:
    args = parse_args()
    os.environ.setdefault("BREAKER_STATE_CAN_OVERRIDE", "0")
    research_out = Path(args.research_outdir)
    research_out.mkdir(parents=True, exist_ok=True)
    audit_out = Path(args.audit_outdir)
    policy_for_mc = str(args.mc_policy).strip().upper() or "FULL"
    compare_policies = _normalize_policies(args.compare_policies)

    global_start = min(pd.Timestamp(args.mc_start_min), pd.Timestamp(args.start_2022))
    global_end = max(pd.Timestamp(args.data_end), pd.Timestamp(args.end_2022))
    dataset = load_sleeve1_dataset(
        start=global_start,
        end=global_end,
        synthetic=bool(args.synthetic),
    )

    run_id_prefix = str(args.audit_run_id).strip()
    run_id_2022 = f"{run_id_prefix}_2022_full" if run_id_prefix else "2022_full"
    run_id_worst = f"{run_id_prefix}_mc_worst_full" if run_id_prefix else "mc_worst_full"

    result_2022_full = run_window_backtest(
        dataset,
        start=args.start_2022,
        end=args.end_2022,
        breaker_policy="FULL",
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )

    audit_paths: list[Path] = []
    if bool(args.audit_export):
        out_2022 = write_audit_bundle(
            run_id=run_id_2022,
            outdir=_audit_run_path(audit_out, run_id_2022),
            trades_df=result_2022_full["trades"],
            holdings_daily_df=result_2022_full["holdings_daily"],
            portfolio_daily_df=result_2022_full["portfolio_daily"],
            summary=result_2022_full["summary"],
        )
        audit_paths.append(out_2022)

    windows = sample_random_windows(
        trading_dates=dataset.prices_wide.index,
        n_windows=int(args.mc_n),
        years=int(args.mc_years),
        seed=int(args.mc_seed),
        sample_start_min=args.mc_start_min,
    )
    mc_df = evaluate_windows(
        dataset,
        windows=windows,
        breaker_policy=policy_for_mc,
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )
    worst = select_worst_window(mc_df, metric=args.mc_metric)

    mc_windows_path = research_out / f"random_windows_{args.mc_years}y_{policy_for_mc.lower()}.csv"
    mc_summary_path = research_out / f"random_windows_summary_{policy_for_mc.lower()}.csv"
    worst_json_path = research_out / f"worst_window_{policy_for_mc.lower()}.json"
    mc_df.to_csv(mc_windows_path, index=False)
    pd.DataFrame(
        [
            {
                "policy": policy_for_mc,
                "years": int(args.mc_years),
                "n_windows": int(args.mc_n),
                "seed": int(args.mc_seed),
                "selection_metric": str(args.mc_metric).upper(),
                "worst_start_date": worst["start_date"],
                "worst_end_date": worst["end_date"],
                "worst_max_drawdown": float(worst["max_drawdown"]),
                "worst_cagr": float(worst["cagr"]),
                "worst_ulcer_index": float(worst["ulcer_index"]),
            }
        ]
    ).to_csv(mc_summary_path, index=False)
    worst_json_path.write_text(json.dumps(worst, indent=2) + "\n", encoding="utf-8")

    result_worst_full = run_window_backtest(
        dataset,
        start=worst["start_date"],
        end=worst["end_date"],
        breaker_policy="FULL",
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )
    if bool(args.audit_export):
        out_worst = write_audit_bundle(
            run_id=run_id_worst,
            outdir=_audit_run_path(audit_out, run_id_worst),
            trades_df=result_worst_full["trades"],
            holdings_daily_df=result_worst_full["holdings_daily"],
            portfolio_daily_df=result_worst_full["portfolio_daily"],
            summary=result_worst_full["summary"],
        )
        audit_paths.append(out_worst)

    compare_2022_summary, compare_2022_curves = _run_policy_compare(
        dataset,
        start=args.start_2022,
        end=args.end_2022,
        policies=compare_policies,
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )
    compare_worst_summary, compare_worst_curves = _run_policy_compare(
        dataset,
        start=worst["start_date"],
        end=worst["end_date"],
        policies=compare_policies,
        top_n=int(args.top_n),
        initial_equity=float(args.initial_equity),
        commission_bps=float(args.commission_bps),
        slippage_bps=float(args.slippage_bps),
    )

    compare_2022_summary_path = research_out / "policy_compare_2022.csv"
    compare_2022_curves_path = research_out / "policy_compare_2022_equity_curves.csv"
    compare_worst_summary_path = research_out / "policy_compare_mc_worst.csv"
    compare_worst_curves_path = research_out / "policy_compare_mc_worst_equity_curves.csv"
    compare_2022_summary.to_csv(compare_2022_summary_path, index=False)
    compare_2022_curves.to_csv(compare_2022_curves_path, index=False)
    compare_worst_summary.to_csv(compare_worst_summary_path, index=False)
    compare_worst_curves.to_csv(compare_worst_curves_path, index=False)

    print("[AUDIT] 2022_full_summary")
    print(
        f"  total_return={float(result_2022_full['summary']['total_return']):.4f} "
        f"cagr={float(result_2022_full['summary']['cagr']):.4f} "
        f"max_dd={float(result_2022_full['summary']['max_drawdown']):.4f}"
    )
    print("[AUDIT] mc_worst_window")
    print(
        f"  start={worst['start_date']} end={worst['end_date']} "
        f"metric={str(args.mc_metric).upper()} "
        f"max_dd={float(worst['max_drawdown']):.4f} cagr={float(worst['cagr']):.4f}"
    )
    for p in audit_paths:
        print(f"[AUDIT] artifact_dir={p}")
    print(f"[AUDIT] mc_windows_csv={mc_windows_path}")
    print(f"[AUDIT] mc_summary_csv={mc_summary_path}")
    print(f"[AUDIT] worst_json={worst_json_path}")
    print(f"[AUDIT] compare_2022_csv={compare_2022_summary_path}")
    print(f"[AUDIT] compare_2022_curves_csv={compare_2022_curves_path}")
    print(f"[AUDIT] compare_mc_worst_csv={compare_worst_summary_path}")
    print(f"[AUDIT] compare_mc_worst_curves_csv={compare_worst_curves_path}")


if __name__ == "__main__":
    main()
