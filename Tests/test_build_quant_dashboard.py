from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "research" / "build_quant_dashboard.py"


class BuildQuantDashboardTest(unittest.TestCase):
    def test_build_quant_dashboard_writes_broker_authoritative_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-04-08T072000-0400_test"
            run_root.mkdir(parents=True)
            broker_dir = run_root / "broker"
            broker_dir.mkdir(parents=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "run_root": str(run_root),
                        "trade_date": "2026-04-08",
                        "workflow_stage": "execution",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (run_root / "operator_summary.json").write_text(
                json.dumps(
                    {
                        "run_id": "run-123",
                        "trade_date": "2026-04-08",
                        "pretrade_status": "READY",
                        "post_execution_recon_status": "PASS",
                        "broker_authoritative_state": True,
                        "broker_pretrade_snapshot_ok": True,
                        "broker_posttrade_snapshot_ok": True,
                        "broker_preflight_cash": 1000.5,
                        "broker_preflight_equity": 5000.0,
                        "broker_preflight_buying_power": 7500.0,
                        "broker_preflight_warning_flags": ["pdt_watch"],
                        "repair_suggestions": ["none"],
                        "affected_symbols": ["AAPL", "MSFT"],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (broker_dir / "pretrade_account_snapshot.json").write_text(
                json.dumps({"cash": "1000.50", "equity": "5000.00", "buying_power": "7500.00"}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_positions.json").write_text(
                json.dumps([{"symbol": "AAPL"}, {"symbol": "MSFT"}]) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_account_snapshot.json").write_text(
                json.dumps({"cash": "900.25", "equity": "5100.00"}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_positions.json").write_text(
                json.dumps([{"symbol": "AAPL"}, {"symbol": "MSFT"}, {"symbol": "NVDA"}]) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "recon_posttrade.json").write_text(
                json.dumps({"verdict": "PASS", "repair_suggestions": ["none"], "affected_symbols": ["NVDA"]}) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard-data.json").read_text(encoding="utf-8"))
            dashboard_js = (tmp_path / "web" / "dashboard" / "dashboard-data.js").read_text(encoding="utf-8")
            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            copied_summary = json.loads((tmp_path / "web" / "dashboard" / "trading_day_summary.json").read_text(encoding="utf-8"))

            self.assertTrue(payload["broker"]["authoritativeState"])
            self.assertEqual(payload["broker"]["trustLevel"], "HIGH")
            self.assertEqual(payload["broker"]["pretrade"]["positionsCount"], 2)
            self.assertEqual(payload["broker"]["posttrade"]["positionsCount"], 3)
            self.assertEqual(payload["broker"]["delta"]["positionsCount"], 1)
            self.assertEqual(payload["broker"]["delta"]["cash"], -100.25)
            self.assertEqual(
                payload["broker"]["authoritativeMessage"],
                "Today's run used broker-authoritative state.",
            )
            self.assertTrue(dashboard_js.startswith("window.DASHBOARD_DATA = "))
            self.assertEqual(legacy_payload["broker_snapshot"]["equity"], 5100.0)
            self.assertEqual(legacy_payload["kpis"]["holdings"], 3)
            self.assertEqual(copied_summary["trade_date"], "2026-04-08")

    def test_fallback_banner_uses_governed_date_when_latest_run_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-04-08T072000-0400_failed"
            run_root.mkdir(parents=True)
            (tmp_path / "outputs").mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "perf").mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-04-08",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-04-08T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-04-08",
                        "mode": "alpaca",
                        "created_at": "2026-04-08T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "perf" / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-04-07,10050,2050,0.796,0.796,0.004,500,0.05,0.05\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "ledger" / "paper").mkdir(
                parents=True, exist_ok=True
            )
            (
                tmp_path / "outputs" / "ledger" / "paper" / "daily_nav.csv"
            ).write_text(
                "date,equity,profit_loss,profit_loss_pct,base_value,source,pulled_at_utc\n"
                "2026-04-07,9999,-1,-0.0001,10000,alpaca_portfolio_history,2026-04-08T12:00:00Z\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "perf" / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-07,505.0,0.001\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "2100", "equity": "10100", "buying_power": "20200", "last_equity": "10000"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 4, "positions": [{"symbol": "AAPL"}]}) + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "recon_posttrade_2026-04-08.json").write_text(
                json.dumps({"drift_status": "PASS"}) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertEqual(legacy_payload["run_meta"]["report_date"], "2026-04-07")
            self.assertEqual(legacy_payload["run_meta"]["run_id"], "governed-fallback-2026-04-07")
            self.assertEqual(
                legacy_payload["builder_notes"]["performance"]["source_name"],
                "broker_truth_paper",
            )
            self.assertEqual(
                legacy_payload["governed_snapshot"]["portfolio_value"], 9999.0
            )
            self.assertEqual(legacy_payload["run_meta"]["selected_governed_run"]["report_date"], "2026-04-07")
            self.assertIn("Showing governed dashboard state from 2026-04-07.", legacy_payload["run_meta"]["status_banner"])
            self.assertIn("newer broker snapshot is available", legacy_payload["run_meta"]["status_banner"])
            self.assertNotIn("incomplete or failed", legacy_payload["run_meta"]["status_banner"])

    def test_live_broker_overlay_prefers_same_day_broker_activity_and_ignores_stale_recon(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-04-08T072000-0400_failed"
            run_root.mkdir(parents=True)
            (tmp_path / "outputs" / "perf").mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker_snapshot").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-04-08",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-04-08T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-04-08",
                        "mode": "alpaca",
                        "created_at": "2026-04-08T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "perf" / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-04-07,10050,2050,0.796,0.796,0.004,500,0.05,0.05\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "perf" / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-07,505.0,0.001\n",
                encoding="utf-8",
            )

            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "2100", "equity": "10100", "buying_power": "20200", "last_equity": "10000"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 4, "positions": [{"symbol": "MSFT"}, {"symbol": "NVDA"}]}) + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "intended_orders_2026-03-31.json").write_text(
                json.dumps(
                    {
                        "report_date": "2026-03-31",
                        "run_id": "stale-plan",
                        "orders_intended": [{"ticker": "AAPL", "side": "BUY", "notional": 1000}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "recon_posttrade_2026-03-31.json").write_text(
                json.dumps({"trade_date": "2026-03-31", "drift_status": "DRIFT_DETECTED"}) + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-08.json").write_text(
                json.dumps(
                    {
                        "meta": {"report_date": "2026-04-08"},
                        "orders_report_date": [
                            {"symbol": "MSFT", "side": "buy", "status": "filled", "filled_avg_price": "300", "filled_qty": "5"},
                            {"symbol": "NVDA", "side": "sell", "status": "filled", "filled_avg_price": "900", "filled_qty": "1"},
                        ],
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
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertTrue(legacy_payload["run_meta"]["live_broker_overlay"])
            self.assertIn("Live broker overlay active for 2026-04-08.", legacy_payload["run_meta"]["status_banner"])
            self.assertEqual(legacy_payload["data_freshness"]["broker_vs_run_alignment"], "overlay")
            self.assertEqual(legacy_payload["activity"]["activity_source"], "broker_snapshot_current")
            self.assertEqual(legacy_payload["activity"]["orders_filled"], 2)
            self.assertEqual(legacy_payload["activity"]["source_report_date"], "2026-04-08")
            self.assertEqual(legacy_payload["execution_integrity"]["post_execution_recon_status"], "OVERLAY_ONLY")
            self.assertEqual(legacy_payload["kpis"]["portfolio_value"], 10100.0)
            self.assertEqual(legacy_payload["kpis"]["daily_pl"], 100.0)
            self.assertAlmostEqual(legacy_payload["kpis"]["daily_return"], 0.01)
            self.assertAlmostEqual(legacy_payload["kpis"]["benchmark_return"], 0.001)
            self.assertAlmostEqual(legacy_payload["kpis"]["excess_return"], 0.009)
            self.assertEqual(legacy_payload["run_meta"]["comparison_mode"], "previous_trading_day")
            self.assertEqual(legacy_payload["run_meta"]["benchmark_asof_date"], "2026-04-07")
            self.assertEqual(legacy_payload["top_changes"][0]["ticker"], "MSFT")
            self.assertFalse(
                any(item["category"] == "Reconciliation" for item in legacy_payload["exceptions"])
            )

    def test_historical_failed_run_is_downgraded_when_live_overlay_is_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-03-26T151337-0400_failed"
            run_root.mkdir(parents=True)
            (tmp_path / "outputs" / "perf").mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-03-18",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-03-18",
                        "mode": "alpaca",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "perf" / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-02-27,9999.55,3148.25,0.68,0.68,0.0,0,0.0,0.0\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "perf" / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-02-27,505.0,0.001\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "2100", "equity": "10100", "buying_power": "20200", "last_equity": "10000"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 4, "positions": [{"symbol": "MSFT"}, {"symbol": "NVDA"}]}) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            execution_exception = next(item for item in legacy_payload["exceptions"] if item["category"] == "Execution")
            self.assertEqual("warning", execution_exception["status"])
            self.assertIn("Historical latest attempted run status", execution_exception["message"])
            artifact_exception = next(item for item in legacy_payload["exceptions"] if item["category"] == "Data / artifacts")
            self.assertEqual("warning", artifact_exception["status"])
            self.assertIn("Missing historical run artifacts", artifact_exception["message"])
            self.assertNotIn("critical", artifact_exception["message"].lower())
            run_check = next(item for item in legacy_payload["operating_checks"] if item["label"] == "Run completed")
            self.assertEqual("warning", run_check["status"])

    def test_plan_only_latest_run_with_missing_execution_results_still_uses_overlay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-04-10T150856-0400_35b3591"
            broker_dir = run_root / "broker"
            broker_dir.mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker_snapshot").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "2026-04-10T150856-0400_35b3591",
                        "run_root": str(run_root),
                        "trade_date": "2026-04-09",
                        "mode": "alpaca",
                        "status": "no_action",
                        "created_at": "2026-04-10T19:08:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "2026-04-10T150856-0400_35b3591",
                        "path": str(run_root),
                        "report_date": "2026-04-09",
                        "mode": "alpaca",
                        "created_at": "2026-04-10T19:08:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (run_root / "operator_summary.json").write_text(
                json.dumps({"run_id": "2026-04-10T150856-0400_35b3591", "mode": "PAPER", "trade_date": "2026-04-09"}) + "\n",
                encoding="utf-8",
            )
            (run_root / "execution_payload.json").write_text(
                json.dumps({"run_id": "2026-04-10T150856-0400_35b3591", "status": "READY", "trades": []}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-10",
                        "captured_at": "2026-04-10T19:08:57Z",
                        "account": {"cash": "1471.25", "equity": "9602.73", "buying_power": "10517.98", "last_equity": "9600"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (broker_dir / "pretrade_positions.json").write_text(
                json.dumps({"positions_count": 16, "positions": [{"symbol": "MSFT"}, {"symbol": "NVDA"}]}) + "\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-10",
                        "captured_at": "2026-04-10T19:08:57Z",
                        "account": {"cash": "1471.25", "equity": "9602.73", "buying_power": "10517.98", "last_equity": "9600"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 16, "positions": [{"symbol": "MSFT"}, {"symbol": "NVDA"}]}) + "\n",
                encoding="utf-8",
            )
            (run_root / "reports").mkdir(parents=True, exist_ok=True)
            (run_root / "reports" / "quant_report_2099-01-01.html").write_text("<html>ok</html>\n", encoding="utf-8")
            (tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-04-10.json").write_text(
                json.dumps(
                    {
                        "meta": {"report_date": "2026-04-10"},
                        "orders_report_date": [{"symbol": "MSFT", "side": "buy", "status": "filled", "filled_avg_price": "300", "filled_qty": "5"}],
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
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertTrue(legacy_payload["run_meta"]["live_broker_overlay"])
            self.assertEqual(legacy_payload["data_freshness"]["broker_vs_run_alignment"], "overlay")
            self.assertIn("plan-only/no_action smoke run", legacy_payload["run_meta"]["status_banner"])
            self.assertNotIn("2099-01-01", legacy_payload["run_meta"]["status_banner"])
            artifact_exception = next(item for item in legacy_payload["exceptions"] if item["category"] == "Data / artifacts")
            self.assertIn("Missing historical run artifacts", artifact_exception["message"])
            self.assertIn("expected for plan-only/no_action runs", artifact_exception["message"])
            self.assertEqual("warning", artifact_exception["status"])

    def test_live_overlay_performance_series_drives_perf_summary_and_same_day_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-03-26T151337-0400_failed"
            run_root.mkdir(parents=True)
            perf_dir = tmp_path / "outputs" / "perf"
            perf_dir.mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-03-18",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-03-18",
                        "mode": "alpaca",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (perf_dir / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-02-27,9999.55,3148.25,0.68,0.68,0.0,0,0.0,0.0\n",
                encoding="utf-8",
            )
            (perf_dir / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-02-27,505.0,0.001\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_nav_series.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2025-12-10,0,,,,,,,\n"
                "2026-04-07,9600,,,,,,,\n"
                "2026-04-08,9720,1580.45,,,,,,\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-07,500,\n"
                "2026-04-08,505,0.01\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "1580.45", "equity": "9720", "buying_power": "19440", "last_equity": "9600"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 16, "positions": [{"symbol": "MSFT"}, {"symbol": "NVDA"}]}) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertEqual(
                legacy_payload["run_meta"]["performance_source"],
                "paper_broker_intraday_overlay",
            )
            self.assertEqual(legacy_payload["run_meta"]["portfolio_asof_date"], "2026-04-08")
            self.assertEqual(legacy_payload["run_meta"]["benchmark_asof_date"], "2026-04-08")
            self.assertEqual(legacy_payload["run_meta"]["comparison_mode"], "same_day")
            self.assertAlmostEqual(legacy_payload["perf_summary"]["mtd_return"], 0.0125)
            self.assertAlmostEqual(legacy_payload["perf_summary"]["qtd_return"], 0.0125)
            self.assertAlmostEqual(legacy_payload["perf_summary"]["since_inception_return"], 0.0125)
            self.assertAlmostEqual(legacy_payload["perf_summary"]["since_inception_alpha"], 0.0025)
            self.assertAlmostEqual(legacy_payload["perf_summary"]["best_day"], 0.0125)
            self.assertAlmostEqual(legacy_payload["perf_summary"]["worst_day"], 0.0125)
            self.assertAlmostEqual(legacy_payload["kpis"]["benchmark_return"], 0.01)
            self.assertAlmostEqual(legacy_payload["kpis"]["excess_return"], 0.0025)
            self.assertEqual(legacy_payload["series"]["nav"][-1]["date"], "2026-04-08")

    def test_one_row_live_overlay_series_yields_null_perf_stats_instead_of_zeroes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-03-26T151337-0400_failed"
            run_root.mkdir(parents=True)
            perf_dir = tmp_path / "outputs" / "perf"
            perf_dir.mkdir(parents=True, exist_ok=True)
            (tmp_path / "outputs" / "broker").mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-03-18",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-03-18",
                        "mode": "alpaca",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (perf_dir / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-02-27,9999.55,3148.25,0.68,0.68,0.0,0,0.0,0.0\n",
                encoding="utf-8",
            )
            (perf_dir / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-02-27,505.0,0.001\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_nav_series.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-04-08,9720,1580.45,,,,,,\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-07,500,\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "1580.45", "equity": "9720", "buying_power": "19440", "last_equity": "9600"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "broker" / "posttrade_positions.json").write_text(
                json.dumps({"positions_count": 16, "positions": [{"symbol": "MSFT"}]}) + "\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--repo-root",
                    str(tmp_path),
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertEqual(
                legacy_payload["run_meta"]["performance_source"],
                "paper_broker_intraday_overlay",
            )
            self.assertIsNone(legacy_payload["perf_summary"]["mtd_return"])
            self.assertIsNone(legacy_payload["perf_summary"]["qtd_return"])
            self.assertIsNone(legacy_payload["perf_summary"]["since_inception_return"])
            self.assertIsNone(legacy_payload["perf_summary"]["since_inception_alpha"])
            self.assertIsNone(legacy_payload["perf_summary"]["current_drawdown"])
            self.assertIsNone(legacy_payload["perf_summary"]["best_day"])
            self.assertIsNone(legacy_payload["perf_summary"]["worst_day"])

    def test_attribution_and_edge_diagnostics_are_included_in_overlay_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            run_root = tmp_path / "outputs" / "runs" / "2026-03-26T151337-0400_failed"
            run_root.mkdir(parents=True)
            perf_dir = tmp_path / "outputs" / "perf"
            broker_dir = tmp_path / "outputs" / "broker"
            broker_snapshot_dir = tmp_path / "outputs" / "broker_snapshot"
            perf_dir.mkdir(parents=True, exist_ok=True)
            broker_dir.mkdir(parents=True, exist_ok=True)
            broker_snapshot_dir.mkdir(parents=True, exist_ok=True)

            (tmp_path / "outputs" / "latest_run.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "run_root": str(run_root),
                        "trade_date": "2026-03-18",
                        "mode": "alpaca",
                        "status": "failed_unknown",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (tmp_path / "outputs" / "latest.json").write_text(
                json.dumps(
                    {
                        "run_id": "failed-run",
                        "path": str(run_root),
                        "report_date": "2026-03-18",
                        "mode": "alpaca",
                        "created_at": "2026-03-26T13:00:00Z",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (perf_dir / "nav_timeseries.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-04-07,100.0,20.0,0.8,0.8,0.0,0,0.0,0.0\n",
                encoding="utf-8",
            )
            (perf_dir / "benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-07,100.0,\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_nav_series.csv").write_text(
                "date,equity,cash,gross_exposure,net_exposure,return_1d,turnover_dollars,turnover_pct,turnover\n"
                "2026-04-06,100.0,,,,,,,\n"
                "2026-04-07,101.0,10.0,,,,,,\n"
                "2026-04-08,103.02,10.3,,,,,,\n",
                encoding="utf-8",
            )
            (perf_dir / "live_overlay_benchmark_close_history.csv").write_text(
                "date,spy_close,spy_return\n"
                "2026-04-06,100.0,\n"
                "2026-04-07,101.0,0.01\n"
                "2026-04-08,102.515,0.015\n",
                encoding="utf-8",
            )
            (perf_dir / "contribution_tickers_2026-04-07.csv").write_text(
                "ticker,weight_start,return,contribution,sleeve\n"
                "AAA,0.20,-0.01,-0.002,core\n"
                "BBB,0.15,0.03,0.0045,core\n"
                "CCC,0.10,0.02,0.0020,defensive\n",
                encoding="utf-8",
            )
            (perf_dir / "contribution_sleeves_2026-04-07.csv").write_text(
                "sleeve,weight_start,sleeve_return,contribution\n"
                "core,0.35,0.00714,0.0025\n"
                "defensive,0.10,0.02,0.0020\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_account_snapshot.json").write_text(
                json.dumps(
                    {
                        "trade_date": "2026-04-08",
                        "captured_at": "2026-04-08T14:00:00Z",
                        "account": {"cash": "10.3", "equity": "103.02", "buying_power": "206.04", "last_equity": "101.0"},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (broker_dir / "posttrade_positions.json").write_text(
                json.dumps(
                    {
                        "positions_count": 3,
                        "positions": [
                            {"symbol": "AAA", "market_value": "30.0"},
                            {"symbol": "BBB", "market_value": "25.0"},
                            {"symbol": "CCC", "market_value": "20.0"},
                        ],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (broker_snapshot_dir / "broker_snapshot_2026-04-08.json").write_text(
                json.dumps(
                    {
                        "meta": {"report_date": "2026-04-08"},
                        "positions_current": [
                            {"symbol": "AAA", "market_value": "30.0", "unrealized_plpc": "0.01"},
                            {"symbol": "BBB", "market_value": "25.0", "unrealized_plpc": "0.02"},
                            {"symbol": "CCC", "market_value": "20.0", "unrealized_plpc": "-0.01"},
                        ],
                        "orders_report_date": [
                            {"symbol": "AAA", "side": "buy", "status": "filled", "filled_avg_price": "10", "filled_qty": "1"},
                            {"symbol": "BBB", "side": "sell", "status": "filled", "filled_avg_price": "20", "filled_qty": "1"},
                        ],
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
                    "--output-dir",
                    "web/dashboard",
                ],
                cwd=tmp_path,
                check=True,
                capture_output=True,
                text=True,
            )

            legacy_payload = json.loads((tmp_path / "web" / "dashboard" / "dashboard_data.json").read_text(encoding="utf-8"))
            self.assertEqual(
                legacy_payload["run_meta"]["performance_source"],
                "paper_broker_intraday_overlay",
            )
            self.assertEqual(legacy_payload["attribution"]["n_days"], 2)
            self.assertAlmostEqual(legacy_payload["attribution"]["cumulative_alpha"], 0.00505, places=6)
            self.assertAlmostEqual(legacy_payload["attribution"]["upside_capture"], 1.2)
            self.assertEqual(legacy_payload["contribution_snapshot"]["asof_date"], "2026-04-07")
            self.assertEqual(legacy_payload["contribution_snapshot"]["top_winners"][0]["ticker"], "BBB")
            self.assertAlmostEqual(legacy_payload["edge_diagnostics"]["current_cash_ratio"], 0.0999805862948942)
            self.assertAlmostEqual(legacy_payload["edge_diagnostics"]["largest_position_weight"], 30.0 / 103.02)
            self.assertTrue(legacy_payload["edge_diagnostics"]["signals"])
            self.assertEqual(legacy_payload["portfolio_history"]["summary"]["counts"]["transactions"], 2)
            self.assertEqual(legacy_payload["portfolio_history"]["summary"]["counts"]["positions"], 3)
            self.assertEqual(legacy_payload["portfolio_history"]["transactions"][0]["ticker"], "AAA")
            self.assertEqual(legacy_payload["portfolio_history"]["positions"][0]["ticker"], "AAA")
            self.assertTrue((tmp_path / "outputs" / "portfolio_history" / "transactions.csv").exists())
            self.assertTrue(
                any(
                    item["path"] == "outputs/portfolio_history/transactions.csv" and item["status"] == "used"
                    for item in legacy_payload["sources"]
                )
            )


if __name__ == "__main__":
    unittest.main()
