from __future__ import annotations

import json
import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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


class FailingLiveSnapshotBroker(FakeBroker):
    def __init__(self) -> None:
        super().__init__()
        self.account_calls = 0

    def get_account(self):
        self.account_calls += 1
        raise RuntimeError("504 request timed out")


class GateFlipBroker(FakeBroker):
    """Flip one mutable runtime gate after planning but before adapter submit."""

    def __init__(self, env: dict[str, str], key: str, value: str) -> None:
        super().__init__()
        self.env = env
        self.key = key
        self.value = value

    def get_asset(self, symbol):
        asset = super().get_asset(symbol)
        self.env[self.key] = self.value
        return asset


class PositionShrinkBroker(FakeBroker):
    """Position exists at planning snapshot, then disappears before submission."""

    def __init__(self) -> None:
        super().__init__(
            buying_power="150",
            cash="150",
            equity="500",
            positions=[{"symbol": "OLD", "qty": "1", "market_value": "100"}],
        )
        self.position_reads = 0

    def get_positions(self):
        self.position_reads += 1
        if self.position_reads == 1:
            return super().get_positions()
        return []


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
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED": "1",
        # Master sell gate fails closed: sells stay blocked unless explicitly enabled.
        # Tests that exercise the sell/rotation path opt in here; the default-off
        # behavior is covered by the dedicated tests below.
        "CAERUS_LIVE_PILOT_SELLS_ENABLED": "1",
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
    # Cap is now resolved from the account portfolio value (FakeBroker equity=500),
    # tightened by the optional env cap (also 500). No fixed program ceiling.
    assert gate_state["configured_cap_usd"] == 500.0  # optional env override still recorded
    assert gate_state["approved_max_cap_usd"] is None  # no fixed program ceiling
    assert gate_state["effective_cap_usd"] == 500.0
    assert gate_state["cap_source"] == "portfolio_value_env_capped"
    assert gate_state["portfolio_value_usd"] == 500.0
    assert gate_state["broker_orders_submitted"] == 0

    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "DRY_RUN_NOT_SUBMITTED"
    assert submitted["orders"][0]["sleeve"] == "polaris"
    assert submitted["orders"][0]["sleeve_source"] == "precompute_live_strategy_id"
    assert submitted["orders"][0]["source_signal_sleeve"] == "sleeve_trend"
    history = json.loads((run_root / "live_pilot_entry_attempt_history.json").read_text())
    assert history["orders"][0]["sleeve"] == "polaris"
    assert history["orders"][0]["source_strategy_id"] == "polaris"


def test_live_snapshot_timeout_remains_single_attempt_and_fail_closed(tmp_path: Path) -> None:
    broker = FailingLiveSnapshotBroker()

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=_env(dry_run="1"),
        run_id="run-live-snapshot-timeout",
        output_root=tmp_path / "outputs" / "live_pilot",
    )

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_pre_snapshot_failed"
    assert result["run_root"].endswith("run-live-snapshot-timeout")
    assert broker.account_calls == 1
    assert broker.submit_calls == 0


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


