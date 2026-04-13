from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "research" / "build_engine_evaluation.py"


class BuildEngineEvaluationTest(unittest.TestCase):
    def test_build_engine_evaluation_writes_summary_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "outputs" / "perf").mkdir(parents=True)
            (tmp_path / "outputs" / "broker_snapshot").mkdir(parents=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True)
            (tmp_path / "outputs" / "execution_email").mkdir(parents=True)
            (tmp_path / "outputs" / "research").mkdir(parents=True)
            (tmp_path / "signals").mkdir(parents=True)
            (tmp_path / "data").mkdir(parents=True)

            (tmp_path / "outputs" / "perf" / "live_overlay_nav_series.csv").write_text(
                "\n".join(
                    [
                        "date,equity,return_1d",
                        "2026-04-07,100.0,0.0",
                        "2026-04-08,101.0,0.01",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "perf" / "live_overlay_benchmark_close_history.csv").write_text(
                "\n".join(
                    [
                        "date,spy_close,spy_return",
                        "2026-04-07,100.0,0.0",
                        "2026-04-08,103.0,0.03",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-08.json").write_text(
                json.dumps(
                    {
                        "orders_closed_recent": [
                            {
                                "client_order_id": "2026-04-07:main:growth_engine_v4:AAA:BUY",
                                "filled_at": "2026-04-07T14:00:00Z",
                                "filled_avg_price": "100",
                                "filled_qty": "1",
                                "side": "buy",
                                "status": "filled",
                                "symbol": "AAA",
                            },
                            {
                                "client_order_id": "2026-04-08:main:growth_engine_v4:AAA:SELL",
                                "filled_at": "2026-04-08T14:00:00Z",
                                "filled_avg_price": "102",
                                "filled_qty": "1",
                                "side": "sell",
                                "status": "filled",
                                "symbol": "AAA",
                            },
                            {
                                "client_order_id": "2026-04-08:main:growth_engine_v4:BBB:BUY",
                                "filled_at": "2026-04-08T14:05:00Z",
                                "filled_avg_price": "50",
                                "filled_qty": "2",
                                "side": "buy",
                                "status": "filled",
                                "symbol": "BBB",
                            },
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "equity": "101.0",
                        "cash": "11.0",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps(
                    {
                        "positions": [
                            {
                                "symbol": "BBB",
                                "qty": "2",
                                "market_value": "90.0",
                                "unrealized_pl": "4.0",
                                "unrealized_plpc": "0.046",
                            }
                        ]
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "signals" / "2026-04-07.json").write_text(
                json.dumps({"signals": [{"ticker": "AAA", "target_weight": 1.0}]}) + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "execution_email" / "2026-04-07.json").write_text(
                json.dumps(
                    {
                        "execution_status": "PLANNED",
                        "plan_only": True,
                        "orders_submitted_count": 1,
                        "orders_filled_count": 0,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "data" / "universe.csv").write_text(
                "ticker,sector\nAAA,Information Technology\nBBB,Financials\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "research" / "sleeve1_backtest_2009_2025_summary.csv").write_text(
                "\n".join(
                    [
                        "window_name,start_date,end_date,port_cagr,port_sharpe,port_max_dd,spy_cagr,spy_sharpe,spy_max_dd,avg_holdings",
                        "full_period,2009-01-01,2025-12-31,0.11,1.0,-0.10,0.12,0.7,-0.30,4.0",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "research" / "sleeve1_alpha_variant_summary.csv").write_text(
                "\n".join(
                    [
                        "start_date,end_date,net_cagr,net_sharpe,net_max_drawdown,net_beta_vs_spy,net_cagr_cb,net_sharpe_cb,net_max_drawdown_cb,net_beta_vs_spy_cb,avg_turnover,cost_bps,spy_cagr",
                        "2009-01-01,2025-12-31,0.21,0.95,-0.36,1.10,0.24,1.05,-0.31,1.02,0.80,25,0.12",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "research" / "worst_window_full.json").write_text(
                json.dumps(
                    {
                        "start_date": "2022-01-07",
                        "end_date": "2025-01-06",
                        "max_drawdown": -0.35,
                        "cagr": 0.15,
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "ic_monitor").mkdir(parents=True)
            (tmp_path / "outputs" / "ic_monitor" / "ic_summary.json").write_text(
                json.dumps(
                    {
                        "status": "ok",
                        "as_of_date": "2026-04-08",
                        "alerts": ["sleeve_quality: 20d rolling IC has been <= 0 for 12 consecutive days"],
                        "sleeves": {
                            "sleeve_quality": {
                                "latest_date": "2026-04-08",
                                "latest_ic_by_horizon": {"1": 0.158, "5": 0.124, "10": 0.112, "21": 0.089},
                                "latest_rolling_ic_by_horizon": {"1": {"20": -0.081, "60": 0.013}},
                                "alerts": ["sleeve_quality: 20d rolling IC has been <= 0 for 12 consecutive days"],
                            }
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            summary = json.loads((tmp_path / "outputs" / "engine_evaluation" / "summary.json").read_text(encoding="utf-8"))
            report = (tmp_path / "outputs" / "engine_evaluation" / "report.md").read_text(encoding="utf-8")
            audit_tape = (tmp_path / "outputs" / "engine_evaluation" / "audit_tape.csv").read_text(encoding="utf-8")

            self.assertEqual(summary["as_of_date"], "2026-04-08")
            self.assertTrue(summary["live_performance"]["available"])
            self.assertAlmostEqual(summary["live_performance"]["portfolio_total_return"], 0.01, places=6)
            self.assertAlmostEqual(summary["live_performance"]["benchmark_total_return"], 0.03, places=6)
            self.assertEqual(summary["objective_contract"]["benchmark"], "SPY")
            self.assertEqual(summary["objective_scorecard"]["overall_status"], "off_track")
            self.assertIn("Live annualized return", {check["label"] for check in summary["objective_scorecard"]["checks"]})
            self.assertAlmostEqual(summary["artifact_coverage"]["signal_snapshot_fill_date_coverage"], 0.5, places=6)
            self.assertAlmostEqual(summary["artifact_coverage"]["execution_email_fill_date_coverage"], 0.5, places=6)
            self.assertEqual(len(summary["artifact_coverage"]["telemetry_mismatches"]), 1)
            self.assertEqual(summary["broker_activity"]["quick_flip_count"], 1)
            self.assertIn("canonical daily audit tape", " ".join(summary["recommendations"]))
            self.assertIn("# Engine Evaluation", report)
            self.assertIn("## North Star", report)
            self.assertIn("Scorecard status: OFF_TRACK", report)
            self.assertIn("Trade churn is high relative to account size", report)
            self.assertIn("## Signal IC Monitor", report)
            self.assertIn("sleeve_quality: 20d rolling IC -0.08", report)
            self.assertIn("latest 1d IC 0.16", report)
            self.assertIn("Active alerts:", report)
            self.assertIn("20d rolling IC has been <= 0 for 12 consecutive days", report)
            self.assertIn("trade_date,signal_snapshot_present", audit_tape)
            self.assertIn("2026-04-07,True,True", audit_tape)


if __name__ == "__main__":
    unittest.main()
