from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from .data import available_window_years, ensure_price_panel, load_universe
from .v2_analysis import build_event_study_v2
from .v2_backtest import FlowBacktestV2Config, run_strategy_backtest_v2
from .v2_random_windows import run_randomized_window_suite_v2
from .v2_signals import build_flow_signals_v2

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only Flow Detection v2 research harness")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--window-years", nargs="+", type=int, default=[2, 3, 5, 10])
    parser.add_argument("--num-sims", type=int, default=25)
    parser.add_argument("--output-dir", default="outputs/research/flow_detection_v2")
    parser.add_argument("--universe-path", default="data/universe.csv")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--allow-download", action="store_true")
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
        allow_download=bool(args.allow_download),
    )
    if panel.empty:
        raise SystemExit("No OHLCV history available for flow detection v2 research.")

    signals = build_flow_signals_v2(panel)
    event_rows, event_summary = build_event_study_v2(signals)
    config = FlowBacktestV2Config(
        top_n=args.top_n,
        transaction_cost_bps=args.transaction_cost_bps,
        fallback_behavior=args.fallback_behavior,
    )
    backtests = {
        strategy: run_strategy_backtest_v2(
            signals,
            strategy=strategy,
            config=config,
            start_date=args.start_date,
            end_date=args.end_date,
        )
        for strategy in ("baseline", "participation_entry", "participation_exit", "regime_conditional_participation")
    }
    window_results, window_summary = run_randomized_window_suite_v2(
        signals,
        config=config,
        window_years=args.window_years,
        num_sims=args.num_sims,
        seed=args.seed,
    )
    summary = build_summary_v2(
        panel_meta=panel_meta,
        signals=signals,
        backtests={k: v["summary"] for k, v in backtests.items()},
        event_study_summary=event_summary,
        randomized_window_summary=window_summary,
    )
    write_artifacts_v2(
        output_dir=output_dir,
        summary=summary,
        signals=signals,
        event_rows=event_rows,
        event_summary=event_summary,
        backtests=backtests,
        window_results=window_results,
        window_summary=window_summary,
    )
    return 0


def build_summary_v2(*, panel_meta: dict, signals: pd.DataFrame, backtests: dict, event_study_summary: list[dict], randomized_window_summary: dict) -> dict:
    return {
        "schema_version": "flow_detection_v2",
        "data": panel_meta,
        "signal_diagnostics": {
            "rows": int(len(signals)),
            "tickers": int(signals["ticker"].nunique()),
            "dates": int(signals["date"].nunique()),
            "participation_entry_rate": round(float(signals["participation_entry_signal"].mean()), 6),
            "exhaustion_flow_rate": round(float(signals["exhaustion_flow"].mean()), 6),
        },
        "methodology": {
            "slower_participation": "3d/5d average volume_z, signed participation accumulation, persistence counts",
            "exit_signal": "extended momentum plus exhaustion flow",
            "regime_overlay": "participation entry active only in strong_up/weak_up and normal vol",
        },
        "backtests": backtests,
        "event_study_summary": event_study_summary,
        "randomized_window_summary": randomized_window_summary,
        "available_history_years": available_window_years(signals[["date", "ticker", "close"]].drop_duplicates()),
        "initial_conclusion": _initial_conclusion(backtests, randomized_window_summary),
    }


def _initial_conclusion(backtests: dict, randomized_window_summary: dict) -> dict:
    baseline_sharpe = backtests.get("baseline", {}).get("sharpe")
    winners = {}
    for strategy, summary in backtests.items():
        if strategy == "baseline":
            continue
        winners[strategy] = bool(summary.get("sharpe") is not None and baseline_sharpe is not None and summary.get("sharpe") > baseline_sharpe)
    robust = {}
    for strategy in ("participation_entry", "participation_exit", "regime_conditional_participation"):
        pcts = []
        for block in randomized_window_summary.get("windows", []):
            val = (((block.get("strategies") or {}).get(strategy) or {}).get("pct_windows_beating_baseline"))
            if val is not None:
                pcts.append(float(val))
        robust[strategy] = bool(pcts and sum(1 for x in pcts if x >= 0.5) >= max(1, len(pcts) // 2))
    promising = [strategy for strategy in robust if winners.get(strategy) and robust.get(strategy)]
    return {
        "beats_baseline_on_full_period_sharpe": winners,
        "robust_across_random_windows": robust,
        "recommendation": promising or ["none"],
    }


def write_artifacts_v2(*, output_dir: Path, summary: dict, signals: pd.DataFrame, event_rows: pd.DataFrame, event_summary: list[dict], backtests: dict, window_results: pd.DataFrame, window_summary: dict) -> None:
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    signals.to_parquet(output_dir / "signals.parquet", index=False)
    event_rows.to_csv(output_dir / "event_study.csv", index=False)
    (output_dir / "event_study_summary.json").write_text(json.dumps(event_summary, indent=2))
    for name, payload in backtests.items():
        (output_dir / f"backtest_{name}.json").write_text(json.dumps(payload["summary"], indent=2))
        payload["nav"].to_csv(output_dir / f"backtest_{name}_nav.csv", index=False)
        payload["daily"].to_csv(output_dir / f"backtest_{name}_daily.csv", index=False)
    window_results.to_csv(output_dir / "randomized_window_results.csv", index=False)
    (output_dir / "randomized_window_summary.json").write_text(json.dumps(window_summary, indent=2))
    (output_dir / "report.md").write_text(_build_report(summary))


def _build_report(summary: dict) -> str:
    b = summary["backtests"]["baseline"]
    entry = summary["backtests"]["participation_entry"]
    exit_ = summary["backtests"]["participation_exit"]
    regime = summary["backtests"]["regime_conditional_participation"]
    conclusion = summary["initial_conclusion"]
    lines = [
        "# Flow Detection v2",
        "",
        "## What v1 showed",
        "- Single-day spike volume was not a viable entry model.",
        "- The flow-priority portfolio increased churn and underperformed decisively.",
        "",
        "## What v2 changes",
        "- Slower participation replaces single-day volume spikes for entry research.",
        "- Participation is also tested as an exit/trim overlay.",
        "- Regime-conditional participation is tested separately instead of assuming universality.",
        "",
        "## Full-Period Backtests",
        f"- Baseline: CAGR `{b.get('cagr')}` | Sharpe `{b.get('sharpe')}` | MaxDD `{b.get('max_drawdown')}`",
        f"- Participation entry: CAGR `{entry.get('cagr')}` | Sharpe `{entry.get('sharpe')}` | MaxDD `{entry.get('max_drawdown')}`",
        f"- Participation exit: CAGR `{exit_.get('cagr')}` | Sharpe `{exit_.get('sharpe')}` | MaxDD `{exit_.get('max_drawdown')}`",
        f"- Regime-conditional participation: CAGR `{regime.get('cagr')}` | Sharpe `{regime.get('sharpe')}` | MaxDD `{regime.get('max_drawdown')}`",
        "",
        "## Conclusion",
        f"- Strategies beating baseline on full-period Sharpe: `{conclusion.get('beats_baseline_on_full_period_sharpe')}`",
        f"- Random-window robustness: `{conclusion.get('robust_across_random_windows')}`",
        f"- Recommendation: `{conclusion.get('recommendation')}`",
        "",
        "## Limitations",
        "- DEV-only research path.",
        "- Uses PIT-safe OHLCV only.",
        "- Randomized windows are historical window sampling, not synthetic Monte Carlo.",
        "- No production promotion should follow unless a later version wins clearly and robustly.",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
