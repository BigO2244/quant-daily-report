#!/usr/bin/env python3
import argparse
from pathlib import Path
import subprocess
import pandas as pd

LIVE_PATH = Path("data/live_nav.csv")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--research-outdir", default="outputs/research")
    ap.add_argument("--report-outdir", default="outputs/alpha_report")
    ap.add_argument("--skip-report", action="store_true", help="Do not regenerate alpha_report after updating ledger")
    args = ap.parse_args()

    research_dir = Path(args.research_outdir)
    report_dir = Path(args.report_outdir)
    ts_path = research_dir / "sleeve1_alpha_variant_timeseries.csv"

    if not LIVE_PATH.exists():
        raise SystemExit(f"Missing live ledger: {LIVE_PATH}")
    if not ts_path.exists():
        raise SystemExit(f"Missing research timeseries: {ts_path} (run alpha_report.py first)")

    live = pd.read_csv(LIVE_PATH)
    live["date"] = pd.to_datetime(live["date"])
    live = live.sort_values("date").reset_index(drop=True)
    last_live_date = live["date"].iloc[-1]

    ts = pd.read_csv(ts_path)
    ts["date"] = pd.to_datetime(ts["date"])
    ts = ts.sort_values("date").reset_index(drop=True)

    nav_col = "net_nav_cb" if "net_nav_cb" in ts.columns else "net_nav"

    future = ts[ts["date"] > last_live_date]
    if future.empty:
        print("[DAILY_ALPHA] No new trading day to append.")
        if not args.skip_report:
            subprocess.check_call([
                "python3", "scripts/alpha_report.py",
                "--skip-backtest",
                "--output-dir", str(report_dir),
                "--research-outdir", str(research_dir),
            ])
        return

    next_row = future.iloc[0]
    next_date = next_row["date"]

    prev_model = ts.loc[ts["date"] == last_live_date, nav_col]
    if prev_model.empty:
        raise SystemExit(
            f"[DAILY_ALPHA] Model does not contain last live date={last_live_date.date()} "
            f"for {nav_col}. Align data/live_nav.csv seed date with timeseries start."
        )

    prev_model_nav = float(prev_model.iloc[0])
    next_model_nav = float(next_row[nav_col])
    daily_ret = (next_model_nav / prev_model_nav) - 1.0

    prev_live_nav = float(live["nav"].iloc[-1])
    new_live_nav = prev_live_nav * (1.0 + daily_ret)

    exposure = float(next_row.get("exposure_multiplier", 1.0))

    live = pd.concat(
        [live, pd.DataFrame([{"date": next_date, "nav": new_live_nav, "exposure": exposure}])],
        ignore_index=True,
    )
    live.to_csv(LIVE_PATH, index=False)
    print(f"[DAILY_ALPHA] Appended {next_date.date()} | nav={new_live_nav:.6f} | exposure={exposure:.3f}")

    if not args.skip_report:
        subprocess.check_call([
            "python3", "scripts/alpha_report.py",
            "--skip-backtest",
            "--output-dir", str(report_dir),
            "--research-outdir", str(research_dir),
        ])

if __name__ == "__main__":
    main()
