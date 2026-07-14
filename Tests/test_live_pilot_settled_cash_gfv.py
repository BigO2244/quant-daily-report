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
        "CAERUS_LIVE_PILOT_SUBMIT_APPROVED": "1",
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

# --------------------------------------------------------------------------- #
# Verifier round 2: bulk-history freshness, date bounds, date-only fills
# --------------------------------------------------------------------------- #
def test_date_only_filled_at_is_not_shifted_to_prior_day() -> None:
    """A date-only filled_at ("2026-07-13") must be used AS-IS as the ET trade date.

    The old datetime path parsed it as naive midnight -> UTC -> ET = the PRIOR day,
    which moved settlement one day EARLIER (unsafe: a today-filled sell would count
    as already settled today).
    """
    orders = [
        {"side": "sell", "status": "filled", "symbol": "X", "filled_qty": "1",
         "filled_avg_price": "100", "filled_at": "2026-07-13", "id": "d1"},
    ]
    total, breakdown = unsettled_proceeds(orders, "2026-07-13")
    assert total == pytest.approx(100.0)  # still unsettled TODAY (settles 07-14)
    assert breakdown[0]["fill_date"] == "2026-07-13"
    assert breakdown[0]["settlement_date"] == "2026-07-14"


def test_confirmed_fill_missing_from_bulk_history_is_injected_as_unsettled() -> None:
    """Cross-check (finding #1, unit level): a same-run confirmed sell fill absent
    from the lagging bulk listing has its proceeds injected as unsettled."""
    r = compute_settled_cash(
        broker_cash=404.0,
        orders=[],  # lagging bulk read: shows nothing
        as_of_date="2026-07-13",
        buy_buffer_pct=0.98,
        confirmed_sells=[{"order_id": "sim-1", "proceeds": 111.0, "symbol": "ALL"}],
    )
    assert r.unsettled_proceeds == pytest.approx(111.0)
    assert r.settled_cash == pytest.approx(293.0)
    assert any(
        row.get("source") == "confirmed_fill_missing_from_bulk_history"
        for row in r.unsettled_orders
    )


def test_confirmed_fill_partially_visible_in_bulk_injects_only_shortfall() -> None:
    """Bulk shows a stale partial fill ($40 of a $111 confirmed sell): only the $71
    shortfall is injected, never double-counted."""
    bulk = [
        {"id": "sim-1", "side": "sell", "status": "partially_filled", "symbol": "ALL",
         "filled_qty": "0.4", "filled_avg_price": "100",
         "filled_at": "2026-07-13T14:00:00Z"},
    ]
    r = compute_settled_cash(
        broker_cash=404.0,
        orders=bulk,
        as_of_date="2026-07-13",
        confirmed_sells=[{"order_id": "sim-1", "proceeds": 111.0, "symbol": "ALL"}],
    )
    assert r.unsettled_proceeds == pytest.approx(111.0)  # 40 bulk + 71 shortfall
    assert r.settled_cash == pytest.approx(293.0)


class LaggingBulkHistoryBroker(SettlementSimBroker):
    """Bulk list_orders lags per-order reads: orders filled TODAY are missing from
    the status=all listing, while get_order (per-order poll) sees them filled and
    account cash already includes their proceeds. This is the freshness gap from
    verifier finding #1."""

    def list_orders(self, status: str = "open", limit: int = 100) -> list[dict]:
        if str(status).lower() == "open":
            return []
        stale = [o for o in self.history if str(o.get("filled_at") or "")[:10] != self.today]
        return [dict(o) for o in stale][-int(limit):]


