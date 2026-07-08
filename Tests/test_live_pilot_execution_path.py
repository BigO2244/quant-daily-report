from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import core.live_trade_ledger as live_trade_ledger
from scripts.live_pilot_execute import (
    LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
    LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
    LIVE_PILOT_SELL_SETTLEMENT_CASH_NOT_REFLECTED,
    refresh_live_pilot_reconciliation,
    run_live_pilot,
)


ET = ZoneInfo("America/New_York")


class FakeBroker:
    paper = False
    base_url = "https://api.alpaca.markets"

    def __init__(
        self,
        *,
        order_status: str = "accepted",
        buying_power: str = "500",
        cash: str | None = None,
        equity: str = "500",
        positions: list[dict[str, object]] | None = None,
    ) -> None:
        self.submit_calls = 0
        self.market_calls = 0
        self.limit_calls = 0
        self.submitted_methods: list[str] = []
        self.submitted_sides: list[str] = []
        self.order_status = order_status
        self.open_orders: list[dict[str, object]] = []
        self.buying_power = buying_power
        self.cash = cash if cash is not None else buying_power
        self.equity = equity
        self.positions = list(positions or [])

    def get_account(self):
        return {
            "id": "acct-123",
            "status": "ACTIVE",
            "cash": self.cash,
            "equity": self.equity,
            "buying_power": self.buying_power,
            "portfolio_value": self.equity,
        }

    def get_positions(self):
        return list(self.positions)

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
        self.submitted_sides.append(str(kwargs.get("side") or "").upper())
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
        self.submitted_sides.append(str(kwargs.get("side") or "").upper())
        return {
            "id": f"order-{self.submit_calls}",
            "status": self.order_status,
            "symbol": kwargs.get("symbol"),
            "client_order_id": kwargs.get("client_order_id"),
        }


def _env(*, dry_run: str = "1", max_orders: str = "1") -> dict[str, str]:
    return {
        "TRADING_MODE": "live_pilot",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": "https://api.alpaca.markets",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "500",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "polaris",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "acct-123",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": max_orders,
        "CAERUS_LIVE_PILOT_DRY_RUN": dry_run,
        # Kill switch fails closed: unset/garbage blocks; arming requires explicit "off".
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
    }


def _gate_state(run_root: Path) -> dict[str, object]:
    return json.loads((run_root / "live_pilot_gate_state.json").read_text(encoding="utf-8"))


def _ledger_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _plan() -> dict[str, object]:
    return {
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 150,
                "order_type": "market",
                "sleeve": "polaris",
                "sleeve_source": "precompute_live_strategy_id",
                "source_strategy_id": "polaris",
                "source_signal_sleeve": "sleeve_trend",
                "sleeve_provenance": {
                    "sleeve_source": "precompute_live_strategy_id",
                    "source_strategy_id": "polaris",
                    "source_signal_sleeve": "sleeve_trend",
                },
            },
        ]
    }


def _limit_plan() -> dict[str, object]:
    return {
        "trades": [
            {
                "ticker": "AAPL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 150,
                "order_type": "limit",
                "sleeve": "polaris",
                "sleeve_source": "precompute_live_strategy_id",
                "source_strategy_id": "polaris",
                "source_signal_sleeve": "sleeve_trend",
                "sleeve_provenance": {
                    "sleeve_source": "precompute_live_strategy_id",
                    "source_strategy_id": "polaris",
                    "source_signal_sleeve": "sleeve_trend",
                },
            },
        ]
    }


def _all_plan() -> dict[str, object]:
    return {
        "trades": [
            {
                "ticker": "ALL",
                "side": "BUY",
                "shares": 1,
                "limit_price": 250.33,
                "order_type": "market",
                "sleeve": "polaris",
                "sleeve_source": "precompute_live_strategy_id",
                "source_strategy_id": "polaris",
                "source_signal_sleeve": "sleeve_trend",
                "sleeve_provenance": {
                    "sleeve_source": "precompute_live_strategy_id",
                    "source_strategy_id": "polaris",
                    "source_signal_sleeve": "sleeve_trend",
                },
            },
        ]
    }


