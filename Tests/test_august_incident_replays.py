from __future__ import annotations

import json
from pathlib import Path
import copy
import datetime as dt
import hashlib
import os
import tempfile
from zoneinfo import ZoneInfo

from core.execution_attempt_registry import (
    AttemptRecord,
    IncidentEvent,
    SelectionStatus,
    append_attempt,
    append_incident_event,
    read_attempts,
    select_from_registry,
)
from core.failure_semantics import FailureClass, TerminalOutcome, get_failure_policy
from authority.exact_plan import build_exact_execution_plan
from execution.exact_executor import execute_exact_plan
from Tests.test_exact_execution_choice2 import TEST_NOW_ET


FIXTURES = Path(__file__).parent / "fixtures" / "incidents"
_REPLAY_REGIME_ROOT = Path(tempfile.mkdtemp(prefix="caerus-replay-regime-"))


def _load(day: str) -> dict:
    return json.loads((FIXTURES / day / "replay.json").read_text(encoding="utf-8"))


def test_august_7_replay_preserves_all_distinct_authority_and_execution_sets() -> None:
    replay = _load("2026-08-07")
    precompute = {(symbol, side, qty) for symbol, side, qty in replay["precompute_orders"]}
    alternative = set(replay["historical_alternative_target_symbols"])
    governed = set(replay["governed_target_symbols"])
    submitted = {(symbol, side, qty) for symbol, side, qty in replay["observed_submissions"]}

    assert len(precompute) == 10
    assert alternative == {"FTNT", "GM", "KLAC", "ROST", "GS", "QCOM", "BMY"}
    assert governed == {"BKNG", "ROST", "SPG", "FTNT", "QCOM", "RTX", "AMGN"}
    assert submitted == {("QCOM", "SELL", 1), ("BMY", "SELL", 2)}
    assert alternative != governed
    assert {symbol for symbol, _side, _qty in submitted} != governed
    assert FailureClass(replay["incident_class"]) is FailureClass.PLAN_INTEGRITY_FAILURE
    policy = get_failure_policy(replay["incident_class"])
    assert policy.fail_closed is True
    assert policy.retry_policy.value == "NEVER"


def test_august_12_replay_preserves_failed_attempt_after_clean_recovery(tmp_path) -> None:
    replay = _load("2026-08-12")
    registry = tmp_path / "attempt_registry"

    assert replay["planned_symbols"]["BUY"] == [
        "AMGN", "BDX", "BKNG", "FTNT", "HON", "ROST", "RTX"
    ]
    assert replay["planned_symbols"]["SELL"] == ["INTC", "LRCX", "MU", "STX", "WDC"]

    for raw in replay["attempts"]:
        record = AttemptRecord(
            attempt_id=raw["attempt_id"],
            trade_date=replay["trade_date"],
            run_id=raw["run_id"],
            lane="paper",
            sequence=raw["sequence"],
            terminal_outcome=TerminalOutcome(raw["terminal_outcome"]),
            recorded_at=raw["recorded_at"],
            run_root=raw["run_root"],
            submitted_count=raw["submitted_count"],
            filled_count=raw["filled_count"],
            failure_class=(FailureClass(raw["failure_class"]) if raw["failure_class"] else None),
            reason_code=raw["reason_code"],
            source_artifacts=tuple(raw["source_artifacts"]),
            incident_id="incident-20260812" if raw["failure_class"] else None,
        )
        append_attempt(registry, record)
        if raw["failure_class"]:
            append_incident_event(
                registry,
                IncidentEvent(
                    event_id="initial-detection",
                    incident_id="incident-20260812",
                    trade_date=replay["trade_date"],
                    event_type="DETECTED",
                    recorded_at=raw["recorded_at"],
                    failure_class=FailureClass(raw["failure_class"]),
                    reason_code=raw["reason_code"],
                    attempt_id=raw["attempt_id"],
                    evidence_artifacts=tuple(raw["source_artifacts"]),
                    detail="terminal execution/reconciliation/operator artifacts were absent",
                ),
            )

    selection = select_from_registry(registry, trade_date=replay["trade_date"])
    attempts = read_attempts(registry, trade_date=replay["trade_date"])

    assert selection.status is SelectionStatus.RESOLVED
    assert selection.selected_attempt_id == replay["canonical_pointer"]["selected_attempt_id"]
    assert selection.attempt_count == 2
    assert attempts[0].terminal_outcome is TerminalOutcome.SYSTEM_FAILURE
    assert attempts[0].submitted_count == 0
    assert attempts[0].reason_code == "paper_lane_dry_run_failed"
    assert attempts[1].terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert attempts[1].submitted_count == attempts[1].filled_count == 1

    incident_path = (
        registry
        / "2026-08-12"
        / "incidents"
        / "incident-20260812"
        / "initial-detection.json"
    )
    incident = json.loads(incident_path.read_text(encoding="utf-8"))
    assert incident["failure_class"] == "EXECUTION_FAILURE"
    assert incident["content_hash"]


