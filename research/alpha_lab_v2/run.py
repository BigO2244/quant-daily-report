from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from research.alpha_lab_v1.signals import build_alpha_lab_signal_frame
from research.flow_detection.data import ensure_price_panel, load_universe

from .engine import run_backtest
from .hypotheses import SINGLE_CHANGE_VARIANTS, build_strategy_specs
from .robustness import run_randomized_windows


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="DEV-only Alpha Lab v2 interaction study")
    parser.add_argument("--start-date", default="2014-01-01")
    parser.add_argument("--end-date", default=pd.Timestamp.today().normalize().strftime("%Y-%m-%d"))
    parser.add_argument("--num-sims", type=int, default=50)
    parser.add_argument("--output-dir", default="outputs/research/alpha_lab_v2")
    parser.add_argument("--price-cache-path", default="outputs/research/flow_detection_v1/price_panel.parquet")
    parser.add_argument("--allow-download", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
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
    specs = build_strategy_specs()
    results = {spec.name: run_backtest(signals, spec, start_date=args.start_date, end_date=args.end_date) for spec in specs}
    comparison = build_comparison_table(results)
    best_single_change_name = identify_best_single_change(comparison)
    robustness_results, robustness_summary = run_randomized_windows(
        signals,
        specs=specs,
        start_date=args.start_date,
        end_date=args.end_date,
        window_years=[2, 3, 5, 10],
        num_sims=args.num_sims,
        seed=args.seed,
        baseline_name="baseline_top10_daily",
        best_single_change_name=best_single_change_name,
    )
    ranked_variants = build_ranked_variants(results, robustness_summary, best_single_change_name)
    summary = {
        "schema_version": "alpha_lab_v2",
        "data": panel_meta,
        "baseline": results["baseline_top10_daily"]["summary"],
        "best_single_change_variant": best_single_change_name,
        "best_single_change_metrics": results[best_single_change_name]["summary"],
        "ranked_variants": ranked_variants,
        "study_answers": build_study_answers(results, ranked_variants, best_single_change_name),
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
                "pct_positive_months": summary.get("pct_positive_months"),
            }
        )
    return pd.DataFrame(rows).sort_values(["sharpe", "cagr"], ascending=False).reset_index(drop=True)


def identify_best_single_change(comparison: pd.DataFrame) -> str:
    block = comparison[comparison["strategy"].isin(SINGLE_CHANGE_VARIANTS)].copy()
    block = block.sort_values(["sharpe", "cagr"], ascending=False)
    return str(block.iloc[0]["strategy"])


def build_ranked_variants(results: dict[str, dict], robustness_summary: dict, best_single_change_name: str) -> list[dict]:
    robust_map = {item["strategy"]: item["windows"] for item in robustness_summary.get("strategies", [])}
    baseline = results["baseline_top10_daily"]["summary"]
    baseline_sharpe = baseline.get("sharpe")
    best_single_sharpe = results[best_single_change_name]["summary"].get("sharpe")
    ranked = []
    for strategy, payload in results.items():
        summary = payload["summary"]
        windows = robust_map.get(strategy, [])
        avg_beats_baseline = _avg_window_metric(windows, "pct_windows_beating_baseline")
        avg_beats_best_single = _avg_window_metric(windows, "pct_windows_beating_best_single_change")
        ranked.append(
            {
                "strategy": strategy,
                "verdict": classify_variant(
                    summary=summary,
                    baseline_sharpe=baseline_sharpe,
                    best_single_sharpe=best_single_sharpe,
                    avg_beats_baseline=avg_beats_baseline,
                    avg_beats_best_single=avg_beats_best_single,
                    best_single_change_name=best_single_change_name,
                ),
                "metrics": summary,
                "randomized_windows": windows,
                "avg_pct_windows_beating_baseline": avg_beats_baseline,
                "avg_pct_windows_beating_best_single_change": avg_beats_best_single,
            }
        )
    ranked.sort(
        key=lambda item: (
            item["metrics"].get("sharpe") if item["metrics"].get("sharpe") is not None else float("-inf"),
            item["metrics"].get("cagr") if item["metrics"].get("cagr") is not None else float("-inf"),
        ),
        reverse=True,
    )
    return ranked


def classify_variant(
    *,
    summary: dict,
    baseline_sharpe: float | None,
    best_single_sharpe: float | None,
    avg_beats_baseline: float | None,
    avg_beats_best_single: float | None,
    best_single_change_name: str,
) -> str:
    strategy = summary.get("strategy")
    sharpe = summary.get("sharpe")
    if sharpe is None or baseline_sharpe is None:
        return "FAIL"
    full_beats_baseline = sharpe > baseline_sharpe
    robust_beats_baseline = avg_beats_baseline is not None and avg_beats_baseline >= 0.5
    if strategy == best_single_change_name:
        return "PASS" if full_beats_baseline and robust_beats_baseline else "WEAK"
    if full_beats_baseline and robust_beats_baseline:
        if best_single_sharpe is not None and sharpe > best_single_sharpe and (avg_beats_best_single or 0.0) >= 0.5:
            return "PASS"
        return "WEAK"
    return "FAIL"


