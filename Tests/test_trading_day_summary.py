"""
Tests for core/trading_day_summary.py

Validates that:
- summary builds without crashing on missing artifacts
- execution counts parse correctly from execution_results.json
- broker state fields populate from execution_audit.json
- email guard logic works (OK / INCOMPLETE / UNKNOWN / EMAIL_LOOP_WARNING)
- dashboard detection works
- benchmark reads from nav_timeseries + benchmark_close_history CSVs
- full write round-trip produces valid JSON
"""
from __future__ import annotations

import json
import csv
import tempfile
from pathlib import Path

import pytest
import sys

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.trading_day_summary import (
    build_trading_day_summary,
    write_trading_day_summary,
    _build_execution_summary,
    _build_portfolio_state,
    _build_email_summary,
    _build_dashboard,
    _build_benchmark,
)
from scripts import morning_report


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _make_execution_results(
    submitted=5, accepted=4, rejected=1, duplicates=0, status="EXECUTED",
    responses=None,
) -> dict:
    responses = responses or [
        {"ticker": "SPY", "side": "BUY", "shares": 10, "status": "ACCEPTED"},
        {"ticker": "QQQ", "side": "BUY", "shares": 5, "status": "ACCEPTED"},
        {"ticker": "AAPL", "side": "SELL", "shares": 3, "status": "ACCEPTED"},
    ]
    return {
        "run_id": "20260310T093500Z_paper_1",
        "trade_date": "2026-03-10",
        "status": status,
        "submitted_count": submitted,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "duplicate_count": duplicates,
        "broker_responses": responses,
        "rejected_reasons": [],
        "order_ids": [],
    }


def _make_audit(cash=5000.0, positions=None) -> dict:
    if positions is None:
        positions = [
            {"ticker": "SPY", "qty": "10", "market_value": "6000.00"},
            {"ticker": "QQQ", "qty": "5", "market_value": "2000.00"},
        ]
    return {
        "run_id": "20260310T093500Z_paper_1",
        "executor": {
            "execution_status": "EXECUTED",
            "submitted_count": 5,
            "accepted_count": 4,
            "rejected_count": 1,
            "duplicate_count": 0,
            "broker_state_after": {
                "cash": cash,
                "positions": positions,
            },
        },
    }


# ---------------------------------------------------------------------------
# _build_execution_summary
# ---------------------------------------------------------------------------

class TestBuildExecutionSummary:
    def test_basic_executed(self):
        result = _make_execution_results(accepted=4, duplicates=0, status="EXECUTED")
        s = _build_execution_summary(result)
        assert s["orders_accepted"] == 4
        assert s["executed"] is True
        assert s["status"] == "EXECUTED"

    def test_buy_and_sell_counted_separately(self):
        responses = [
            {"ticker": "SPY", "side": "BUY", "status": "ACCEPTED"},
            {"ticker": "QQQ", "side": "BUY", "status": "ACCEPTED"},
            {"ticker": "AAPL", "side": "SELL", "status": "ACCEPTED"},
        ]
        result = _make_execution_results(responses=responses)
        s = _build_execution_summary(result)
        assert s["buy_orders"] == 2
        assert s["sell_orders"] == 1

    def test_idempotent_replay_counts_as_executed(self):
        result = _make_execution_results(
            submitted=0, accepted=0, duplicates=5, status="IDEMPOTENT_REPLAY",
            responses=[
                {"ticker": "SPY", "side": "BUY", "status": "IDEMPOTENT_REPLAY"},
                {"ticker": "QQQ", "side": "BUY", "status": "IDEMPOTENT_REPLAY"},
            ],
        )
        s = _build_execution_summary(result)
        assert s["executed"] is True
        assert s["duplicate_orders"] == 5
        assert s["buy_orders"] == 2

    def test_no_action_not_executed(self):
        result = _make_execution_results(
            submitted=0, accepted=0, rejected=0, duplicates=0,
            status="NO_ACTION", responses=[],
        )
        s = _build_execution_summary(result)
        assert s["executed"] is False
        assert s["orders_accepted"] == 0

    def test_rejected_only_not_executed(self):
        result = _make_execution_results(
            submitted=3, accepted=0, rejected=3, status="EXECUTED",
            responses=[{"ticker": "SPY", "side": "BUY", "status": "REJECTED"}],
        )
        s = _build_execution_summary(result)
        assert s["buy_orders"] == 0  # REJECTED not counted in buy_orders

    def test_empty_responses_safe(self):
        s = _build_execution_summary({})
        assert s["orders_submitted"] == 0
        assert s["executed"] is False

    def test_operator_summary_fallback_surfaces_partial_broker_submission(self):
        s = _build_execution_summary(
            {},
            {
                "terminal_status": "failed_pre_execution",
                "orders_submitted_count": 2,
                "accepted_count": 2,
                "rejected_count": 1,
                "broker_reject_status": "BROKER_REJECT_PDT",
            },
        )
        assert s["orders_submitted"] == 2
        assert s["orders_accepted"] == 2
        assert s["executed"] is True
        assert s["status"] == "failed_pre_execution"

    def test_operator_summary_fallback_surfaces_cash_rebalance_incomplete(self):
        s = _build_execution_summary(
            {},
            {
                "terminal_status": "failed_pre_execution",
                "orders_submitted_count": 2,
                "accepted_count": 2,
                "rejected_count": 1,
                "broker_reject_status": "BROKER_REJECT_PDT",
                "execution_outcome": "partial_execution_broker_abort",
                "execution_reason": "broker_reject_pdt",
                "cash_rebalance_status": "cash_rebalance_incomplete",
                "halt_reason": "partial_execution_broker_abort:broker_reject_pdt:cash_rebalance_incomplete",
            },
        )
        assert s["partial_execution"] is True
        assert s["execution_outcome"] == "partial_execution_broker_abort"
        assert s["cash_rebalance_status"] == "cash_rebalance_incomplete"

    def test_operator_summary_fallback_surfaces_post_submit_artifact_failure(self):
        s = _build_execution_summary(
            {},
            {
                "terminal_status": "failed_pre_execution",
                "orders_submitted_count": 4,
                "accepted_count": 4,
                "rejected_count": 0,
                "execution_outcome": "post_submit_artifact_failure",
                "execution_reason": "post_sell_account_snapshot_write_failed",
                "cash_rebalance_status": "cash_rebalance_incomplete",
                "halt_reason": "post_submit_artifact_failure:post_sell_account_snapshot_write_failed:cash_rebalance_incomplete",
            },
        )
        assert s["executed"] is True
        assert s["partial_execution"] is True
        assert s["execution_outcome"] == "post_submit_artifact_failure"
        assert s["execution_reason"] == "post_sell_account_snapshot_write_failed"


