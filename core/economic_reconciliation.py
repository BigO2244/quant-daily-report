"""Canonical economic-truth and sleeve-attribution verification contracts.

The verifier is pure and broker-independent.  It proves two independent
identities from explicit inputs:

* starting positions/cash + fills = ending positions/cash;
* ending positions x marks + ending cash = broker/canonical NAV.

Sleeve attribution is reconciled separately by trade date so reporting cannot
silently create an economically independent portfolio result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ECONOMIC_SCHEMA_VERSION = "caerus.economic_reconciliation.v1"
ATTRIBUTION_SCHEMA_VERSION = "caerus.sleeve_attribution_reconciliation.v1"


class ReconciliationStatus(str, Enum):
    RECONCILED = "RECONCILED"
    FAILED_RECONCILIATION = "FAILED_RECONCILIATION"


@dataclass(frozen=True)
class EconomicTolerance:
    quantity_abs: float = 1e-8
    cash_abs: float = 0.01
    position_value_abs: float = 0.01
    nav_abs: float = 0.01
    attribution_abs: float = 0.01

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if not _finite(value) or float(value) < 0:
                raise ValueError(f"{name} must be a finite non-negative number")


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: str
    quantity: float
    price: float
    fees: float = 0.0
    sleeve: str = ""
    order_id: str = ""

    def __post_init__(self) -> None:
        if not _symbol(self.symbol):
            raise ValueError("fill symbol is required")
        if str(self.side or "").strip().upper() not in {"BUY", "SELL"}:
            raise ValueError(f"invalid fill side: {self.side!r}")
        if not _finite(self.quantity) or float(self.quantity) <= 0:
            raise ValueError("fill quantity must be finite and positive")
        if not _finite(self.price) or float(self.price) < 0:
            raise ValueError("fill price must be finite and non-negative")
        if not _finite(self.fees) or float(self.fees) < 0:
            raise ValueError("fill fees must be finite and non-negative")


@dataclass(frozen=True)
class MarkedPosition:
    symbol: str
    quantity: float
    mark: float
    broker_market_value: float | None = None

    def __post_init__(self) -> None:
        if not _symbol(self.symbol):
            raise ValueError("position symbol is required")
        if not _finite(self.quantity):
            raise ValueError("position quantity must be finite")
        if not _finite(self.mark) or float(self.mark) < 0:
            raise ValueError("position mark must be finite and non-negative")
        if self.broker_market_value is not None and not _finite(self.broker_market_value):
            raise ValueError("broker market value must be finite")


@dataclass(frozen=True)
class EconomicReconciliation:
    trade_date: str
    status: ReconciliationStatus
    reason_codes: tuple[str, ...]
    expected_ending_positions: Mapping[str, float]
    actual_ending_positions: Mapping[str, float]
    position_quantity_deltas: Mapping[str, float]
    expected_ending_cash: float
    actual_ending_cash: float
    cash_delta: float
    marked_position_value: float
    broker_position_value: float | None
    position_value_delta: float | None
    calculated_nav: float
    broker_equity: float
    nav_delta: float
    fill_notional_buys: float
    fill_notional_sells: float
    fill_fees: float
    tolerance: EconomicTolerance

    @property
    def reconciled(self) -> bool:
        return self.status is ReconciliationStatus.RECONCILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ECONOMIC_SCHEMA_VERSION,
            "trade_date": self.trade_date,
            "status": self.status.value,
            "reconciled": self.reconciled,
            "reason_codes": list(self.reason_codes),
            "positions": {
                "expected": dict(self.expected_ending_positions),
                "actual": dict(self.actual_ending_positions),
                "quantity_deltas": dict(self.position_quantity_deltas),
            },
            "cash": {
                "expected": self.expected_ending_cash,
                "actual": self.actual_ending_cash,
                "delta": self.cash_delta,
            },
            "fills": {
                "buy_notional": self.fill_notional_buys,
                "sell_notional": self.fill_notional_sells,
                "fees": self.fill_fees,
            },
            "marks": {
                "calculated_position_value": self.marked_position_value,
                "broker_position_value": self.broker_position_value,
                "delta": self.position_value_delta,
            },
            "nav": {
                "calculated": self.calculated_nav,
                "broker_equity": self.broker_equity,
                "delta": self.nav_delta,
            },
            "tolerance": asdict(self.tolerance),
        }


@dataclass(frozen=True)
class SleeveAttributionRow:
    trade_date: str
    sleeve: str
    result_dollars: float
    source_artifact: str = ""

    def __post_init__(self) -> None:
        if not str(self.trade_date or "").strip():
            raise ValueError("attribution trade_date is required")
        if not str(self.sleeve or "").strip():
            raise ValueError("attribution sleeve is required")
        if not _finite(self.result_dollars):
            raise ValueError("attribution result must be finite")


@dataclass(frozen=True)
class SleeveAttributionReconciliation:
    trade_date: str
    status: ReconciliationStatus
    reason_codes: tuple[str, ...]
    sleeve_results: Mapping[str, float]
    attributed_result: float
    portfolio_result: float
    attribution_delta: float
    tolerance: float
    source_artifacts: tuple[str, ...]

    @property
    def reconciled(self) -> bool:
        return self.status is ReconciliationStatus.RECONCILED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ATTRIBUTION_SCHEMA_VERSION,
            "trade_date": self.trade_date,
            "status": self.status.value,
            "reconciled": self.reconciled,
            "reason_codes": list(self.reason_codes),
            "sleeve_results": dict(self.sleeve_results),
            "attributed_result": self.attributed_result,
            "portfolio_result": self.portfolio_result,
            "attribution_delta": self.attribution_delta,
            "tolerance": self.tolerance,
            "source_artifacts": list(self.source_artifacts),
        }


@dataclass(frozen=True)
class CanonicalEconomicVerification:
    """Joint verdict: broker economic truth and attribution must both pass."""

    trade_date: str
    status: ReconciliationStatus
    economic_reconciliation: EconomicReconciliation
    sleeve_attribution_reconciliation: SleeveAttributionReconciliation

    @property
    def reconciled(self) -> bool:
        return self.status is ReconciliationStatus.RECONCILED

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": "caerus.canonical_economic_verification.v1",
            "trade_date": self.trade_date,
            "status": self.status.value,
            "reconciled": self.reconciled,
            "economic_reconciliation": self.economic_reconciliation.to_dict(),
            "sleeve_attribution_reconciliation": (
                self.sleeve_attribution_reconciliation.to_dict()
            ),
        }
        payload["content_hash"] = hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return payload


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _symbol(value: str) -> str:
    return str(value or "").strip().upper()


def _normalized_positions(positions: Mapping[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for raw_symbol, raw_quantity in positions.items():
        symbol = _symbol(raw_symbol)
        if not symbol:
            raise ValueError("position symbol is required")
        if not _finite(raw_quantity):
            raise ValueError(f"position quantity must be finite: {symbol}")
        normalized[symbol] = normalized.get(symbol, 0.0) + float(raw_quantity)
    return normalized


def reconcile_economic_truth(
    *,
    trade_date: str,
    starting_cash: float,
    starting_positions: Mapping[str, float],
    fills: Sequence[Fill],
    ending_cash: float,
    ending_positions: Sequence[MarkedPosition],
    broker_equity: float,
    broker_position_value: float | None = None,
    tolerance: EconomicTolerance | None = None,
) -> EconomicReconciliation:
    """Verify position, cash, mark, and NAV identities from broker evidence."""

    if not str(trade_date or "").strip():
        raise ValueError("trade_date is required")
    for name, value in {
        "starting_cash": starting_cash,
        "ending_cash": ending_cash,
        "broker_equity": broker_equity,
    }.items():
        if not _finite(value):
            raise ValueError(f"{name} must be finite")
    if broker_position_value is not None and not _finite(broker_position_value):
        raise ValueError("broker_position_value must be finite")
    limits = tolerance or EconomicTolerance()

    expected_positions = _normalized_positions(starting_positions)
    buy_notional = 0.0
    sell_notional = 0.0
    fees = 0.0
    for fill in fills:
        symbol = _symbol(fill.symbol)
        quantity = float(fill.quantity)
        notional = quantity * float(fill.price)
        if fill.side.strip().upper() == "BUY":
            expected_positions[symbol] = expected_positions.get(symbol, 0.0) + quantity
            buy_notional += notional
        else:
            expected_positions[symbol] = expected_positions.get(symbol, 0.0) - quantity
            sell_notional += notional
        fees += float(fill.fees)
    expected_positions = {
        symbol: quantity
        for symbol, quantity in expected_positions.items()
        if abs(quantity) > limits.quantity_abs
    }

    actual_positions: dict[str, float] = {}
    marks_by_symbol: dict[str, MarkedPosition] = {}
    duplicate_symbols: set[str] = set()
    for position in ending_positions:
        symbol = _symbol(position.symbol)
        if symbol in marks_by_symbol:
            duplicate_symbols.add(symbol)
        marks_by_symbol[symbol] = position
        actual_positions[symbol] = actual_positions.get(symbol, 0.0) + float(position.quantity)
    actual_positions = {
        symbol: quantity
        for symbol, quantity in actual_positions.items()
        if abs(quantity) > limits.quantity_abs
    }

    all_symbols = sorted(set(expected_positions) | set(actual_positions))
    quantity_deltas = {
        symbol: actual_positions.get(symbol, 0.0) - expected_positions.get(symbol, 0.0)
        for symbol in all_symbols
    }
    quantity_deltas = {
        symbol: delta
        for symbol, delta in quantity_deltas.items()
        if abs(delta) > limits.quantity_abs
    }

    expected_cash = float(starting_cash) - buy_notional + sell_notional - fees
    cash_delta = float(ending_cash) - expected_cash

    marked_value = sum(
        float(position.quantity) * float(position.mark)
        for position in ending_positions
    )
    provided_position_delta = (
        marked_value - float(broker_position_value)
        if broker_position_value is not None
        else None
    )
    per_position_mark_mismatches = [
        symbol
        for symbol, position in marks_by_symbol.items()
        if position.broker_market_value is not None
        and abs(
            float(position.quantity) * float(position.mark)
            - float(position.broker_market_value)
        ) > limits.position_value_abs
    ]

    calculated_nav = float(ending_cash) + marked_value
    nav_delta = calculated_nav - float(broker_equity)

    reasons: list[str] = []
    if duplicate_symbols:
        reasons.append("DUPLICATE_ENDING_POSITION")
    if any(quantity < -limits.quantity_abs for quantity in actual_positions.values()):
        reasons.append("UNEXPECTED_SHORT_POSITION")
    if quantity_deltas:
        reasons.append("POSITION_FROM_FILLS_MISMATCH")
    if abs(cash_delta) > limits.cash_abs:
        reasons.append("CASH_FROM_FILLS_MISMATCH")
    if per_position_mark_mismatches:
        reasons.append("POSITION_MARK_MISMATCH")
    if provided_position_delta is not None and abs(provided_position_delta) > limits.position_value_abs:
        reasons.append("BROKER_POSITION_VALUE_MISMATCH")
    if abs(nav_delta) > limits.nav_abs:
        reasons.append("BROKER_EQUITY_MISMATCH")

    return EconomicReconciliation(
        trade_date=str(trade_date),
        status=(
            ReconciliationStatus.FAILED_RECONCILIATION
            if reasons
            else ReconciliationStatus.RECONCILED
        ),
        reason_codes=tuple(reasons or ["ECONOMIC_TRUTH_RECONCILED"]),
        expected_ending_positions=dict(sorted(expected_positions.items())),
        actual_ending_positions=dict(sorted(actual_positions.items())),
        position_quantity_deltas=dict(sorted(quantity_deltas.items())),
        expected_ending_cash=expected_cash,
        actual_ending_cash=float(ending_cash),
        cash_delta=cash_delta,
        marked_position_value=marked_value,
        broker_position_value=(float(broker_position_value) if broker_position_value is not None else None),
        position_value_delta=provided_position_delta,
        calculated_nav=calculated_nav,
        broker_equity=float(broker_equity),
        nav_delta=nav_delta,
        fill_notional_buys=buy_notional,
        fill_notional_sells=sell_notional,
        fill_fees=fees,
        tolerance=limits,
    )


_UNKNOWN_SLEEVES = {"UNKNOWN", "UNATTRIBUTED", "UNASSIGNED", "NONE", "N/A"}


def reconcile_sleeve_attribution(
    *,
    trade_date: str,
    portfolio_result: float,
    rows: Iterable[SleeveAttributionRow],
    tolerance: float = 0.01,
) -> SleeveAttributionReconciliation:
    """Prove that date+sleeve attribution sums to the portfolio result."""

    normalized_date = str(trade_date or "").strip()
    if not normalized_date:
        raise ValueError("trade_date is required")
    if not _finite(portfolio_result):
        raise ValueError("portfolio_result must be finite")
    if not _finite(tolerance) or float(tolerance) < 0:
        raise ValueError("tolerance must be finite and non-negative")

    sleeve_results: dict[str, float] = {}
    sources: set[str] = set()
    wrong_dates: set[str] = set()
    unknown_sleeves: set[str] = set()
    row_count = 0
    for row in rows:
        row_count += 1
        row_date = str(row.trade_date).strip()
        sleeve = str(row.sleeve).strip()
        if row_date != normalized_date:
            wrong_dates.add(row_date)
        if sleeve.upper() in _UNKNOWN_SLEEVES:
            unknown_sleeves.add(sleeve)
        sleeve_results[sleeve] = sleeve_results.get(sleeve, 0.0) + float(row.result_dollars)
        if str(row.source_artifact or "").strip():
            sources.add(str(row.source_artifact).strip())

    attributed = sum(sleeve_results.values())
    delta = attributed - float(portfolio_result)
    reasons: list[str] = []
    if row_count == 0:
        reasons.append("ATTRIBUTION_MISSING")
    if wrong_dates:
        reasons.append("ATTRIBUTION_TRADE_DATE_MISMATCH")
    if unknown_sleeves:
        reasons.append("UNATTRIBUTED_RESULT_PRESENT")
    if abs(delta) > float(tolerance):
        reasons.append("SLEEVE_SUM_PORTFOLIO_MISMATCH")

    return SleeveAttributionReconciliation(
        trade_date=normalized_date,
        status=(
            ReconciliationStatus.FAILED_RECONCILIATION
            if reasons
            else ReconciliationStatus.RECONCILED
        ),
        reason_codes=tuple(reasons or ["SLEEVE_ATTRIBUTION_RECONCILED"]),
        sleeve_results=dict(sorted(sleeve_results.items())),
        attributed_result=attributed,
        portfolio_result=float(portfolio_result),
        attribution_delta=delta,
        tolerance=float(tolerance),
        source_artifacts=tuple(sorted(sources)),
    )


def verify_canonical_economics(
    *,
    economic_reconciliation: EconomicReconciliation,
    sleeve_attribution_reconciliation: SleeveAttributionReconciliation,
) -> CanonicalEconomicVerification:
    """Combine both contracts without allowing either verdict to be hidden."""

    if economic_reconciliation.trade_date != sleeve_attribution_reconciliation.trade_date:
        raise ValueError("economic and attribution trade dates must match")
    reconciled = (
        economic_reconciliation.reconciled
        and sleeve_attribution_reconciliation.reconciled
    )
    return CanonicalEconomicVerification(
        trade_date=economic_reconciliation.trade_date,
        status=(
            ReconciliationStatus.RECONCILED
            if reconciled
            else ReconciliationStatus.FAILED_RECONCILIATION
        ),
        economic_reconciliation=economic_reconciliation,
        sleeve_attribution_reconciliation=sleeve_attribution_reconciliation,
    )


def verify_canonical_economic_artifact_hash(payload: Mapping[str, Any]) -> bool:
    expected = str(payload.get("content_hash") or "")
    unhashed = dict(payload)
    unhashed.pop("content_hash", None)
    actual = hashlib.sha256(
        json.dumps(
            unhashed,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return bool(expected) and expected == actual


def write_canonical_economic_verification(
    path: Path | str,
    verification: CanonicalEconomicVerification,
) -> Path:
    """Persist one immutable, content-hashed combined economic verdict."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = verification.to_dict()
    try:
        with target.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        raise FileExistsError(
            f"canonical economic verification is immutable: {target}"
        ) from None
    return target
