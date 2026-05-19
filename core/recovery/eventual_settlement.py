from __future__ import annotations

from dataclasses import dataclass

from core.recovery.interrupted_state import OrderState


@dataclass(frozen=True)
class SettlementAssessment:
    status: str
    confidence: str
    reasons: list[str]
    pending_orders: list[str]
    terminal_orders: list[str]

    def to_artifact(self) -> dict[str, object]:
        return {
            "status": self.status,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "pending_orders": self.pending_orders,
            "terminal_orders": self.terminal_orders,
        }


def assess_eventual_settlement(
    *,
    observed_orders: list[OrderState],
    reconciliation_passed: bool,
    expected_sell_client_ids: set[str] | None = None,
) -> SettlementAssessment:
    expected = set(expected_sell_client_ids or [])
    relevant = [
        order for order in observed_orders if not expected or order.client_order_id in expected
    ]
    pending = [order.client_order_id for order in relevant if not order.is_terminal]
    terminal = [order.client_order_id for order in relevant if order.is_terminal]
    reasons: list[str] = []

    if pending:
        reasons.append("non_terminal_orders_observed")
        return SettlementAssessment("PENDING_TERMINALITY", "LOW", reasons, pending, terminal)

    missing = sorted(expected - {order.client_order_id for order in relevant})
    if missing:
        reasons.append("expected_orders_missing_from_observation")
        return SettlementAssessment("OBSERVED_STATE_INCOMPLETE", "LOW", reasons + missing, pending, terminal)

    if reconciliation_passed:
        reasons.append("all_observed_orders_terminal_and_reconciliation_passed")
        return SettlementAssessment("EVENTUALLY_RECONCILED", "HIGH", reasons, pending, terminal)

    reasons.append("terminal_orders_observed_but_reconciliation_not_passed")
    return SettlementAssessment("DELAYED_CONVERGENCE", "MODERATE", reasons, pending, terminal)

