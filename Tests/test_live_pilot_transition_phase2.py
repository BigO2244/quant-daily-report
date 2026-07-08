"""Workstream C Phase 2 — live pilot on the Transition Engine (Option A), end to end.

All dry-run; the kill switch is disarmed only in the test env fixture so the gate
sequence is exercised (the FakeBroker never submits). Covers the mandatory July 6
scenario (rotation block + buying-power block variant), clean-slate weight-priority
selection, and held-and-targeted incremental sizing.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from scripts.live_pilot_execute import (
    LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE,
    LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION,
    LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER,
    run_live_pilot,
)

ET = ZoneInfo("America/New_York")


class FakeBroker:
    paper = False
    base_url = "https://api.alpaca.markets"

    def __init__(self, *, buying_power="1000", cash=None, equity="1000", positions=None):
        self.submit_calls = 0
        self.market_calls = 0
        self.limit_calls = 0
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
        return {"symbol": symbol, "status": "active", "asset_class": "us_equity", "tradable": True}

    def list_orders(self, status="open", limit=100):
        return list(self.open_orders)

    def submit_market_order(self, **kwargs):
        self.submit_calls += 1
        self.market_calls += 1
        return {"id": f"order-{self.submit_calls}", "status": "accepted", **kwargs}

    def submit_limit_order(self, **kwargs):
        self.submit_calls += 1
        self.limit_calls += 1
        return {"id": f"order-{self.submit_calls}", "status": "accepted", **kwargs}


def _env(*, dry_run="1", fractional="1"):
    return {
        "TRADING_MODE": "live_pilot",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": "https://api.alpaca.markets",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_CAPITAL_CAP": "500",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "polaris",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "acct-123",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": "1",
        "CAERUS_LIVE_PILOT_DRY_RUN": dry_run,
        "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL": fractional,
        # Disarmed ONLY for the test harness; production kill switch stays engaged.
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
    }


def _market_open() -> dt.datetime:
    return dt.datetime(2026, 3, 17, 9, 35, tzinfo=ET)


def _target_row(symbol, weight, price):
    return {"symbol": symbol, "ticker": symbol, "side": "BUY", "target_weight": weight,
            "price": price, "sleeve": "polaris"}


def _run(broker, plan, *, dry_run="1", fractional="1", run_id="run-p2", tmp_path):
    return run_live_pilot(
        plan=plan,
        broker=broker,
        env=_env(dry_run=dry_run, fractional=fractional),
        run_id=run_id,
        output_root=tmp_path / "outputs" / "live_pilot",
        now_et=_market_open(),
    ), tmp_path / "outputs" / "live_pilot" / "runs" / run_id


# --------------------------------------------------------------------------- #
# 1. July 6 — live path no longer blocks rotation in the engine, but sell whitelist
# still blocks any unapproved SELL before submission.
# --------------------------------------------------------------------------- #
def _july6_positions():
    return [
        {"symbol": "ABBV", "qty": "1.186242896", "market_value": "303.227409"},
        {"symbol": "ALL", "qty": "0.420274014", "market_value": "104.061947"},
        {"symbol": "C", "qty": "0.68975031", "market_value": "98.654987"},
    ]


def test_july6_live_path_blocks_unwhitelisted_rotation_sell(tmp_path: Path) -> None:
    broker = FakeBroker(buying_power="0.88", cash="0.88", equity="506.82", positions=_july6_positions())
    plan = {"target_portfolio": [_target_row("ALL", 0.5, 180.0)]}

    result, run_root = _run(broker, plan, run_id="run-july6-rotation", tmp_path=tmp_path)

    assert result["terminal_status"] == "BLOCKED"
    assert "sell_not_whitelisted" in result["reason_code"]
    assert broker.submit_calls == 0

    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["schema_version"] == "caerus.execution_core_transition_plan.v1"
    assert transition["blocked"] is False
    # The paper-native core applies the min-trade floor before validation; ABBV remains
    # an actionable unwhitelisted exit, while sub-$100 C is dust.
    assert set(transition["holdings_to_sell"]) == {"ABBV"}

    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["decision"] == "ALLOWED"
    assert capital_gate["required_sell_count"] == 1  # paper-native min-trade floor drops C dust
    assert capital_gate["tradable_capital_usd"] > 0.88  # includes 95% expected-sell-proceeds haircut


def test_july6_variant_blocks_on_buying_power_when_no_rotation(tmp_path: Path) -> None:
    # No rotation: hold only ALL (a target name). Residual ALL need > $0.88 buying
    # power -> blocked on buying power, never sized against the $500 cap.
    broker = FakeBroker(
        buying_power="0.88", cash="0.88", equity="506.82",
        positions=[{"symbol": "ALL", "qty": "0.420274014", "market_value": "104.061947"}],
    )
    plan = {"target_portfolio": [_target_row("ALL", 0.9, 180.0)]}

    result, run_root = _run(broker, plan, run_id="run-july6-bp", tmp_path=tmp_path)

    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_INSUFFICIENT_BUYING_POWER
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["holdings_to_sell"] == []  # no rotation
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    # Tradable capital tracks buying power, never the cap.
    assert capital_gate["tradable_capital_usd"] == 0.88
    assert capital_gate["approved_cap_usd"] == 500.0
    assert capital_gate["planned_buy_notional_usd"] > 0.88  # need exceeds buying power


# --------------------------------------------------------------------------- #
# 2. Clean slate — one weight-priority buy, sized, dry-run boundary
# --------------------------------------------------------------------------- #
def test_clean_slate_selects_highest_weight_and_halts_dry_run(tmp_path: Path) -> None:
    # Pilot regime: equity ~= the $500 cap.
    broker = FakeBroker(buying_power="500", cash="500", equity="500")
    # Two targets; BBB has the higher weight -> engine selects BBB (max_orders=1),
    # NOT precompute row order (AAA is listed first).
    plan = {"target_portfolio": [
        _target_row("AAA", 0.2, 100.0),
        _target_row("BBB", 0.3, 50.0),
    ]}

    result, run_root = _run(broker, plan, dry_run="1", run_id="run-clean", tmp_path=tmp_path)

    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0  # halts at the dry-run boundary, no submission

    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["blocked"] is False
    buys = transition["buy_orders_intended"]
    assert len(buys) == 1
    assert buys[0]["symbol"] == "BBB"  # highest weight, not first row
    # BBB target = 0.3*500 = $150 -> 3 shares @ $50; sized by min(bp, cap, need)=min(500,500,150)=150.
    assert buys[0]["notional"] == 150.0
    assert buys[0]["notional"] <= 500.0  # within cap
    intended = json.loads((run_root / "live_pilot_orders_intended.json").read_text())
    assert intended["orders"][0]["symbol"] == "BBB"


def test_clean_slate_min_trade_floor_blocks_dust(tmp_path: Path) -> None:
    # A sub-$100 residual need now produces NO order (min-trade is new to live).
    broker = FakeBroker(buying_power="500", cash="500", equity="500")
    plan = {"target_portfolio": [_target_row("AAA", 0.05, 100.0)]}  # $25 target < $100 floor
    result, run_root = _run(broker, plan, run_id="run-dust", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_transition_no_actionable_order"
    assert broker.submit_calls == 0


def test_buying_power_zero_blocks_unavailable(tmp_path: Path) -> None:
    # #1: a real 0.0 buying power (unsettled cash) must block, NOT size against cash.
    # This is the live account's condition today (~$8 free cash -> bp at/near 0).
    broker = FakeBroker(buying_power="0", cash="500", equity="500")
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}  # $200 target need
    result, run_root = _run(broker, plan, dry_run="0", run_id="run-bp0", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE
    assert broker.submit_calls == 0
    capital_gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
    assert capital_gate["decision"] == "BLOCKED"
    assert capital_gate["live_buying_power_before"] == 0.0  # 0.0 is visible in evidence


def test_unpriceable_held_position_missing_market_value_blocks(tmp_path: Path) -> None:
    # #2: valid qty but missing market_value -> not cleanly priceable -> fail closed.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "XYZ", "qty": "2"}],  # no market_value
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-nomv", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["diagnostics"]["unpriceable_holding_symbol"] == "XYZ"
    assert "missing_nonpositive_or_nonfinite_market_value" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_uncountable_held_position_missing_qty_blocks(tmp_path: Path) -> None:
    # Generalized guard: present positive market_value but missing qty -> not cleanly
    # countable -> fail closed. Must NOT be skipped just because market_value is present.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "XYZ", "market_value": "200"}],  # no qty
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-noqty", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["diagnostics"]["unpriceable_holding_symbol"] == "XYZ"
    assert "missing_zero_or_nonfinite_qty" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_symbol_missing_holding_blocks_rotation(tmp_path: Path) -> None:
    # Shared-predicate guarantee: a position with qty+market_value but NO symbol is a
    # real holding that holdings_from_snapshot drops -> must fail closed, not skip.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"qty": "2", "market_value": "200"}],  # no symbol
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-nosym", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert "missing_symbol" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_nonfinite_qty_holding_blocks_rotation(tmp_path: Path) -> None:
    # Non-finite qty (inf) would map to price 0.0 in the engine and be dropped -> block.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "XYZ", "qty": "inf", "market_value": "200"}],
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-inf", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert "missing_zero_or_nonfinite_qty" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_degenerate_price_underflow_blocks_rotation(tmp_path: Path) -> None:
    # qty and market_value each pass individually, but abs(mv/qty) UNDERFLOWS to 0.0
    # (a price the engine treats as unpriceable and silently drops). The shared predicate
    # now validates the quotient, so this fails closed.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "XYZ", "qty": "1e300", "market_value": "1e-300"}],
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-underflow", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert "degenerate_price" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_degenerate_price_overflow_blocks_rotation(tmp_path: Path) -> None:
    # Overflow counterpart: huge mv / small (but above the zero-threshold) qty ->
    # abs(mv/qty) = inf (would produce an inf-notional intent). qty=1e-11 clears the
    # abs(qty)>1e-12 countability check, so the degenerate-price check is what blocks it.
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "XYZ", "qty": "1e-11", "market_value": "1e300"}],
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-overflow", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_EXISTING_POSITIONS_REQUIRE_ROTATION
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert "degenerate_price" in transition["diagnostics"]["unpriceable_holding_reason"]


def test_bp_zero_with_planned_but_clipped_blocks(tmp_path: Path) -> None:
    # bp=0.0 (real), cash below min-trade so the sized buy is clipped away entirely
    # (buy_orders_intended empty) while planned_buy_notional>0. This case is caught by
    # the engine's deployed<=0 INSUFFICIENT_BUYING_POWER backstop; the bp-guard OR
    # (buy_orders_intended OR planned>0) is defense-in-depth behind it. Either way the
    # run fails closed with no submission.
    broker = FakeBroker(buying_power="0", cash="50", equity="500")
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}  # need $200 > $50 budget
    result, run_root = _run(broker, plan, dry_run="0", run_id="run-bp0clip", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == LIVE_PILOT_BLOCKED_BUYING_POWER_UNAVAILABLE
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["buy_orders_intended"] == []  # sized buy was clipped away
    assert transition["diagnostics"]["buying_power_nonpositive"] is True


def test_cleanly_priced_and_counted_holding_proceeds(tmp_path: Path) -> None:
    # Both fields valid -> the guard skips the position and the run proceeds (dry-run).
    # (AAA held+targeted, no exits, so no rotation.)
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "AAA", "qty": "1", "market_value": "100"}],
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.6, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-clean-hold", tmp_path=tmp_path)
    assert result["terminal_status"] == "DRY_RUN"
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["blocked"] is False


def test_missing_equity_blocks(tmp_path: Path) -> None:
    # equity=None (broker omitted equity/portfolio_value) must fail closed explicitly.
    broker = FakeBroker(buying_power="500", cash="500", equity=None)
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-noeq", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_equity_unavailable"
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["diagnostics"]["equity_unavailable"] is True


def test_equity_above_cap_regime_blocks(tmp_path: Path) -> None:
    # #3-guard: equity above the $520 cap-regime ceiling halts (sizing model assumes
    # equity ~= cap). Deferred: exposure-aware cap when the account is funded above cap.
    broker = FakeBroker(buying_power="5000", cash="5000", equity="5000")
    plan = {"target_portfolio": [_target_row("AAA", 0.1, 100.0)]}
    result, run_root = _run(broker, plan, dry_run="1", run_id="run-equity-regime", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_equity_exceeds_cap_regime"
    assert broker.submit_calls == 0
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["diagnostics"]["equity_usd"] == 5000.0


def test_equity_tight_collar_blocks_just_above_ceiling(tmp_path: Path) -> None:
    # Tightened collar: equity $550 (below the old $600, above the new $520 ceiling)
    # now blocks -- no top-up tolerance band above the $500 cap.
    broker = FakeBroker(buying_power="550", cash="550", equity="550")
    plan = {"target_portfolio": [_target_row("AAA", 0.4, 100.0)]}
    result, _run_root = _run(broker, plan, dry_run="1", run_id="run-collar", tmp_path=tmp_path)
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == "live_pilot_equity_exceeds_cap_regime"
    assert broker.submit_calls == 0


# --------------------------------------------------------------------------- #
# 3. Held-and-targeted — incremental need, never a blind full-size buy
# --------------------------------------------------------------------------- #
def test_held_and_targeted_buys_only_incremental(tmp_path: Path) -> None:
    # Hold 1 share of AAA @ $100; target 3 shares. No other holdings (no rotation).
    # Pilot regime: equity ~= the $500 cap (weight 0.6 * $500 = $300 target = 3 sh).
    broker = FakeBroker(
        buying_power="500", cash="500", equity="500",
        positions=[{"symbol": "AAA", "qty": "1", "market_value": "100"}],
    )
    plan = {"target_portfolio": [_target_row("AAA", 0.6, 100.0)]}

    result, run_root = _run(broker, plan, dry_run="1", run_id="run-held", tmp_path=tmp_path)

    assert result["terminal_status"] == "DRY_RUN"
    transition = json.loads((run_root / "live_pilot_transition_plan.json").read_text())
    assert transition["blocked"] is False
    buys = transition["buy_orders_intended"]
    assert len(buys) == 1
    assert buys[0]["symbol"] == "AAA"
    # target 3 shares, hold 1 -> incremental 2 shares = $200 (NOT the full 3/$300).
    assert buys[0]["shares"] == 2.0
    assert buys[0]["notional"] == 200.0
    assert "AAA" in transition["holdings_to_increase"]
    assert transition["holdings_to_sell"] == []
