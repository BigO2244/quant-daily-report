from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ExecutionLifecycleState(str, Enum):
    NORMAL_EXECUTION = "NORMAL_EXECUTION"
    PARTIAL_EXECUTION = "PARTIAL_EXECUTION"
    SELL_PHASE_TIMEOUT = "SELL_PHASE_TIMEOUT"
    SETTLEMENT_PENDING = "SETTLEMENT_PENDING"
    RECOVERY_CANDIDATE = "RECOVERY_CANDIDATE"
    RECOVERY_SIMULATED = "RECOVERY_SIMULATED"
    RECOVERY_APPROVED = "RECOVERY_APPROVED"
    RECOVERY_EXECUTED = "RECOVERY_EXECUTED"
    RECOVERY_RECONCILED = "RECOVERY_RECONCILED"


@dataclass(frozen=True)
class OrderState:
    client_order_id: str
    symbol: str
    side: str
    qty: float
    filled_qty: float = 0.0
    status: str = ""
    filled_avg_price: float | None = None

    @property
    def normalized_side(self) -> str:
        return self.side.strip().upper()

    @property
    def normalized_status(self) -> str:
        return self.status.strip().lower().replace("orderstatus.", "")

    @property
    def is_terminal(self) -> bool:
        return self.normalized_status in {
            "filled",
            "canceled",
            "expired",
            "rejected",
            "failed",
            "done_for_day",
        }

    @property
    def is_filled(self) -> bool:
        return self.normalized_status == "filled" and abs(self.filled_qty - self.qty) <= 1e-9


@dataclass(frozen=True)
class PositionState:
    symbol: str
    qty: float
    market_value: float | None = None
    current_price: float | None = None


@dataclass(frozen=True)
class BrokerState:
    captured_at: str | None = None
    account_status: str | None = None
    trading_blocked: bool = False
    cash: float | None = None
    equity: float | None = None
    buying_power: float | None = None
    positions: dict[str, float] = field(default_factory=dict)
    orders: list[OrderState] = field(default_factory=list)
    open_orders_count: int = 0


@dataclass(frozen=True)
class IntendedOrder:
    symbol: str
    side: str
    qty: float
    planned_notional: float | None = None
    reason: str | None = None

    @property
    def normalized_side(self) -> str:
        return self.side.strip().upper()


@dataclass(frozen=True)
class InterruptedRunSnapshot:
    source_run_id: str
    trade_date: str
    execution_status: str | None
    execution_outcome: str | None
    halt_reason: str | None
    submitted_count: int = 0
    accepted_count: int = 0
    intended_orders: list[IntendedOrder] = field(default_factory=list)
    pretrade_positions: dict[str, float] = field(default_factory=dict)
    current_broker_state: BrokerState = field(default_factory=BrokerState)
    execution_lock_present: bool = False
    posttrade_reconciliation_status: str | None = None


def coerce_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except Exception:
        return default


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()

