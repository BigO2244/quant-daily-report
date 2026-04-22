from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from research.flow_detection.data import ensure_price_panel, load_universe

from .engine import run_backtest
from .hypotheses import HYPOTHESIS_ORDER, build_strategy_specs
from .robustness import run_randomized_windows
from .signals import build_alpha_lab_signal_frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only Alpha Lab v1")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--num-sims", type=int, default=25)
    parser.add_argument("--output-dir", default="outputs/research/alpha_lab_v1")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    universe = load_universe("data/universe.csv")
    panel, panel_meta = ensure_price_panel(
        symbols=sorted(set(universe + ["SPY"])),
        start_date=args.start_date,
        end_date=args.end_date,
        cache_path=args.price_cache_path,
        allow_download=bool(args.allow_download),
    )
    signals = build_alpha_lab_signal_frame(panel)
    specs = build_strategy_specs(args.top_n)
    results = {spec.name: run_backtest(signals, spec, start_date=args.start_date, end_date=args.end_date) for spec in specs}
    robustness_results, robustness_summary = run_randomized_windows(
        signals,
        specs=specs,
        start_date=args.start_date,
        end_date=args.end_date,
        window_years=[2, 3, 5, 10],
        num_sims=args.num_sims,
        seed=args.seed,
        baseline_name="baseline_top10_daily",
    )

    comparison = build_comparison_table(results)
    hypothesis_payload = build_hypothesis_payload(results, robustness_summary)
    summary = {
        "schema_version": "alpha_lab_v1",
        "data": panel_meta,
        "baseline": results["baseline_top10_daily"]["summary"],
        "hypotheses": hypothesis_payload,
    }
    write_artifacts(
        output_dir=output_dir,
        summary=summary,
        results=results,
        comparison=comparison,
        robustness_results=robustness_results,
        robustness_summary=robustness_summary,
    )
    return 0


def build_comparison_table(results: dict[str, dict]) -> pd.DataFrame:
    rows = []
    for strategy, payload in results.items():
        summary = payload["summary"]
        rows.append(
            {
                "strategy": strategy,
                "hypothesis_id": summary.get("hypothesis_id"),
                "description": summary.get("description"),
                "cagr": summary.get("cagr"),
                "annualised_vol": summary.get("annualised_vol"),
                "sharpe": summary.get("sharpe"),
                "max_drawdown": summary.get("max_drawdown"),
                "avg_turnover": summary.get("avg_turnover"),
                "avg_holding_period_days": summary.get("avg_holding_period_days"),
                "excess_return_vs_spy": summary.get("excess_return_vs_spy"),
                "win_rate": summary.get("win_rate"),
            }
        )
    return pd.DataFrame(rows)


def build_hypothesis_payload(results: dict[str, dict], robustness_summary: dict) -> list[dict]:
    comparison = {name: payload["summary"] for name, payload in results.items()}
    baseline_sharpe = comparison["baseline_top10_daily"].get("sharpe")
    robust_map = {
        item["strategy"]: item["windows"]
        for item in robustness_summary.get("strategies", [])
    }
    hypotheses = []
    by_h = {}
    for name, summary in comparison.items():
        hid = summary.get("hypothesis_id")
        by_h.setdefault(hid, []).append((name, summary))

    for hid in HYPOTHESIS_ORDER:
        variants = by_h.get(hid, [])
        variant_payloads = []
        pass_flag = False
        for name, summary in variants:
            robust = robust_map.get(name, [])
            beats_baseline_any = any((window.get("pct_windows_beating_baseline") or 0) >= 0.5 for window in robust)
            beats_baseline_full = bool(summary.get("sharpe") is not None and baseline_sharpe is not None and summary.get("sharpe") > baseline_sharpe)
            if beats_baseline_full and beats_baseline_any:
                pass_flag = True
            variant_payloads.append(
                {
                    "strategy": name,
                    "metrics": summary,
                    "randomized_windows": robust,
                    "beats_baseline_full_period": beats_baseline_full,
                    "beats_baseline_randomized_majority": beats_baseline_any,
                }
            )
        verdict = "PASS" if pass_flag else "WEAK" if any(v["beats_baseline_full_period"] for v in variant_payloads) else "FAIL"
        hypotheses.append({"hypothesis_id": hid, "verdict": verdict, "variants": variant_payloads})
    return hypotheses


def write_artifacts(*, output_dir: Path, summary: dict, results: dict[str, dict], comparison: pd.DataFrame, robustness_results: pd.DataFrame, robustness_summary: dict) -> None:
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    comparison.to_csv(output_dir / "comparison_table.csv", index=False)
    (output_dir / "randomized_windows_summary.json").write_text(json.dumps(robustness_summary, indent=2))
    robustness_results.to_csv(output_dir / "randomized_windows_results.csv", index=False)
    for strategy, payload in results.items():
        (output_dir / f"{strategy}.json").write_text(json.dumps(payload["summary"], indent=2))
        payload["nav"].to_csv(output_dir / f"{strategy}_nav.csv", index=False)
        payload["daily"].to_csv(output_dir / f"{strategy}_daily.csv", index=False)
    (output_dir / "report.md").write_text(build_report(summary, comparison))


def build_report(summary: dict, comparison: pd.DataFrame) -> str:
    lines = [
        "# Alpha Lab v1",
        "",
        "## Methodology",
        "- Control: daily equal-weight top-N momentum baseline with 10 bps turnover cost.",
        "- Each hypothesis is tested independently against the same baseline.",
        "- Robustness uses randomized historical windows, not synthetic Monte Carlo.",
        "",
        "## Hypotheses",
        "- H1 Slower Rebalance: daily vs twice-weekly vs weekly.",
        "- H2 Rank Decay Exit: only sell after rank decay beyond top N*2.",
        "- H3 Regime Gating: reduce allocation when SPY is below 200DMA.",
        "- H4 Mean Reversion: buy bottom decile of 3-day returns and hold 5 days.",
        "- H5 Post-Move Drift: buy top-decile positive day after 2-day follow-through and hold 5 days.",
        "- H6 Concentration: compare top 5 / top 10 / top 20.",
        "",
        "## Comparison",
    ]
    header = "| strategy | hypothesis | cagr | sharpe | max_dd | turnover | holding_days | excess_vs_spy |"
    sep = "|---|---:|---:|---:|---:|---:|---:|---:|"
    lines.extend([header, sep])
    for _, row in comparison.sort_values(["hypothesis_id", "strategy"]).iterrows():
        lines.append(
            f"| {row['strategy']} | {row['hypothesis_id']} | {row['cagr']} | {row['sharpe']} | {row['max_drawdown']} | {row['avg_turnover']} | {row['avg_holding_period_days']} | {row['excess_return_vs_spy']} |"
        )
    lines.extend(["", "## Verdicts"])
    for item in summary.get("hypotheses", []):
        lines.append(f"- {item['hypothesis_id']}: {item['verdict']}")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