class _ReplayBroker:
    paper = True
    base_url = "https://paper-api.alpaca.markets"

    def __init__(self, *, positions: dict[str, float], prices: dict[str, float], cash: float):
        self.positions = dict(positions)
        self.prices = dict(prices)
        self.cash = float(cash)
        self.orders: dict[str, dict] = {}
        self.submit_calls = 0

    def get_account(self):
        equity = self.cash + sum(
            quantity * self.prices[symbol]
            for symbol, quantity in self.positions.items()
        )
        return {
            "id": "paper-incident-replay",
            "cash": str(self.cash),
            "equity": str(equity),
            "portfolio_value": str(equity),
            "buying_power": str(self.cash),
            "status": "ACTIVE",
        }

    def get_positions(self):
        return [
            {
                "symbol": symbol,
                "qty": str(quantity),
                "market_value": str(quantity * self.prices[symbol]),
                "current_price": str(self.prices[symbol]),
            }
            for symbol, quantity in sorted(self.positions.items())
            if quantity > 0
        ]

    def get_asset(self, symbol):
        return {"symbol": symbol, "status": "active", "asset_class": "us_equity", "tradable": True}

    def list_orders(self, status="open", **_kwargs):
        return [] if status == "open" else list(self.orders.values())

    def find_order_by_client_id(self, client_id):
        return copy.deepcopy(self.orders.get(client_id))

    def get_order(self, order_id):
        return copy.deepcopy(next(row for row in self.orders.values() if row["id"] == order_id))

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        symbol = str(kwargs["symbol"])
        side = str(kwargs["side"]).upper()
        quantity = float(kwargs["qty"])
        price = self.prices[symbol]
        client_id = str(kwargs["client_order_id"])
        row = {
            "id": f"replay-broker-{self.submit_calls}",
            "client_order_id": client_id,
            "symbol": symbol,
            "side": side,
            "qty": str(quantity),
            "status": "filled",
            "filled_qty": str(quantity),
            "filled_avg_price": str(price),
        }
        self.orders[client_id] = row
        if side == "SELL":
            self.positions[symbol] = self.positions.get(symbol, 0.0) - quantity
            self.cash += quantity * price
        else:
            self.positions[symbol] = self.positions.get(symbol, 0.0) + quantity
            self.cash -= quantity * price
        self.positions = {symbol: qty for symbol, qty in self.positions.items() if qty > 1e-9}
        return copy.deepcopy(row)

    def submit_limit_order(self, **kwargs):
        quantity = float(kwargs["qty"])
        limit_price = float(kwargs["limit_price"])
        return self.submit_market_order(
            symbol=kwargs["symbol"],
            qty=quantity,
            side=kwargs["side"],
            client_order_id=kwargs["client_order_id"],
            tif=kwargs.get("tif", "day"),
            estimated_notional=quantity * limit_price,
        )


def _replay_env() -> dict[str, str]:
    test_identity = os.environ.get("PYTEST_CURRENT_TEST", "incident-replay")
    return {
        "MODE": "paper",
        "TRADING_MODE": "paper",
        "ALPACA_PAPER": "1",
        "ALPACA_BASE_URL": "https://paper-api.alpaca.markets",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "10000",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "50",
        "CAERUS_EXACT_MAX_PLAN_AGE_SECONDS": "999999999",
        "CAERUS_EXACT_FILL_REFRESH_ATTEMPTS": "1",
        "CAERUS_EXACT_FILL_REFRESH_DELAY_SECONDS": "0",
        "CAERUS_EXACT_ACCOUNT_AUTHORITY_ROOT": str(
            Path(tempfile.gettempdir())
            / "caerus-incident-replay-authority"
            / str(os.getpid())
            / hashlib.sha256(test_identity.encode("utf-8")).hexdigest()[:20]
        ),
    }


