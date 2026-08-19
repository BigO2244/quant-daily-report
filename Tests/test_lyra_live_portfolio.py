from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

import core.lyra_live_portfolio as subject
from brokers.alpaca_broker import AlpacaBroker, _LYRA_LIVE_PORTFOLIO_CAPABILITY
from core.lyra_live_execution import _mutation_context, execute_portfolio_plan
from scripts.manage_lyra_live_cron import INIT_LINE, WEEKLY_LINE, render


ROOT = Path(__file__).resolve().parents[1]
OWNER = json.loads(
    (ROOT / "docs/governance/decision_records/lyra_live_owner_decision_20260819.json").read_text()
)


def _target(signal: str = "2026-08-24") -> bytes:
    return json.dumps({
        "strategy_name": "Caerus Lyra", "strategy_slug": "caerus_lyra",
        "source_variant": "h1_weekly_h6_top5", "trade_date": signal,
        "effective_trade_date": signal,
        "target_weights": {"AAA": .2, "BBB": .2, "CCC": .2, "DDD": .2, "EEE": .2},
    }, sort_keys=True).encode()


def _args(**changes):
    raw = _target()
    base = dict(
        owner_decision=OWNER, raw_target_source=raw, mode="recurring",
        execution_session="2026-08-25", planned_at="2026-08-25T09:35:00-04:00",
        account_id_hash="a" * 64, equity_usd=460.90, cash_usd=460.90,
        buying_power_usd=460.90, positions=[], open_orders=[],
        assets={symbol: {"status": "active", "tradable": True, "fractionable": True}
                for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        latest_prices={symbol: 100.0 for symbol in ("AAA", "BBB", "CCC", "DDD", "EEE")},
        deployed_sha="b" * 40,
    )
    base.update(changes)
    return base


def test_owner_decision_is_exact_and_hashed():
    assert subject.validate_owner_decision(OWNER)["content_hash"] == OWNER["content_hash"]


def test_recurring_plan_uses_actual_nav_full_fractional_basket():
    plan = subject.build_portfolio_plan(**_args())
    assert plan["status"] == "READY"
    assert len(plan["orders"]) == 5
    assert all(order["side"] == "BUY" and order["quantity"] is None for order in plan["orders"])
    assert [order["notional"] for order in plan["orders"]] == [87.57] * 5
    assert plan["maximum_gross_usd"] == pytest.approx(437.855)
    assert plan["required_cash_reserve_usd"] == pytest.approx(23.045)
    assert plan["total_buy_notional_usd"] == pytest.approx(437.85)


def test_nav_compounds_and_declines_without_nominal_cap():
    larger = subject.build_portfolio_plan(**_args(equity_usd=1000, cash_usd=1000, buying_power_usd=1000))
    smaller = subject.build_portfolio_plan(**_args(equity_usd=300, cash_usd=300, buying_power_usd=300))
    assert larger["total_buy_notional_usd"] == pytest.approx(950)
    assert smaller["total_buy_notional_usd"] == pytest.approx(285)


def test_plan_fails_closed_on_open_order_nonfractionable_or_leverage():
    with pytest.raises(subject.LyraLivePortfolioError, match="open orders"):
        subject.build_portfolio_plan(**_args(open_orders=[{"id": "existing"}]))
    assets = _args()["assets"]
    assets["AAA"] = {"status": "active", "tradable": True, "fractionable": False}
    with pytest.raises(subject.LyraLivePortfolioError, match="fractionable"):
        subject.build_portfolio_plan(**_args(assets=assets))
    with pytest.raises(subject.LyraLivePortfolioError, match="leverage"):
        subject.build_portfolio_plan(**_args(buying_power_usd=921.8))


def test_recurring_rebalance_sells_before_buying_and_stays_long_only():
    plan = subject.build_portfolio_plan(**_args(
        cash_usd=23.045, buying_power_usd=460.90,
        positions=[{"symbol": "ZZZ", "qty": 4.37855}],
        latest_prices={**_args()["latest_prices"], "ZZZ": 100.0},
    ))
    assert plan["orders"][0]["side"] == "SELL"
    assert plan["orders"][0]["quantity"] <= 4.37855
    assert len(plan["orders"]) == 6
    assert plan["total_buy_notional_usd"] <= plan["maximum_buy_notional_usd"]


def test_dry_run_persists_intent_without_broker_write(tmp_path):
    plan = subject.build_portfolio_plan(**_args())
    result = execute_portfolio_plan(
        owner_decision=OWNER, plan=plan, broker=object(), state_root=tmp_path,
        executed_at="2026-08-19T22:00:00+00:00", submit_enabled=False,
    )
    assert result["status"] == "DRY_RUN_READY"
    assert result["broker_write_performed"] is False
    assert (tmp_path / "2026-08-25" / "intent.json").exists()


def test_full_fake_fill_path_reconciles_scaled_target(tmp_path):
    plan = subject.build_portfolio_plan(**_args())

    class Broker:
        def __init__(self):
            self.positions = {}
            self.cash = 460.90

        def find_order_by_client_id(self, client_id):
            return None

        def submit_lyra_live_portfolio_market_order(self, **values):
            notional = float(values["notional"])
            quantity = notional / 100.0
            self.positions[values["symbol"]] = quantity
            self.cash -= notional
            return {
                "id": f"broker-{values['symbol']}",
                "client_order_id": values["client_order_id"],
                "symbol": values["symbol"], "side": "BUY", "status": "filled",
                "qty": str(quantity), "filled_qty": str(quantity),
                "filled_avg_price": "100.0",
            }

        def get_order(self, order_id):
            raise AssertionError("filled market receipt should not need polling")

        def get_account(self):
            return {"equity": "460.90", "cash": str(self.cash)}

        def get_positions(self):
            return [{"symbol": symbol, "qty": str(quantity)} for symbol, quantity in self.positions.items()]

        def get_latest_trades(self, symbols):
            return {symbol: {"price": 100.0} for symbol in symbols}

    result = execute_portfolio_plan(
        owner_decision=OWNER, plan=plan, broker=Broker(), state_root=tmp_path,
        executed_at="2026-08-25T09:36:00-04:00", submit_enabled=True,
    )
    assert result["status"] == "COMPLETE"
    assert result["broker_write_performed"] is True
    assert len(result["submitted_orders"]) == 5
    assert result["posttrade_reconciliation"]["status"] == "ALIGNED"


def test_initialization_is_hash_and_symbol_pinned(monkeypatch):
    raw = json.dumps({
        "strategy_slug": "caerus_lyra", "source_variant": subject.LYRA_VARIANT,
        "trade_date": "2026-08-17", "effective_trade_date": "2026-08-17",
        "target_weights": {symbol: .2 for symbol in subject.TARGET_SYMBOLS},
    }, sort_keys=True).encode()
    monkeypatch.setattr(subject, "INITIALIZATION_TARGET_SHA256", __import__("hashlib").sha256(raw).hexdigest())
    target = subject.validate_target_source(raw, mode="initialization", execution_session="2026-08-20")
    assert set(target["weights"]) == subject.TARGET_SYMBOLS


def test_live_broker_boundary_accepts_fractional_notional_only_with_capability(monkeypatch):
    enums = types.ModuleType("alpaca.trading.enums")
    enums.OrderSide = types.SimpleNamespace(BUY="buy", SELL="sell")
    enums.TimeInForce = types.SimpleNamespace(DAY="day")
    requests = types.ModuleType("alpaca.trading.requests")

    class MarketOrderRequest:
        def __init__(self, **values):
            self.__dict__.update(values)

    requests.MarketOrderRequest = MarketOrderRequest
    monkeypatch.setitem(sys.modules, "alpaca.trading.enums", enums)
    monkeypatch.setitem(sys.modules, "alpaca.trading.requests", requests)

    class Client:
        order_data = None

        def submit_order(self, *, order_data):
            self.order_data = order_data
            return {
                "id": "broker-1", "client_order_id": order_data.client_order_id,
                "symbol": order_data.symbol, "side": order_data.side,
                "status": "accepted", "notional": order_data.notional,
            }

    plan = subject.build_portfolio_plan(**_args())
    order = plan["orders"][0]
    context = _mutation_context(plan, order)
    client = Client()
    broker = AlpacaBroker(client, paper=False, base_url="https://api.alpaca.markets")
    with pytest.raises(PermissionError, match="capability"):
        broker.submit_lyra_live_portfolio_market_order(
            symbol=order["symbol"], side=order["side"],
            client_order_id=order["client_order_id"], notional=order["notional"],
            mutation_context=context,
        )
    receipt = broker.submit_lyra_live_portfolio_market_order(
        symbol=order["symbol"], side=order["side"],
        client_order_id=order["client_order_id"], notional=order["notional"],
        mutation_context=context,
        _lyra_live_portfolio_capability=_LYRA_LIVE_PORTFOLIO_CAPABILITY,
    )
    assert receipt["client_order_id"] == order["client_order_id"]
    assert float(client.order_data.notional) == order["notional"]
    assert client.order_data.qty is None


def test_cron_contains_one_time_initialization_and_tuesday_cadence():
    installed = render("", install=True)
    assert INIT_LINE in installed
    assert WEEKLY_LINE in installed
    assert render(installed, install=True) == installed
    assert render(installed, install=False) == ""
