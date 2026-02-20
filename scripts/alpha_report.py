#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate standalone alpha engine report")
    ap.add_argument("--research-outdir", default="outputs/research")
    ap.add_argument("--output-dir", default="outputs/alpha_report")
    ap.add_argument("--apply-costs", action="store_true")
    ap.add_argument("--cost-bps", type=float, default=25.0)
    ap.add_argument("--skip-backtest", action="store_true")
    return ap.parse_args()


def fmt_pct(x: float) -> str:
    if pd.isna(x):
        return "n/a"
    return f"{x * 100:.2f}%"


def fmt_num(x: float) -> str:
    if pd.isna(x):
        return "n/a"
    return f"{x:.2f}"


def current_state(exposure: float) -> tuple[str, str]:
    if exposure <= 0.0:
        return "CAPITAL LOCK", "#8b0000"
    if exposure < 1.0:
        return "STRUCTURAL BEAR", "#b26b00"
    return "NORMAL", "#006d5b"


def render_charts(ts: pd.DataFrame, outdir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(ts["date"], ts["net_nav_cb"], label="Net NAV (CB)", color="#0c4a6e", linewidth=2)
    ax.plot(ts["date"], ts["spy_nav"], label="SPY", color="#64748b", linewidth=1.5)
    ax.set_title("Equity Curve")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(outdir / "equity_curve.png", dpi=140)
    plt.close(fig)

    dd = ts["net_nav_cb"] / ts["net_nav_cb"].cummax() - 1.0
    fig, ax = plt.subplots(figsize=(10, 3.8))
    ax.fill_between(ts["date"], dd, 0.0, color="#b91c1c", alpha=0.35)
    ax.plot(ts["date"], dd, color="#991b1b", linewidth=1.2)
    ax.set_title("Drawdown (CB)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "drawdown.png", dpi=140)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 2.8))
    ax.step(ts["date"], ts["exposure_multiplier"], where="post", color="#7c3aed", linewidth=1.7)
    ax.set_ylim(-0.05, 1.05)
    ax.set_yticks([0.0, 0.5, 1.0])
    ax.set_title("Breaker Timeline (Exposure Multiplier)")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(outdir / "breaker_timeline.png", dpi=140)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    research_dir = Path(args.research_outdir)
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)

    if not args.skip_backtest:
        cmd = [
            "python3",
            "scripts/research_backtest_sleeve1_alpha_variant.py",
            "--output-dir",
            str(research_dir),
        ]
        if args.apply_costs:
            cmd.extend(["--apply-costs", "--cost-bps", str(args.cost_bps)])
        subprocess.check_call(cmd)

    ts_path = research_dir / "sleeve1_alpha_variant_timeseries.csv"
    summary_path = research_dir / "sleeve1_alpha_variant_summary.csv"
    if not ts_path.exists() or not summary_path.exists():
        raise SystemExit("Missing research outputs. Run without --skip-backtest first.")

    ts = pd.read_csv(ts_path)
    ts["date"] = pd.to_datetime(ts["date"])
    ts = ts.sort_values("date").reset_index(drop=True)
    summary_df = pd.read_csv(summary_path)
    row = summary_df.iloc[0].to_dict()

    nav_col = "net_nav_cb" if "net_nav_cb" in ts.columns else "net_nav"
    dd_now = float((ts[nav_col] / ts[nav_col].cummax() - 1.0).iloc[-1])
    rolling_12m = np.nan
    if len(ts) > 252:
        rolling_12m = float(ts[nav_col].iloc[-1] / ts[nav_col].iloc[-252] - 1.0)

    exposure = float(ts["exposure_multiplier"].iloc[-1]) if "exposure_multiplier" in ts.columns else 1.0
    state_label, state_color = current_state(exposure)

    cards = {
        "Net CAGR (CB)": row.get("net_cagr_cb", np.nan),
        "Net Sharpe (CB)": row.get("net_sharpe_cb", np.nan),
        "Max DD (CB)": row.get("net_max_drawdown_cb", np.nan),
        "Beta (CB)": row.get("net_beta_vs_spy_cb", np.nan),
        "Rolling 12M (CB)": rolling_12m,
        "Breaker Days (Any)": row.get("breaker_days_any", np.nan),
    }

    summary = {
        "as_of": ts["date"].iloc[-1].date().isoformat(),
        "net_cagr_cb": float(cards["Net CAGR (CB)"]) if pd.notna(cards["Net CAGR (CB)"]) else None,
        "net_sharpe_cb": float(cards["Net Sharpe (CB)"]) if pd.notna(cards["Net Sharpe (CB)"]) else None,
        "max_dd_cb": float(cards["Max DD (CB)"]) if pd.notna(cards["Max DD (CB)"]) else None,
        "beta_cb": float(cards["Beta (CB)"]) if pd.notna(cards["Beta (CB)"]) else None,
        "rolling_12m_cb": float(cards["Rolling 12M (CB)"]) if pd.notna(cards["Rolling 12M (CB)"]) else None,
        "breaker_days_any": int(cards["Breaker Days (Any)"]) if pd.notna(cards["Breaker Days (Any)"]) else None,
        "current_drawdown_cb": dd_now,
        "current_exposure_multiplier": exposure,
        "state": state_label,
    }

    state = {
        "as_of": summary["as_of"],
        "exposure_multiplier": exposure,
        "state": state_label,
        "current_drawdown_cb": dd_now,
    }

    render_charts(ts, outdir)

    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (outdir / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    card_html = "".join(
        [
            f"<div class='card'><div class='label'>{k}</div><div class='value'>"
            + (
                fmt_pct(v)
                if "CAGR" in k or "DD" in k or "12M" in k
                else (fmt_num(v) if "Breaker Days" not in k else f"{int(v)}")
            )
            + "</div></div>"
            for k, v in cards.items()
        ]
    )

    html = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Alpha Engine Report</title>
<style>
body {{ font-family: Arial, sans-serif; margin: 20px; color: #0f172a; }}
.banner {{ background: {state_color}; color: #fff; padding: 12px 16px; border-radius: 8px; font-weight: 700; }}
.grid {{ display: grid; grid-template-columns: repeat(3, minmax(220px, 1fr)); gap: 10px; margin: 14px 0 18px; }}
.card {{ border: 1px solid #e2e8f0; border-radius: 8px; padding: 10px; background: #f8fafc; }}
.label {{ color: #475569; font-size: 12px; margin-bottom: 6px; }}
.value {{ font-size: 20px; font-weight: 700; }}
img {{ width: 100%; max-width: 1050px; border: 1px solid #e2e8f0; border-radius: 6px; margin-bottom: 12px; }}
</style></head>
<body>
<h1>Alpha Engine — Standalone Report</h1>
<div class='banner'>Current Exposure: {exposure:.1f} — {state_label} | Current DD (CB): {fmt_pct(dd_now)}</div>
<div class='grid'>{card_html}</div>
<h2>Equity Curve</h2><img src='equity_curve.png' alt='equity_curve'>
<h2>Drawdown</h2><img src='drawdown.png' alt='drawdown'>
<h2>Breaker Timeline</h2><img src='breaker_timeline.png' alt='breaker_timeline'>
</body></html>"""
    (outdir / "alpha_report.html").write_text(html, encoding="utf-8")
    print(f"[ALPHA_REPORT] Wrote report to {outdir}")


if __name__ == "__main__":
    main()