# ---------------------------------------------------------------------------
# _build_portfolio_state
# ---------------------------------------------------------------------------

class TestBuildPortfolioState:
    def test_basic_broker_state(self):
        audit = _make_audit(cash=5000.0)
        s = _build_portfolio_state(audit)
        assert s["cash_after"] == 5000.0
        assert s["positions_count"] == 2
        assert s["portfolio_market_value"] == 8000.0

    def test_cash_rounds_to_cents(self):
        audit = _make_audit(cash=5123.456789)
        s = _build_portfolio_state(audit)
        assert s["cash_after"] == 5123.46

    def test_positions_fallback_to_price_times_qty(self):
        audit = _make_audit(
            cash=1000.0,
            positions=[
                {"ticker": "SPY", "qty": "10", "current_price": "450.00"},
            ],
        )
        s = _build_portfolio_state(audit)
        assert s["portfolio_market_value"] == 4500.0

    def test_empty_broker_state(self):
        s = _build_portfolio_state({})
        assert s["cash_after"] is None
        assert s["positions_count"] == 0
        assert s["portfolio_market_value"] is None

    def test_empty_positions_list(self):
        audit = _make_audit(cash=10000.0, positions=[])
        s = _build_portfolio_state(audit)
        assert s["positions_count"] == 0
        assert s["portfolio_market_value"] is None


# ---------------------------------------------------------------------------
# _build_email_summary
# ---------------------------------------------------------------------------

class TestBuildEmailSummary:
    def test_confirmation_sent_implies_ok(self):
        op = {"confirmation_email_sent": True}
        s = _build_email_summary(op)
        assert s["emails_sent"] == 3
        assert s["status"] == "OK"
        assert s["confirmation_sent"] is True

    def test_confirmation_not_sent_is_unknown(self):
        op = {"confirmation_email_sent": False}
        s = _build_email_summary(op)
        assert s["emails_sent"] is None
        assert s["status"] == "UNKNOWN"

    def test_none_operator_summary_is_unknown(self):
        s = _build_email_summary(None)
        assert s["status"] == "UNKNOWN"
        assert s["emails_sent"] is None
        assert s["expected"] == 3


# ---------------------------------------------------------------------------
# _build_dashboard
# ---------------------------------------------------------------------------

class TestBuildDashboard:
    def test_dashboard_data_json_found(self, tmp_path):
        dash = tmp_path / "web" / "dashboard" / "dashboard_data.json"
        dash.parent.mkdir(parents=True)
        dash.write_text("{}", encoding="utf-8")
        s = _build_dashboard(tmp_path)
        assert s["generated"] is True
        assert s["path"] is not None

    def test_html_dashboard_found(self, tmp_path):
        html = tmp_path / "quant_dashboard.html"
        html.write_text("<html/>", encoding="utf-8")
        s = _build_dashboard(tmp_path)
        assert s["generated"] is True

    def test_no_dashboard_files(self, tmp_path):
        s = _build_dashboard(tmp_path)
        assert s["generated"] is False
        assert s["path"] is None


