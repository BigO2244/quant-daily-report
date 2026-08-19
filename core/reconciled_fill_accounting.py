"""Pure bridge from validated lane reconciliation to accounting journal entries.

The bridge emits exactly one balanced BUY/SELL journal entry for each
sleeve-split ``caerus.reconciled_fill.v1`` row.  It does not read broker state,
runtime configuration, files, or the strategy registry, and it does not write
the journal.  The reconciliation content hash is the source hash on every
entry, preserving the complete reconciliation proof as the accounting source.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from core.accounting_journal import (
    ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
    AccountingJournalError,
    canonical_json,
    seal_journal_entry,
    validate_journal_batch,
)
from core.lane_reconciliation import (
    LaneReconciliationError,
    validate_lane_reconciliation,
)
from authority.lane_exact_plan import validate_lane_exact_execution_plan


_ZERO = Decimal("0")
_TOLERANCE = Decimal("0.00000001")
_SURFACE = {
    "PAPER": ("FACTUAL_PAPER", "BROKER_RECONCILED"),
    "LIVE": ("FACTUAL_LIVE", "BROKER_RECONCILED"),
}


class ReconciledFillAccountingError(ValueError):
    """Raised when reconciled economics cannot be journaled exactly."""


def _decimal(value: Any, *, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ReconciledFillAccountingError(f"{label} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ReconciledFillAccountingError(f"{label} must be numeric") from exc
    if not result.is_finite():
        raise ReconciledFillAccountingError(f"{label} must be finite")
    return result


def _equal(left: Decimal, right: Decimal, *, label: str) -> None:
    if abs(left - right) > _TOLERANCE:
        raise ReconciledFillAccountingError(
            f"{label} mismatch: observed={left}, expected={right}"
        )


def _proof_reconciled_economics(reconciliation: Mapping[str, Any]) -> None:
    """Independently prove split rows do not exceed or omit broker economics."""

    for proof_name in (
        "position_reconciliation",
        "cash_reconciliation",
        "nav_reconciliation",
    ):
        if reconciliation[proof_name]["status"] != "PASS":
            raise ReconciledFillAccountingError(
                f"accounting-ready reconciliation has non-green {proof_name}"
            )

    intended_by_order: dict[str, Mapping[str, Any]] = {}
    for row in reconciliation["intended_orders"]:
        if not isinstance(row, Mapping):
            raise ReconciledFillAccountingError("intended order summary must be an object")
        order_id = str(row.get("order_id") or "")
        if not order_id or order_id in intended_by_order:
            raise ReconciledFillAccountingError("intended order identities are missing or duplicated")
        intended_by_order[order_id] = row

    broker_by_fill: dict[str, Mapping[str, Any]] = {}
    for row in reconciliation["broker_fills"]:
        if not isinstance(row, Mapping):
            raise ReconciledFillAccountingError("broker fill summary must be an object")
        fill_id = str(row.get("fill_id") or "")
        if not fill_id or fill_id in broker_by_fill:
            raise ReconciledFillAccountingError("broker fill identities are missing or duplicated")
        broker_by_fill[fill_id] = row
    summary_hashes = sorted(str(row.get("evidence_hash") or "") for row in broker_by_fill.values())
    if summary_hashes != sorted(reconciliation["source_hashes"]["broker_fills"]):
        raise ReconciledFillAccountingError(
            "broker fill summaries do not match reconciliation source hashes"
        )

    split_by_fill: dict[str, list[Mapping[str, Any]]] = {}
    for row in reconciliation["reconciled_fills"]:
        split_by_fill.setdefault(row["fill_id"], []).append(row)
    if set(split_by_fill) != set(broker_by_fill):
        raise ReconciledFillAccountingError(
            "sleeve-split fills do not cover exactly the reconciled broker fills"
        )

    for fill_id in sorted(broker_by_fill):
        broker = broker_by_fill[fill_id]
        rows = split_by_fill[fill_id]
        order_id = str(broker.get("order_id") or "")
        intended = intended_by_order.get(order_id)
        if intended is None:
            raise ReconciledFillAccountingError(
                f"broker fill lacks intended order: {fill_id}"
            )
        broker_quantity = _decimal(broker.get("quantity"), label="broker fill quantity")
        broker_price = _decimal(broker.get("price"), label="broker fill price")
        broker_fee = _decimal(broker.get("fee_amount"), label="broker fill fee")
        intended_quantity = _decimal(
            intended.get("quantity"), label="intended order quantity"
        )
        quantity_sum = sum(
            (_decimal(row["quantity"], label="split quantity") for row in rows),
            _ZERO,
        )
        fee_sum = sum(
            (_decimal(row["fee_amount"], label="split fee") for row in rows),
            _ZERO,
        )
        planned_sum = sum(
            (
                _decimal(
                    row["planned_contribution_quantity"],
                    label="planned contribution quantity",
                )
                for row in rows
            ),
            _ZERO,
        )
        order_fraction_sum = sum(
            (_decimal(row["order_fraction"], label="order fraction") for row in rows),
            _ZERO,
        )
        fill_fraction_sum = sum(
            (
                _decimal(row["fill_allocation_fraction"], label="fill fraction")
                for row in rows
            ),
            _ZERO,
        )
        _equal(quantity_sum, broker_quantity, label=f"{fill_id} split quantity")
        _equal(fee_sum, broker_fee, label=f"{fill_id} split fee")
        _equal(planned_sum, intended_quantity, label=f"{fill_id} planned quantity")
        _equal(order_fraction_sum, Decimal("1"), label=f"{fill_id} order fractions")
        _equal(fill_fraction_sum, Decimal("1"), label=f"{fill_id} fill fractions")
        sleeve_ids: set[str] = set()
        for row in rows:
            if row["sleeve_id"] in sleeve_ids:
                raise ReconciledFillAccountingError(
                    f"{fill_id} duplicates a sleeve split"
                )
            sleeve_ids.add(row["sleeve_id"])
            expected_bindings = {
                "order_id": order_id,
                "broker_order_id": broker.get("broker_order_id"),
                "symbol": intended.get("symbol"),
                "side": intended.get("side"),
                "client_order_id": intended.get("client_order_id"),
                "exact_order_hash": intended.get("exact_order_hash"),
                "broker_fill_source_hash": broker.get("evidence_hash"),
            }
            for field, expected in expected_bindings.items():
                if row[field] != expected:
                    raise ReconciledFillAccountingError(
                        f"{fill_id} split {field} does not match reconciled evidence"
                    )
            _equal(
                _decimal(row["price"], label="split price"),
                broker_price,
                label=f"{fill_id} split price",
            )
            order_fraction = _decimal(row["order_fraction"], label="order fraction")
            fill_fraction = _decimal(
                row["fill_allocation_fraction"], label="fill allocation fraction"
            )
            _equal(
                _decimal(
                    row["planned_contribution_quantity"],
                    label="planned contribution quantity",
                ),
                intended_quantity * order_fraction,
                label=f"{fill_id} planned contribution fraction",
            )
            _equal(
                _decimal(row["quantity"], label="split quantity"),
                broker_quantity * fill_fraction,
                label=f"{fill_id} fill allocation fraction",
            )
            _equal(
                _decimal(row["fee_amount"], label="split fee"),
                broker_fee * fill_fraction,
                label=f"{fill_id} fee allocation fraction",
            )


def _posting(
    *, identity: str, account: str, sleeve_id: str, debit: Decimal, credit: Decimal
) -> dict[str, Any]:
    return {
        "posting_id": identity,
        "ledger_account": account,
        "sleeve_id": sleeve_id,
        "currency": "USD",
        "debit_amount": float(debit),
        "credit_amount": float(credit),
    }


def _journal_entry(
    row: Mapping[str, Any], *, reconciliation_hash: str
) -> dict[str, Any]:
    side = row["side"]
    sleeve_id = row["sleeve_id"]
    gross = _decimal(row["gross_amount"], label="gross_amount")
    fee = _decimal(row["fee_amount"], label="fee_amount")
    net = _decimal(row["net_amount"], label="net_amount")
    prefix = f"posting:{row['reconciled_fill_id']}"
    if side == "BUY":
        postings = [
            _posting(
                identity=f"{prefix}:security",
                account="ASSET:SECURITY",
                sleeve_id=sleeve_id,
                debit=gross,
                credit=_ZERO,
            )
        ]
        if fee > _ZERO:
            postings.append(
                _posting(
                    identity=f"{prefix}:fee",
                    account="EXPENSE:FEE",
                    sleeve_id=sleeve_id,
                    debit=fee,
                    credit=_ZERO,
                )
            )
        postings.append(
            _posting(
                identity=f"{prefix}:cash",
                account="ASSET:CASH",
                sleeve_id=sleeve_id,
                debit=_ZERO,
                credit=-net,
            )
        )
    else:
        postings = [
            _posting(
                identity=f"{prefix}:cash",
                account="ASSET:CASH",
                sleeve_id=sleeve_id,
                debit=net,
                credit=_ZERO,
            )
        ]
        if fee > _ZERO:
            postings.append(
                _posting(
                    identity=f"{prefix}:fee",
                    account="EXPENSE:FEE",
                    sleeve_id=sleeve_id,
                    debit=fee,
                    credit=_ZERO,
                )
            )
        postings.append(
            _posting(
                identity=f"{prefix}:security",
                account="ASSET:SECURITY",
                sleeve_id=sleeve_id,
                debit=_ZERO,
                credit=gross,
            )
        )
    surface, authority = _SURFACE[row["lane_kind"]]
    body = {
        "schema_version": ACCOUNTING_JOURNAL_ENTRY_SCHEMA,
        "journal_entry_id": f"journal:{row['reconciled_fill_id']}",
        "event_type": side,
        "event_time": row["event_time"],
        "trade_date": row["trade_date"],
        "account_id_hash": row["account_id_hash"],
        "lane_id": row["lane_id"],
        "lane_kind": row["lane_kind"],
        "deployment_version": row["deployment_version"],
        "sleeve_id": sleeve_id,
        "counterparty_sleeve_id": None,
        "attribution_status": "ATTRIBUTED",
        "performance_surface": surface,
        "economic_authority": authority,
        "symbol": row["symbol"],
        "quantity": row["quantity"],
        "price": row["price"],
        "gross_amount": row["gross_amount"],
        "fee_amount": row["fee_amount"],
        "net_amount": row["net_amount"],
        "session_id": row["session_id"],
        "decision_id": row["decision_id"],
        "allocation_id": row["allocation_id"],
        "plan_id": row["plan_id"],
        "broker_order_id": row["broker_order_id"],
        "fill_id": row["fill_id"],
        "source_hash": reconciliation_hash,
        "postings": postings,
    }
    try:
        return seal_journal_entry(body)
    except AccountingJournalError as exc:
        raise ReconciledFillAccountingError(
            f"reconciled fill cannot produce a journal entry: {exc}"
        ) from exc


def build_reconciled_fill_journal_entries(
    reconciliation: Mapping[str, Any],
    *,
    exact_plan: Mapping[str, Any],
    existing_entries: Iterable[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Return deterministic new journal entries for one accounting-ready artifact.

    Passing existing journal entries applies the journal's immutable idempotency
    rules and returns only additions.  This function itself performs no write.
    """

    try:
        plan_failures = validate_lane_exact_execution_plan(exact_plan)
        if plan_failures:
            raise ReconciledFillAccountingError(
                "exact plan is invalid: " + ",".join(plan_failures)
            )
        checked = validate_lane_reconciliation(
            reconciliation, exact_plan=exact_plan
        )
    except LaneReconciliationError as exc:
        raise ReconciledFillAccountingError(
            f"lane reconciliation is invalid: {exc}"
        ) from exc
    if checked["status"] not in {"PASS", "PARTIAL"}:
        raise ReconciledFillAccountingError(
            "accounting accepts only PASS or PARTIAL reconciliation"
        )
    if checked["accounting_ready"] is not True:
        raise ReconciledFillAccountingError(
            "reconciliation is not explicitly accounting_ready"
        )
    if not checked["reconciled_fills"]:
        raise ReconciledFillAccountingError(
            "accounting-ready reconciliation has no reconciled fills"
        )
    _proof_reconciled_economics(checked)
    ordered = sorted(
        checked["reconciled_fills"],
        key=lambda row: (
            row["event_time"],
            row["fill_id"],
            row["sleeve_id"],
            row["reconciled_fill_id"],
        ),
    )
    entries = [
        _journal_entry(row, reconciliation_hash=checked["content_hash"])
        for row in ordered
    ]
    try:
        return validate_journal_batch(entries, existing_entries=existing_entries)
    except AccountingJournalError as exc:
        raise ReconciledFillAccountingError(
            f"reconciled journal batch is invalid: {exc}"
        ) from exc


def serialize_reconciled_fill_journal_entries(
    reconciliation: Mapping[str, Any],
    *,
    exact_plan: Mapping[str, Any],
    existing_entries: Iterable[Mapping[str, Any]] = (),
) -> str:
    """Serialize only new deterministic entries; no filesystem write occurs."""

    entries = build_reconciled_fill_journal_entries(
        reconciliation,
        exact_plan=exact_plan,
        existing_entries=existing_entries,
    )
    return "".join(canonical_json(row) + "\n" for row in entries)


__all__ = [
    "ReconciledFillAccountingError",
    "build_reconciled_fill_journal_entries",
    "serialize_reconciled_fill_journal_entries",
]