class RotatingFakeBroker(FakeBroker):
    def __init__(self, *, sell_fills: bool = True, settle_cash: bool = True) -> None:
        super().__init__(
            order_status="filled",
            buying_power="150",
            cash="150",
            equity="500",
            positions=[{"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}],
        )
        self.sell_fills = sell_fills
        self.settle_cash = settle_cash
        self.orders_by_id: dict[str, dict[str, object]] = {}

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        self.market_calls += 1
        side = str(kwargs.get("side") or "").upper()
        symbol = str(kwargs.get("symbol") or "").upper()
        qty = float(kwargs.get("qty") or 0.0)
        estimated = float(kwargs.get("estimated_notional") or 0.0)
        self.submitted_methods.append("market")
        self.submitted_sides.append(side)
        order_id = f"order-{self.submit_calls}"
        status = "filled"
        filled_qty = qty
        filled_avg_price = estimated / qty if qty > 0 else None
        if side == "SELL" and not self.sell_fills:
            status = "accepted"
            filled_qty = None
            filled_avg_price = None
        order = {
            "id": order_id,
            "status": status,
            "symbol": symbol,
            "side": side,
            "client_order_id": kwargs.get("client_order_id"),
            "filled_qty": str(filled_qty) if filled_qty is not None else None,
            "filled_quantity": str(filled_qty) if filled_qty is not None else None,
            "filled_avg_price": str(filled_avg_price) if filled_avg_price is not None else None,
            "submitted_at": "2026-03-17T13:35:00+00:00",
            "filled_at": "2026-03-17T13:35:02+00:00" if status == "filled" else None,
        }
        self.orders_by_id[order_id] = order
        if side == "SELL" and status == "filled" and self.settle_cash:
            self.cash = str(float(self.cash) + estimated)
            self.buying_power = str(float(self.buying_power) + estimated)
            self.positions = [row for row in self.positions if row.get("symbol") != symbol]
        elif side == "BUY" and status == "filled":
            self.cash = str(float(self.cash) - estimated)
            self.buying_power = str(float(self.buying_power) - estimated)
            self.positions.append({"symbol": symbol, "qty": str(qty), "market_value": str(estimated)})
        return order

    def get_order(self, order_id):
        return dict(self.orders_by_id.get(order_id, {}))


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
    assert not (tmp_path / "outputs" / "live_pilot" / "live_trade_ledger.jsonl").exists()
    assert (run_root / "live_pilot_entry_attempt_history.json").exists()
    assert (run_root / "live_pilot_broker_snapshot_pre.json").exists()
    assert (run_root / "live_pilot_broker_snapshot_post.json").exists()
    assert (run_root / "live_pilot_open_order_check.json").exists()
    assert (run_root / "live_pilot_market_hours_gate.json").exists()
    assert (run_root / "live_pilot_reconciliation.json").exists()
    assert (run_root / "live_pilot_evidence_metrics.json").exists()
    assert (run_root / "live_pilot_capital_usage.json").exists()
    assert (run_root / "live_pilot_gate_state.json").exists()
    assert (run_root / "live_pilot_operator_summary.json").exists()
    assert (run_root / "execution_results.json").exists()
    assert not (tmp_path / "outputs" / "runs").exists()

    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "ALLOWED"
    assert gate_state["alpaca_endpoint_category"] == "live"
    assert gate_state["ALPACA_PAPER"] == "0"
    assert gate_state["configured_cap_usd"] == 500.0
    assert gate_state["approved_max_cap_usd"] == 500.0
    assert gate_state["effective_cap_usd"] == 500.0
    assert gate_state["cap_source"] == "env"
    assert gate_state["broker_orders_submitted"] == 0

    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "DRY_RUN_NOT_SUBMITTED"
    assert submitted["orders"][0]["sleeve"] == "polaris"
    assert submitted["orders"][0]["sleeve_source"] == "precompute_live_strategy_id"
    assert submitted["orders"][0]["source_signal_sleeve"] == "sleeve_trend"
    history = json.loads((run_root / "live_pilot_entry_attempt_history.json").read_text())
    assert history["orders"][0]["sleeve"] == "polaris"
    assert history["orders"][0]["source_strategy_id"] == "polaris"


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
    assert submitted["orders"][0]["entry_execution_policy"] == "live_pilot_buy_market_order_immediate"
    assert submitted["orders"][0]["is_marketable"] is True
    assert submitted["orders"][0]["is_passive"] is False
    assert submitted["orders"][0]["expected_price"] == 150
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
    assert usage["submitted_notional_usd"] == 150
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "ALLOWED"
    assert gate_state["broker_orders_submitted"] == 1
    ledger_records = _ledger_records(tmp_path / "outputs" / "live_pilot" / "live_trade_ledger.jsonl")
    assert [row["event"] for row in ledger_records] == ["submitted"]
    assert ledger_records[0]["client_order_id"] == submitted["orders"][0]["client_order_id"]
    assert ledger_records[0]["broker_order_id"] == "order-1"


def test_live_trade_ledger_write_failure_does_not_propagate(tmp_path: Path, monkeypatch) -> None:
    def raise_fsync(_fd):
        raise OSError("fsync failed")

    monkeypatch.setattr(live_trade_ledger.os, "fsync", raise_fsync)

    live_trade_ledger.record_live_order(
        event="submitted",
        symbol="AAPL",
        side="BUY",
        qty=1,
        client_order_id="client-1",
        output_root=tmp_path / "outputs" / "live_pilot",
    )


def test_unwhitelisted_live_sell_blocks_before_broker_submission_and_records_capital_gate(
    tmp_path: Path,
) -> None:
    broker = FakeBroker(
        buying_power="0.88",
        cash="0.88",
        equity="506.82",
        positions=[
            {"symbol": "ABBV", "qty": "1.186242896", "market_value": "303.227409", "cost_basis": "299.400361"},
            {"symbol": "ALL", "qty": "0.420274014", "market_value": "104.061947", "cost_basis": "100.996048"},
            {"symbol": "C", "qty": "0.68975031", "market_value": "98.654987", "cost_basis": "98.668782"},
        ],
    )

    result = run_live_pilot(
        plan=_all_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-existing-positions",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-existing-positions"
    assert result["terminal_status"] == "BLOCKED"
    assert "sell_not_whitelisted" in result["reason_code"]
    assert broker.submit_calls == 0
    assert broker.market_calls == 0
    assert broker.limit_calls == 0
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "BLOCKED"
    assert "sell_not_whitelisted" in gate_state["block_reason"]
    assert gate_state["broker_orders_submitted"] == 0

    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["decision"] == "ALLOWED"
    assert capital_gate["live_buying_power_before"] == 0.88
    assert capital_gate["approved_cap_usd"] == 500.0
    assert capital_gate["required_sell_count"] >= 1
    assert capital_gate["sell_first_supported"] is True
    assert capital_gate["rebudget_after_sell_supported"] is True
    assert capital_gate["broker_orders_submitted"] == 0
    assert [row["symbol"] for row in capital_gate["live_positions_before"]] == ["ABBV", "ALL", "C"]

    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"] == []
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "BLOCKED_PRE_SUBMISSION"
    assert reconciliation["state"] == "BLOCKED"
    assert reconciliation["buy_block_reason"] is None
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["status"] == "BLOCKED"
    assert results["broker_responses"] == []


def _rotation_plan() -> dict[str, object]:
    return {
        "target_portfolio": [
            {
                "ticker": "NEW",
                "symbol": "NEW",
                "side": "BUY",
                "target_weight": 0.4,
                "price": 100,
                "order_type": "market",
                "sleeve": "polaris",
            }
        ]
    }


def test_core_routed_live_rotation_sells_settles_rebudgets_and_buys(tmp_path: Path) -> None:
    broker = RotatingFakeBroker(sell_fills=True)
    env = _env(dry_run="0", max_orders="1")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "OLD"

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-rotation-core",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-rotation-core"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_sides == ["SELL", "BUY"]
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert [row["side"] for row in submitted["orders"]] == ["SELL", "BUY"]
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["sell_orders_intended"][0]["symbol"] == "OLD"
    assert transition["buy_orders_intended"][0]["symbol"] == "NEW"
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["required_sell_count"] == 1
    assert capital_gate["post_sell_buy_budget"]["post_sell_cash"] == 250.0
    assert capital_gate["post_sell_buy_budget"]["buy_budget_after_safeguards"] == 250.0


def test_core_routed_live_filled_sell_with_reflected_cash_allows_buy(tmp_path: Path) -> None:
    broker = RotatingFakeBroker(sell_fills=True, settle_cash=True)
    env = _env(dry_run="0", max_orders="1")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "OLD"

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-filled-reflected-settlement",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-filled-reflected-settlement"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_sides == ["SELL", "BUY"]
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert [row["side"] for row in transition["buy_orders_intended"]] == ["BUY"]
    settlement = transition["diagnostics"]["sell_fill_meta"]["settlement_reflection"]
    assert settlement["buying_power_reflected"] is True
    assert settlement["cash_reflected"] is True
    assert settlement["settlement_reflected"] is True
    assert settlement["post_sell_buying_power"] == 250.0
    assert settlement["post_sell_cash"] == 250.0


def test_core_routed_live_filled_sell_with_stale_cash_blocks_buys(tmp_path: Path) -> None:
    broker = RotatingFakeBroker(sell_fills=True, settle_cash=False)
    env = _env(dry_run="0", max_orders="1")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "OLD"
    env["CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS"] = "0"

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-filled-stale-settlement",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-filled-stale-settlement"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_sides == ["SELL"]
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert [row["side"] for row in submitted["orders"]] == ["SELL"]
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["buy_orders_intended"] == []
    assert transition["diagnostics"]["rebudget"]["reason_codes"] == [
        LIVE_PILOT_SELL_SETTLEMENT_CASH_NOT_REFLECTED
    ]
    settlement = transition["diagnostics"]["sell_fill_meta"]["settlement_reflection"]
    assert settlement["buying_power_reflected"] is False
    assert settlement["cash_reflected"] is False
    assert settlement["post_sell_buying_power"] == 150.0
    assert settlement["post_sell_cash"] == 150.0
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["post_sell_buy_budget"]["post_sell_cash"] == 150.0


def test_core_routed_live_settlement_timeout_uses_actual_cash_and_does_not_overbuy(tmp_path: Path) -> None:
    broker = RotatingFakeBroker(sell_fills=False)
    env = _env(dry_run="0", max_orders="1")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "OLD"
    env["CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS"] = "0"

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-settlement-timeout",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-settlement-timeout"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_sides == ["SELL"]
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert [row["side"] for row in submitted["orders"]] == ["SELL"]
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["buy_orders_intended"] == []
    assert transition["diagnostics"]["rebudget"]["reason_codes"] == ["live_pilot_sell_settlement_timeout"]
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["post_sell_buy_budget"]["post_sell_cash"] == 150.0


def test_insufficient_live_buying_power_blocks_even_when_approved_cap_allows_plan(
    tmp_path: Path,
) -> None:
    broker = FakeBroker(buying_power="0.88", cash="0.88", equity="500")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-insufficient-buying-power",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-insufficient-buying-power"
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
    assert broker.submit_calls == 0
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["live_positions_before"] == []
    assert capital_gate["live_buying_power_before"] == 0.88
    assert capital_gate["approved_cap_usd"] == 500.0
    assert capital_gate["planned_buy_notional_usd"] == 150.0
    assert capital_gate["tradable_capital_usd"] == 0.88
    assert capital_gate["buy_block_reason"] == LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"] == []


def test_missing_cap_blocks_before_broker_submission_and_writes_gate_state(tmp_path: Path) -> None:
    broker = FakeBroker()
    env = _env(dry_run="0")
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="run-missing-cap",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-missing-cap"
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "missing_positive_live_pilot_capital_cap"
    assert broker.submit_calls == 0
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "BLOCKED"
    assert gate_state["block_reason"] == "missing_positive_live_pilot_capital_cap"
    assert gate_state["cap_source"] == "missing"
    assert gate_state["configured_cap_usd"] is None
    assert gate_state["effective_cap_usd"] is None
    assert gate_state["broker_orders_submitted"] == 0


def test_kill_switch_blocks_before_broker_submission_and_writes_gate_state(tmp_path: Path) -> None:
    broker = FakeBroker()
    env = _env(dry_run="0")
    env["CAERUS_LIVE_PILOT_KILL_SWITCH"] = "1"

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="run-kill-switch",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-kill-switch"
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_kill_switch_enabled"
    assert broker.submit_calls == 0
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "BLOCKED"
    assert gate_state["block_reason"] == "live_pilot_kill_switch_enabled"
    assert gate_state["kill_switch_set"] is True
    assert gate_state["broker_orders_submitted"] == 0


def test_live_buy_limit_plan_is_submitted_as_market_with_policy_metadata(tmp_path: Path) -> None:
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
    assert broker.submitted_methods == ["market"]
    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    assert intended["orders"][0]["approved_order_type"] == "limit"
    assert intended["orders"][0]["submitted_order_type"] == "market"
    assert intended["orders"][0]["escalation_reason"] == "approved_limit_buy_overridden_to_market"
    assert intended["orders"][0]["sleeve"] == "polaris"
    assert intended["orders"][0]["sleeve_source"] == "precompute_live_strategy_id"
    assert intended["orders"][0]["source_signal_sleeve"] == "sleeve_trend"
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["approved_order_type"] == "limit"
    assert submitted["orders"][0]["submitted_order_type"] == "market"
    assert submitted["orders"][0]["sleeve"] == "polaris"
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["entry_execution_policy"] == "live_pilot_buy_market_order_immediate"
    assert results["submitted_order_type"] == "market"
    assert results["marketable_order_count"] == 1
    assert results["passive_order_count"] == 0


def test_repeated_unfilled_live_buy_attempts_escalate_from_artifacts(tmp_path: Path) -> None:
    output_root = tmp_path / "outputs" / "live_pilot"
    for idx, trade_date in enumerate(["2026-06-20", "2026-06-22", "2026-06-23"], start=1):
        prior_root = output_root / "runs" / f"{trade_date}T093500-0400_prior{idx}"
        prior_root.mkdir(parents=True)
        (prior_root / "live_pilot_operator_summary.json").write_text(
            json.dumps({"run_id": prior_root.name, "generated_at": f"{trade_date}T13:35:00+00:00"}),
            encoding="utf-8",
        )
        (prior_root / "live_pilot_orders_submitted.json").write_text(
            json.dumps(
                {
                    "orders": [
                        {
                            "symbol": "AAPL",
                            "side": "BUY",
                            "status": "OrderStatus.NEW",
                            "submitted_order_type": "limit",
                            "client_order_id": f"prior-{idx}",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
    broker = FakeBroker(order_status="filled")

    result = run_live_pilot(
        plan=_limit_plan(),
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-escalated",
        output_root=output_root,
        now_et=_market_open_now(),
    )

    run_root = output_root / "runs" / "run-escalated"
    assert result["terminal_status"] == "SUBMITTED"
    assert broker.submitted_methods == ["market"]
    history = json.loads((run_root / "live_pilot_entry_attempt_history.json").read_text())
    assert history["orders"][0]["prior_unfilled_attempts"] == 3
    assert history["orders"][0]["escalation_reason"] == "prior_unfilled_attempts_reached_three_session_limit"
    assert history["orders"][0]["prior_unfilled_attempts_detail"][0]["trade_date"] == "2026-06-20"
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["prior_unfilled_attempts"] == 3
    assert results["escalated_buy_count"] == 1
    assert results["unfilled_buy_count"] == 0


def test_over_cap_plan_does_not_submit_and_writes_operator_action(tmp_path: Path) -> None:
    # Fail-closed pilot policy (operator decision 2026-07-06): an intent whose full
    # need ($600) exceeds the approved cap ($500) is HALTED, not silently right-sized.
    # The engine would clip-and-deploy; the live lane blocks instead until live
    # behavior is trusted.
    broker = FakeBroker()

    result = run_live_pilot(
        plan={"trades": [{"ticker": "AAPL", "side": "BUY", "shares": 6, "limit_price": 100}]},
        broker=broker,
        env=_env(dry_run="0"),
        run_id="run-blocked",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-blocked"
    assert result["terminal_status"] == "BLOCKED"
    assert broker.submit_calls == 0
    assert "live_pilot_total_notional_exceeds_cap" in result["reason_code"]
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "BLOCKED"
    assert gate_state["block_reason"] == "live_pilot_total_notional_exceeds_cap"
    assert gate_state["broker_orders_submitted"] == 0
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["decision"] == "BLOCKED"
    assert capital_gate["buy_block_reason"] == "live_pilot_total_notional_exceeds_cap"
    transition_plan = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition_plan["blocked"] is True
    assert transition_plan["buy_orders_intended"] == []
    assert transition_plan["diagnostics"]["over_cap_intent"] is True


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
                        "limit_price": 150,
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
                        "limit_price": 150,
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