def build_study_answers(results: dict[str, dict], ranked_variants: list[dict], best_single_change_name: str) -> dict:
    by_name = {item["strategy"]: item for item in ranked_variants}
    best_overall = ranked_variants[0]
    h6 = results["h6_top5_daily"]["summary"]
    h2_h6 = results["h2_rank_decay_exit_h6_top5"]["summary"]
    h1_h2 = results["h1_weekly_h2_rank_decay_exit"]["summary"]
    h1_h6 = results["h1_weekly_h6_top5"]["summary"]
    h1_h2_h6 = results["h1_weekly_h2_rank_decay_exit_h6_top5"]["summary"]
    baseline = results["baseline_top10_daily"]["summary"]

    h1_incremental = [
        ("h1_weekly_h2_rank_decay_exit", _incremental_label(h1_h2.get("sharpe"), results["h2_rank_decay_exit_top10_daily"]["summary"].get("sharpe"))),
        ("h1_weekly_h6_top5", _incremental_label(h1_h6.get("sharpe"), h6.get("sharpe"))),
        ("h1_weekly_h2_rank_decay_exit_h6_top5", _incremental_label(h1_h2_h6.get("sharpe"), h2_h6.get("sharpe"))),
    ]
    shadow_candidate = (
        best_overall["verdict"] == "PASS"
        and best_overall["strategy"] != best_single_change_name
        and (best_overall.get("avg_pct_windows_beating_best_single_change") or 0.0) >= 0.5
    )

    if shadow_candidate:
        next_action = "promote_to_side_by_side_shadow_candidate"
    elif best_overall["verdict"] == "PASS":
        next_action = "continue_dev_research"
    else:
        next_action = "terminate_combinations"

    return {
        "which_single_change_adds_most_value": {
            "strategy": best_single_change_name,
            "metrics": results[best_single_change_name]["summary"],
        },
        "does_h2_plus_h6_outperform_h6_alone": {
            "answer": bool((h2_h6.get("sharpe") or float("-inf")) > (h6.get("sharpe") or float("-inf"))),
            "h2_plus_h6_sharpe": h2_h6.get("sharpe"),
            "h6_alone_sharpe": h6.get("sharpe"),
        },
        "does_h1_add_incremental_value": {
            "comparisons": [{"strategy": name, "label": label} for name, label in h1_incremental],
        },
        "is_best_result_robust_enough_for_shadow_later": {
            "answer": bool(shadow_candidate),
            "best_strategy": best_overall["strategy"],
            "best_verdict": best_overall["verdict"],
        },
        "recommended_next_action": next_action,
        "best_overall_vs_baseline": {
            "best_strategy": best_overall["strategy"],
            "best_sharpe": best_overall["metrics"].get("sharpe"),
            "baseline_sharpe": baseline.get("sharpe"),
        },
        "ranked_order": [item["strategy"] for item in ranked_variants],
        "verdict_map": {item["strategy"]: item["verdict"] for item in ranked_variants},
        "best_variant_details": by_name.get(best_overall["strategy"]),
    }


def write_artifacts(
    *,
    output_dir: Path,
    summary: dict,
    results: dict[str, dict],
    comparison: pd.DataFrame,
    robustness_results: pd.DataFrame,
    robustness_summary: dict,
) -> None:
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
    answers = summary.get("study_answers", {})
    lines = [
        "# Alpha Lab v2",
        "",
        "## Methodology",
        "- DEV-only interaction study on the Alpha Lab v1 winners.",
        "- Control: baseline top-10 daily momentum portfolio.",
        "- Single changes tested independently: H2 rank-decay exit, H6 top-5 concentration, H1 weekly rebalance.",
        "- Combinations tested narrowly and apples-to-apples against the same signal frame and turnover drag.",
        "- Robustness uses randomized historical windows, not synthetic Monte Carlo.",
        "- Randomized windows are deterministic for a fixed seed; defaults are 50 simulations and seed=42.",
        "",
        "## Key Answers",
        f"- Best single change: `{answers.get('which_single_change_adds_most_value', {}).get('strategy')}`.",
        f"- H2 + H6 outperforms H6 alone: `{answers.get('does_h2_plus_h6_outperform_h6_alone', {}).get('answer')}`.",
        f"- Shadow consideration later: `{answers.get('is_best_result_robust_enough_for_shadow_later', {}).get('answer')}`.",
        f"- Recommended next action: `{answers.get('recommended_next_action')}`.",
        "",
        "## Ranked Comparison",
    ]
    header = "| rank | strategy | verdict | cagr | sharpe | max_dd | turnover | holding_days | excess_vs_spy | pct_positive_months |"
    sep = "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"
    verdict_map = answers.get("verdict_map", {})
    lines.extend([header, sep])
    for idx, (_, row) in enumerate(comparison.iterrows(), start=1):
        lines.append(
            f"| {idx} | {row['strategy']} | {verdict_map.get(row['strategy'])} | {row['cagr']} | {row['sharpe']} | {row['max_drawdown']} | {row['avg_turnover']} | {row['avg_holding_period_days']} | {row['excess_return_vs_spy']} | {row['pct_positive_months']} |"
        )
    lines.extend(["", "## Variant Verdicts"])
    for item in summary.get("ranked_variants", []):
        lines.append(f"- {item['strategy']}: {item['verdict']}")
    return "\n".join(lines) + "\n"


def _avg_window_metric(windows: list[dict], field: str) -> float | None:
    if not windows:
        return None
    values = [window.get(field) for window in windows if window.get(field) is not None]
    if not values:
        return None
    return round(float(sum(values) / len(values)), 4)


def _incremental_label(new_value: float | None, base_value: float | None) -> str:
    if new_value is None or base_value is None:
        return "unknown"
    if new_value > base_value:
        return "adds_value"
    if new_value < base_value:
        return "weakens_result"
    return "flat"


if __name__ == "__main__":
    raise SystemExit(main())
