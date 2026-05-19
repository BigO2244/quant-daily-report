from __future__ import annotations

from dataclasses import dataclass

from core.recovery.interrupted_state import IntendedOrder


@dataclass(frozen=True)
class RecoveryDeltaOrder:
    symbol: str
    side: str
    qty: float
    planned_notional: float | None = None
    reason: str | None = None


def target_positions_from_intent(
    *,
    pretrade_positions: dict[str, float],
    intended_orders: list[IntendedOrder],
) -> dict[str, float]:
    target = {str(symbol).upper(): float(qty) for symbol, qty in pretrade_positions.items()}
    for order in intended_orders:
        symbol = order.symbol.upper()
        if order.normalized_side == "BUY":
            target[symbol] = target.get(symbol, 0.0) + float(order.qty)
        elif order.normalized_side == "SELL":
            target[symbol] = target.get(symbol, 0.0) - float(order.qty)
        else:
            raise ValueError(f"unsupported intended order side: {order.side!r}")
        if abs(target[symbol]) <= 1e-9:
            target.pop(symbol, None)
    return dict(sorted(target.items()))


def compute_recovery_delta(
    *,
    current_positions: dict[str, float],
    target_positions: dict[str, float],
    intended_orders: list[IntendedOrder],
    buy_only: bool = True,
) -> list[RecoveryDeltaOrder]:
    intended_lookup = {
        (order.symbol.upper(), order.normalized_side): order
        for order in intended_orders
    }
    deltas: list[RecoveryDeltaOrder] = []
    for symbol in sorted(set(current_positions) | set(target_positions)):
        current_qty = float(current_positions.get(symbol, 0.0))
        target_qty = float(target_positions.get(symbol, 0.0))
        delta_qty = target_qty - current_qty
        if abs(delta_qty) <= 1e-9:
            continue
        side = "BUY" if delta_qty > 0 else "SELL"
        if buy_only and side != "BUY":
            continue
        intended = intended_lookup.get((symbol, side))
        deltas.append(
            RecoveryDeltaOrder(
                symbol=symbol,
                side=side,
                qty=abs(delta_qty),
                planned_notional=intended.planned_notional if intended else None,
                reason=intended.reason if intended else "normalization_delta",
            )
        )
    return deltas


def position_drift_rows(
    *,
    current_positions: dict[str, float],
    target_positions: dict[str, float],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for symbol in sorted(set(current_positions) | set(target_positions)):
        current_qty = float(current_positions.get(symbol, 0.0))
        target_qty = float(target_positions.get(symbol, 0.0))
        delta_qty = target_qty - current_qty
        if abs(delta_qty) <= 1e-9:
            classification = "MATCH"
        elif delta_qty > 0:
            classification = "UNDER_TARGET"
        else:
            classification = "OVER_TARGET"
        rows.append(
            {
                "symbol": symbol,
                "current_qty": current_qty,
                "target_qty": target_qty,
                "delta_qty": delta_qty,
                "classification": classification,
            }
        )
    return rows

