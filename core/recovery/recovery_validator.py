from __future__ import annotations

from dataclasses import dataclass, field

from core.recovery.interrupted_state import BrokerState, OrderState
from core.recovery.recovery_delta import RecoveryDeltaOrder


@dataclass(frozen=True)
class RecoveryValidationResult:
    ok: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _recovery_client_id(symbol: str, trade_date: str, recovery_id: str) -> str:
    return f"{trade_date}:{recovery_id}:BUY_ONLY_NORMALIZATION:{symbol.upper()}:BUY"


def validate_recovery_candidate(
    *,
    broker_state: BrokerState,
    recovery_delta: list[RecoveryDeltaOrder],
    target_positions: dict[str, float],
    trade_date: str,
    recovery_id: str = "recovery_01",
    buy_only: bool = True,
    stale_execution_lock_present: bool = False,
    min_cash_after: float = 100.0,
) -> RecoveryValidationResult:
    failures: list[str] = []
    warnings: list[str] = []

    account_status = (broker_state.account_status or "").strip().upper().replace("ACCOUNTSTATUS.", "")
    if account_status != "ACTIVE":
        failures.append("account_not_active")
    if broker_state.trading_blocked:
        failures.append("account_trading_blocked")
    if broker_state.open_orders_count > 0:
        failures.append("open_orders_present")
    if stale_execution_lock_present:
        warnings.append("stale_execution_lock_present_do_not_reuse")

    existing_client_ids = {order.client_order_id for order in broker_state.orders}
    for order in recovery_delta:
        if buy_only and order.side.upper() != "BUY":
            failures.append(f"illegal_recovery_sell:{order.symbol}")
        current_qty = float(broker_state.positions.get(order.symbol.upper(), 0.0))
        target_qty = float(target_positions.get(order.symbol.upper(), 0.0))
        if order.side.upper() == "BUY" and current_qty >= target_qty:
            failures.append(f"buy_symbol_already_at_or_above_target:{order.symbol}")
        client_id = _recovery_client_id(order.symbol, trade_date, recovery_id)
        if client_id in existing_client_ids:
            failures.append(f"duplicate_recovery_client_order_id:{client_id}")

    planned_buy_notional = sum(
        float(order.planned_notional or 0.0)
        for order in recovery_delta
        if order.side.upper() == "BUY"
    )
    if broker_state.cash is not None and broker_state.cash - planned_buy_notional < min_cash_after:
        failures.append("insufficient_cash_after_recovery_reserve")

    return RecoveryValidationResult(ok=not failures, failures=failures, warnings=warnings)


def assert_prior_sells_terminal_filled(
    *,
    orders: list[OrderState],
    expected_sell_client_ids: set[str],
) -> RecoveryValidationResult:
    failures: list[str] = []
    by_client_id = {order.client_order_id: order for order in orders}
    for client_id in sorted(expected_sell_client_ids):
        order = by_client_id.get(client_id)
        if order is None:
            failures.append(f"prior_sell_missing:{client_id}")
            continue
        if not order.is_filled:
            failures.append(f"prior_sell_not_terminal_filled:{client_id}:{order.normalized_status}")
    return RecoveryValidationResult(ok=not failures, failures=failures)

