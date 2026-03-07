"""
Analyzer Validation & Excess Return Attribution v1

Comprehensive empirical evaluation of:
1. Premarket analyzer signal predictiveness
2. Strategy behavior on analyzer alarm days
3. Excess return vs SPY attribution
4. Overlay performance conditioned on analyzer signal
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

import json
import numpy as np
import pandas as pd
from pathlib import Path


@dataclass
class ConfusionMatrix:
    """2x2 classification table: analyzer prediction vs actual SPY direction"""
    true_positive: int = 0  # predicted bearish, SPY down
    false_positive: int = 0  # predicted bearish, SPY up
    true_negative: int = 0  # predicted bullish, SPY up
    false_negative: int = 0  # predicted bullish, SPY down

    @property
    def total(self) -> int:
        return self.true_positive + self.false_positive + self.true_negative + self.false_negative

    @property
    def hit_rate(self) -> float | None:
        """True positive rate: P(correct | bearish signal)"""
        denom = self.true_positive + self.false_positive
        return self.true_positive / denom if denom > 0 else None

    @property
    def false_positive_rate(self) -> float | None:
        """FPR: P(signal bearish | SPY actually up)"""
        denom = self.false_positive + self.true_negative
        return self.false_positive / denom if denom > 0 else None

    @property
    def false_negative_rate(self) -> float | None:
        """FNR: P(signal bullish | SPY actually down)"""
        denom = self.false_negative + self.true_positive
        return self.false_negative / denom if denom > 0 else None

    @property
    def accuracy(self) -> float | None:
        """Proportion of correct predictions"""
        return (self.true_positive + self.true_negative) / self.total if self.total > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "true_negative": self.true_negative,
            "false_negative": self.false_negative,
            "total": self.total,
            "hit_rate": self.hit_rate,
            "false_positive_rate": self.false_positive_rate,
            "false_negative_rate": self.false_negative_rate,
            "accuracy": self.accuracy,
        }


@dataclass
class AlarmDayMetrics:
    """Summary metrics for analyzer alarm days (premarket_score <= threshold)"""
    threshold: float
    alarm_day_count: int = 0
    coverage_count: int = 0
    avg_spy_return_alarm: float | None = None
    avg_strategy_return_alarm: float | None = None
    avg_excess_return_alarm: float | None = None
    strategy_return_std_alarm: float | None = None
    spy_return_std_alarm: float | None = None
    confusion_matrix: ConfusionMatrix | None = None
    condition_sample_size: int = 0
    condition_note: str = ""

    def is_sufficient_support(self, min_samples: int = 10) -> bool:
        """Check if results are based on sufficient data"""
        return self.coverage_count >= min_samples and self.condition_sample_size >= 3

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "alarm_day_count": self.alarm_day_count,
            "coverage_count": self.coverage_count,
            "avg_spy_return_alarm": self.avg_spy_return_alarm,
            "avg_strategy_return_alarm": self.avg_strategy_return_alarm,
            "avg_excess_return_alarm": self.avg_excess_return_alarm,
            "strategy_return_std_alarm": self.strategy_return_std_alarm,
            "spy_return_std_alarm": self.spy_return_std_alarm,
            "confusion_matrix": self.confusion_matrix.to_dict() if self.confusion_matrix else None,
            "condition_sample_size": self.condition_sample_size,
            "condition_note": self.condition_note,
            "sufficient_support": self.is_sufficient_support(),
        }


class AnalyzerValidator:
    """Validate analyzer signal predictiveness and compute excess return attribution"""

    MIN_COVERAGE_SUPPORT = 10
    MIN_CONDITION_SUPPORT = 3

    def __init__(self, canonical_df: pd.DataFrame, overlay_df: pd.DataFrame | None = None):
        """
        Args:
            canonical_df: DataFrame with columns: date, premarket_score, spy_return, strategy_return, etc.
            overlay_df: Optional DataFrame with overlay conditioning. Must have same date index.
        """
        self.canonical = canonical_df.copy()
        self.overlay = overlay_df.copy() if overlay_df is not None else None
        self._normalize_columns()

    def _normalize_columns(self) -> None:
        """Coerce numeric columns"""
        for col in ["premarket_score", "spy_return", "strategy_return", "excess_return"]:
            if col in self.canonical.columns:
                self.canonical[col] = pd.to_numeric(self.canonical[col], errors="coerce")
        if self.overlay is not None:
            for col in ["premarket_score", "spy_return", "strategy_return", "overlay_multiplier", "overlay_return"]:
                if col in self.overlay.columns:
                    self.overlay[col] = pd.to_numeric(self.overlay[col], errors="coerce")

    def compute_alarm_day_metrics(self, threshold: float = 0.5) -> AlarmDayMetrics:
        """
        Compute metrics for days where premarket_score <= threshold.

        Args:
            threshold: Score cutoff for "alarm" signal (default 0.5)

        Returns:
            AlarmDayMetrics with all computed statistics
        """
        df = self.canonical.copy()
        has_score = df["premarket_score"].notna()
        is_alarm = has_score & (df["premarket_score"] <= threshold)

        metrics = AlarmDayMetrics(threshold=threshold)
        metrics.alarm_day_count = int(is_alarm.sum())
        metrics.coverage_count = int(has_score.sum())

        # SPY return statistics on alarm days
        spy_alarm = df.loc[is_alarm, "spy_return"].dropna()
        if len(spy_alarm) > 0:
            metrics.avg_spy_return_alarm = float(spy_alarm.mean())
            metrics.spy_return_std_alarm = float(spy_alarm.std())

        # Strategy return statistics on alarm days
        strat_alarm = df.loc[is_alarm, "strategy_return"].dropna()
        if len(strat_alarm) > 0:
            metrics.avg_strategy_return_alarm = float(strat_alarm.mean())
            metrics.strategy_return_std_alarm = float(strat_alarm.std())

        # Excess return statistics on alarm days
        if "excess_return" in df.columns:
            excess_alarm = df.loc[is_alarm, "excess_return"].dropna()
            if len(excess_alarm) > 0:
                metrics.avg_excess_return_alarm = float(excess_alarm.mean())

        # Confusion matrix: does bearish signal predict SPY downside?
        has_both = has_score & df["spy_return"].notna()
        if has_both.sum() >= 1:
            eval_df = df.loc[has_both, ["premarket_score", "spy_return"]].copy()
            eval_df["pred_bearish"] = eval_df["premarket_score"] <= threshold
            eval_df["actual_down"] = eval_df["spy_return"] < 0

            cm = ConfusionMatrix()
            cm.true_positive = int((eval_df["pred_bearish"] & eval_df["actual_down"]).sum())
            cm.false_positive = int((eval_df["pred_bearish"] & ~eval_df["actual_down"]).sum())
            cm.true_negative = int((~eval_df["pred_bearish"] & ~eval_df["actual_down"]).sum())
            cm.false_negative = int((~eval_df["pred_bearish"] & eval_df["actual_down"]).sum())

            metrics.confusion_matrix = cm
            metrics.condition_sample_size = int(has_both.sum())

            if cm.total < self.MIN_CONDITION_SUPPORT:
                metrics.condition_note = f"Low support: only {cm.total} evaluation rows"
            else:
                metrics.condition_note = "Sufficient support"

        if not metrics.is_sufficient_support(self.MIN_COVERAGE_SUPPORT):
            metrics.condition_note = f"Low coverage: only {metrics.coverage_count} rows with premarket_score"

        return metrics

    def threshold_sweep(
        self, thresholds: list[float] | None = None
    ) -> list[AlarmDayMetrics]:
        """
        Evaluate analyzer performance across multiple threshold cutoffs.

        Args:
            thresholds: List of score thresholds to test. Default: [0.25, 0.5, 0.75]

        Returns:
            List of AlarmDayMetrics, one per threshold
        """
        if thresholds is None:
            thresholds = [0.25, 0.5, 0.75]

        return [self.compute_alarm_day_metrics(t) for t in sorted(thresholds)]

    def compute_excess_return_attribution(self) -> dict[str, Any]:
        """
        Compute benchmark-relative performance metrics.

        Returns:
            Dict with cumulative return, excess return, rolling beta, rolling alpha, etc.
        """
        df = self.canonical.copy().sort_values("date")
        results = {}

        # Simple cumulative returns
        nav = pd.to_numeric(df.get("strategy_nav"), errors="coerce")
        spy_closes = pd.to_numeric(df.get("spy_close"), errors="coerce")

        # Total return from NAV
        if nav.notna().sum() >= 2:
            first_nav = nav.dropna().iloc[0]
            last_nav = nav.dropna().iloc[-1]
            results["strategy_total_return"] = float((last_nav / first_nav - 1.0)) if first_nav != 0 else None
        else:
            results["strategy_total_return"] = None

        # Total return from SPY
        if spy_closes.notna().sum() >= 2:
            first_spy = spy_closes.dropna().iloc[0]
            last_spy = spy_closes.dropna().iloc[-1]
            results["spy_total_return"] = float((last_spy / first_spy - 1.0)) if first_spy != 0 else None
        else:
            results["spy_total_return"] = None

        # Average daily excess return
        excess = pd.to_numeric(df.get("excess_return"), errors="coerce")
        if excess.notna().any():
            results["avg_daily_excess_return"] = float(excess.dropna().mean())
            results["excess_return_std"] = float(excess.dropna().std())
            results["excess_return_count"] = int(excess.notna().sum())
        else:
            results["avg_daily_excess_return"] = None
            results["excess_return_std"] = None
            results["excess_return_count"] = 0

        # Rolling beta and alpha (over 20-day windows)
        returns = pd.to_numeric(df.get("strategy_return"), errors="coerce")
        spy_returns = pd.to_numeric(df.get("spy_return"), errors="coerce")
        
        rolling_window = 20
        if len(returns) >= rolling_window:
            betas = []
            alphas = []
            for i in range(rolling_window, len(df)):
                window = df.iloc[i - rolling_window : i]
                w_strat = pd.to_numeric(window["strategy_return"], errors="coerce")
                w_spy = pd.to_numeric(window["spy_return"], errors="coerce")
                
                # Need both series to have data
                mask = w_strat.notna() & w_spy.notna()
                if mask.sum() >= 3:
                    try:
                        coef = np.polyfit(w_spy[mask].values, w_strat[mask].values, 1)
                        alpha = coef[1]  # intercept
                        beta = coef[0]   # slope
                        alphas.append(alpha)
                        betas.append(beta)
                    except:
                        pass

            if betas:
                results["avg_rolling_beta"] = float(np.mean(betas))
                results["avg_rolling_alpha"] = float(np.mean(alphas))
            else:
                results["avg_rolling_beta"] = None
                results["avg_rolling_alpha"] = None
        else:
            results["avg_rolling_beta"] = None
            results["avg_rolling_alpha"] = None

        # Tracking error and information ratio
        if results.get("excess_return_std") is not None and results.get("avg_daily_excess_return") is not None:
            tracking_error = results["excess_return_std"]
            excess_ret = results["avg_daily_excess_return"]
            # Annualized (assuming 252 trading days)
            results["tracking_error_annualized"] = float(tracking_error * np.sqrt(252)) if tracking_error > 0 else None
            results["information_ratio"] = float((excess_ret * 252) / (tracking_error * np.sqrt(252))) if tracking_error > 0 else None
        else:
            results["tracking_error_annualized"] = None
            results["information_ratio"] = None

        # Upside/downside capture
        spy_pos = spy_returns[spy_returns > 0].dropna()
        spy_neg = spy_returns[spy_returns < 0].dropna()

        if len(spy_pos) > 0:
            strat_up = returns[spy_returns > 0].dropna()
            up_mask = spy_returns > 0
            if (up_mask & returns.notna() & spy_returns.notna()).sum() > 0:
                results["upside_capture"] = float(returns[up_mask].mean() / spy_returns[up_mask].mean()) if spy_returns[up_mask].mean() != 0 else None
            else:
                results["upside_capture"] = None
        else:
            results["upside_capture"] = None

        if len(spy_neg) > 0:
            down_mask = spy_returns < 0
            if (down_mask & returns.notna() & spy_returns.notna()).sum() > 0:
                results["downside_capture"] = float(returns[down_mask].mean() / spy_returns[down_mask].mean()) if spy_returns[down_mask].mean() != 0 else None
            else:
                results["downside_capture"] = None
        else:
            results["downside_capture"] = None

        return results

    def overlay_conditioned_benchmarking(self) -> dict[str, Any]:
        """
        Evaluate overlay effectiveness conditioned on analyzer alarm state.

        Returns:
            Dict with metrics grouped by analyzer signal and overlay type
        """
        if self.overlay is None:
            return {"error": "overlay_df not provided"}

        df = self.overlay.copy().sort_values("date")
        df["premarket_score"] = pd.to_numeric(df["premarket_score"], errors="coerce")
        df["spy_return"] = pd.to_numeric(df["spy_return"], errors="coerce")
        df["strategy_return"] = pd.to_numeric(df["strategy_return"], errors="coerce")
        df["overlay_return"] = pd.to_numeric(df["overlay_return"], errors="coerce")
        df["overlay_multiplier"] = pd.to_numeric(df["overlay_multiplier"], errors="coerce")

        results = {}

        # Condition 1: Analyzer alarm days (score <= 0.5)
        has_alarm = df["premarket_score"].notna()
        is_alarm = has_alarm & (df["premarket_score"] <= 0.5)
        no_alarm = has_alarm & (df["premarket_score"] > 0.5)

        # For each condition, compare overlay active vs inactive
        for condition_name, condition_mask in [
            ("alarm_days", is_alarm),
            ("normal_days", no_alarm),
            ("all_days", df["premarket_score"].notna()),
        ]:
            if condition_mask.sum() < 1:
                continue

            cond_df = df.loc[condition_mask].copy()

            # Compare active vs inactive overlay
            has_active = cond_df["overlay_multiplier"].notna()
            is_active = has_active & (cond_df["overlay_multiplier"] < 1.0)
            is_inactive = has_active & cond_df["overlay_multiplier"].eq(1.0)

            condition_results = {
                "sample_size": int(condition_mask.sum()),
                "rows_with_overlay_signal": int(has_active.sum()),
            }

            for overlay_state, state_mask in [("active", is_active), ("inactive", is_inactive)]:
                state_df = cond_df.loc[state_mask]
                if len(state_df) < 1:
                    continue

                strat_ret = state_df["strategy_return"].dropna()
                spy_ret = state_df["spy_return"].dropna()

                state_key = f"{overlay_state}_overlay"
                condition_results[state_key] = {
                    "sample_size": int(len(state_df)),
                    "avg_strategy_return": float(strat_ret.mean()) if len(strat_ret) > 0 else None,
                    "avg_spy_return": float(spy_ret.mean()) if len(spy_ret) > 0 else None,
                    "strategy_return_std": float(strat_ret.std()) if len(strat_ret) > 1 else None,
                    "downside_capture": None,  # Would require fuller recomputation per state
                }

            # Compute downside capture on alarm days with overlay
            if condition_name == "alarm_days" and is_active.sum() > 0:
                down_mask = state_df["spy_return"] < 0
                if down_mask.sum() > 0:
                    down_spy = state_df.loc[down_mask, "spy_return"].dropna()
                    down_strat = state_df.loc[down_mask, "strategy_return"].dropna()
                    if len(down_spy) > 0 and down_spy.mean() != 0:
                        dc = float(down_strat.mean() / down_spy.mean())
                        if "active_overlay" in condition_results:
                            condition_results["active_overlay"]["downside_capture"] = dc

            results[condition_name] = condition_results

        return results


def threshold_sweep_to_csv(metrics_list: list[AlarmDayMetrics]) -> pd.DataFrame:
    """Convert threshold sweep results to DataFrame for CSV export"""
    rows = []
    for metrics in metrics_list:
        row = {
            "threshold": metrics.threshold,
            "alarm_day_count": metrics.alarm_day_count,
            "coverage_count": metrics.coverage_count,
            "avg_spy_return_alarm": metrics.avg_spy_return_alarm,
            "avg_strategy_return_alarm": metrics.avg_strategy_return_alarm,
            "avg_excess_return_alarm": metrics.avg_excess_return_alarm,
            "sufficient_support": metrics.is_sufficient_support(),
        }
        if metrics.confusion_matrix:
            cm = metrics.confusion_matrix
            row.update({
                "true_positive": cm.true_positive,
                "false_positive": cm.false_positive,
                "true_negative": cm.true_negative,
                "false_negative": cm.false_negative,
                "cm_total": cm.total,
                "hit_rate": cm.hit_rate,
                "false_positive_rate": cm.false_positive_rate,
                "false_negative_rate": cm.false_negative_rate,
                "accuracy": cm.accuracy,
            })
        rows.append(row)
    return pd.DataFrame(rows)


def generate_analyzer_validation_summary(
    canonical_path: str | Path,
    overlay_path: str | Path | None = None,
    output_dir: str | Path = "outputs/alpha_assessment",
) -> dict[str, str]:
    """
    Orchestrate full analyzer validation and persist outputs.

    Args:
        canonical_path: Path to canonical_performance.csv
        overlay_path: Optional path to overlay_backtest.csv
        output_dir: Directory for outputs

    Returns:
        Dict with paths to generated files
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load data
    canonical = pd.read_csv(canonical_path)
    overlay = pd.read_csv(overlay_path) if overlay_path else None

    # Create validator
    validator = AnalyzerValidator(canonical, overlay)

    # Compute main metrics (default threshold)
    main_metrics = validator.compute_alarm_day_metrics(threshold=0.5)

    # Threshold sweep
    sweep_metrics = validator.threshold_sweep()
    sweep_df = threshold_sweep_to_csv(sweep_metrics)

    # Excess return attribution
    excess_attr = validator.compute_excess_return_attribution()

    # Overlay conditioning
    overlay_cond = validator.overlay_conditioned_benchmarking() if overlay is not None else {}

    # Build summary
    summary = {
        "metadata": {
            "date_generated": pd.Timestamp.now().isoformat(),
            "canonical_source": str(canonical_path),
            "overlay_source": str(overlay_path) if overlay_path else None,
            "note": "Small sample sizes may limit statistical confidence. See data notes.",
        },
        "analyzer_main_metrics": main_metrics.to_dict(),
        "excess_return_attribution": excess_attr,
        "overlay_conditioned": overlay_cond,
        "threshold_sweep": [m.to_dict() for m in sweep_metrics],
    }

    # Persist outputs
    summary_json = output_dir / "analyzer_validation_summary.json"
    with open(summary_json, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    sweep_csv = output_dir / "analyzer_threshold_sweep.csv"
    sweep_df.to_csv(sweep_csv, index=False)

    excess_csv = output_dir / "excess_return_attribution.csv"
    excess_df = pd.DataFrame([excess_attr])
    excess_df.to_csv(excess_csv, index=False)

    # Generate HTML report
    html = _generate_html_report(summary, main_metrics, sweep_df)
    html_path = output_dir / "analyzer_validation_summary.html"
    with open(html_path, "w") as f:
        f.write(html)

    return {
        "json": str(summary_json),
        "csv_sweep": str(sweep_csv),
        "csv_excess": str(excess_csv),
        "html": str(html_path),
    }


def _generate_html_report(summary: dict, main_metrics: AlarmDayMetrics, sweep_df: pd.DataFrame) -> str:
    """Generate HTML report from validation results"""
    cm = main_metrics.confusion_matrix or ConfusionMatrix()
    sufficient = main_metrics.is_sufficient_support()

    # Format values to avoid f-string format specifier issues
    avg_spy_val = f"{main_metrics.avg_spy_return_alarm:.4f}" if main_metrics.avg_spy_return_alarm is not None else "N/A"
    avg_strat_val = f"{main_metrics.avg_strategy_return_alarm:.4f}" if main_metrics.avg_strategy_return_alarm is not None else "N/A"
    avg_excess_val = f"{main_metrics.avg_excess_return_alarm:.4f}" if main_metrics.avg_excess_return_alarm is not None else "N/A"
    hit_rate_val = f"{cm.hit_rate:.2%}" if cm.hit_rate is not None else "N/A"
    fp_rate_val = f"{cm.false_positive_rate:.2%}" if cm.false_positive_rate is not None else "N/A"
    fnr_val = f"{cm.false_negative_rate:.2%}" if cm.false_negative_rate is not None else "N/A"
    accuracy_val = f"{cm.accuracy:.2%}" if cm.accuracy is not None else "N/A"

    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Analyzer Validation Report</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }}
        .section {{ background-color: white; padding: 20px; margin: 10px 0; border-radius: 5px; }}
        .metric {{ display: inline-block; margin: 10px 20px 10px 0; }}
        .metric-label {{ font-weight: bold; color: #333; }}
        .metric-value {{ font-size: 1.3em; color: #0066cc; }}
        table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
        th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
        th {{ background-color: #f2f2f2; font-weight: bold; }}
        .warning {{ color: #ff6600; font-weight: bold; }}
        .success {{ color: #00cc00; font-weight: bold; }}
        .insufficient {{ color: #ff0000; font-weight: bold; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
    </style>
</head>
<body>
<div class="container">
    <h1>Analyzer Validation & Excess Return Attribution</h1>
    <p><em>Generated: {summary["metadata"]["date_generated"]}</em></p>

    <div class="section">
        <h2>Main Metrics (Threshold = {main_metrics.threshold})</h2>
        <div class="metric">
            <div class="metric-label">Alarm Days</div>
            <div class="metric-value">{main_metrics.alarm_day_count}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Coverage Count</div>
            <div class="metric-value">{main_metrics.coverage_count}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Avg SPY Return (Alarm)</div>
            <div class="metric-value">{avg_spy_val}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Avg Strategy Return (Alarm)</div>
            <div class="metric-value">{avg_strat_val}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Avg Excess Return (Alarm)</div>
            <div class="metric-value">{avg_excess_val}</div>
        </div>
        <div class="metric">
            <div class="metric-label">Support Status</div>
            <div class="metric-value" style="color: {'#00cc00' if sufficient else '#ff0000'};">
                {'✓ Sufficient' if sufficient else '✗ Low Sample'}
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Confusion Matrix (Analyzer Predictiveness)</h2>
        <table>
            <tr>
                <th colspan="3">Analyzer vs SPY Direction (threshold={main_metrics.threshold})</th>
            </tr>
            <tr>
                <th></th>
                <th>SPY Up (+)</th>
                <th>SPY Down (-)</th>
            </tr>
            <tr>
                <th>Signal Bearish (≤{main_metrics.threshold})</th>
                <td>{cm.false_positive} (FP)</td>
                <td>{cm.true_positive} (TP)</td>
            </tr>
            <tr>
                <th>Signal Bullish (>{main_metrics.threshold})</th>
                <td>{cm.true_negative} (TN)</td>
                <td>{cm.false_negative} (FN)</td>
            </tr>
            <tr>
                <th>Total</th>
                <td colspan="2">{cm.total} rows</td>
            </tr>
        </table>

        <table>
            <tr>
                <th>Metric</th>
                <th>Value</th>
                <th>Interpretation</th>
            </tr>
            <tr>
                <td>Hit Rate (TP Rate)</td>
                <td>{hit_rate_val}</td>
                <td>% of bearish signals that correctly predicted SPY down</td>
            </tr>
            <tr>
                <td>False Positive Rate</td>
                <td>{fp_rate_val}</td>
                <td>% of SPY up days incorrectly signaled as bearish</td>
            </tr>
            <tr>
                <td>False Negative Rate</td>
                <td>{fnr_val}</td>
                <td>% of SPY down days incorrectly signaled as bullish</td>
            </tr>
            <tr>
                <td>Overall Accuracy</td>
                <td>{accuracy_val}</td>
                <td>% of all predictions correct</td>
            </tr>
        </table>
    </div>

    <div class="section">
        <h2>Excess Return Attribution</h2>
        <table>
            <tr>
                <th>Metric</th>
                <th>Value</th>
            </tr>
            <tr>
                <td>Strategy Total Return</td>
                <td>{summary["excess_return_attribution"].get("strategy_total_return", "N/A")}</td>
            </tr>
            <tr>
                <td>SPY Total Return</td>
                <td>{summary["excess_return_attribution"].get("spy_total_return", "N/A")}</td>
            </tr>
            <tr>
                <td>Avg Daily Excess Return</td>
                <td>{summary["excess_return_attribution"].get("avg_daily_excess_return", "N/A")}</td>
            </tr>
            <tr>
                <td>Excess Return Volatility</td>
                <td>{summary["excess_return_attribution"].get("excess_return_std", "N/A")}</td>
            </tr>
            <tr>
                <td>Avg Rolling Beta (20-day)</td>
                <td>{summary["excess_return_attribution"].get("avg_rolling_beta", "N/A")}</td>
            </tr>
            <tr>
                <td>Avg Rolling Alpha (20-day)</td>
                <td>{summary["excess_return_attribution"].get("avg_rolling_alpha", "N/A")}</td>
            </tr>
            <tr>
                <td>Information Ratio (annualized)</td>
                <td>{summary["excess_return_attribution"].get("information_ratio", "N/A")}</td>
            </tr>
            <tr>
                <td>Upside Capture Ratio</td>
                <td>{summary["excess_return_attribution"].get("upside_capture", "N/A")}</td>
            </tr>
            <tr>
                <td>Downside Capture Ratio</td>
                <td>{summary["excess_return_attribution"].get("downside_capture", "N/A")}</td>
            </tr>
        </table>
    </div>

    <div class="section">
        <h2>Threshold Sweep</h2>
        <table>
            <tr>
                <th>Threshold</th>
                <th>Alarm Days</th>
                <th>Coverage</th>
                <th>Avg SPY Return</th>
                <th>Hit Rate</th>
                <th>FP Rate</th>
                <th>Support</th>
            </tr>
"""

    for idx, row in sweep_df.iterrows():
        threshold = row["threshold"]
        alarm_count = row["alarm_day_count"]
        coverage = row["coverage_count"]
        avg_spy = row["avg_spy_return_alarm"]
        hit_rate = row["hit_rate"] if "hit_rate" in row and pd.notna(row["hit_rate"]) else "N/A"
        fp_rate = row["false_positive_rate"] if "false_positive_rate" in row and pd.notna(row["false_positive_rate"]) else "N/A"
        sufficient = row.get("sufficient_support", False)
        support_str = "✓" if sufficient else "✗"

        # Format values for display
        avg_spy_display = f"{avg_spy:.4f}" if pd.notna(avg_spy) else "N/A"
        hit_rate_display = f"{hit_rate:.1%}" if isinstance(hit_rate, (int, float)) else hit_rate
        fp_rate_display = f"{fp_rate:.1%}" if isinstance(fp_rate, (int, float)) else fp_rate

        html += f"""            <tr>
                <td>{threshold}</td>
                <td>{int(alarm_count)}</td>
                <td>{int(coverage)}</td>
                <td>{avg_spy_display}</td>
                <td>{hit_rate_display}</td>
                <td>{fp_rate_display}</td>
                <td>{support_str}</td>
            </tr>
"""

    html += """        </table>
    </div>

    <div class="section">
        <h2>Analysis Notes</h2>
        <ul>
            <li><strong>Sample Size:</strong> Results marked with ✗ (insufficient support) should be interpreted cautiously. Recommend ≥10 evaluation rows for threshold metrics.</li>
            <li><strong>Hit Rate:</strong> % of "bearish" signals that correctly predicted SPY downside. Higher is better.</li>
            <li><strong>False Positive Rate:</strong> % of "up" days incorrectly signaled as bearish. Lower is better.</li>
            <li><strong>Excess Return:</strong> Strategy return minus SPY benchmark return on same days.</li>
            <li><strong>Beta:</strong> Rolling 20-day regression slope (SPY sensitivity). &lt;1.0 = defensive positioning.</li>
            <li><strong>Alpha:</strong> Average outperformance vs regression line (intercept of rolling regression).</li>
            <li><strong>Information Ratio:</strong> Excess return / tracking error, annualized. Higher &gt; better.</li>
        </ul>
    </div>

    <div class="section">
        <h2>Data Availability Notes</h2>
        <p><em>Note: {summary["metadata"].get("note", "")}</em></p>
    </div>
</div>
</body>
</html>
"""

    return html
