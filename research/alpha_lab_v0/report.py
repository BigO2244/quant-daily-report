"""Generate Alpha Lab diagnostic report."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def generate_report(
    run_dir: Path,
    run_id: str,
    signals_stats: dict[str, Any],
    trades_stats: dict[str, Any],
    positions: list[dict[str, Any]],
    nav_summary: dict[str, Any],
    inputs: dict[str, Any]
) -> Path:
    """Generate alpha_lab_report.md in run directory."""
    report_path = run_dir / "alpha_lab_report.md"
    
    with open(report_path, "w") as f:
        f.write(f"# Alpha Lab V0 Diagnostic Report\n\n")
        f.write(f"**RUN_ID:** `{run_id}`\n\n")
        
        # 1. Run Overview
        f.write("## 1. Run Overview\n\n")
        f.write(f"- **Signals Store:** `{inputs.get('signals_store', 'N/A')}`\n")
        f.write(f"- **Ledger Path:** `{inputs.get('ledger_path', 'N/A')}`\n")
        f.write(f"- **Prices CSV:** `{inputs.get('prices_csv', 'Not provided')}`\n")
        f.write(f"- **Benchmark CSV:** `{inputs.get('benchmark_csv', 'Not provided')}`\n\n")
        
        if signals_stats.get("date_range"):
            f.write(f"- **Signal Date Range:** {signals_stats['date_range'][0]} to {signals_stats['date_range'][1]}\n")
        if trades_stats.get("date_range"):
            f.write(f"- **Trade Date Range:** {trades_stats['date_range'][0]} to {trades_stats['date_range'][1]}\n")
        f.write("\n")
        
        # 2. Data Inventory
        f.write("## 2. Data Inventory\n\n")
        f.write("### Signals\n")
        f.write(f"- **Files Ingested:** {signals_stats.get('num_files', 0)}\n")
        f.write(f"- **Total Rows:** {signals_stats.get('num_rows', 0)}\n")
        f.write(f"- **Unique Dates:** {signals_stats.get('num_dates', 0)}\n\n")
        
        f.write("### Ledger\n")
        f.write(f"- **Total Trades:** {trades_stats.get('num_trades', 0)}\n")
        f.write(f"- **Unique Trade Dates:** {trades_stats.get('num_dates', 0)}\n")
        f.write(f"- **Buys:** {trades_stats.get('buys', 0)}\n")
        f.write(f"- **Sells:** {trades_stats.get('sells', 0)}\n")
        if trades_stats.get('gross_notional'):
            f.write(f"- **Gross Notional Traded:** ${trades_stats['gross_notional']:,.2f}\n")
        f.write("\n")
        
        f.write("### Prices\n")
        f.write(f"- **Provided:** {'Yes' if inputs.get('prices_csv') else 'No'}\n")
        if not inputs.get('prices_csv'):
            f.write("- **Note:** NAV calculations unavailable without price data\n")
        f.write("\n")
        
        # 3. Signals Snapshot Quality
        f.write("## 3. Signals Snapshot Quality\n\n")
        
        if signals_stats.get('weight_sums'):
            ws = signals_stats['weight_sums']
            f.write("### Weight Sum Per Day\n")
            f.write(f"- **Min:** {ws.get('min', 'N/A')}\n")
            f.write(f"- **Median:** {ws.get('median', 'N/A')}\n")
            f.write(f"- **Max:** {ws.get('max', 'N/A')}\n\n")
        
        if signals_stats.get('tickers'):
            f.write("### Top 10 Tickers by Frequency\n\n")
            f.write("| Ticker | Count |\n")
            f.write("|--------|-------|\n")
            for ticker, count in list(signals_stats['tickers'].items())[:10]:
                f.write(f"| {ticker} | {count} |\n")
            f.write("\n")
        
        # 4. Executions / Trades Summary
        f.write("## 4. Executions / Trades Summary\n\n")
        f.write(f"- **Total Trades:** {trades_stats.get('num_trades', 0)}\n")
        f.write(f"- **Buys vs Sells:** {trades_stats.get('buys', 0)} buys, {trades_stats.get('sells', 0)} sells\n")
        if trades_stats.get('gross_notional'):
            f.write(f"- **Gross Notional:** ${trades_stats['gross_notional']:,.2f}\n")
        f.write("\n")
        
        # 5. Positions (Latest)
        f.write("## 5. Positions (Latest)\n\n")
        if positions:
            f.write("### Top 20 Positions\n\n")
            f.write("| Ticker | Shares | Avg Cost | Market Price | Market Value |\n")
            f.write("|--------|--------|----------|--------------|-------------|\n")
            
            # Sort by shares (or market value if available)
            pos_df = pd.DataFrame(positions)
            if "market_value" in pos_df.columns and pos_df["market_value"].notna().any():
                pos_df = pos_df.sort_values("market_value", ascending=False)
            else:
                pos_df = pos_df.sort_values("shares", ascending=False)
            
            for _, row in pos_df.head(20).iterrows():
                ticker = row["ticker"]
                shares = f"{row['shares']:.2f}"
                avg_cost = f"${row['avg_cost']:.2f}" if pd.notna(row.get('avg_cost')) else "N/A"
                price = f"${row['market_price']:.2f}" if pd.notna(row.get('market_price')) else "N/A"
                value = f"${row['market_value']:,.2f}" if pd.notna(row.get('market_value')) else "N/A"
                f.write(f"| {ticker} | {shares} | {avg_cost} | {price} | {value} |\n")
            f.write("\n")
        else:
            f.write("No positions available.\n\n")
        
        # 6. Portfolio NAV & Drawdown
        f.write("## 6. Portfolio NAV & Drawdown\n\n")
        
        if nav_summary and nav_summary.get('max_drawdown') is not None:
            f.write(f"- **Max Drawdown:** {nav_summary['max_drawdown']:.2%}\n")
            if nav_summary.get('sharpe'):
                f.write(f"- **Sharpe Ratio:** {nav_summary['sharpe']:.2f}\n")
            if nav_summary.get('volatility'):
                f.write(f"- **Volatility (annualized):** {nav_summary['volatility']:.2%}\n")
            if nav_summary.get('total_return'):
                f.write(f"- **Total Return:** {nav_summary['total_return']:.2%}\n")
            f.write("\n")
            
            # Worst drawdowns table
            if nav_summary.get('worst_drawdowns'):
                f.write("### Worst Drawdown Periods\n\n")
                f.write("| Start | Trough | Recovery | Depth | Duration (days) |\n")
                f.write("|-------|--------|----------|-------|----------------|\n")
                for dd in nav_summary['worst_drawdowns'][:5]:
                    f.write(f"| {dd['start']} | {dd['trough']} | {dd['recovery']} | {dd['depth']:.2%} | {dd['duration_days']} |\n")
                f.write("\n")
        else:
            f.write("**NAV unavailable:** Price data not provided. Cannot compute drawdowns.\n\n")
            f.write("To enable NAV calculations, provide `--prices_csv <path>` with columns: date, ticker, close\n\n")
        
        # 7. Regime Sensitivity
        f.write("## 7. Regime Sensitivity (Optional)\n\n")
        f.write("Regime analysis requires `--benchmark_csv` with SPY data. Not implemented in V0.\n\n")
        
        # 8. Data Gaps & Next Steps
        f.write("## 8. Data Gaps & Next Steps\n\n")
        
        gaps = []
        if not inputs.get('prices_csv'):
            gaps.append("**Missing Prices:** Provide `--prices_csv` to enable NAV, returns, and drawdown calculations")
        if not inputs.get('benchmark_csv'):
            gaps.append("**Missing Benchmark:** Provide `--benchmark_csv` to enable regime analysis")
        
        if gaps:
            f.write("### Missing Inputs\n\n")
            for gap in gaps:
                f.write(f"- {gap}\n")
            f.write("\n")
        
        f.write("### Next Steps\n\n")
        f.write("1. Add price history CSV to unlock:\n")
        f.write("   - True NAV calculation\n")
        f.write("   - Forward returns for IC / decile spread analysis\n")
        f.write("   - Position-level P&L attribution\n\n")
        f.write("2. Add benchmark data (SPY) to unlock:\n")
        f.write("   - Regime-based performance analysis\n")
        f.write("   - Beta and alpha calculations\n\n")
        f.write("3. Extend signal dataset for:\n")
        f.write("   - Longer backtest history\n")
        f.write("   - More robust factor analysis\n\n")
        
        f.write("---\n\n")
        f.write(f"*Report generated by Alpha Lab V0*\n")
    
    logger.info(f"[ALPHA_LAB] Report written to {report_path}")
    return report_path


def save_tables(
    run_dir: Path,
    signals: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    nav_series: list[dict[str, Any]],
    daily_metrics: list[dict[str, Any]],
    turnover: list[dict[str, Any]]
) -> None:
    """Save summary tables as CSV files."""
    tables_dir = run_dir / "tables"
    tables_dir.mkdir(exist_ok=True)
    
    # Signals summary
    if signals:
        df = pd.DataFrame(signals)
        summary = df.groupby("signal_date").agg({
            "ticker": "count",
            "target_weight": ["sum", "mean", "std"]
        }).reset_index()
        summary.to_csv(tables_dir / "signals_summary.csv", index=False)
    
    # Trades summary
    if trades:
        df = pd.DataFrame(trades)
        df.to_csv(tables_dir / "trades_summary.csv", index=False)
    
    # Positions latest
    if positions:
        df = pd.DataFrame(positions)
        df.to_csv(tables_dir / "positions_latest.csv", index=False)
    
    # NAV series
    if nav_series:
        df = pd.DataFrame(nav_series)
        df.to_csv(tables_dir / "nav_series.csv", index=False)
    
    # Daily metrics
    if daily_metrics:
        df = pd.DataFrame(daily_metrics)
        # Add turnover if available
        if turnover:
            turnover_df = pd.DataFrame(turnover)
            df = df.merge(turnover_df, on="date", how="left", suffixes=("", "_turno"))
            if "turnover_turno" in df.columns:
                df["turnover"] = df["turnover_turno"]
                df = df.drop(columns=["turnover_turno"])
        df.to_csv(tables_dir / "drawdowns.csv", index=False)
    
    # Turnover daily
    if turnover:
        df = pd.DataFrame(turnover)
        df.to_csv(tables_dir / "turnover_daily.csv", index=False)
    
    logger.info(f"[ALPHA_LAB] Tables saved to {tables_dir}")


def create_charts(
    run_dir: Path,
    nav_series: list[dict[str, Any]],
    daily_metrics: list[dict[str, Any]]
) -> None:
    """Create visualization charts if matplotlib available."""
    try:
        import matplotlib
        matplotlib.use('Agg')  # Non-interactive backend
        import matplotlib.pyplot as plt
    except ImportError:
        logger.warning("[ALPHA_LAB] matplotlib not available, skipping charts")
        return
    
    charts_dir = run_dir / "charts"
    charts_dir.mkdir(exist_ok=True)
    
    # NAV curve
    if nav_series:
        nav_df = pd.DataFrame(nav_series)
        nav_df = nav_df[nav_df["nav"].notna()]
        
        if not nav_df.empty:
            plt.figure(figsize=(12, 6))
            plt.plot(nav_df["date"], nav_df["nav"], linewidth=2)
            plt.title("Portfolio NAV", fontsize=14, fontweight="bold")
            plt.xlabel("Date")
            plt.ylabel("NAV ($)")
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(charts_dir / "nav_curve.png", dpi=150)
            plt.close()
    
    # Drawdown curve
    if daily_metrics:
        metrics_df = pd.DataFrame(daily_metrics)
        metrics_df = metrics_df[metrics_df["drawdown"].notna()]
        
        if not metrics_df.empty:
            plt.figure(figsize=(12, 6))
            plt.fill_between(range(len(metrics_df)), metrics_df["drawdown"] * 100, 0, alpha=0.3, color='red')
            plt.plot(metrics_df["drawdown"] * 100, color='darkred', linewidth=2)
            plt.title("Drawdown (%)", fontsize=14, fontweight="bold")
            plt.xlabel("Trading Days")
            plt.ylabel("Drawdown (%)")
            plt.grid(True, alpha=0.3)
            plt.tight_layout()
            plt.savefig(charts_dir / "drawdown_curve.png", dpi=150)
            plt.close()
    
    logger.info(f"[ALPHA_LAB] Charts saved to {charts_dir}")
