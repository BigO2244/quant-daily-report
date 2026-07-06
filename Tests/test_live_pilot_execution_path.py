from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.live_pilot_execute import refresh_live_pilot_reconciliation, run_live_pilot


ET = ZoneInfo("America/New_York")


class FakeBroker:
    paper = False
    base_url = "https://api.alpaca.markets"

    def __init__(self, *, order_status: str = "accepted") -> None:
        self.submit_calls = 0
        self.market_calls = 0
        self.limit_calls = 0
        self.submitted_methods: list[str] = []
        self.order_status = order_status
        self.open_orders: list[dict[str, object]] = []

    def get_account(self):
        return {
            "id": "acct-123",
            "status": "ACTIVE",
            "cash": "500",
            "equity": "500",
            "buying_power": "500",
            "portfolio_value": "500",
        }

    def get_positions(self):
        return []

    def get_asset(self, symbol):
        return {
            "symbol": symbol,
            "status": "active",
            "asset_class": "us_equity",
            "tradable": True,
        }

    def get_order(self, order_id):
        return {
            "id": order_id,
            "status": self.order_status,
            "symbol": "AAPL",
            "client_order_id": "client-refresh",
            "filled_qty": "1" if self.order_status == "filled" else None,
            "filled_quantity": "1" if self.order_status == "filled" else None,
            "filled_avg_price": "50.10" if self.order_status == "filled" else None,
            "filled_at": "2026-03-17T13:35:02+00:00" if self.order_status == "filled" else None,
        }

    def list_orders(self, status="open", limit=100):
        return list(self.open_orders)

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        self.market_calls += 1
        self.submitted_methods.append("market")
        return {
            "id": f"order-{self.submit_calls}",
            "status": self.order_status,
            "symbol": kwargs.get("symbol"),
            "client_order_id": kwargs.get("client_order_id"),
            "filled_avg_price": "50.10" if self.order_status == "filled" else None,
            "submitted_at": "2026-03-17T13:35:00+00:00",
            "filled_at": "2026-03-17T13:35:02+00:00" if self.order_status == "filled" else None,
        }

    def submit_limit_order(self, **kwargs):
        self.submit_calls += 1
        self.limit_calls += 1
        self.submitted_methods.append("limit")
        return {
            "id": f"order-{self.submit_calls}",
            "status": self.order_status,
            "symbol": kwargs.get("symbol"),
            "client_order_id": kwargs.get("client_order_id"),
        }


def _env(*, dry_run: str = "1", max_orders: str = "1") -> dict[str, str]:
    return {
        "TRADING_MODE": "live_pilot",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "100",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "polaris",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "acct-123",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": max_orders,
        "CAERUS_LIVE_PILOT_DRY_RUN": dry_run,
    }


def _plan() -> dict[str, object]:
    return {
        "trades": [
            {"ticker": "AAPL", "side": "BUY", "shares": 1, "limit_price": 50, "order_type": "market"},
        ]
    }


def _limit_plan() -> dict[str, object]:
    return {
        "trades": [
            {"ticker": "AAPL", "side": "BUY", "shares": 1, "limit_price": 50, "order_type": "limit"},
        ]
    }


def _market_open_now() -> dt.datetime:
    return dt.datetime(2026, 3, 17, 9, 35, tzinfo=ET)


def test_dry_run_writes_isolated_artifacts_and_does_not_submit(tmp_path: Path) -> None:
    broker = FakeBroker()

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="1"),
        run_id="run-dry",
        output_root=tmp_path / "outputs" / "live_pilot",
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-dry"
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0
    assert (run_root / "live_pilot_execution_payload.json").exists()
    assert (run_root / "live_pilot_preflight.json").exists()
    assert (run_root / "live_pilot_orders_intended.json").exists()
    assert (run_root / "live_pilot_orders_submitted.json").exists()
    assert (run_root / "live_pilot_broker_snapshot_pre.json").exists()
    assert (run_root / "live_pilot_broker_snapshot_post.json").exists()
    assert (run_root / "live_pilot_open_order_check.json").exists()
    assert (run_root / "live_pilot_market_hours_gate.json").exists()
    assert (run_root / "live_pilot_reconciliation.json").exists()
    assert (run_root / "live_pilot_evidence_metrics.json").exists()
    assert (run_root / "live_pilot_capital_usage.json").exists()
    assert (run_root / "live_pilot_operator_summary.json").exists()
    assert (run_root / "execution_results.json").exists()
    assert not (tmp_path / "outputs" / "runs").exists()

    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "DRY_RUN_NOT_SUBMITTED"