def test_lagging_bulk_history_cannot_defeat_the_clamp(tmp_path: Path) -> None:
    """Finding #1 (integration): sells fill and credit cash, but the bulk order
    listing feeding the settled-cash recompute hasn't caught up. The confirmed-fill
    cross-check must inject the known proceeds as unsettled so buys stay clamped to
    the PRE-SELL settled cash. Without the cross-check this run buys ~$395 against
    $293 settled (a GFV); the double-entry ledger would go negative."""
    broker = LaggingBulkHistoryBroker(
        settled_cash=293.0,
        positions={"ALL": 0.42, "C": 0.69},  # $111 of long-settled holdings
        price=100.0,
    )
    broker.advance_to("2026-07-13")
    output_root = tmp_path / "outputs" / "live_pilot"
    run_id = "lagging-bulk"
    result = run_live_pilot(
        plan=_rotation_plan("NEWA"),
        broker=broker,
        env=_env(),
        run_id=run_id,
        output_root=output_root,
        now_et=_open(dt.date(2026, 7, 13)),
    )
    run_root = output_root / "runs" / run_id
    assert result["terminal_status"] == "SUBMITTED", result
    # Sells happened and buys happened...
    assert broker.buy_events, "rotation should still deploy buys"
    # ...but INVARIANT HOLDS: no buy drew on unsettled proceeds despite the lagging
    # bulk read (the ledger never went negative).
    assert broker.min_settled_funds_seen >= -1e-6, broker.buy_events
    total_buys = sum(e["notional"] for e in broker.buy_events)
    assert total_buys <= 293.0 * 0.98 + 1e-6, total_buys
    # The post-sell settled-cash artifact records the injected cross-check rows.
    settled = json.loads((run_root / "live_pilot_settled_cash.json").read_text())
    assert settled["phase"] == "post_sell"
    assert any(
        row.get("source") == "confirmed_fill_missing_from_bulk_history"
        for row in settled["unsettled_orders"]
    ), settled["unsettled_orders"]
    assert settled["settled_cash"] == pytest.approx(293.0)


def test_page_full_inside_unsettled_window_fails_closed() -> None:
    """Finding #3: a full history page whose oldest row is still unsettled may have
    truncated older unsettled sells -> treat history as unavailable (fail closed)."""
    from scripts.live_pilot_execute import _settled_cash_context

    class PageFullBroker:
        def list_orders(self, status: str = "open", limit: int = 100) -> list[dict]:
            # Exactly `limit` rows, all filled today (unsettled as of today).
            return [
                {"id": f"o{i}", "side": "sell", "status": "filled", "symbol": "X",
                 "filled_qty": "1", "filled_avg_price": "10",
                 "filled_at": "2026-07-13T14:00:00Z"}
                for i in range(int(limit))
            ]

    env = {"CAERUS_LIVE_PILOT_SETTLED_CASH_ORDER_LOOKBACK": "3"}
    result, orders, availability = _settled_cash_context(
        PageFullBroker(), broker_cash=500.0, as_of_date="2026-07-13", env=env
    )
    assert availability == "page_full_unsettled_window"
    assert orders is None
    assert result.fail_closed is True
    assert result.settled_cash == 0.0


def test_page_full_with_oldest_row_settled_does_not_fail_closed() -> None:
    """Control: a full page whose oldest row is ALREADY settled proves the unsettled
    window is fully covered -> normal computation."""
    from scripts.live_pilot_execute import _settled_cash_context

    class PageFullSettledOldestBroker:
        def list_orders(self, status: str = "open", limit: int = 100) -> list[dict]:
            rows = [
                {"id": f"o{i}", "side": "sell", "status": "filled", "symbol": "X",
                 "filled_qty": "1", "filled_avg_price": "10",
                 "filled_at": "2026-07-13T14:00:00Z"}
                for i in range(int(limit) - 1)
            ]
            # Oldest row: filled a week ago -> long settled -> window fully covered.
            rows.append(
                {"id": "old", "side": "sell", "status": "filled", "symbol": "Y",
                 "filled_qty": "1", "filled_avg_price": "10",
                 "filled_at": "2026-07-06T14:00:00Z"}
            )
            return rows

    env = {"CAERUS_LIVE_PILOT_SETTLED_CASH_ORDER_LOOKBACK": "3"}
    result, orders, availability = _settled_cash_context(
        PageFullSettledOldestBroker(), broker_cash=500.0, as_of_date="2026-07-13", env=env
    )
    assert result.fail_closed is False
    assert result.unsettled_proceeds == pytest.approx(20.0)  # 2 unsettled today-fills
    assert result.settled_cash == pytest.approx(480.0)


def test_order_history_query_is_date_bounded_when_broker_supports_after() -> None:
    """Finding #3: brokers supporting `after` get a date-bounded query covering the
    unsettled window (after = prev trading day of as_of); others fall back cleanly."""
    from scripts.live_pilot_execute import _settled_cash_context

    class AfterCapableBroker:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        def list_orders(self, status: str = "open", limit: int = 100, after: str | None = None) -> list[dict]:
            self.calls.append({"status": status, "limit": limit, "after": after})
            return []

    broker = AfterCapableBroker()
    result, _orders, availability = _settled_cash_context(
        broker, broker_cash=100.0, as_of_date="2026-07-13", env={}
    )
    assert availability == "ok_date_bounded"
    # Monday 2026-07-13 -> prev trading day Friday 2026-07-10.
    assert broker.calls[0]["after"] == "2026-07-10"
    assert result.fail_closed is False
    assert result.settled_cash == pytest.approx(100.0)