def _functional_exact_plan(*, replay: dict, broker: _ReplayBroker):
    orders = [
        {"symbol": symbol, "side": side, "quantity": quantity,
         "expected_price": broker.prices[symbol], "notional": quantity * broker.prices[symbol]}
        for symbol, side, quantity in replay["precompute_orders"]
    ] if "precompute_orders" in replay else [
        {"symbol": symbol, "side": side, "quantity": 1,
         "expected_price": broker.prices[symbol], "notional": broker.prices[symbol]}
        for side, symbols in replay["planned_symbols"].items()
        for symbol in symbols
    ]
    sells = [row for row in orders if row["side"] == "SELL"]
    buys = [row for row in orders if row["side"] == "BUY"]
    for row in orders:
        row.update(
            {
                "order_type": "limit",
                "time_in_force": "day",
                "extended_hours": False,
                "limit_price": row["expected_price"],
                "cap_enforcement_price": row["expected_price"],
            }
        )
    expected = dict(broker.positions)
    expected_cash = broker.cash
    for row in [*sells, *buys]:
        if row["side"] == "SELL":
            expected[row["symbol"]] = expected.get(row["symbol"], 0.0) - row["quantity"]
            expected_cash += row["notional"]
        else:
            expected[row["symbol"]] = expected.get(row["symbol"], 0.0) + row["quantity"]
            expected_cash -= row["notional"]
    expected_rows = [
        {"symbol": symbol, "quantity": quantity}
        for symbol, quantity in sorted(expected.items()) if quantity > 1e-9
    ]
    account = broker.get_account()
    account_hash = hashlib.sha256(str(account["id"]).encode("utf-8")).hexdigest()
    from core.regime_state_store import persist_regime_authority

    regime_state = persist_regime_authority(
        _REPLAY_REGIME_ROOT,
        account_scope="PAPER",
        account_id=account_hash,
        sleeve_id="caerus_orion",
        authorization_run_id=f"functional-replay-{replay['trade_date']}",
        trade_date=replay["trade_date"],
        recorded_at=f"{replay['trade_date']}T13:35:01Z",
        observed_state="NORMAL",
        confidence=1.0,
        acute_risk=False,
        risk_package_id=f"risk:fixture:{replay['trade_date']}",
        risk_package_hash=hashlib.sha256(
            f"risk:fixture:{replay['trade_date']}".encode("utf-8")
        ).hexdigest(),
        market_state_id=f"fixture-market:{replay['trade_date']}",
    ).regime_state()
    return build_exact_execution_plan(
        run_id=f"functional-replay-{replay['trade_date']}",
        as_of=f"{replay['trade_date']}T09:35:00-04:00",
        created_at="2026-08-12T13:35:01+00:00",
        orchestrator_version="choice2.incident-replay",
        source_precompute_ids=[f"fixture:{replay['trade_date']}"],
        source_artifact_hashes={"fixture": "a" * 64},
        market_state_id=f"fixture-market:{replay['trade_date']}",
        market_state={"source": "historical_incident_fixture"},
        regime_state=regime_state,
        sleeve_allocations=[{"sleeve_id": "caerus_orion", "capital_eligible": True}],
        portfolio_nav=float(account["portfolio_value"]),
        starting_positions=[
            {"symbol": symbol, "quantity": quantity}
            for symbol, quantity in sorted(broker.positions.items())
        ],
        starting_cash=broker.cash,
        account_id_hash=account_hash,
        risk_state={"status": "PASS"},
        sell_orders=sells,
        buy_orders=buys,
        expected_posttrade_positions=expected_rows,
        expected_posttrade_cash=expected_cash,
        constraints={
            "max_orders": 50,
            "capital_cap_usd": 10000.0,
            "cash_reconciliation_tolerance_usd": 0.01,
            "max_adverse_fill_slippage_bps": 100.0,
            "new_order_execution_style": "protective_day_limit",
        },
        authorization_state={
            "status": "AUTHORIZED",
            "authority": "CAERUS_ORCHESTRATOR",
            "authorized_at": "2026-08-12T13:35:01+00:00",
            "authorization_reason": "FUNCTIONAL_INCIDENT_REPLAY",
        },
    )


def test_august_7_functional_exact_replay_ignores_alternative_target_and_submits_only_sealed_orders(tmp_path):
    replay = _load("2026-08-07")
    prices = {symbol: 10.0 for symbol in {
        *(row[0] for row in replay["precompute_orders"]),
        *replay["historical_alternative_target_symbols"],
    }}
    positions = {symbol: float(quantity) for symbol, side, quantity in replay["precompute_orders"] if side == "SELL"}
    broker = _ReplayBroker(positions=positions, prices=prices, cash=8000.0)
    plan = _functional_exact_plan(replay=replay, broker=broker)
    payload = plan.to_dict()
    alternate_evidence = {
        "alternate_target_symbols": replay["historical_alternative_target_symbols"]
    }
    assert set(alternate_evidence).isdisjoint(payload)

    result = execute_exact_plan(
        plan_payload=payload, broker=broker, env=_replay_env(), wal_root=tmp_path / "wal",
        attempt_id="aug7-functional",
        dry_run=False,
        now_et=dt.datetime(
            2026, 8, 7, 13, 35, tzinfo=ZoneInfo("America/New_York")
        ),
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert {(row["symbol"], row["side"], int(row["quantity"])) for row in result.orders_submitted} == {
        tuple(row) for row in replay["precompute_orders"]
    }
    assert broker.submit_calls == 10


def test_august_12_functional_exact_replay_submits_all_twelve_and_writes_wal(tmp_path):
    replay = _load("2026-08-12")
    all_symbols = set(replay["planned_symbols"]["BUY"] + replay["planned_symbols"]["SELL"])
    prices = {symbol: 10.0 for symbol in all_symbols}
    positions = {symbol: 1.0 for symbol in replay["planned_symbols"]["SELL"]}
    broker = _ReplayBroker(positions=positions, prices=prices, cash=9000.0)
    plan = _functional_exact_plan(replay=replay, broker=broker)

    result = execute_exact_plan(
        plan_payload=plan.to_dict(), broker=broker, env=_replay_env(), wal_root=tmp_path / "wal",
        attempt_id="aug12-functional", dry_run=False, now_et=TEST_NOW_ET,
    )

    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert len(result.orders_submitted) == len(result.orders_filled) == 12
    assert {row["symbol"] for row in result.orders_submitted} == all_symbols
    intents = list((tmp_path / "wal" / "2026-08-12" / "intents").glob("*.json"))
    assert len(intents) == 12