def test_stale_sell_whitelist_does_not_block_held_exits_in_dry_run(
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
        env=_env(dry_run="1"),
        run_id="run-existing-positions",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-existing-positions"
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0
    assert broker.market_calls == 0
    assert broker.limit_calls == 0
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "ALLOWED"
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

    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    held_exits = {(row["symbol"], row["side"]) for row in intended["orders"]}
    assert ("ABBV", "SELL") in held_exits
    assert ("C", "SELL") not in held_exits  # sub-$100 dust is clipped by the core
    assert intended["dropped_orders"] == []
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "DRY_RUN_NO_SUBMISSION"
    results = json.loads((run_root / "execution_results.json").read_text())
    assert results["status"] == "DRY_RUN"
    assert [row["status"] for row in results["broker_responses"]] == ["DRY_RUN_NOT_SUBMITTED"]


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
    # Settled-cash guard (Blocker #2) + freshness cross-check: this run's confirmed
    # $100 sell fill is MISSING from the broker's bulk order history (FakeBroker's
    # list_orders returns only open orders), so its proceeds are injected as
    # unsettled. Budget = (250 cash - 100 unsettled proceeds) * 0.98 buffer = 147.
    # This is also economically correct: same-day sale proceeds ARE unsettled T+1
    # funds and must not fund buys.
    assert capital_gate["post_sell_buy_budget"]["buy_budget_after_safeguards"] == (250.0 - 100.0) * 0.98
    assert capital_gate["post_sell_buy_budget"]["settled_cash_guard"]["buy_buffer_pct"] == 0.98
    assert capital_gate["post_sell_buy_budget"]["settled_cash_guard"]["settled_cash"] == 150.0


def test_sell_inventory_is_rechecked_immediately_before_fake_submission(tmp_path: Path) -> None:
    broker = PositionShrinkBroker()
    env = _env(dry_run="0", max_orders="1")

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-position-shrank",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-position-shrank"
    assert broker.submit_calls == 0
    assert broker.market_calls == 0
    assert broker.limit_calls == 0
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert [row["status"] for row in submitted["orders"]] == ["REJECTED"]
    assert "live_pilot_sell_inventory_changed:OLD" in submitted["orders"][0]["error"]
    assert result["terminal_status"] != "CLEAN"


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
    # WARNING §f fix: the sell stays "accepted" (zero fill) at snapshot time — that
    # is a live/open broker order, not a completed submission. CLEAN now requires
    # actual fills, so this is SUBMITTED_UNFILLED (not a failure, not CLEAN).
    assert result["terminal_status"] == "SUBMITTED_UNFILLED"
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
    # Settled-cash guard (Blocker #2): available-for-buys is trimmed by the 0.98
    # slippage buffer ($0.88 * 0.98). The buy ($150) is still blocked as insufficient.
    assert capital_gate["tradable_capital_usd"] == 0.88 * 0.98
    assert capital_gate["buy_block_reason"] == LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"] == []


def test_missing_env_cap_resolves_from_account_portfolio_value(tmp_path: Path) -> None:
    # No env cap: the cap resolves dynamically from the account portfolio value
    # (FakeBroker equity/portfolio_value = 500), so a missing env cap no longer blocks.
    broker = FakeBroker()
    env = _env(dry_run="0")
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="run-no-env-cap",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-no-env-cap"
    assert result["terminal_status"] != "BLOCKED"
    gate_state = _gate_state(run_root)
    assert gate_state["decision"] == "ALLOWED"
    assert gate_state["configured_cap_usd"] is None  # no env override present
    assert gate_state["effective_cap_usd"] == 500.0  # from portfolio value
    assert gate_state["cap_source"] == "portfolio_value"
    assert gate_state["portfolio_value_usd"] == 500.0


def test_unresolvable_cap_blocks_when_no_account_value_and_no_env_cap(tmp_path: Path) -> None:
    # Fail-closed: account reports no portfolio value/equity AND no env override ->
    # the cap cannot be resolved, so the run blocks before any broker submission.
    broker = FakeBroker(equity="0")
    env = _env(dry_run="0")
    env.pop("CAERUS_LIVE_PILOT_CAPITAL_CAP")

    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id="run-cap-unresolved",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-cap-unresolved"
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_capital_cap_unresolved"
    assert broker.submit_calls == 0


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


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("CAERUS_LIVE_PILOT_KILL_SWITCH", "1", "live_pilot_kill_switch_enabled"),
        ("CAERUS_LIVE_PILOT_SUBMIT_APPROVED", "0", "live_pilot_submit_not_approved"),
    ],
)
def test_runtime_gates_are_rechecked_immediately_before_submission(
    tmp_path: Path, key: str, value: str, reason: str
) -> None:
    env = _env(dry_run="0")
    broker = GateFlipBroker(env, key, value)
    result = run_live_pilot(
        plan=_plan(),
        broker=broker,
        env=env,
        run_id=f"run-last-mile-{key.lower()}",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )
    assert broker.submit_calls == 0
    assert result["terminal_status"] == "FAILED_RECONCILIATION"
    run_root = Path(str(result["run_root"]))
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"]
    assert all(row["status"] == "REJECTED" for row in submitted["orders"])
    assert reason in json.dumps(submitted)
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reason in json.dumps(reconciliation)


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


def test_accepted_open_order_produces_submitted_unfilled_reconciliation(tmp_path: Path) -> None:
    # WARNING §f fix (PRE_ARM_SWEEP_2026-07-13): this used to report CLEAN with
    # ZERO fills (the order merely sits accepted/new at the broker). CLEAN now
    # requires filled_count == submitted_count; a fully-submitted-but-unfilled
    # batch gets its own non-failure status instead: state=OPEN /
    # status=SUBMITTED_UNFILLED ("orders live at broker, not yet filled").
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
    assert result["terminal_status"] == "SUBMITTED_UNFILLED"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "SUBMITTED_UNFILLED"
    assert reconciliation["state"] == "OPEN"
    assert reconciliation["accepted_count"] == 1
    assert reconciliation["open_count"] == 1
    assert reconciliation["filled_count"] == 0
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