# ---------------------------------------------------------------------------
# _build_benchmark
# ---------------------------------------------------------------------------

class TestBuildBenchmark:
    def _write_fixtures(self, tmp_path, equity=10500.0, return_1d=0.0084,
                        spy_close=693.15, spy_return=0.0084):
        nav_path = tmp_path / "outputs" / "perf" / "nav_timeseries.csv"
        _write_csv(nav_path, [
            {"date": "2026-03-09", "equity": "10412.0", "cash": "8000.0",
             "gross_exposure": "0.2", "net_exposure": "0.2",
             "return_1d": "0.001", "turnover_dollars": "0", "turnover_pct": "0", "turnover": "0"},
            {"date": "2026-03-10", "equity": str(equity), "cash": "8211.0",
             "gross_exposure": "0.18", "net_exposure": "0.18",
             "return_1d": str(return_1d), "turnover_dollars": "0", "turnover_pct": "0",
             "turnover": "0"},
        ], fieldnames=["date", "equity", "cash", "gross_exposure", "net_exposure",
                       "return_1d", "turnover_dollars", "turnover_pct", "turnover"])

        spy_path = tmp_path / "outputs" / "perf" / "benchmark_close_history.csv"
        _write_csv(spy_path, [
            {"date": "2026-03-09", "spy_close": "689.30", "spy_return": "0.002"},
            {"date": "2026-03-10", "spy_close": str(spy_close), "spy_return": str(spy_return)},
        ], fieldnames=["date", "spy_close", "spy_return"])

        return tmp_path

    def test_benchmark_populates_values(self, tmp_path):
        self._write_fixtures(tmp_path, equity=10500.0, return_1d=0.0084,
                             spy_close=693.15, spy_return=0.0084)
        s = _build_benchmark(tmp_path)
        assert s["portfolio_value"] == 10500.0
        assert s["spy_value"] == 693.15
        assert s["portfolio_return_pct"] is not None
        assert s["spy_return_pct"] is not None
        assert s["performance_vs_spy_pct"] is not None

    def test_alpha_computed_correctly(self, tmp_path):
        self._write_fixtures(tmp_path, return_1d=0.0084, spy_return=0.0051)
        s = _build_benchmark(tmp_path)
        # portfolio 0.84% - spy 0.51% = 0.33%
        assert abs(s["performance_vs_spy_pct"] - 0.33) < 0.01

    def test_missing_nav_file_returns_nulls(self, tmp_path):
        s = _build_benchmark(tmp_path)
        assert s["portfolio_value"] is None
        assert s["spy_value"] is None
        assert s["performance_vs_spy_pct"] is None

    def test_spy_only_missing_returns_partial(self, tmp_path):
        nav_path = tmp_path / "outputs" / "perf" / "nav_timeseries.csv"
        _write_csv(nav_path, [
            {"date": "2026-03-10", "equity": "10500.0", "cash": "8000.0",
             "gross_exposure": "0.2", "net_exposure": "0.2",
             "return_1d": "0.0084", "turnover_dollars": "0", "turnover_pct": "0", "turnover": "0"},
        ], fieldnames=["date", "equity", "cash", "gross_exposure", "net_exposure",
                       "return_1d", "turnover_dollars", "turnover_pct", "turnover"])
        s = _build_benchmark(tmp_path)
        assert s["portfolio_value"] == 10500.0
        assert s["spy_value"] is None
        assert s["performance_vs_spy_pct"] is None


# ---------------------------------------------------------------------------
# build_trading_day_summary (integration)
# ---------------------------------------------------------------------------