def test_successful_mocked_live_pilot_submits_after_all_gates(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="filled")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-submit",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-submit"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submit_calls == 1
    assert broker.market_calls == 1
    assert broker.limit_calls == 0
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["submitted_order_type"] == "market"
    assert submitted["orders"][0]["expected_price"] == 50
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert reconciliation["state"] == "CLEAN"
    evidence = json.loads((run_root / "live_pilot_evidence_metrics.json").read_text())
    assert evidence["submitted_count"] == 1
    assert evidence["filled_count"] == 1
    assert evidence["fill_rate"] == 1.0
    assert evidence["average_time_to_fill_seconds"] == 2.0
    assert evidence["slippage_bps"] is not None
    usage = json.loads((run_root / "live_pilot_capital_usage.json").read_text())
    assert usage["submitted_notional_usd"] == 50


def test_live_buy_limit_plan_uses_approved_limit_order_without_policy_override(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="filled")

    result = run_live_pilot(
        plan=_limit_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-limit-to-market",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-limit-to-market"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_methods == ["limit"]
    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    assert intended["orders"][0]["order_type"] == "limit"
    assert "entry_execution_policy" not in intended["orders"][0]
    assert "approved_order_type" not in intended["orders"][0]
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["submitted_order_type"] == "limit"
    assert "entry_execution_policy" not in submitted["orders"][0]
    assert "order_type_submitted" not in submitted["orders"][0]
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["entry_execution_policy"] is None
    assert results["submitted_order_type"] == "limit"
    assert results["marketable_order_count"] == 0
    assert results["passive_order_count"] == 0


def test_over_cap_plan_does_not_submit_and_writes_operator_action(tmp_path: Path) -> None:
    broker = FakeBroker()

    result = run_live_pilot(
        plan={"trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "limit_price": 150}]},
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-blocked",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-blocked"
    assert result["terminal_status"] == "BLOCKED"
    assert broker.submit_calls == 0
    summary = json.loads((run_root / "live_pilot_operator_summary.json").read_text())
    assert "live_pilot_total_notional_exceeds_cap" in summary["reason_code"]


def test_rejected_order_produces_failed_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="rejected")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-rejected",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-rejected"
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "FAILED_RECONCILIATION"
    assert reconciliation["state"] == "REJECTED"
    assert reconciliation["operator_action"]


def test_accepted_open_order_produces_clean_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="OrderStatus.NEW")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-open",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-open"
    assert result["terminal_status"] == "SUBMITTED"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert reconciliation["state"] == "CLEAN"
    assert reconciliation["accepted_count"] == 1
    assert reconciliation["open_count"] == 1
    assert reconciliation["unresolved_count"] == 0


def test_unknown_order_status_produces_failed_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="pending_review")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-unresolved",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-unresolved"
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["state"] == "UNRESOLVED"
    assert reconciliation["unresolved_count"] == 1



