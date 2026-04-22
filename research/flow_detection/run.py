from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from .analysis import attach_forward_returns, build_event_study
from .backtest import FlowBacktestConfig, run_strategy_backtest
from .data import available_window_years, ensure_price_panel, load_universe
from .random_windows import run_randomized_window_analysis
from .signals import build_flow_signals

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only Flow Detection v1 research harness")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--use-efficiency-filter", action="store_true")
    parser.add_argument("--window-years", nargs="+", type=int, default=[2, 3, 5, 10])
    parser.add_argument("--num-sims", type=int, default=25)
    parser.add_argument("--output-dir", default="outputs/research/flow_detection_v1")
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--transaction-cost-bps", type=float, default=10.0)
    parser.add_argument("--fallback-behavior", choices=["baseline_fill", "cash"], default="baseline_fill")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO), format="%(levelname)s %(message)s")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    universe = load_universe(args.universe_path)
    symbols = sorted(set(universe + ["SPY"]))
    panel, panel_meta = ensure_price_panel(
        symbols=symbols,
        start_date=args.start_date,
        end_date=args.end_date,
        cache_path=args.price_cache_path,
        allow_download=True,
    )
    if panel.empty:
        raise SystemExit("No OHLCV history available for flow detection research.")

    signals = build_flow_signals(panel, use_efficiency_filter=args.use_efficiency_filter)
    signals = attach_forward_returns(signals)
    event_study_rows, event_study_summary = build_event_study(signals)

    config = FlowBacktestConfig(
        top_n=args.top_n,
        transaction_cost_bps=args.transaction_cost_bps,
        fallback_behavior=args.fallback_behavior,
        use_efficiency_filter=args.use_efficiency_filter,
    )
    baseline = run_strategy_backtest(signals, strategy="baseline", config=config, start_date=args.start_date, end_date=args.end_date)
    flow = run_strategy_backtest(signals, strategy="flow_filtered", config=config, start_date=args.start_date, end_date=args.end_date)
    window_results, window_summary = run_randomized_window_analysis(
        signals,
        config=config,
        window_years=args.window_years,
        num_sims=args.num_sims,
        seed=args.seed,
    )

    summary = build_summary(
        panel_meta=panel_meta,
        signals=signals,
        baseline=baseline["summary"],
        flow=flow["summary"],
        event_study_summary=event_study_summary,
        randomized_window_summary=window_summary,
        use_efficiency_filter=args.use_efficiency_filter,
    )

    _write_artifacts(
        output_dir=output_dir,
        summary=summary,
        signals=signals,
        event_study_rows=event_study_rows,
        event_study_summary=event_study_summary,
        baseline=baseline,
        flow=flow,
        window_results=window_results,
        window_summary=window_summary,
    )
    return 0


def build_summary(
    *,
    panel_meta: dict,
    signals: pd.DataFrame,
    baseline: dict,
    flow: dict,
    event_study_summary: list[dict],
    randomized_window_summary: dict,
    use_efficiency_filter: bool,
) -> dict:
    flow_mask = signals["flow_active_v1_1"] if use_efficiency_filter else signals["flow_active"]
    flow_rate = float(flow_mask.mean()) if len(signals) else 0.0
    windows_available = available_window_years(signals[["date", "ticker", "close"]].drop_duplicates())
    return {
        "schema_version": "flow_detection_v1",
        "methodology": {
            "flow_definition": {
                "volume_z_threshold": 1.5,
                "r1_threshold": 0.005,
                "r3_threshold": 0.015,
                "use_efficiency_filter": bool(use_efficiency_filter),
            },
            "strategy_definition": {
                "baseline": "top-N daily momentum score, equal weight",
                "flow_filtered": "top-N daily momentum score with flow-active priority, equal weight",
            },
        },
        "data": panel_meta,
        "signal_diagnostics": {
            "rows": int(len(signals)),
            "tickers": int(signals["ticker"].nunique()),
            "dates": int(signals["date"].nunique()),
            "flow_event_rate": round(flow_rate, 6),
        },
        "baseline_backtest": baseline,
        "flow_filtered_backtest": flow,
        "event_study_summary": event_study_summary,
        "randomized_window_summary": randomized_window_summary,
        "initial_conclusion": _build_initial_conclusion(baseline, flow, randomized_window_summary),
        "available_history_years": windows_available,
    }