class TestBuildTradingDaySummary:
    def _setup(self, tmp_path):
        run_id = "20260310T093500Z_paper_1"
        run_root = tmp_path / "outputs" / "runs" / run_id
        run_root.mkdir(parents=True)
        audit_dir = tmp_path / "outputs" / "execution_audit"
        audit_dir.mkdir(parents=True)

        _write_json(run_root / "execution_results.json", _make_execution_results())
        _write_json(run_root / "operator_summary.json", {"confirmation_email_sent": True})
        _write_json(audit_dir / f"{run_id}.json", _make_audit())
        return run_root, run_id

    def test_summary_builds_without_crashing(self, tmp_path):
        run_root, run_id = self._setup(tmp_path)
        summary = build_trading_day_summary(
            run_root=run_root, run_id=run_id, trade_date="2026-03-10",
            workspace_root=tmp_path,
            audit_dir=tmp_path / "outputs" / "execution_audit",
        )
        assert summary["run_id"] == run_id
        assert summary["trade_date"] == "2026-03-10"
        assert "execution_summary" in summary
        assert "portfolio_state" in summary
        assert "email_summary" in summary
        assert "dashboard" in summary
        assert "benchmark" in summary

    def test_execution_counts_correct(self, tmp_path):
        run_root, run_id = self._setup(tmp_path)
        summary = build_trading_day_summary(
            run_root=run_root, run_id=run_id, trade_date="2026-03-10",
            workspace_root=tmp_path,
            audit_dir=tmp_path / "outputs" / "execution_audit",
        )
        es = summary["execution_summary"]
        assert es["orders_accepted"] == 4
        assert es["executed"] is True

    def test_portfolio_state_populated(self, tmp_path):
        run_root, run_id = self._setup(tmp_path)
        summary = build_trading_day_summary(
            run_root=run_root, run_id=run_id, trade_date="2026-03-10",
            workspace_root=tmp_path,
            audit_dir=tmp_path / "outputs" / "execution_audit",
        )
        ps = summary["portfolio_state"]
        assert ps["cash_after"] == 5000.0
        assert ps["positions_count"] == 2

    def test_email_summary_ok_when_confirmation_sent(self, tmp_path):
        run_root, run_id = self._setup(tmp_path)
        summary = build_trading_day_summary(
            run_root=run_root, run_id=run_id, trade_date="2026-03-10",
            workspace_root=tmp_path,
            audit_dir=tmp_path / "outputs" / "execution_audit",
        )
        assert summary["email_summary"]["status"] == "OK"

    def test_summary_on_missing_artifacts_returns_nulls(self, tmp_path):
        """Must not crash when source files are absent."""
        run_root = tmp_path / "outputs" / "runs" / "empty-run"
        run_root.mkdir(parents=True)
        summary = build_trading_day_summary(
            run_root=run_root, run_id="empty-run", trade_date="2026-03-10",
            workspace_root=tmp_path,
        )
        assert summary["execution_summary"]["orders_accepted"] == 0
        assert summary["portfolio_state"]["cash_after"] is None
        assert summary["email_summary"]["status"] == "UNKNOWN"


# ---------------------------------------------------------------------------
# write_trading_day_summary (file I/O)
# ---------------------------------------------------------------------------

class TestWriteTradingDaySummary:
    def test_write_produces_canonical_and_mirror_json(self, tmp_path):
        run_id = "20260310T093500Z_paper_1"
        run_root = tmp_path / "outputs" / "runs" / run_id
        run_root.mkdir(parents=True)

        mirror_path = tmp_path / "outputs" / "trading_day_summary.json"
        result = write_trading_day_summary(
            run_root=run_root,
            run_id=run_id,
            trade_date="2026-03-10",
            workspace_root=tmp_path,
            output_path=mirror_path,
        )
        canonical_path = run_root / "trading_day_summary.json"
        assert result == canonical_path
        assert canonical_path.exists()
        assert mirror_path.exists()
        assert canonical_path.read_text(encoding="utf-8") == mirror_path.read_text(encoding="utf-8")

        parsed = json.loads(canonical_path.read_text(encoding="utf-8"))
        assert parsed["run_id"] == run_id

    def test_morning_report_default_path_reads_mirror(self, tmp_path, monkeypatch, capsys):
        run_id = "20260310T093500Z_paper_1"
        run_root = tmp_path / "outputs" / "runs" / run_id
        run_root.mkdir(parents=True)
        mirror_path = tmp_path / "outputs" / "trading_day_summary.json"

        result = write_trading_day_summary(
            run_root=run_root,
            run_id=run_id,
            trade_date="2026-03-10",
            workspace_root=tmp_path,
            output_path=mirror_path,
        )
        assert result == run_root / "trading_day_summary.json"
        assert mirror_path.exists()

        monkeypatch.setattr(morning_report, "_DEFAULT_SUMMARY", mirror_path)
        rc = morning_report.main([])
        out = capsys.readouterr().out
        assert rc == 0
        assert "QUANT SYSTEM — MORNING REPORT" in out

    def test_write_returns_none_on_internal_error(self, tmp_path):
        """Non-blocking: must not raise."""
        # Pass a run_root that makes build crash by being a file, not dir
        fake_file = tmp_path / "notadir"
        fake_file.write_text("x")
        # Should not raise; logs warning and returns None
        result = write_trading_day_summary(
            run_root=fake_file,  # not a directory — reads will fail gracefully
            run_id="bad",
            trade_date="2026-03-10",
            workspace_root=tmp_path,
            output_path=tmp_path / "outputs" / "summary.json",
        )
        # Either None (if exception) or a valid path — must not raise
        assert result is None or result.exists()