def test_refresh_existing_run_reconciles_open_broker_order_as_submitted_unfilled(tmp_path: Path) -> None:
    # WARNING §f fix: a refreshed order still open at the broker (zero fill) is
    # SUBMITTED_UNFILLED, not CLEAN — CLEAN now requires actual fills.
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

    assert result["terminal_status"] == "SUBMITTED_UNFILLED"
    submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
    assert submitted["orders"][0]["status"] == "OrderStatus.NEW"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "SUBMITTED_UNFILLED"
    assert reconciliation["state"] == "OPEN"
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


def test_snapshot_time_partial_fill_produces_submitted_unfilled_not_failed(tmp_path: Path) -> None:
    # WARNING §f fix: a `partially_filled` order observed at snapshot time is
    # NON-TERMINAL — the order is still open at the broker and may fill
    # further. It must not be a false FAILED_RECONCILIATION; it belongs to the
    # same OPEN/SUBMITTED_UNFILLED family as an unfilled accepted/new order.
    # Only a TERMINAL partial (order done/canceled after partially filling,
    # which buckets as "rejected" via its canceled/expired broker status) stays
    # FAILED_RECONCILIATION.
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
    assert result["terminal_status"] == "SUBMITTED_UNFILLED"
    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "SUBMITTED_UNFILLED"
    assert reconciliation["state"] == "OPEN"
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


# --------------------------------------------------------------------------- #
# BLOCKER 3 (PRE_ARM_SWEEP_2026-07-13 §d) — per-order validation partitioning.
# A single bad/unresolvable order must never block the rest of the batch, and
# a buy-side problem must never block the sells that free capital.
# --------------------------------------------------------------------------- #


def _nine_order_rotation_plan() -> dict[str, object]:
    # 7 BUY targets; the 2 currently-held positions below (OLD1/OLD2) are absent
    # from this target book, so the transition engine proposes SELLing both.
    return {
        "target_portfolio": [
            {
                "symbol": symbol,
                "ticker": symbol,
                "side": "BUY",
                "target_weight": 0.05,
                "price": 50.0,
                "sleeve": "polaris",
            }
            for symbol in ("XOM", "MNST", "FTNT", "GM", "UNP", "PNC", "CVS")
        ]
    }


