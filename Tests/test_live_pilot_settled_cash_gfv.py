"""Hermetic tests for the settled-cash / GFV guard (PRE_ARM_SWEEP Blocker #2).

No network. A stub broker models an Alpaca CASH account with T+1 settlement:
sale proceeds are credited to ``cash`` IMMEDIATELY but only *settle* on the next
NYSE trading day. An independent double-entry ledger inside the broker tracks
genuinely-settled funds and asserts no buy ever spends unsettled money — a real
good-faith-violation (GFV) would drive that ledger negative.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from core.settled_cash import (
    GFV_SELL_OF_UNSETTLED_ACQUISITION,
    GFV_SETTLED_CASH_UNAVAILABLE,
    compute_settled_cash,
    detect_gfv_risky_sells,
    settlement_date,
    unsettled_proceeds,
)
from scripts.live_pilot_execute import run_live_pilot

ET = ZoneInfo("America/New_York")


# --------------------------------------------------------------------------- #
# Pure-module unit tests
# --------------------------------------------------------------------------- #
def test_settlement_date_friday_to_monday() -> None:
    # Fri 2026-07-10 fill settles Mon 2026-07-13 (skips the weekend).
    assert settlement_date("2026-07-10") == "2026-07-13"


def test_settlement_date_skips_market_holiday() -> None:
    # 2026-07-04 (Independence Day) is a Saturday -> observed Fri 2026-07-03.
    # A Thu 2026-07-02 fill settles Mon 2026-07-06 (skips the holiday + weekend).
    assert settlement_date("2026-07-02") == "2026-07-06"


def test_unsettled_proceeds_counts_only_unsettled_filled_sells() -> None:
    orders = [
        # Filled sell today -> settles tomorrow -> UNSETTLED as of today.
        {"side": "sell", "status": "filled", "symbol": "ALL", "filled_qty": "0.42",
         "filled_avg_price": "256.45", "filled_at": "2026-07-13T14:00:00Z", "id": "s1"},
        # Filled sell last week -> already settled -> excluded.
        {"side": "sell", "status": "filled", "symbol": "OLD", "filled_qty": "1",
         "filled_avg_price": "100", "filled_at": "2026-07-06T14:00:00Z", "id": "s2"},
        # Buys never count.
        {"side": "buy", "status": "filled", "symbol": "C", "filled_qty": "1",
         "filled_avg_price": "140", "filled_at": "2026-07-13T14:00:00Z", "id": "b1"},
    ]
    total, breakdown = unsettled_proceeds(orders, "2026-07-13")
    assert total == pytest.approx(0.42 * 256.45)
    assert [row["symbol"] for row in breakdown] == ["ALL"]


def test_unsettled_proceeds_partial_fill_uses_filled_portion_only() -> None:
    orders = [
        {"side": "sell", "status": "partially_filled", "symbol": "X", "filled_qty": "0.5",
         "qty": "2", "filled_avg_price": "100", "filled_at": "2026-07-13T14:00:00Z", "id": "p1"},
    ]
    total, _ = unsettled_proceeds(orders, "2026-07-13")
    assert total == pytest.approx(50.0)  # 0.5 * 100, NOT 2 * 100


def test_compute_settled_cash_subtracts_unsettled() -> None:
    orders = [
        {"side": "sell", "status": "filled", "symbol": "ALL", "filled_qty": "1",
         "filled_avg_price": "100", "filled_at": "2026-07-13T14:00:00Z", "id": "s1"},
    ]
    r = compute_settled_cash(broker_cash=497.0, orders=orders, as_of_date="2026-07-13", buy_buffer_pct=0.98)
    assert r.settled_cash == pytest.approx(397.0)
    assert r.unsettled_proceeds == pytest.approx(100.0)
    assert r.buffered_settled_cash == pytest.approx(397.0 * 0.98)
    assert r.fail_closed is False


def test_compute_settled_cash_fails_closed_when_history_unavailable() -> None:
    r = compute_settled_cash(broker_cash=497.0, orders=None, as_of_date="2026-07-13")
    assert r.fail_closed is True
    assert r.settled_cash == 0.0
    assert r.buffered_settled_cash == 0.0
    assert r.unsettled_proceeds == pytest.approx(497.0)  # max plausible unsettled
    assert r.reason_code == GFV_SETTLED_CASH_UNAVAILABLE


def test_buffer_arithmetic() -> None:
    r = compute_settled_cash(broker_cash=300.0, orders=[], as_of_date="2026-07-13", buy_buffer_pct=0.9)
    assert r.settled_cash == pytest.approx(300.0)  # no sells => all settled
    assert r.buffered_settled_cash == pytest.approx(270.0)


def test_detect_gfv_risky_sell_flags_not_yet_settled_acquisition() -> None:
    # Bought X today; selling it today would sell a not-yet-settled acquisition.
    orders = [
        {"side": "buy", "status": "filled", "symbol": "X", "filled_qty": "1",
         "filled_avg_price": "100", "filled_at": "2026-07-13T14:00:00Z", "id": "b1"},
    ]
    alerts = detect_gfv_risky_sells(planned_sell_symbols=["X"], orders=orders, as_of_date="2026-07-13")
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "X"
    assert alerts[0]["reason_code"] == GFV_SELL_OF_UNSETTLED_ACQUISITION


def test_detect_gfv_risky_sell_silent_on_settled_acquisition() -> None:
    # Bought X on Monday; selling on Tuesday -> Monday's buy has settled -> no flag.
    orders = [
        {"side": "buy", "status": "filled", "symbol": "X", "filled_qty": "1",
         "filled_avg_price": "100", "filled_at": "2026-07-13T14:00:00Z", "id": "b1"},
    ]
    alerts = detect_gfv_risky_sells(planned_sell_symbols=["X"], orders=orders, as_of_date="2026-07-14")
    assert alerts == []


# --------------------------------------------------------------------------- #
# Settlement-simulating stub broker
# --------------------------------------------------------------------------- #
class SettlementSimBroker:
    """Alpaca cash-account model with T+1 settlement and a double-entry ledger.

    ``settled_funds`` is an INDEPENDENT accounting of genuinely-settled cash. Buys
    debit it; a buy that drives it negative would be spending unsettled proceeds
    (a GFV). Sale proceeds are held in ``pending`` keyed by settlement date and move
    into ``settled_funds`` when the simulated date advances past that date.
    """

    paper = False
    base_url = "https://api.alpaca.markets"

    def __init__(self, *, settled_cash: float, positions: dict[str, float], price: float) -> None:
        self.today = "2026-07-13"
        self.price = float(price)
        self._cash = float(settled_cash) + sum(positions.values()) * 0.0  # only cash is cash
        self._cash = float(settled_cash)
        self.settled_funds = float(settled_cash)  # independent settled-funds ledger
        self.pending: list[tuple[str, float]] = []  # (settlement_date, amount)
        self.positions: dict[str, float] = dict(positions)  # symbol -> shares
        self.orders_by_id: dict[str, dict] = {}
        self.history: list[dict] = []
        self._seq = 0
        self.min_settled_funds_seen = float(settled_cash)
        self.buy_events: list[dict] = []

    # -- date control ------------------------------------------------------- #
    def advance_to(self, date_iso: str) -> None:
        self.today = str(date_iso)
        still_pending: list[tuple[str, float]] = []
        for settle_date, amount in self.pending:
            if settle_date <= self.today:
                self.settled_funds += amount  # proceeds have now settled
            else:
                still_pending.append((settle_date, amount))
        self.pending = still_pending

    # -- broker API --------------------------------------------------------- #
    def get_account(self) -> dict:
        equity = self._cash + sum(sh * self.price for sh in self.positions.values())
        return {
            "id": "acct-sim",
            "status": "ACTIVE",
            "cash": str(self._cash),
            "equity": str(equity),
            "buying_power": str(self._cash),
            "portfolio_value": str(equity),
        }

    def get_positions(self) -> list[dict]:
        return [
            {"symbol": s, "qty": str(sh), "qty_available": str(sh),
             "market_value": str(sh * self.price), "cost_basis": str(sh * self.price),
             "current_price": str(self.price)}
            for s, sh in self.positions.items()
            if sh > 1e-12
        ]

    def get_asset(self, symbol: str) -> dict:
        return {"symbol": symbol, "status": "active", "asset_class": "us_equity",
                "tradable": True, "fractionable": True}

    def list_orders(self, status: str = "open", limit: int = 100) -> list[dict]:
        if str(status).lower() == "open":
            return []
        return [dict(o) for o in self.history][-int(limit):]

    def get_order(self, order_id: str) -> dict:
        return dict(self.orders_by_id.get(order_id, {}))

    def submit_market_order(self, **kwargs) -> dict:
        self._seq += 1
        side = str(kwargs.get("side") or "").upper()
        symbol = str(kwargs.get("symbol") or "").upper()
        qty = float(kwargs.get("qty") or 0.0)
        notional = float(kwargs.get("estimated_notional") or qty * self.price)
        order_id = f"sim-{self._seq}"
        if side == "SELL":
            self._cash += notional
            # proceeds settle T+1 -> held as pending, NOT settled yet
            self.pending.append((settlement_date(self.today), notional))
            self.positions[symbol] = max(0.0, self.positions.get(symbol, 0.0) - qty)
            if self.positions.get(symbol, 0.0) <= 1e-12:
                self.positions.pop(symbol, None)
        else:  # BUY
            self._cash -= notional
            self.settled_funds -= notional  # buys draw on SETTLED funds only
            self.min_settled_funds_seen = min(self.min_settled_funds_seen, self.settled_funds)
            self.buy_events.append({"date": self.today, "symbol": symbol, "notional": notional,
                                    "settled_funds_after": self.settled_funds})
            self.positions[symbol] = self.positions.get(symbol, 0.0) + qty
        order = {
            "id": order_id, "status": "filled", "symbol": symbol, "side": side.lower(),
            "client_order_id": kwargs.get("client_order_id"),
            "qty": str(qty), "filled_qty": str(qty), "filled_quantity": str(qty),
            "filled_avg_price": str(notional / qty if qty else self.price),
            "submitted_at": f"{self.today}T13:35:00+00:00",
            "filled_at": f"{self.today}T13:35:02+00:00",
        }
        self.orders_by_id[order_id] = order
        self.history.append(order)
        return order


def _env(max_orders: str = "5") -> dict:
    return {
        "TRADING_MODE": "live_pilot",
        "ALPACA_PAPER": "0",
        "ALPACA_BASE_URL": "https://api.alpaca.markets",
        "CAERUS_LIVE_PILOT_APPROVED": "1",
        "CAERUS_LIVE_PILOT_SLEEVE_ID": "polaris",
        "CAERUS_LIVE_PILOT_ACCOUNT_ID": "acct-sim",
        "CAERUS_LIVE_PILOT_MAX_ORDERS": max_orders,
        "CAERUS_LIVE_PILOT_DRY_RUN": "0",
        "CAERUS_LIVE_PILOT_KILL_SWITCH": "0",
        "CAERUS_LIVE_PILOT_SELLS_ENABLED": "1",
        "CAERUS_LIVE_PILOT_SELL_WHITELIST": "*",
        "CAERUS_LIVE_PILOT_ALLOW_FRACTIONAL": "1",
        "CAERUS_LIVE_PILOT_MIN_TRADE_USD": "10",
    }


def _rotation_plan(symbol: str) -> dict:
    # Single fresh name at 0.9 weight -> target notional (~$448) EXCEEDS settled cash,
    # so the clamp must bind (proving buys never draw on unsettled proceeds).
    return {"target_portfolio": [
        {"ticker": symbol, "symbol": symbol, "side": "BUY", "target_weight": 0.9,
         "price": 100.0, "order_type": "market", "sleeve": "polaris"}
    ]}


def _open(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, 9, 35, tzinfo=ET)


def test_three_day_full_rotation_never_creates_gfv(tmp_path: Path) -> None:
    """>=3 consecutive daily 100% rotations at real scale ($497 / T+1).

    Day N sells everything held and buys an entirely new name. Buys are clamped to
    SETTLED cash each day; the independent double-entry ledger proves no buy ever
    spends unsettled proceeds, and the sell-side guard never fires.
    """
    broker = SettlementSimBroker(
        settled_cash=293.0,
        positions={"ALL": 0.42, "C": 0.69},  # legacy, long-settled holdings
        price=100.0,
    )
    output_root = tmp_path / "outputs" / "live_pilot"
    days = [dt.date(2026, 7, 13), dt.date(2026, 7, 14), dt.date(2026, 7, 15)]
    fresh_names = ["NEWA", "NEWB", "NEWC"]

    deployed_each_day: list[float] = []
    for i, (day, name) in enumerate(zip(days, fresh_names)):
        broker.advance_to(day.isoformat())
        run_id = f"rot-{day.isoformat()}"
        result = run_live_pilot(
            plan=_rotation_plan(name),
            broker=broker,
            env=_env(),
            run_id=run_id,
            output_root=output_root,
            now_et=_open(day),
        )
        run_root = output_root / "runs" / run_id
        assert result["terminal_status"] == "SUBMITTED", (day, result)
        # No GFV alert artifact was ever written.
        assert not (run_root / "live_pilot_gfv_alert.json").exists(), day
        # The settled-cash guard ran and its proof is in the capital gate.
        gate = json.loads((run_root / "live_pilot_capital_gate.json").read_text())
        assert gate.get("settled_cash_usd") is not None, day
        assert gate.get("buy_buffer_pct") == 0.98
        assert gate.get("settled_cash_fail_closed") is False
        # A buy was actually deployed (rotation is real, not a no-op).
        submitted = json.loads((run_root / "live_pilot_orders_submitted.json").read_text())
        buys = [o for o in submitted["orders"] if str(o.get("side")).upper() == "BUY"]
        assert buys, (day, submitted)
        deployed = sum(float(o.get("notional") or 0.0) for o in buys)
        deployed_each_day.append(deployed)

    # INVARIANT PROOF: buys never drew on unsettled funds on any day.
    assert broker.min_settled_funds_seen >= -1e-6, broker.buy_events
    for event in broker.buy_events:
        assert event["settled_funds_after"] >= -1e-6, event
    # Day 1 buys were clamped strictly below the full ($498) cash balance -> the
    # clamp genuinely bound against unsettled proceeds.
    assert deployed_each_day[0] <= 293.0 * 0.98 + 1e-6
    assert deployed_each_day[0] < 490.0


def test_fail_closed_blocks_buys_when_order_history_unavailable(tmp_path: Path) -> None:
    """Broker cannot report order history -> settled cash treated as $0 -> buys blocked."""

    class NoHistoryBroker(SettlementSimBroker):
        def list_orders(self, status: str = "open", limit: int = 100) -> list[dict]:
            if str(status).lower() == "open":
                return []
            raise RuntimeError("order history endpoint down")

    broker = NoHistoryBroker(settled_cash=293.0, positions={}, price=100.0)
    broker.advance_to("2026-07-13")
    output_root = tmp_path / "outputs" / "live_pilot"
    run_id = "failclosed"
    result = run_live_pilot(
        plan=_rotation_plan("NEWA"),
        broker=broker,
        env=_env(),
        run_id=run_id,
        output_root=output_root,
        now_et=_open(dt.date(2026, 7, 13)),
    )
    run_root = output_root / "runs" / run_id
    # No buy reached the broker; the run blocked with the loud GFV reason code.
    assert broker.min_settled_funds_seen >= -1e-6
    assert not broker.buy_events
    assert result["terminal_status"] == "BLOCKED"
    assert result["reason_code"] == GFV_SETTLED_CASH_UNAVAILABLE
    settled = json.loads((run_root / "live_pilot_settled_cash.json").read_text())
    assert settled["fail_closed"] is True
    assert settled["settled_cash"] == 0.0