def test_refresh_existing_run_reconciles_open_broker_order(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="OrderStatus.NEW")
    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-refresh"
    run_root.mkdir(parents=True)
    (run_root / "live_pilot_orders_intended.json").write_text(
        json.dumps({"orders": [{"symbol": "AAPL", "side": "BUY", "qty": 1, "limit_price": 50}]}),
        encoding="utf-8",
    )
    (run_root / "live_pilot_orders_submitted.json").write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "symbol": "AAPL",
                        "side": "BUY",
                        "qty": 1,
                        "limit_price": 50,
                        "status": "OrderStatus.PENDING_NEW",
                        "order": {"id": "broker-order-1", "status": "OrderStatus.PENDING_NEW"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = refresh_live_pilot_reconciliation(run_root=run_root, broker=broker)

    assert result["terminal_status"] == "SUBMITTED"
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "OrderStatus.NEW"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert reconciliation["open_count"] == 1
    assert reconciliation["refreshed_existing_run"] is True


def test_refresh_existing_market_order_updates_stale_pending_to_filled(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="filled")
    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-refresh-filled"
    run_root.mkdir(parents=True)
    (run_root / "live_pilot_orders_intended.json").write_text(
        json.dumps({"orders": [{"symbol": "AAPL", "side": "BUY", "qty": 1, "limit_price": 50, "order_type": "market"}]}),
        encoding="utf-8",
    )
    (run_root / "live_pilot_orders_submitted.json").write_text(
        json.dumps(
            {
                "orders": [
                    {
                        "symbol": "AAPL",
                        "side": "BUY",
                        "qty": 1,
                        "limit_price": 50,
                        "status": "OrderStatus.PENDING_NEW",
                        "submitted_order_type": "market",
                        "order_type_submitted": "market",
                        "entry_execution_policy": "live_pilot_buy_market_order_immediate",
                        "is_marketable": True,
                        "is_passive": False,
                        "order": {"id": "broker-order-1", "status": "OrderStatus.PENDING_NEW"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = refresh_live_pilot_reconciliation(run_root=run_root, broker=broker)

    assert result["terminal_status"] == "SUBMITTED"
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "filled"
    assert submitted["orders"][0]["filled_qty"] == "1"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert reconciliation["filled_count"] == 1
    assert reconciliation["open_count"] == 0
    assert reconciliation["broker_status_refresh"] == "OK"
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["filled_count"] == 1
    assert results["filled_qty"] == 1.0
    assert results["avg_fill_price"] == 50.10
    assert results["open_orders_count"] == 0
    assert results["broker_status_refresh"] == "OK"
    assert results["idle_cash_reason"] != "submitted_not_filled"


def test_partial_order_produces_partial_failed_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="partially_filled")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-partial",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-partial"
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["state"] == "PARTIAL"
    assert reconciliation["partial_count"] == 1


def test_unsupported_asset_class_does_not_submit(tmp_path: Path) -> None:
    class CryptoBroker(FakeBroker):
        def get_asset(self, symbol):
            return {
                "symbol": symbol,
                "status": "active",
                "asset_class": "crypto",
                "tradable": True,
            }

    broker = CryptoBroker()

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-asset-blocked",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
    assert "unsupported_asset_class" in result["reason_code"]
    assert broker.submit_calls == 0


def test_account_mismatch_blocks_before_submit(tmp_path: Path) -> None:
    broker = FakeBroker()
    env = _env(dry_run="0")
    env["CAERUS_LIVE_PILOT_ACCOUNT_ID"] = "other-account"

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="run-account-block",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "account_id_mismatch"
    assert broker.submit_calls == 0


def test_open_live_pilot_order_blocks_duplicate_submission(tmp_path: Path) -> None:
    broker = FakeBroker()
    broker.open_orders = [
        {
            "symbol": "AAPL",
            "status": "new",
            "client_order_id": "caerus-live-pilot-existing",
        }
    ]

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-open-order-block",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-open-order-block"
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_OPEN_PILOT_ORDER"
    assert broker.submit_calls == 0
    open_check = json.loads((run_root / "live_pilot_open_order_check.json").read_text())
    assert open_check["block_submission"] is True
    assert open_check["blocking_open_orders"][0]["duplicate_reason"] == "open_live_pilot_order"
    evidence = json.loads((run_root / "live_pilot_evidence_metrics.json").read_text())
    assert evidence["idle_cash_reason"] == "open_order_blocked_duplicate_exposure"


def test_live_pilot_market_order_fails_closed_outside_market_hours(tmp_path: Path) -> None:
    broker = FakeBroker()

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-market-closed",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=dt.datetime(2026, 3, 17, 8, 0, tzinfo=ET),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-market-closed"
    assert result["terminal_status"] == "BLOCKED"
    assert "live_pilot_market_closed" in result["reason_code"]
    assert broker.submit_calls == 0
    market_gate = json.loads((run_root / "live_pilot_market_hours_gate.json").read_text())
    assert market_gate["status"] == "BLOCKED"