class AssetAwareBroker(FakeBroker):
    """FakeBroker whose get_asset() flags a single configured symbol as bad."""

    def __init__(self, *, bad_asset_symbol: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.bad_asset_symbol = bad_asset_symbol

    def get_asset(self, symbol):
        if symbol == self.bad_asset_symbol:
            return {
                "symbol": symbol,
                "status": "inactive",
                "asset_class": "us_equity",
                "tradable": False,
            }
        return super().get_asset(symbol)


def test_bad_buy_symbol_dropped_sells_and_good_buys_still_proceed(tmp_path: Path) -> None:
    """2 sells + 7 buys, one buy symbol fails asset validation.

    The bad buy is dropped (own reason_code); the 2 sells and 6 good buys still
    plan/submit-intend, and reconcile treats intended as 8 (not 9) — the
    dropped order never counts toward `intended`.
    """
    broker = AssetAwareBroker(
        bad_asset_symbol="FTNT",
        buying_power="10000",
        cash="10000",
        equity="10000",
        positions=[
            {"symbol": "ALLX", "qty": "1", "market_value": "100", "cost_basis": "100"},
            {"symbol": "COTY", "qty": "1", "market_value": "100", "cost_basis": "100"},
        ],
    )
    env = _env(dry_run="1", max_orders="10")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "*"
    # Large enough cap that all 7 buy targets ($500 notional each) fit — the
    # base fixture's $500 cap would otherwise clip the buy book down to a
    # single name on capital grounds, unrelated to what this test exercises.
    env["CAERUS_LIVE_PILOT_CAPITAL_CAP"] = "100000"

    result = run_live_pilot(
        plan=_nine_order_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-partition-bad-buy",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-partition-bad-buy"
    # The batch was NOT blocked: it reached the DRY_RUN terminal state with the
    # 8 surviving orders (this fixture never reaches real broker submission,
    # so DRY_RUN is the correct proceed-state to assert against).
    assert result["terminal_status"] == "DRY_RUN"
    assert result["dropped_orders_count"] == 1
    assert result["dropped_sell_orders_count"] == 0

    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    dropped = intended["dropped_orders"]
    assert len(dropped) == 1
    assert dropped[0]["symbol"] == "FTNT"
    assert dropped[0]["side"] == "BUY"
    assert dropped[0]["severity"] == "warning"
    assert dropped[0]["stage"] == "asset_validation"
    assert "asset_not_tradable" in dropped[0]["reason_code"]

    surviving_symbols = {row["symbol"] for row in intended["orders"]}
    assert surviving_symbols == {"ALLX", "COTY", "XOM", "MNST", "GM", "UNP", "PNC", "CVS"}
    surviving_sides = [row["side"] for row in intended["orders"]]
    assert surviving_sides.count("SELL") == 2
    assert surviving_sides.count("BUY") == 6

    reconciliation = json.loads((run_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "DRY_RUN_NO_SUBMISSION"
    # The dropped order never counts toward `intended` — reconcile sees 8, not 9.
    assert reconciliation["intended_count"] == 8
    assert reconciliation["submitted_count"] == 8


def test_stale_sell_whitelist_cannot_drop_held_position_exit(tmp_path: Path) -> None:
    """Broker inventory authorizes both held exits; the static list is stale."""
    broker = FakeBroker(
        buying_power="10000",
        cash="10000",
        equity="10000",
        positions=[
            {"symbol": "ALLX", "qty": "1", "market_value": "100", "cost_basis": "100"},
            {"symbol": "COTY", "qty": "1", "market_value": "100", "cost_basis": "100"},
        ],
    )
    env = _env(dry_run="1", max_orders="10")
    # Only COTY is present in the stale static list. Both broker-held exits must
    # survive validation, while this dry run proves no broker submission occurs.
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "COTY"
    env["CAERUS_LIVE_PILOT_CAPITAL_CAP"] = "100000"

    result = run_live_pilot(
        plan=_nine_order_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-partition-bad-sell",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-partition-bad-sell"
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0
    assert result["dropped_orders_count"] == 0
    assert result["dropped_sell_orders_count"] == 0

    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    assert intended["dropped_orders"] == []

    surviving = {(row["symbol"], row["side"]) for row in intended["orders"]}
    assert ("ALLX", "SELL") in surviving
    assert ("COTY", "SELL") in surviving
    assert sum(1 for row in intended["orders"] if row["side"] == "BUY") == 7


def test_all_orders_invalid_blocks_the_run(tmp_path: Path) -> None:
    """If every candidate order is invalid, the run is BLOCKED (fail closed on
    total failure), same as before the partitioning fix."""
    broker = FakeBroker(
        buying_power="10000",
        cash="10000",
        equity="10000",
        positions=[
            {"symbol": "ALLX", "qty": "1", "market_value": "100", "cost_basis": "100"},
        ],
    )
    env = _env(dry_run="1", max_orders="10")
    # Sells master-disabled and no buy targets at all -> nothing survives.
    env["CAERUS_LIVE_PILOT_SELLS_ENABLED"] = "0"

    result = run_live_pilot(
        plan={"target_portfolio": []},
        broker=broker,
        env=env,
        run_id="run-partition-all-invalid",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    assert result["terminal_status"] == "BLOCKED"
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


# --------------------------------------------------------------------------- #
# Master sell gate (CAERUS_LIVE_PILOT_SELLS_ENABLED), fail-closed.
# A sell may only proceed when the master flag is explicitly on AND immutable
# broker inventory proves the position is held. Static whitelist configuration
# cannot arm live selling.
# --------------------------------------------------------------------------- #
def _sell_trade(symbol: str = "ABBV", qty: float = 1.0, price: float = 150.0) -> dict[str, object]:
    return {"symbol": symbol, "side": "SELL", "qty": qty, "limit_price": price, "order_type": "market"}


def _validate_sell(*, sell_inventory: dict[str, object] | None = None, **env_overrides):
    from core.live_pilot_guardrails import validate_live_pilot_plan

    env = _env(dry_run="1")
    env.pop("CAERUS_LIVE_PILOT_SELLS_ENABLED", None)  # start from the true production default (off)
    env.update(env_overrides)
    inventory = {"ABBV": 1.0} if sell_inventory is None else sell_inventory
    return validate_live_pilot_plan(
        [_sell_trade()],
        env=env,
        capital_cap_usd=500.0,
        max_orders=1,
        run_id="t-sellgate",
        sell_inventory=inventory,
    )


def test_master_sell_gate_default_off_blocks_even_when_held() -> None:
    v = _validate_sell()  # flag unset -> fail-closed
    assert v.status == "BLOCKED"
    assert any("sells_disabled" in r for r in v.reason_codes)
    assert not any("sell_position_not_held" in r for r in v.reason_codes)


def test_master_sell_gate_explicit_off_blocks() -> None:
    v = _validate_sell(CAERUS_LIVE_PILOT_SELLS_ENABLED="0", CAERUS_LIVE_PILOT_SELL_WHITELIST="ABBV")
    assert v.status == "BLOCKED"
    assert any("sells_disabled" in r for r in v.reason_codes)


def test_master_sell_gate_on_and_held_passes_without_static_whitelist() -> None:
    v = _validate_sell(CAERUS_LIVE_PILOT_SELLS_ENABLED="1")
    assert v.status == "PASS"
    assert [o.side for o in v.orders] == ["SELL"]


def test_master_sell_gate_on_but_unheld_blocks_even_when_whitelisted() -> None:
    v = _validate_sell(
        sell_inventory={},
        CAERUS_LIVE_PILOT_SELLS_ENABLED="1",
        CAERUS_LIVE_PILOT_SELL_WHITELIST="ABBV",
    )
    assert v.status == "BLOCKED"
    assert any("sell_position_not_held" in r for r in v.reason_codes)


def test_sell_wildcard_cannot_authorize_unheld_symbol() -> None:
    v = _validate_sell(
        sell_inventory={},
        CAERUS_LIVE_PILOT_SELLS_ENABLED="1",
        CAERUS_LIVE_PILOT_SELL_WHITELIST="*",
    )
    assert v.status == "BLOCKED"
    assert any("sell_position_not_held" in r for r in v.reason_codes)


def test_sell_wildcard_does_not_bypass_master_gate() -> None:
    # Legacy wildcard config must not arm sells on its own: the fail-closed master
    # flag (unset here) still blocks before broker inventory is consulted.
    v = _validate_sell(CAERUS_LIVE_PILOT_SELL_WHITELIST="*")  # SELLS_ENABLED popped -> off
    assert v.status == "BLOCKED"
    assert any("sells_disabled" in r for r in v.reason_codes)
    assert not any("sell_position_not_held" in r for r in v.reason_codes)


# --------------------------------------------------------------------------- #
# Sell-settlement wait: bounded exponential backoff (2026-07-09/10 hardening)
# and halt-state convergence regressions. The two real incidents both halted
# mid-rebalance in the sell-first coupling; these tests pin down (1) the
# retry/backoff contract of the settlement wait and (2) that BOTH real post-halt
# broker states produce a valid plan and pass every gate on the NEXT run.
# --------------------------------------------------------------------------- #


class SellScriptedBroker(FakeBroker):
    """Multi-position broker whose sell outcomes are scripted per symbol.

    ``sell_behavior[symbol]`` is one of:
      - "filled": fills immediately and settles cash/positions.
      - "rejected": broker returns a terminal rejected order (pre-book reject).
      - int N >= 1: order stays "accepted" for N get_order refreshes, then
        fills and settles (exercises the settlement retry/backoff).
    Buys always fill.
    """

    def __init__(
        self,
        *,
        positions: list[dict[str, object]],
        cash: str = "150",
        equity: str = "500",
        sell_behavior: dict[str, object] | None = None,
    ) -> None:
        super().__init__(
            order_status="filled",
            buying_power=cash,
            cash=cash,
            equity=equity,
            positions=positions,
        )
        self.sell_behavior: dict[str, object] = dict(sell_behavior or {})
        self.orders_by_id: dict[str, dict[str, object]] = {}
        self.refresh_counts: dict[str, int] = {}
        self._pending_settlement: dict[str, dict[str, float]] = {}

    def _settle_sell(self, symbol: str, qty: float, notional: float) -> None:
        self.cash = str(float(self.cash) + notional)
        self.buying_power = str(float(self.buying_power) + notional)
        self.positions = [row for row in self.positions if row.get("symbol") != symbol]

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
        behavior = self.sell_behavior.get(symbol, "filled") if side == "SELL" else "filled"
        if behavior == "rejected":
            status = "rejected"
        elif isinstance(behavior, int):
            status = "accepted"
        else:
            status = "filled"
        filled = status == "filled"
        fill_price = (estimated / qty) if qty > 0 else None
        order = {
            "id": order_id,
            "status": status,
            "symbol": symbol,
            "side": side,
            "client_order_id": kwargs.get("client_order_id"),
            "filled_qty": str(qty) if filled else None,
            "filled_quantity": str(qty) if filled else None,
            "filled_avg_price": str(fill_price) if filled and fill_price else None,
            "submitted_at": "2026-03-17T13:35:00+00:00",
            "filled_at": "2026-03-17T13:35:02+00:00" if filled else None,
        }
        self.orders_by_id[order_id] = order
        if side == "SELL":
            if filled:
                self._settle_sell(symbol, qty, estimated)
            elif isinstance(behavior, int):
                self._pending_settlement[order_id] = {"qty": qty, "notional": estimated}
        elif side == "BUY" and filled:
            self.cash = str(float(self.cash) - estimated)
            self.buying_power = str(float(self.buying_power) - estimated)
            self.positions.append({"symbol": symbol, "qty": str(qty), "market_value": str(estimated)})
        return order

    def get_order(self, order_id):
        order = dict(self.orders_by_id.get(order_id, {}))
        if not order:
            return order
        symbol = str(order.get("symbol") or "")
        behavior = self.sell_behavior.get(symbol)
        if order.get("status") == "accepted" and isinstance(behavior, int):
            count = self.refresh_counts.get(order_id, 0) + 1
            self.refresh_counts[order_id] = count
            if count >= behavior:
                pending = self._pending_settlement.pop(order_id, None)
                qty = float((pending or {}).get("qty") or 0.0)
                notional = float((pending or {}).get("notional") or 0.0)
                fill_price = (notional / qty) if qty > 0 else None
                order.update(
                    {
                        "status": "filled",
                        "filled_qty": str(qty),
                        "filled_quantity": str(qty),
                        "filled_avg_price": str(fill_price) if fill_price else None,
                        "filled_at": "2026-03-17T13:35:05+00:00",
                    }
                )
                self.orders_by_id[order_id] = dict(order)
                self._settle_sell(symbol, qty, notional)
        return order


def _rotation_env(**overrides: str) -> dict[str, str]:
    env = _env(dry_run="0", max_orders="1")
    env["CAERUS_LIVE_PILOT_SELL_WHITELIST"] = "*"
    # Hermetic backoff: retries allowed, zero sleep.
    env["CAERUS_LIVE_PILOT_SETTLEMENT_BASE_DELAY_SECONDS"] = "0"
    env.update(overrides)
    return env


def test_settlement_wait_retries_with_backoff_until_settled(tmp_path: Path) -> None:
    """A sell that settles on the 3rd broker refresh is retried (bounded backoff)
    and the run completes the full sell->settle->rebudget->buy lifecycle."""
    broker = SellScriptedBroker(
        positions=[{"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}],
        sell_behavior={"OLD": 3},
    )
    env = _rotation_env(CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS="5")

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-settlement-retry",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-settlement-retry"
    # WARNING §f fix: reconciliation is built from the ORIGINAL submit-time
    # order rows, whose SELL leg was captured "accepted" before the settlement
    # retry loop confirmed the fill (the confirmed fill is only recorded in
    # sell_fill_meta below, a separate diagnostic — a pre-existing gap this fix
    # does not paper over). Previously that stale "accepted" status still
    # reported CLEAN (the exact zero-fill-CLEAN bug this fix closes); it now
    # correctly reads SUBMITTED_UNFILLED instead of falsely claiming CLEAN.
    assert result["terminal_status"] == "SUBMITTED_UNFILLED"
    assert broker.submitted_sides == ["SELL", "BUY"]
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    wait_meta = transition["diagnostics"]["sell_fill_meta"]["settlement_wait"]
    assert wait_meta["backoff"] == "bounded_exponential"
    assert wait_meta["attempts_used"] == 3
    assert wait_meta["max_attempts"] == 5
    assert wait_meta["exhausted_reason"] is None
    settlement = transition["diagnostics"]["sell_fill_meta"]["settlement_reflection"]
    assert settlement["settlement_reflected"] is True


def test_settlement_wait_exhausts_attempts_and_fails_closed(tmp_path: Path) -> None:
    """A sell that never settles exhausts the bounded attempts and the buy phase
    stays fail-closed (no buys sized from unconfirmed proceeds)."""
    broker = SellScriptedBroker(
        positions=[{"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}],
        sell_behavior={"OLD": 99},  # never settles within the attempt budget
    )
    env = _rotation_env(CAERUS_LIVE_PILOT_SETTLEMENT_MAX_ATTEMPTS="3")

    result = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-settlement-exhausted",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-settlement-exhausted"
    assert broker.submitted_sides == ["SELL"]  # no buy submitted
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["buy_orders_intended"] == []
    assert transition["diagnostics"]["rebudget"]["reason_codes"] == ["live_pilot_sell_settlement_timeout"]
    wait_meta = transition["diagnostics"]["sell_fill_meta"]["settlement_wait"]
    assert wait_meta["attempts_used"] == 3
    assert wait_meta["exhausted_reason"] == "max_attempts_exhausted"
    # WARNING §f fix: the sell stayed "accepted" (zero fill) at submission time —
    # that is a live/open broker order, not a completed submission. CLEAN now
    # requires actual fills, so this is SUBMITTED_UNFILLED (previously this
    # falsely reported CLEAN/SUBMITTED with zero fills — the exact bug closed
    # by this fix).
    assert result["terminal_status"] == "SUBMITTED_UNFILLED"


def test_settlement_timeout_zero_preserves_single_pass_fail_fast(tmp_path: Path) -> None:
    """Legacy contract: TIMEOUT=0 means one refresh pass, no retries, fail closed."""
    broker = SellScriptedBroker(
        positions=[{"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}],
        sell_behavior={"OLD": 2},
    )
    env = _rotation_env(CAERUS_LIVE_PILOT_SETTLEMENT_TIMEOUT_SECONDS="0")

    run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=env,
        run_id="run-settlement-timeout-zero",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )

    run_root = tmp_path / "outputs" / "live_pilot" / "runs" / "run-settlement-timeout-zero"
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    wait_meta = transition["diagnostics"]["sell_fill_meta"]["settlement_wait"]
    assert wait_meta["attempts_used"] == 1
    assert wait_meta["exhausted_reason"] == "timeout_exhausted"
    assert wait_meta["delays_slept_seconds"] == []
    assert transition["buy_orders_intended"] == []


def test_halt_state_a_rejected_sells_converge_on_next_run(tmp_path: Path) -> None:
    """2026-07-09 incident shape: every sell rejected before reaching the broker
    book. The halted run must (1) fail closed with next_run_expectation =
    converges_next_run, and (2) the NEXT run over the unchanged broker state must
    produce a valid plan, pass all gates, and complete the rebalance."""
    output_root = tmp_path / "outputs" / "live_pilot"
    broker = SellScriptedBroker(
        positions=[{"symbol": "OLD", "qty": "1", "market_value": "100", "cost_basis": "100"}],
        sell_behavior={"OLD": "rejected"},
    )

    halted = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=_rotation_env(),
        run_id="run-halt-a",
        output_root=output_root,
        now_et=_market_open_now(),
    )

    assert halted["terminal_status"] == "FAILED_RECONCILIATION"
    assert halted["next_run_expectation"] == "converges_next_run"
    assert halted["next_run_expectation_reason"]
    assert broker.submitted_sides == ["SELL"]  # buy phase stayed closed
    # Post-halt broker state: positions and cash unchanged, no open orders.
    assert [row["symbol"] for row in broker.get_positions()] == ["OLD"]
    assert float(broker.cash) == 150.0
    summary = json.loads((output_root / "runs" / "run-halt-a" / "live_pilot_operator_summary.json").read_text())
    assert summary["next_run_expectation"] == "converges_next_run"

    # Next cycle: same broker (sell no longer rejected), same artifact root.
    broker.sell_behavior["OLD"] = "filled"
    recovered = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=_rotation_env(),
        run_id="run-halt-a-next",
        output_root=output_root,
        now_et=_market_open_now(),
    )

    next_root = output_root / "runs" / "run-halt-a-next"
    assert recovered["terminal_status"] == "SUBMITTED"
    open_check = json.loads((next_root / "live_pilot_open_order_check.json").read_text())
    assert open_check["status"] == "PASS"
    gate_state = json.loads((next_root / "live_pilot_gate_state.json").read_text())
    assert gate_state["decision"] == "ALLOWED"
    transition = json.loads((next_root / "live_pilot_transition_plan.json").read_text())
    assert transition["blocked"] is False
    assert transition["sell_orders_intended"][0]["symbol"] == "OLD"
    assert transition["buy_orders_intended"][0]["symbol"] == "NEW"
    assert broker.submitted_sides[-2:] == ["SELL", "BUY"]
    reconciliation = json.loads((next_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"


def test_halt_state_b_one_sell_filled_then_halt_converges_on_next_run(tmp_path: Path) -> None:
    """2026-07-10 incident shape: one sell filled, then the run halted, leaving
    idle cash plus remaining positions. The NEXT run must replan from the
    mutated broker state, pass all gates, and finish the rotation."""
    output_root = tmp_path / "outputs" / "live_pilot"
    broker = SellScriptedBroker(
        positions=[
            {"symbol": "OLDA", "qty": "1", "market_value": "100", "cost_basis": "100"},
            {"symbol": "OLDB", "qty": "1", "market_value": "100", "cost_basis": "100"},
        ],
        sell_behavior={"OLDA": "filled", "OLDB": "rejected"},
    )

    halted = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=_rotation_env(),
        run_id="run-halt-b",
        output_root=output_root,
        now_et=_market_open_now(),
    )

    assert halted["terminal_status"] == "FAILED_RECONCILIATION"
    assert halted["next_run_expectation"] == "converges_next_run"
    # Post-halt broker state matches the incident: one sell's proceeds idle in
    # cash, remaining position still held, no open orders, no buys submitted.
    assert [row["symbol"] for row in broker.get_positions()] == ["OLDB"]
    assert float(broker.cash) == 250.0
    assert "BUY" not in broker.submitted_sides

    # Next cycle: remaining sell now succeeds; plan recomputed from broker truth.
    broker.sell_behavior["OLDB"] = "filled"
    recovered = run_live_pilot(
        plan=_rotation_plan(),
        broker=broker,
        env=_rotation_env(),
        run_id="run-halt-b-next",
        output_root=output_root,
        now_et=_market_open_now(),
    )

    next_root = output_root / "runs" / "run-halt-b-next"
    assert recovered["terminal_status"] == "SUBMITTED"
    open_check = json.loads((next_root / "live_pilot_open_order_check.json").read_text())
    assert open_check["status"] == "PASS"
    gate_state = json.loads((next_root / "live_pilot_gate_state.json").read_text())
    assert gate_state["decision"] == "ALLOWED"
    transition = json.loads((next_root / "live_pilot_transition_plan.json").read_text())
    assert transition["blocked"] is False
    assert transition["sell_orders_intended"][0]["symbol"] == "OLDB"
    assert transition["buy_orders_intended"][0]["symbol"] == "NEW"
    reconciliation = json.loads((next_root / "live_pilot_reconciliation.json").read_text())
    assert reconciliation["status"] == "CLEAN"
    assert recovered["next_run_expectation"] == "converges_next_run"


def test_blocked_halt_paths_write_next_run_expectation(tmp_path: Path) -> None:
    """Every halt path writes the machine-readable convergence contract:
    operator-latched blocks require manual action; transient blocks converge."""
    # Kill switch: stays blocked until an operator disarms it.
    env = _env(dry_run="0")
    env["CAERUS_LIVE_PILOT_KILL_SWITCH"] = "1"
    blocked = run_live_pilot(
        plan=_plan(),
        broker=FakeBroker(),
        env=env,
        run_id="run-nre-kill-switch",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )
    assert blocked["terminal_status"] == "BLOCKED"
    assert blocked["next_run_expectation"] == "requires_manual_action"
    assert blocked["next_run_expectation_reason"]

    # Market closed: converges on the next in-hours scheduled run.
    closed = run_live_pilot(
        plan=_plan(),
        broker=FakeBroker(),
        env=_env(dry_run="0"),
        run_id="run-nre-market-closed",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=dt.datetime(2026, 3, 17, 8, 0, tzinfo=ET),
    )
    assert closed["terminal_status"] == "BLOCKED"
    assert closed["next_run_expectation"] == "converges_next_run"

    # Snapshot-time partial fill: broker truth not terminal -> not a failure
    # (WARNING §f fix), but still requires manual confirmation since the order
    # remains open at the broker and blocks a duplicate next submission.
    partial = run_live_pilot(
        plan=_plan(),
        broker=FakeBroker(order_status="partially_filled"),
        env=_env(dry_run="0"),
        run_id="run-nre-partial",
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open_now(),
    )
    assert partial["terminal_status"] == "SUBMITTED_UNFILLED"
    assert partial["next_run_expectation"] == "requires_manual_action"
