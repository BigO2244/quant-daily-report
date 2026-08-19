"""Deterministic lane valuation from the append-only accounting journal.

This module is an in-memory Stage 12 foundation.  It never reads broker state,
runtime configuration, or files.  A caller must provide both an immutable
journal history for exactly one deployment and a hash-bound valuation-state
snapshot for exactly one as-of.

PAPER and LIVE valuations accept only ``BROKER_RECONCILED`` state with a
terminal ``PASS`` status.  SHADOW accepts only a distinct theoretical state
schema.  The separation is structural so modeled prices cannot be relabeled as
factual performance by changing a display label.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from typing import Any, Iterable, Mapping, Sequence

from core.accounting_journal import (
    LEGACY_UNATTRIBUTED,
    AccountingJournalError,
    canonical_json,
    validate_accounting_journal,
)


LANE_VALUATION_SCHEMA = "caerus.lane_valuation.v1"
RECONCILED_LANE_STATE_SCHEMA = "caerus.reconciled_lane_state.v1"
THEORETICAL_LANE_STATE_SCHEMA = "caerus.theoretical_lane_state.v1"

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.00000001")

_STATE_FIELDS = frozenset(
    {
        "schema_version",
        "status",
        "as_of",
        "valuation_date",
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "performance_surface",
        "economic_authority",
        "cash",
        "equity",
        "positions",
        "journal_hash",
        "source_hash",
        "content_hash",
    }
)
_STATE_POSITION_FIELDS = frozenset(
    {"symbol", "quantity", "price", "market_value", "source_hash"}
)
_VALUATION_FIELDS = frozenset(
    {
        "schema_version",
        "valuation_id",
        "valuation_date",
        "as_of",
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "performance_surface",
        "economic_authority",
        "valuation_basis",
        "reconciliation_status",
        "causal_start",
        "journal_entry_count",
        "journal_hash",
        "state_hash",
        "source_hashes",
        "sleeves",
        "legacy_unattributed",
        "lane_cash",
        "lane_positions_market_value",
        "lane_nav",
        "cumulative_external_flow",
        "cumulative_fee_amount",
        "proof",
        "content_hash",
    }
)
_SLEEVE_FIELDS = frozenset(
    {
        "sleeve_id",
        "cash",
        "positions_market_value",
        "nav",
        "positions",
        "causal_start",
        "cumulative_flow",
        "cumulative_fee_amount",
    }
)
_VALUED_POSITION_FIELDS = frozenset(
    {"symbol", "quantity", "price", "market_value", "price_source_hash"}
)
_PROOF_FIELDS = frozenset(
    {
        "status",
        "tolerance",
        "attributed_sleeve_nav_sum",
        "legacy_unattributed_nav",
        "decomposed_nav",
        "state_equity",
        "difference",
    }
)


class LaneValuationError(ValueError):
    """Raised when one lane valuation cannot be proven from its inputs."""


def _strict_fields(payload: Mapping[str, Any], expected: frozenset[str], *, label: str) -> None:
    missing = sorted(expected - set(payload))
    unknown = sorted(set(payload) - expected)
    if missing or unknown:
        raise LaneValuationError(
            f"{label} fields mismatch; missing={missing}, unknown={unknown}"
        )


def _strict_string(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise LaneValuationError(
            f"{label} must be a non-blank string without surrounding whitespace"
        )
    return value


def _safe_id(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SAFE_ID.fullmatch(result) or ".." in result:
        raise LaneValuationError(f"{label} is invalid")
    return result


def _sha256(value: Any, *, label: str) -> str:
    result = _strict_string(value, label=label)
    if not _SHA256.fullmatch(result):
        raise LaneValuationError(f"{label} must be a lowercase SHA-256 digest")
    return result


def _decimal(value: Any, *, label: str, nonnegative: bool = False) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise LaneValuationError(f"{label} must be a finite number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise LaneValuationError(f"{label} must be a finite number") from exc
    if not result.is_finite() or (nonnegative and result < _ZERO):
        raise LaneValuationError(f"{label} must be finite")
    return result


def _number(value: Decimal) -> float:
    result = float(value)
    return 0.0 if result == 0.0 else result


def _timestamp(value: Any, *, label: str) -> dt.datetime:
    raw = _strict_string(value, label=label)
    try:
        result = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LaneValuationError(f"{label} must be an ISO-8601 timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise LaneValuationError(f"{label} must include a timezone")
    return result


def _date(value: Any, *, label: str) -> str:
    raw = _strict_string(value, label=label)
    try:
        dt.date.fromisoformat(raw)
    except ValueError as exc:
        raise LaneValuationError(f"{label} must be an ISO date") from exc
    return raw


def _earlier_timestamp(left: str | None, right: str) -> str:
    if left is None:
        return right
    return (
        left
        if _timestamp(left, label="event_time") <= _timestamp(right, label="event_time")
        else right
    )


def _hash_body(payload: Mapping[str, Any]) -> str:
    body = copy.deepcopy(dict(payload))
    body.pop("content_hash", None)
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def lane_state_hash(payload: Mapping[str, Any]) -> str:
    """Return the canonical self-excluding hash for a valuation-state snapshot."""

    return _hash_body(payload)


def lane_valuation_hash(payload: Mapping[str, Any]) -> str:
    """Return the canonical self-excluding hash for a lane valuation."""

    return _hash_body(payload)


def accounting_journal_hash(entries: Iterable[Mapping[str, Any]]) -> str:
    """Hash validated journal economics in deterministic economic-time order."""

    try:
        rows = validate_accounting_journal(entries)
    except AccountingJournalError as exc:
        raise LaneValuationError(f"accounting journal is invalid: {exc}") from exc
    ordered = sorted(rows, key=lambda row: (row["event_time"], row["journal_entry_id"]))
    return hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest()


def seal_lane_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Seal and validate an explicit reconciled or theoretical state snapshot."""

    body = json.loads(canonical_json(dict(payload)))
    body["content_hash"] = lane_state_hash(body)
    return validate_lane_state(body)


