"""Settled-cash / GFV guard for the live cash-account pilot (PRE_ARM_SWEEP Blocker #2).

Pure, deterministic, no network I/O and no clock: the ``as_of`` trade date is always
injected by the caller. US equities settle **T+1** on NYSE business days (weekends AND
holidays; a Friday fill settles Monday, a pre-holiday fill skips the holiday). Alpaca
credits sale proceeds to ``cash``/``buying_power`` IMMEDIATELY even though the funds are
unsettled. On a CASH account, buying with those unsettled proceeds and then selling the
purchased security before the proceeds settle is a Good-Faith Violation (GFV).

The PRIMARY defense (applied by the caller in ``execution/core.py``) is the buy-sizing
clamp: never let a buy spend more than *settled* cash.

    INVARIANT (enforced by the clamp that consumes ``settled_cash``):
        sum(buy_notional) <= settled_cash * buy_buffer_pct
    => every buy is funded only by genuinely settled cash
    => no position we hold can ever be GFV-vulnerable when sold next morning.

This module computes ``settled_cash`` from broker truth (account cash + recent FILLED
sell order history) and provides the belt-and-suspenders sell-side detector.

FAIL CLOSED: if broker order history is unavailable or incomplete, treat cash as having
the MAXIMUM plausible unsettled component (``settled_cash = 0``) so buys are blocked.
Never assume cash is fully settled.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from paper.trading_calendar import ET_TZ, next_trading_day

# Reason codes surfaced to the operator / capital gate.
GFV_SETTLED_CASH_UNAVAILABLE = "live_pilot_gfv_settled_cash_unavailable"
GFV_SELL_OF_UNSETTLED_ACQUISITION = "live_pilot_gfv_sell_of_unsettled_acquisition"

_SELL_SIDES = {"sell", "sell_short", "close", "reduce"}
_BUY_SIDES = {"buy", "buy_to_open", "add"}
_FILLED_STATUSES = {"filled", "partially_filled"}


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float(default)


def _order_field(order: Mapping[str, Any], *names: str) -> Any:
    """Read a field from a normalized order dict, falling back to its ``raw`` payload."""
    for name in names:
        if name in order and order.get(name) not in (None, ""):
            return order.get(name)
    raw = order.get("raw")
    if isinstance(raw, Mapping):
        for name in names:
            if raw.get(name) not in (None, ""):
                return raw.get(name)
    return None


def _side(order: Mapping[str, Any]) -> str:
    return str(_order_field(order, "side") or "").strip().lower()


def _status(order: Mapping[str, Any]) -> str:
    return str(_order_field(order, "status") or "").strip().lower()


def _fill_date(order: Mapping[str, Any]) -> str | None:
    """ET calendar date (YYYY-MM-DD) of the fill; ``None`` if unfilled/unparseable.

    Settlement is reckoned from the ET trade date. Alpaca timestamps are UTC ISO
    strings; convert to the exchange timezone before taking the calendar date so a
    late-afternoon ET fill is not pushed to the next UTC day.
    """
    raw = _order_field(order, "filled_at", "submitted_at", "created_at")
    text = str(raw or "").strip()
    if not text:
        return None
    # A date-only string ("2026-07-13") IS the calendar trade date: use it as-is.
    # Routing it through the datetime path would parse it as naive midnight, stamp
    # UTC, and shift it to the PRIOR day in ET — moving settlement one day EARLIER,
    # which is the unsafe direction for the GFV guard.
    try:
        dt.date.fromisoformat(text)
        return text
    except ValueError:
        pass
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Fall back to a bare date prefix if present.
        head = text[:10]
        try:
            dt.date.fromisoformat(head)
            return head
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(ET_TZ).date().isoformat()


def settlement_date(fill_date: str) -> str:
    """T+1 settlement date: the next NYSE business day after ``fill_date``.

    ``next_trading_day`` skips weekends AND market holidays, so a Friday fill settles
    Monday and a pre-holiday fill skips the holiday.
    """
    return next_trading_day(str(fill_date))


def _filled_sell_proceeds(order: Mapping[str, Any]) -> float:
    """Realized proceeds of a filled sell = filled_qty * filled_avg_price.

    Partial fills contribute only the FILLED portion. Falls back to the order
    ``notional`` only when a fill price is genuinely unavailable.
    """
    filled_qty = _to_float(_order_field(order, "filled_qty", "filled_quantity"), 0.0)
    fill_price = _to_float(_order_field(order, "filled_avg_price", "avg_fill_price"), 0.0)
    if filled_qty > 0.0 and fill_price > 0.0:
        return filled_qty * fill_price
    notional = _to_float(_order_field(order, "filled_notional", "notional"), 0.0)
    return max(0.0, notional)


@dataclass(frozen=True)
class SettledCashResult:
    """Result of a stateless settled-cash recompute.

    ``buffered_settled_cash`` is the value the buy clamp should treat as the settled
    ceiling: ``settled_cash * buy_buffer_pct``. When ``fail_closed`` is True the
    settled ceiling is 0 (buys blocked).
    """

    settled_cash: float
    broker_cash: float
    unsettled_proceeds: float
    buffered_settled_cash: float
    buy_buffer_pct: float
    as_of_date: str
    fail_closed: bool
    reason_code: str | None
    unsettled_orders: tuple[dict[str, Any], ...] = ()
    orders_considered: int = 0

    def to_report(self) -> dict[str, Any]:
        return {
            "schema_version": "live_pilot_settled_cash.v1",
            "as_of_date": self.as_of_date,
            "broker_cash": round(float(self.broker_cash), 6),
            "unsettled_proceeds": round(float(self.unsettled_proceeds), 6),
            "settled_cash": round(float(self.settled_cash), 6),
            "buy_buffer_pct": float(self.buy_buffer_pct),
            "buffered_settled_cash": round(float(self.buffered_settled_cash), 6),
            "fail_closed": bool(self.fail_closed),
            "reason_code": self.reason_code,
            "orders_considered": int(self.orders_considered),
            "unsettled_orders": [dict(row) for row in self.unsettled_orders],
        }


def unsettled_proceeds(
    orders: Iterable[Mapping[str, Any]],
    as_of_date: str,
) -> tuple[float, list[dict[str, Any]]]:
    """Sum of FILLED sell proceeds not yet settled as of ``as_of_date``.

    An order's proceeds are unsettled iff ``settlement_date(fill_date) > as_of_date``.
    Returns ``(total, breakdown)``. Only the filled portion of partial fills counts.
    """
    total = 0.0
    breakdown: list[dict[str, Any]] = []
    for order in orders or ():
        if not isinstance(order, Mapping):
            continue
        if _side(order) not in _SELL_SIDES:
            continue
        if _status(order) not in _FILLED_STATUSES:
            continue
        fill_date = _fill_date(order)
        if not fill_date:
            continue
        settle = settlement_date(fill_date)
        if settle <= str(as_of_date):
            continue  # already settled
        proceeds = _filled_sell_proceeds(order)
        if proceeds <= 0.0:
            continue
        total += proceeds
        breakdown.append(
            {
                "symbol": str(_order_field(order, "symbol", "ticker") or "").upper(),
                "order_id": str(_order_field(order, "id", "order_id") or ""),
                "fill_date": fill_date,
                "settlement_date": settle,
                "proceeds": round(proceeds, 6),
            }
        )
    return total, breakdown


def compute_settled_cash(
    *,
    broker_cash: Any,
    orders: Sequence[Mapping[str, Any]] | None,
    as_of_date: str,
    buy_buffer_pct: float = 1.0,
    orders_available: bool = True,
    confirmed_sells: Sequence[Mapping[str, Any]] = (),
) -> SettledCashResult:
    """Stateless settled-cash recompute from broker truth.

    ``orders`` is the recent order history (status=all/closed). Pass ``None`` (or
    ``orders_available=False``) when the history could not be retrieved: the result
    fails closed (settled_cash = 0, buys blocked).

    ``confirmed_sells`` is the freshness cross-check: sells that THIS RUN's per-order
    polling (``get_order``) already confirmed as filled, as rows of
    ``{"order_id", "proceeds", "symbol"}``. The bulk ``orders`` listing can lag those
    per-order reads; a just-filled sell missing (or under-filled) in the bulk payload
    would otherwise count as $0 unsettled while broker cash ALREADY includes its
    proceeds — silently defeating the clamp. Any confirmed proceeds not accounted for
    in the bulk-derived unsettled total are injected as unsettled here.
    """
    cash = max(0.0, _to_float(broker_cash, 0.0))
    buffer = float(buy_buffer_pct) if buy_buffer_pct is not None else 1.0
    if buffer < 0.0:
        buffer = 0.0

    if orders is None or not orders_available:
        # FAIL CLOSED: assume the entire cash balance is unsettled sale proceeds.
        return SettledCashResult(
            settled_cash=0.0,
            broker_cash=cash,
            unsettled_proceeds=cash,
            buffered_settled_cash=0.0,
            buy_buffer_pct=buffer,
            as_of_date=str(as_of_date),
            fail_closed=True,
            reason_code=GFV_SETTLED_CASH_UNAVAILABLE,
            unsettled_orders=(),
            orders_considered=0,
        )

    unsettled, breakdown = unsettled_proceeds(orders, as_of_date)

    # Freshness cross-check (same-run confirmed fills vs the bulk listing). A sell
    # this run confirmed FILLED settles strictly after today, so its proceeds must
    # appear in ``unsettled``. Credit whatever the bulk listing already counted for
    # that order id and inject only the shortfall (covers both a missing order and a
    # stale partial-fill snapshot).
    counted_by_id: dict[str, float] = {}
    for row in breakdown:
        oid = str(row.get("order_id") or "")
        if oid:
            counted_by_id[oid] = counted_by_id.get(oid, 0.0) + float(row.get("proceeds") or 0.0)
    for sell in confirmed_sells or ():
        if not isinstance(sell, Mapping):
            continue
        proceeds = _to_float(sell.get("proceeds"), 0.0)
        if proceeds <= 0.0:
            continue
        oid = str(sell.get("order_id") or "")
        shortfall = proceeds - counted_by_id.get(oid, 0.0)
        if shortfall <= 1e-9:
            continue
        unsettled += shortfall
        breakdown.append(
            {
                "symbol": str(sell.get("symbol") or "").upper(),
                "order_id": oid,
                "fill_date": str(as_of_date),
                "settlement_date": settlement_date(str(as_of_date)),
                "proceeds": round(shortfall, 6),
                "source": "confirmed_fill_missing_from_bulk_history",
            }
        )

    settled = max(0.0, cash - unsettled)
    return SettledCashResult(
        settled_cash=settled,
        broker_cash=cash,
        unsettled_proceeds=unsettled,
        buffered_settled_cash=settled * buffer,
        buy_buffer_pct=buffer,
        as_of_date=str(as_of_date),
        fail_closed=False,
        reason_code=None,
        unsettled_orders=tuple(breakdown),
        orders_considered=int(len(list(orders))),
    )


def detect_gfv_risky_sells(
    *,
    planned_sell_symbols: Iterable[str],
    orders: Sequence[Mapping[str, Any]] | None,
    as_of_date: str,
) -> list[dict[str, Any]]:
    """Belt-and-suspenders (defense #3): flag sells of not-yet-settled acquisitions.

    A planned SELL of ``S`` is flagged when order history shows an acquiring BUY of
    ``S`` whose own T+1 settlement has NOT elapsed as of ``as_of_date``
    (``settlement_date(buy_fill) > as_of_date``) — i.e. we would be selling a lot that
    was purchased so recently it could only have been paid for with unsettled proceeds.
    This is the canonical GFV shape.

    With the buy clamp (defense #2) in place this NEVER fires under normal daily
    rotation: next-morning sells are always of lots that are already >= T+1 old
    (a Monday buy settles Tuesday, and Tuesday's sell sees settlement_date == as_of,
    which is not ``> as_of``). It exists so a future regression in the buy clamp cannot
    silently create GFVs — a hit is a loud, blocking alert.

    Returns a list of alert rows (empty when nothing is risky). No positive order
    history => no evidence => no flag (the buy-side fail-closed already blocks new
    exposure).
    """
    symbols = {str(s).strip().upper() for s in (planned_sell_symbols or ()) if str(s).strip()}
    if not symbols or not orders:
        return []

    # Most-recent filled BUY fill date per symbol.
    latest_buy: dict[str, str] = {}
    for order in orders:
        if not isinstance(order, Mapping):
            continue
        if _side(order) not in _BUY_SIDES:
            continue
        if _status(order) not in _FILLED_STATUSES:
            continue
        symbol = str(_order_field(order, "symbol", "ticker") or "").upper()
        if symbol not in symbols:
            continue
        fill_date = _fill_date(order)
        if not fill_date:
            continue
        if symbol not in latest_buy or fill_date > latest_buy[symbol]:
            latest_buy[symbol] = fill_date

    alerts: list[dict[str, Any]] = []
    for symbol in sorted(symbols):
        buy_fill = latest_buy.get(symbol)
        if not buy_fill:
            continue
        settle = settlement_date(buy_fill)
        if settle > str(as_of_date):
            alerts.append(
                {
                    "symbol": symbol,
                    "acquiring_buy_fill_date": buy_fill,
                    "acquiring_buy_settlement_date": settle,
                    "as_of_date": str(as_of_date),
                    "reason_code": GFV_SELL_OF_UNSETTLED_ACQUISITION,
                }
            )
    return alerts