def _build_initial_conclusion(baseline: dict, flow: dict, randomized_window_summary: dict) -> dict:
    baseline_sharpe = baseline.get("sharpe")
    flow_sharpe = flow.get("sharpe")
    beats_baseline = bool(flow_sharpe is not None and baseline_sharpe is not None and flow_sharpe > baseline_sharpe)
    robust_windows = []
    for block in randomized_window_summary.get("windows", []):
        pct = block.get("pct_windows_flow_beats_baseline")
        if pct is not None:
            robust_windows.append(float(pct))
    robust = bool(robust_windows and sum(1 for x in robust_windows if x >= 0.5) >= max(1, len(robust_windows) // 2))
    recommendation = "promising" if beats_baseline and robust else "not_yet_promising"
    return {
        "flow_beats_baseline_on_sharpe": beats_baseline,
        "robust_across_random_windows": robust,
        "recommendation": recommendation,
    }


def _write_artifacts(
    *,
    output_dir: Path,
    summary: dict,
    signals: pd.DataFrame,
    event_study_rows: pd.DataFrame,
    event_study_summary: list[dict],
    baseline: dict,
    flow: dict,
    window_results: pd.DataFrame,
    window_summary: dict,
) -> None:
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    signals.to_parquet(output_dir / "signals.parquet", index=False)
    event_study_rows.to_csv(output_dir / "event_study.csv", index=False)
    (output_dir / "event_study_summary.json").write_text(json.dumps(event_study_summary, indent=2))
    (output_dir / "backtest_baseline.json").write_text(json.dumps(baseline["summary"], indent=2))
    (output_dir / "backtest_flow_filtered.json").write_text(json.dumps(flow["summary"], indent=2))
    baseline["nav"].to_csv(output_dir / "backtest_baseline_nav.csv", index=False)
    flow["nav"].to_csv(output_dir / "backtest_flow_filtered_nav.csv", index=False)
    baseline["daily"].to_csv(output_dir / "backtest_baseline_daily.csv", index=False)
    flow["daily"].to_csv(output_dir / "backtest_flow_filtered_daily.csv", index=False)
    window_results.to_csv(output_dir / "randomized_window_results.csv", index=False)
    (output_dir / "randomized_window_summary.json").write_text(json.dumps(window_summary, indent=2))
    (output_dir / "report.md").write_text(_build_markdown_report(summary))


def _build_markdown_report(summary: dict) -> str:
    baseline = summary.get("baseline_backtest", {})
    flow = summary.get("flow_filtered_backtest", {})
    conclusion = summary.get("initial_conclusion", {})
    lines = [
        "# Flow Detection v1",
        "",
        "## Methodology",
        f"- Flow active requires `volume_z > 1.5`, `r1 > 0.5%`, and `r3 > 1.5%`.",
        f"- Efficiency filter enabled: `{summary.get('methodology', {}).get('flow_definition', {}).get('use_efficiency_filter')}`.",
        "- Baseline strategy: equal-weight top-N daily momentum score.",
        "- Flow-filtered strategy: equal-weight top-N daily momentum score with flow-active priority.",
        "",
        "## First-Pass Comparison",
        f"- Baseline CAGR: `{baseline.get('cagr')}` | Sharpe: `{baseline.get('sharpe')}` | Max DD: `{baseline.get('max_drawdown')}`",
        f"- Flow CAGR: `{flow.get('cagr')}` | Sharpe: `{flow.get('sharpe')}` | Max DD: `{flow.get('max_drawdown')}`",
        f"- Flow excess return vs SPY: `{flow.get('excess_return_vs_spy')}`",
        "",
        "## Recommendation",
        f"- Recommendation: `{conclusion.get('recommendation')}`",
        f"- Flow beats baseline on Sharpe: `{conclusion.get('flow_beats_baseline_on_sharpe')}`",
        f"- Robust across randomized windows: `{conclusion.get('robust_across_random_windows')}`",
        "",
        "## Limitations",
        "- Research-only build. Not wired to production allocation or execution.",
        "- Uses PIT-safe price/volume history only; no fundamentals.",
        "- Randomized windows are historical window sampling, not a synthetic return generator.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
