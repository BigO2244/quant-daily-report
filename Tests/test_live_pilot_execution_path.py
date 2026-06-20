from __future__ import annotations

import json
from pathlib import Path

from scripts.live_pilot_execute import run_live_pilot


class FakeBroker:
    paper = False
    base_url = "https://api.alpaca.markets"

    def __init__(self, *, order_status: str = "accepted") -> None:
        self.submit_calls = 0
        self.order_status = order_status

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

    def submit_limit_order(self, **kwargs):
        self.submit_calls += 1
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
            {"ticker": "AAPL", "side": "BUY", "shares": 1, "limit_price": 50},
        ]
    }


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
    assert (run_root / "live_pilot_reconciliation.json").exists()
    assert (run_root / "live_pilot_capital_usage.json").exists()
    assert (run_root / "live_pilot_operator_summary.json").exists()
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
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-submit"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submit_calls == 1
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert reconciliation["state"] == "CLEAN"
    usage = json.loads((run_root / "live_pilot_capital_usage.json").read_text())
    assert usage["submitted_notional_usd"] == 50


def test_over_cap_plan_does_not_submit_and_writes_operator_action(tmp_path: Path) -> None:
    broker = FakeBroker()

    result = run_live_pilot(
        plan={"trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1, "limit_price": 150}]},
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-blocked",
        output_root=tmp_path / "outputs" / "live_pilot",
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
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-rejected"
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "FAILED_RECONCILIATION"
    assert reconciliation["state"] == "REJECTED"
    assert reconciliation["operator_action"]


def test_unresolved_order_produces_failed_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="accepted")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-unresolved",
        output_root=tmp_path / "outputs" / "live_pilot",
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-unresolved"
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["state"] == "UNRESOLVED"
    assert reconciliation["unresolved_count"] == 1


def test_partial_order_produces_partial_failed_reconciliation(tmp_path: Path) -> None:
    broker = FakeBroker(order_status="partially_filled")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-partial",
        output_root=tmp_path / "outputs" / "live_pilot",
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
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "account_id_mismatch"
    assert broker.submit_calls == 0