def validate_lane_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate one valuation-state snapshot and return a detached copy."""

    if not isinstance(payload, Mapping):
        raise LaneValuationError("lane state must be an object")
    _strict_fields(payload, _STATE_FIELDS, label="lane state")
    lane_kind = _strict_string(payload["lane_kind"], label="lane_kind")
    schema = payload["schema_version"]
    if lane_kind in {"PAPER", "LIVE"}:
        if schema != RECONCILED_LANE_STATE_SCHEMA:
            raise LaneValuationError("PAPER/LIVE require reconciled lane-state schema")
        if payload["status"] != "PASS":
            raise LaneValuationError("factual lane state must have reconciliation status PASS")
        expected_surface = "FACTUAL_PAPER" if lane_kind == "PAPER" else "FACTUAL_LIVE"
        expected_authority = "BROKER_RECONCILED"
    elif lane_kind == "SHADOW":
        if schema != THEORETICAL_LANE_STATE_SCHEMA:
            raise LaneValuationError("SHADOW requires theoretical lane-state schema")
        if payload["status"] != "MODELED":
            raise LaneValuationError("theoretical lane state must have status MODELED")
        expected_surface = "MODELED_SHADOW_NAV"
        expected_authority = "THEORETICAL_MODEL"
    else:
        raise LaneValuationError(f"unsupported lane_kind: {lane_kind}")
    if payload["performance_surface"] != expected_surface:
        raise LaneValuationError("lane state performance surface does not match lane kind")
    if payload["economic_authority"] != expected_authority:
        raise LaneValuationError("lane state economic authority does not match lane kind")

    _timestamp(payload["as_of"], label="as_of")
    _date(payload["valuation_date"], label="valuation_date")
    _sha256(payload["account_id_hash"], label="account_id_hash")
    _safe_id(payload["lane_id"], label="lane_id")
    _safe_id(payload["deployment_version"], label="deployment_version")
    cash = _decimal(payload["cash"], label="cash")
    equity = _decimal(payload["equity"], label="equity")
    _sha256(payload["journal_hash"], label="journal_hash")
    _sha256(payload["source_hash"], label="source_hash")

    positions = payload["positions"]
    if not isinstance(positions, list):
        raise LaneValuationError("lane state positions must be an array")
    seen: set[str] = set()
    positions_value = _ZERO
    for index, raw in enumerate(positions):
        label = f"positions[{index}]"
        if not isinstance(raw, Mapping):
            raise LaneValuationError(f"{label} must be an object")
        _strict_fields(raw, _STATE_POSITION_FIELDS, label=label)
        symbol = _strict_string(raw["symbol"], label=f"{label}.symbol")
        if not _SYMBOL.fullmatch(symbol) or symbol in seen:
            raise LaneValuationError(f"{label}.symbol is invalid or duplicated")
        seen.add(symbol)
        quantity = _decimal(raw["quantity"], label=f"{label}.quantity")
        price = _decimal(raw["price"], label=f"{label}.price", nonnegative=True)
        market_value = _decimal(raw["market_value"], label=f"{label}.market_value")
        if abs(market_value - quantity * price) > _TOLERANCE:
            raise LaneValuationError(f"{label}.market_value does not equal quantity * price")
        _sha256(raw["source_hash"], label=f"{label}.source_hash")
        positions_value += market_value
    if abs(equity - cash - positions_value) > _TOLERANCE:
        raise LaneValuationError("lane state equity does not equal cash plus positions")
    declared_hash = _sha256(payload["content_hash"], label="content_hash")
    if declared_hash != lane_state_hash(payload):
        raise LaneValuationError("lane state content_hash mismatch")
    return json.loads(canonical_json(payload))


def _journal_state(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cash: dict[str, Decimal] = {}
    quantities: dict[tuple[str, str], Decimal] = {}
    sleeve_flow: dict[str, Decimal] = {}
    sleeve_fees: dict[str, Decimal] = {}
    sleeve_causal_start: dict[str, str] = {}
    sleeve_first_event: dict[str, str] = {}
    lane_external_flow = _ZERO
    lane_fees = _ZERO
    causal_start: str | None = None

    for row in rows:
        sleeve_id = str(row["sleeve_id"])
        event_time = str(row["event_time"])
        sleeve_first_event[sleeve_id] = _earlier_timestamp(
            sleeve_first_event.get(sleeve_id), event_time
        )
        for posting in row["postings"]:
            if posting["ledger_account"] == "ASSET:CASH":
                posting_sleeve = str(posting["sleeve_id"])
                cash[posting_sleeve] = cash.get(posting_sleeve, _ZERO) + _decimal(
                    posting["debit_amount"], label="posting.debit_amount"
                ) - _decimal(posting["credit_amount"], label="posting.credit_amount")

        event_type = str(row["event_type"])
        quantity = _decimal(row["quantity"], label="quantity")
        symbol = row["symbol"]
        if event_type == "BUY":
            key = (sleeve_id, str(symbol))
            quantities[key] = quantities.get(key, _ZERO) + quantity
        elif event_type == "SELL":
            key = (sleeve_id, str(symbol))
            quantities[key] = quantities.get(key, _ZERO) - quantity
        elif event_type == "CORPORATE_ACTION" and symbol is not None:
            # The v1 journal contract records corporate-action quantity as a
            # signed inventory delta; no historical owner is inferred.
            key = (sleeve_id, str(symbol))
            quantities[key] = quantities.get(key, _ZERO) + quantity

        if event_type in {"OPENING_CAPITAL", "EXTERNAL_FLOW"}:
            flow = _decimal(row["net_amount"], label="net_amount")
            sleeve_flow[sleeve_id] = sleeve_flow.get(sleeve_id, _ZERO) + flow
            lane_external_flow += flow
        elif event_type == "ALLOCATION_TRANSFER":
            # Transfers are lane-internal, but they must be removed from each
            # sleeve's return denominator.  Cash postings carry the direction.
            for posting in row["postings"]:
                if posting["ledger_account"] != "ASSET:CASH":
                    continue
                owner = str(posting["sleeve_id"])
                delta = _decimal(posting["debit_amount"], label="debit_amount") - _decimal(
                    posting["credit_amount"], label="credit_amount"
                )
                sleeve_flow[owner] = sleeve_flow.get(owner, _ZERO) + delta

        fee = _decimal(row["fee_amount"], label="fee_amount")
        if fee:
            sleeve_fees[sleeve_id] = sleeve_fees.get(sleeve_id, _ZERO) + fee
            lane_fees += fee

        if (
            event_type in {"BUY", "SELL"}
            and row["attribution_status"] == "ATTRIBUTED"
            and sleeve_id != LEGACY_UNATTRIBUTED
        ):
            sleeve_causal_start[sleeve_id] = _earlier_timestamp(
                sleeve_causal_start.get(sleeve_id), event_time
            )
            causal_start = _earlier_timestamp(causal_start, event_time)

    return {
        "cash": cash,
        "quantities": quantities,
        "sleeve_flow": sleeve_flow,
        "sleeve_fees": sleeve_fees,
        "lane_external_flow": lane_external_flow,
        "lane_fees": lane_fees,
        "sleeve_causal_start": sleeve_causal_start,
        "sleeve_first_event": sleeve_first_event,
        "causal_start": causal_start,
    }


def _build_sleeve_row(
    *,
    sleeve_id: str,
    cash: Decimal,
    quantities: Mapping[tuple[str, str], Decimal],
    prices: Mapping[str, Mapping[str, Any]],
    causal_start: str | None,
    cumulative_flow: Decimal,
    cumulative_fee_amount: Decimal,
) -> dict[str, Any]:
    positions: list[dict[str, Any]] = []
    position_value = _ZERO
    for (owner, symbol), quantity in sorted(quantities.items()):
        if owner != sleeve_id or abs(quantity) <= _TOLERANCE:
            continue
        price_row = prices[symbol]
        price = _decimal(price_row["price"], label=f"{symbol}.price")
        market_value = quantity * price
        position_value += market_value
        positions.append(
            {
                "symbol": symbol,
                "quantity": _number(quantity),
                "price": _number(price),
                "market_value": _number(market_value),
                "price_source_hash": price_row["source_hash"],
            }
        )
    return {
        "sleeve_id": sleeve_id,
        "cash": _number(cash),
        "positions_market_value": _number(position_value),
        "nav": _number(cash + position_value),
        "positions": positions,
        "causal_start": causal_start,
        "cumulative_flow": _number(cumulative_flow),
        "cumulative_fee_amount": _number(cumulative_fee_amount),
    }


def build_lane_valuation(
    *,
    journal_entries: Iterable[Mapping[str, Any]],
    lane_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one hash-bound lane valuation from explicit journal and state inputs."""

    state = validate_lane_state(lane_state)
    try:
        rows = validate_accounting_journal(journal_entries)
    except AccountingJournalError as exc:
        raise LaneValuationError(f"accounting journal is invalid: {exc}") from exc
    if not rows:
        raise LaneValuationError("lane valuation requires a non-empty accounting journal")
    rows = sorted(rows, key=lambda row: (row["event_time"], row["journal_entry_id"]))
    scope_fields = (
        "account_id_hash",
        "lane_id",
        "lane_kind",
        "deployment_version",
        "performance_surface",
        "economic_authority",
    )
    for field in scope_fields:
        if any(row[field] != state[field] for row in rows):
            raise LaneValuationError(f"journal and lane state {field} differ")
    as_of = _timestamp(state["as_of"], label="as_of")
    if any(_timestamp(row["event_time"], label="event_time") > as_of for row in rows):
        raise LaneValuationError("journal contains an event after the valuation as-of")
    observed_journal_hash = accounting_journal_hash(rows)
    if state["journal_hash"] != observed_journal_hash:
        raise LaneValuationError("lane state journal_hash does not bind the supplied journal")

    economic = _journal_state(rows)
    prices = {str(row["symbol"]): row for row in state["positions"]}
    journal_symbols = {
        symbol
        for (_, symbol), quantity in economic["quantities"].items()
        if abs(quantity) > _TOLERANCE
    }
    if journal_symbols != set(prices):
        raise LaneValuationError("lane state symbols do not match journal-owned positions")
    aggregate_quantities: dict[str, Decimal] = {}
    for (_, symbol), quantity in economic["quantities"].items():
        aggregate_quantities[symbol] = aggregate_quantities.get(symbol, _ZERO) + quantity
    for symbol, state_row in prices.items():
        state_quantity = _decimal(state_row["quantity"], label=f"{symbol}.quantity")
        if abs(state_quantity - aggregate_quantities.get(symbol, _ZERO)) > _TOLERANCE:
            raise LaneValuationError(f"state quantity does not reconcile to journal: {symbol}")

    journal_cash = sum(economic["cash"].values(), _ZERO)
    state_cash = _decimal(state["cash"], label="state.cash")
    if abs(journal_cash - state_cash) > _TOLERANCE:
        raise LaneValuationError("state cash does not reconcile to journal cash")

    owners = set(economic["cash"])
    owners.update(owner for owner, _ in economic["quantities"])
    owners.update(economic["sleeve_flow"])
    owners.update(economic["sleeve_fees"])
    theoretical = state["lane_kind"] == "SHADOW"
    attributed_rows: list[dict[str, Any]] = []
    for sleeve_id in sorted(owners - {LEGACY_UNATTRIBUTED}):
        attributed_rows.append(
            _build_sleeve_row(
                sleeve_id=sleeve_id,
                cash=economic["cash"].get(sleeve_id, _ZERO),
                quantities=economic["quantities"],
                prices=prices,
                causal_start=(
                    economic["sleeve_first_event"].get(sleeve_id)
                    if theoretical
                    else economic["sleeve_causal_start"].get(sleeve_id)
                ),
                cumulative_flow=economic["sleeve_flow"].get(sleeve_id, _ZERO),
                cumulative_fee_amount=economic["sleeve_fees"].get(sleeve_id, _ZERO),
            )
        )
    legacy_row = _build_sleeve_row(
        sleeve_id=LEGACY_UNATTRIBUTED,
        cash=economic["cash"].get(LEGACY_UNATTRIBUTED, _ZERO),
        quantities=economic["quantities"],
        prices=prices,
        causal_start=None,
        cumulative_flow=economic["sleeve_flow"].get(LEGACY_UNATTRIBUTED, _ZERO),
        cumulative_fee_amount=economic["sleeve_fees"].get(LEGACY_UNATTRIBUTED, _ZERO),
    )
    attributed_nav = sum((_decimal(row["nav"], label="sleeve.nav") for row in attributed_rows), _ZERO)
    legacy_nav = _decimal(legacy_row["nav"], label="legacy.nav")
    decomposed_nav = attributed_nav + legacy_nav
    state_equity = _decimal(state["equity"], label="state.equity")
    difference = decomposed_nav - state_equity
    if abs(difference) > _TOLERANCE:
        raise LaneValuationError("sleeve NAV decomposition does not reconcile to state equity")
    lane_positions = sum(
        (_decimal(row["market_value"], label="market_value") for row in state["positions"]),
        _ZERO,
    )
    valuation_basis = (
        "FACTUAL_RECONCILED" if state["lane_kind"] in {"PAPER", "LIVE"} else "THEORETICAL_MODELED"
    )
    seed = hashlib.sha256(
        canonical_json(
            {
                "lane_id": state["lane_id"],
                "deployment_version": state["deployment_version"],
                "as_of": state["as_of"],
                "journal_hash": observed_journal_hash,
                "state_hash": state["content_hash"],
            }
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "schema_version": LANE_VALUATION_SCHEMA,
        "valuation_id": f"lane-valuation:{state['lane_id']}:{state['valuation_date']}:{seed[:24]}",
        "valuation_date": state["valuation_date"],
        "as_of": state["as_of"],
        "account_id_hash": state["account_id_hash"],
        "lane_id": state["lane_id"],
        "lane_kind": state["lane_kind"],
        "deployment_version": state["deployment_version"],
        "performance_surface": state["performance_surface"],
        "economic_authority": state["economic_authority"],
        "valuation_basis": valuation_basis,
        "reconciliation_status": state["status"],
        "causal_start": (
            min(
                economic["sleeve_first_event"].values(),
                key=lambda value: _timestamp(value, label="event_time"),
            )
            if theoretical and economic["sleeve_first_event"]
            else economic["causal_start"]
        ),
        "journal_entry_count": len(rows),
        "journal_hash": observed_journal_hash,
        "state_hash": state["content_hash"],
        "source_hashes": sorted(
            {state["source_hash"], *(row["source_hash"] for row in state["positions"])}
        ),
        "sleeves": attributed_rows,
        "legacy_unattributed": legacy_row,
        "lane_cash": _number(state_cash),
        "lane_positions_market_value": _number(lane_positions),
        "lane_nav": _number(state_equity),
        "cumulative_external_flow": _number(economic["lane_external_flow"]),
        "cumulative_fee_amount": _number(economic["lane_fees"]),
        "proof": {
            "status": "PASS",
            "tolerance": _number(_TOLERANCE),
            "attributed_sleeve_nav_sum": _number(attributed_nav),
            "legacy_unattributed_nav": _number(legacy_nav),
            "decomposed_nav": _number(decomposed_nav),
            "state_equity": _number(state_equity),
            "difference": _number(difference),
        },
    }
    body["content_hash"] = lane_valuation_hash(body)
    return validate_lane_valuation(body)


def _validate_sleeve_row(row: Mapping[str, Any], *, label: str) -> tuple[Decimal, Decimal, Decimal]:
    _strict_fields(row, _SLEEVE_FIELDS, label=label)
    _safe_id(row["sleeve_id"], label=f"{label}.sleeve_id")
    cash = _decimal(row["cash"], label=f"{label}.cash")
    position_value = _decimal(
        row["positions_market_value"], label=f"{label}.positions_market_value"
    )
    nav = _decimal(row["nav"], label=f"{label}.nav")
    if abs(nav - cash - position_value) > _TOLERANCE:
        raise LaneValuationError(f"{label}.nav does not equal cash plus positions")
    if row["causal_start"] is not None:
        _timestamp(row["causal_start"], label=f"{label}.causal_start")
    _decimal(row["cumulative_flow"], label=f"{label}.cumulative_flow")
    _decimal(
        row["cumulative_fee_amount"],
        label=f"{label}.cumulative_fee_amount",
        nonnegative=True,
    )
    positions = row["positions"]
    if not isinstance(positions, list):
        raise LaneValuationError(f"{label}.positions must be an array")
    seen: set[str] = set()
    observed_position_value = _ZERO
    for index, position in enumerate(positions):
        position_label = f"{label}.positions[{index}]"
        if not isinstance(position, Mapping):
            raise LaneValuationError(f"{position_label} must be an object")
        _strict_fields(position, _VALUED_POSITION_FIELDS, label=position_label)
        symbol = _strict_string(position["symbol"], label=f"{position_label}.symbol")
        if not _SYMBOL.fullmatch(symbol) or symbol in seen:
            raise LaneValuationError(f"{position_label}.symbol is invalid or duplicated")
        seen.add(symbol)
        quantity = _decimal(position["quantity"], label=f"{position_label}.quantity")
        price = _decimal(position["price"], label=f"{position_label}.price", nonnegative=True)
        market_value = _decimal(position["market_value"], label=f"{position_label}.market_value")
        if abs(market_value - quantity * price) > _TOLERANCE:
            raise LaneValuationError(f"{position_label}.market_value mismatch")
        _sha256(position["price_source_hash"], label=f"{position_label}.price_source_hash")
        observed_position_value += market_value
    if abs(observed_position_value - position_value) > _TOLERANCE:
        raise LaneValuationError(f"{label}.positions_market_value mismatch")
    return cash, position_value, nav


def validate_lane_valuation(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Strictly validate a serialized lane valuation and return a detached copy."""

    if not isinstance(payload, Mapping):
        raise LaneValuationError("lane valuation must be an object")
    _strict_fields(payload, _VALUATION_FIELDS, label="lane valuation")
    if payload["schema_version"] != LANE_VALUATION_SCHEMA:
        raise LaneValuationError("unsupported lane valuation schema")
    _safe_id(payload["valuation_id"], label="valuation_id")
    _date(payload["valuation_date"], label="valuation_date")
    _timestamp(payload["as_of"], label="as_of")
    _sha256(payload["account_id_hash"], label="account_id_hash")
    _safe_id(payload["lane_id"], label="lane_id")
    _safe_id(payload["deployment_version"], label="deployment_version")
    lane_kind = _strict_string(payload["lane_kind"], label="lane_kind")
    expected = {
        "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED", "FACTUAL_RECONCILED", "PASS"),
        "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED", "FACTUAL_RECONCILED", "PASS"),
        "SHADOW": ("MODELED_SHADOW_NAV", "THEORETICAL_MODEL", "THEORETICAL_MODELED", "MODELED"),
    }.get(lane_kind)
    if expected is None or (
        payload["performance_surface"],
        payload["economic_authority"],
        payload["valuation_basis"],
        payload["reconciliation_status"],
    ) != expected:
        raise LaneValuationError("valuation surface/authority/basis/status mismatch")
    if payload["causal_start"] is not None:
        _timestamp(payload["causal_start"], label="causal_start")
    if isinstance(payload["journal_entry_count"], bool) or not isinstance(
        payload["journal_entry_count"], int
    ) or payload["journal_entry_count"] <= 0:
        raise LaneValuationError("journal_entry_count must be a positive integer")
    _sha256(payload["journal_hash"], label="journal_hash")
    _sha256(payload["state_hash"], label="state_hash")
    if not isinstance(payload["source_hashes"], list) or not payload["source_hashes"]:
        raise LaneValuationError("source_hashes must be a non-empty array")
    if payload["source_hashes"] != sorted(set(payload["source_hashes"])):
        raise LaneValuationError("source_hashes must be sorted and unique")
    for source_hash in payload["source_hashes"]:
        _sha256(source_hash, label="source_hash")

    sleeves = payload["sleeves"]
    if not isinstance(sleeves, list):
        raise LaneValuationError("sleeves must be an array")
    seen: set[str] = set()
    attributed_nav = _ZERO
    for index, row in enumerate(sleeves):
        if not isinstance(row, Mapping):
            raise LaneValuationError(f"sleeves[{index}] must be an object")
        _, _, nav = _validate_sleeve_row(row, label=f"sleeves[{index}]")
        sleeve_id = str(row["sleeve_id"])
        if sleeve_id == LEGACY_UNATTRIBUTED or sleeve_id in seen:
            raise LaneValuationError("attributed sleeve IDs must be unique and non-legacy")
        seen.add(sleeve_id)
        attributed_nav += nav
    if [row["sleeve_id"] for row in sleeves] != sorted(seen):
        raise LaneValuationError("sleeves must be sorted by sleeve_id")
    legacy = payload["legacy_unattributed"]
    if not isinstance(legacy, Mapping):
        raise LaneValuationError("legacy_unattributed must be an object")
    _, _, legacy_nav = _validate_sleeve_row(legacy, label="legacy_unattributed")
    if legacy["sleeve_id"] != LEGACY_UNATTRIBUTED or legacy["causal_start"] is not None:
        raise LaneValuationError("legacy_unattributed identity/causal_start is invalid")
    lane_cash = _decimal(payload["lane_cash"], label="lane_cash")
    lane_positions = _decimal(
        payload["lane_positions_market_value"], label="lane_positions_market_value"
    )
    lane_nav = _decimal(payload["lane_nav"], label="lane_nav")
    if abs(lane_nav - lane_cash - lane_positions) > _TOLERANCE:
        raise LaneValuationError("lane_nav does not equal cash plus positions")
    _decimal(payload["cumulative_external_flow"], label="cumulative_external_flow")
    _decimal(
        payload["cumulative_fee_amount"], label="cumulative_fee_amount", nonnegative=True
    )
    proof = payload["proof"]
    if not isinstance(proof, Mapping):
        raise LaneValuationError("proof must be an object")
    _strict_fields(proof, _PROOF_FIELDS, label="proof")
    if proof["status"] != "PASS":
        raise LaneValuationError("valuation proof status must be PASS")
    tolerance = _decimal(proof["tolerance"], label="proof.tolerance", nonnegative=True)
    decomposed = attributed_nav + legacy_nav
    expected_values = {
        "attributed_sleeve_nav_sum": attributed_nav,
        "legacy_unattributed_nav": legacy_nav,
        "decomposed_nav": decomposed,
        "state_equity": lane_nav,
        "difference": decomposed - lane_nav,
    }
    for field, expected_value in expected_values.items():
        actual = _decimal(proof[field], label=f"proof.{field}")
        if abs(actual - expected_value) > _TOLERANCE:
            raise LaneValuationError(f"proof.{field} mismatch")
    if abs(decomposed - lane_nav) > tolerance:
        raise LaneValuationError("valuation proof does not reconcile")
    declared_hash = _sha256(payload["content_hash"], label="content_hash")
    if declared_hash != lane_valuation_hash(payload):
        raise LaneValuationError("lane valuation content_hash mismatch")
    return json.loads(canonical_json(payload))


__all__ = [
    "LANE_VALUATION_SCHEMA",
    "RECONCILED_LANE_STATE_SCHEMA",
    "THEORETICAL_LANE_STATE_SCHEMA",
    "LaneValuationError",
    "accounting_journal_hash",
    "build_lane_valuation",
    "lane_state_hash",
    "lane_valuation_hash",
    "seal_lane_state",
    "validate_lane_state",
    "validate_lane_valuation",
]
