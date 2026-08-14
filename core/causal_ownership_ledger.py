"""Causal PAPER ownership derived from immutable exact plans and broker fills.

Broker positions remain the quantity and valuation authority.  This module adds
the missing causal dimension without relabeling history: fills before the first
allocator-bound exact plan belong to ``legacy_unattributed``; later fills must
trace through broker order ID -> client order ID -> immutable exact order.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence


CAUSAL_FILL_SCHEMA = "caerus.causal_fill.v1"
OWNERSHIP_SCHEMA = "caerus.causal_ownership.v1"
VALUATION_SCHEMA = "caerus.causal_valuation.v1"
LEGACY_OWNER = "legacy_unattributed"
QUANTITY_TOLERANCE = 1e-6


class CausalOwnershipError(RuntimeError):
    pass


def _canonical(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _hash(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise CausalOwnershipError(f"expected JSON object: {path}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise CausalOwnershipError(f"invalid JSONL row {path}:{line_number}")
        rows.append(payload)
    return rows


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n")
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _parse_timestamp(value: Any) -> dt.datetime:
    raw = str(value or "").strip().replace("Z", "+00:00")
    # Python 3.10's fromisoformat accepts only selected fractional-second
    # widths, while Alpaca history can contain (for example) five digits.
    # Normalize any ISO fractional component to microseconds.
    fractional = re.fullmatch(
        r"(?P<prefix>.+T\d{2}:\d{2}:\d{2})\.(?P<fraction>\d+)(?P<zone>[+-]\d{2}:\d{2})",
        raw,
    )
    if fractional:
        micros = fractional.group("fraction")[:6].ljust(6, "0")
        raw = f"{fractional.group('prefix')}.{micros}{fractional.group('zone')}"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CausalOwnershipError(f"invalid fill timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise CausalOwnershipError("fill timestamp lacks timezone")
    return parsed.astimezone(dt.timezone.utc)


def _latest_orders(path: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        broker_id = str(row.get("id") or "").strip()
        if not broker_id:
            continue
        prior = latest.get(broker_id)
        if prior is None or str(row.get("updated_at") or "") >= str(
            prior.get("updated_at") or ""
        ):
            latest[broker_id] = row
    return latest


def _exact_order_index(plan_paths: Sequence[Path]) -> tuple[dict[str, dict[str, Any]], dt.datetime | None]:
    index: dict[str, dict[str, Any]] = {}
    epoch: dt.datetime | None = None
    for path in sorted({candidate.resolve() for candidate in plan_paths if candidate.is_file()}):
        plan = _read_json(path)
        if str(plan.get("schema_version") or "") != "caerus.execution_plan.v3":
            continue
        plan_hash = str(plan.get("content_hash") or "").strip().lower()
        unhashed = dict(plan)
        unhashed.pop("content_hash", None)
        if len(plan_hash) != 64 or _hash(unhashed) != plan_hash:
            raise CausalOwnershipError(f"exact plan content hash mismatch: {path}")
        for order in [*(plan.get("sell_orders") or []), *(plan.get("buy_orders") or [])]:
            if not isinstance(order, Mapping):
                raise CausalOwnershipError(f"exact plan order is malformed: {path}")
            client_id = str(order.get("client_order_id") or "").strip()
            if not client_id:
                raise CausalOwnershipError(f"exact plan order lacks client ID: {path}")
            allocation_id = str(order.get("allocation_id") or "").strip()
            contributions = order.get("sleeve_contributions")
            causal = bool(allocation_id and isinstance(contributions, list) and contributions)
            record = {
                "plan_id": str(plan.get("plan_id") or ""),
                "plan_hash": plan_hash,
                "allocation_id": allocation_id or None,
                "session_id": order.get("session_id"),
                "symbol": str(order.get("symbol") or "").strip().upper(),
                "side": str(order.get("side") or "").strip().lower(),
                "sleeve_contributions": list(contributions or []),
                "causal": causal,
            }
            prior = index.get(client_id)
            if prior is not None and prior != record:
                raise CausalOwnershipError(
                    f"client order ID is bound to conflicting exact plans: {client_id}"
                )
            index[client_id] = record
            if causal:
                created = _parse_timestamp(plan.get("created_at"))
                epoch = created if epoch is None else min(epoch, created)
    return index, epoch


def _fill_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise CausalOwnershipError(f"broker fills ledger is missing: {path}")
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows.sort(key=lambda row: (str(row.get("transaction_time_utc") or ""), str(row.get("activity_id") or "")))
    return rows


def _decision_contributions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise CausalOwnershipError("causal exact order lacks sleeve contributions")
    rows: list[dict[str, Any]] = []
    total = 0.0
    for item in raw:
        if not isinstance(item, Mapping):
            raise CausalOwnershipError("causal sleeve contribution is malformed")
        sleeve_id = str(item.get("sleeve_id") or "").strip().lower()
        fraction = float(item.get("allocation_fraction") or 0.0)
        if not sleeve_id or not math.isfinite(fraction) or fraction <= 0.0:
            raise CausalOwnershipError("causal sleeve contribution is invalid")
        total += fraction
        rows.append({**dict(item), "sleeve_id": sleeve_id, "allocation_fraction": fraction})
    if abs(total - 1.0) > 1e-9:
        raise CausalOwnershipError("causal sleeve contribution fractions do not sum to one")
    rows.sort(key=lambda row: row["sleeve_id"])
    return rows


def _consume_inventory(
    ownership: dict[str, dict[str, float]], symbol: str, quantity: float
) -> list[dict[str, Any]]:
    owners = ownership.setdefault(symbol, {})
    positive = {owner: qty for owner, qty in owners.items() if qty > QUANTITY_TOLERANCE}
    total = sum(positive.values())
    if quantity > total + QUANTITY_TOLERANCE:
        raise CausalOwnershipError(
            f"sell fill exceeds causally reconstructed inventory for {symbol}"
        )
    remaining = quantity
    effects: list[dict[str, Any]] = []
    ordered = sorted(positive)
    for position, owner in enumerate(ordered):
        available = positive[owner]
        consumed = (
            remaining
            if position == len(ordered) - 1
            else min(remaining, quantity * available / total)
        )
        consumed = min(consumed, available)
        owners[owner] = owners.get(owner, 0.0) - consumed
        remaining -= consumed
        effects.append({"sleeve_id": owner, "signed_quantity": -consumed})
    if abs(remaining) > QUANTITY_TOLERANCE:
        raise CausalOwnershipError(f"sell ownership allocation failed for {symbol}")
    return effects


def _record_hash(row: Mapping[str, Any]) -> str:
    unhashed = dict(row)
    unhashed.pop("record_hash", None)
    return _hash(unhashed)


def build_causal_ownership(
    *,
    ledger_dir: Path,
    exact_plan_paths: Sequence[Path],
) -> dict[str, Any]:
    """Build and persist causal fills, current ownership, and one-time valuation."""

    fills = _fill_rows(ledger_dir / "fills.csv")
    broker_orders = _latest_orders(ledger_dir / "orders.jsonl")
    exact_orders, causal_epoch = _exact_order_index(exact_plan_paths)
    ownership: dict[str, dict[str, float]] = {}
    causal_rows: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for fill in fills:
        activity_id = str(fill.get("activity_id") or "").strip()
        symbol = str(fill.get("symbol") or "").strip().upper()
        side = str(fill.get("side") or "").strip().lower()
        quantity = float(fill.get("qty") or 0.0)
        timestamp = _parse_timestamp(fill.get("transaction_time_utc"))
        if not activity_id or not symbol or side not in {"buy", "sell"} or quantity <= 0.0:
            raise CausalOwnershipError("broker fill row is malformed")
        broker_order_id = str(fill.get("order_id") or "").strip()
        broker_order = broker_orders.get(broker_order_id) or {}
        client_order_id = str(broker_order.get("client_order_id") or "").strip()
        exact = exact_orders.get(client_order_id)
        after_epoch = causal_epoch is not None and timestamp >= causal_epoch
        matched = bool(exact and exact.get("causal"))
        if after_epoch and not matched:
            unresolved.append(
                {
                    "activity_id": activity_id,
                    "broker_order_id": broker_order_id,
                    "client_order_id": client_order_id or None,
                    "symbol": symbol,
                    "transaction_time_utc": timestamp.isoformat(),
                }
            )
            continue
        if matched:
            if exact["symbol"] != symbol or exact["side"] != side:
                raise CausalOwnershipError(
                    f"broker fill diverges from exact order identity: {activity_id}"
                )
            decisions = _decision_contributions(exact["sleeve_contributions"])
            status = "ATTRIBUTED"
        else:
            decisions = [{"sleeve_id": LEGACY_OWNER, "allocation_fraction": 1.0}]
            status = "LEGACY_UNATTRIBUTED"

        if side == "buy":
            inventory_effects = []
            owners = ownership.setdefault(symbol, {})
            remaining = quantity
            for position, contribution in enumerate(decisions):
                assigned = (
                    remaining
                    if position == len(decisions) - 1
                    else quantity * float(contribution["allocation_fraction"])
                )
                remaining -= assigned
                owner = str(contribution["sleeve_id"])
                owners[owner] = owners.get(owner, 0.0) + assigned
                inventory_effects.append(
                    {"sleeve_id": owner, "signed_quantity": assigned}
                )
        else:
            inventory_effects = _consume_inventory(ownership, symbol, quantity)

        row = {
            "schema_version": CAUSAL_FILL_SCHEMA,
            "activity_id": activity_id,
            "transaction_time_utc": str(fill.get("transaction_time_utc") or ""),
            "trade_date_et": str(fill.get("trade_date_et") or ""),
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": float(fill.get("price") or 0.0),
            "notional": float(fill.get("notional") or 0.0),
            "broker_order_id": broker_order_id,
            "client_order_id": client_order_id or None,
            "attribution_status": status,
            "plan_id": exact.get("plan_id") if exact else None,
            "plan_hash": exact.get("plan_hash") if exact else None,
            "session_id": exact.get("session_id") if exact else None,
            "allocation_id": exact.get("allocation_id") if exact else None,
            "decision_contributions": decisions,
            "inventory_effects": inventory_effects,
        }
        row["record_hash"] = _record_hash(row)
        causal_rows.append(row)

    if unresolved:
        raise CausalOwnershipError(
            "post-cutover broker fills lack exact-plan lineage: "
            + ",".join(row["activity_id"] for row in unresolved[:5])
        )

    causal_path = ledger_dir / "causal_fills.jsonl"
    existing = {str(row.get("activity_id") or ""): row for row in _read_jsonl(causal_path)}
    additions: list[dict[str, Any]] = []
    for row in causal_rows:
        prior = existing.get(row["activity_id"])
        if prior is not None and prior != row:
            raise CausalOwnershipError(
                f"append-only causal fill changed: {row['activity_id']}"
            )
        if prior is None:
            additions.append(row)
    if additions:
        causal_path.parent.mkdir(parents=True, exist_ok=True)
        with causal_path.open("a", encoding="utf-8") as handle:
            for row in additions:
                handle.write(_canonical(row) + "\n")

    positions_payload = _read_json(ledger_dir / "positions_latest.json")
    as_of = str(positions_payload.get("pulled_at_utc") or "").strip()
    broker_positions = positions_payload.get("positions") or []
    if not as_of or not isinstance(broker_positions, list):
        raise CausalOwnershipError("broker positions snapshot is malformed")
    broker_quantities = {
        str(row.get("symbol") or "").strip().upper(): float(row.get("qty") or 0.0)
        for row in broker_positions
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip()
    }
    ownership_rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    all_symbols = sorted(set(ownership) | set(broker_quantities))
    for symbol in all_symbols:
        owners = {
            owner: quantity
            for owner, quantity in ownership.get(symbol, {}).items()
            if abs(quantity) > QUANTITY_TOLERANCE
        }
        owned_total = sum(owners.values())
        broker_total = broker_quantities.get(symbol, 0.0)
        if abs(owned_total - broker_total) > QUANTITY_TOLERANCE:
            mismatches.append(
                {
                    "symbol": symbol,
                    "causal_quantity": owned_total,
                    "broker_quantity": broker_total,
                }
            )
        for owner, quantity in sorted(owners.items()):
            ownership_rows.append(
                {"symbol": symbol, "sleeve_id": owner, "quantity": quantity}
            )
    if mismatches:
        raise CausalOwnershipError(
            "causal ownership does not reconcile to broker positions: "
            + ",".join(row["symbol"] for row in mismatches[:5])
        )

    ownership_payload = {
        "schema_version": OWNERSHIP_SCHEMA,
        "as_of": as_of,
        "causal_epoch": causal_epoch.isoformat() if causal_epoch else None,
        "source_fill_count": len(fills),
        "attributed_fill_count": sum(
            row["attribution_status"] == "ATTRIBUTED" for row in causal_rows
        ),
        "legacy_unattributed_fill_count": sum(
            row["attribution_status"] == "LEGACY_UNATTRIBUTED" for row in causal_rows
        ),
        "positions": ownership_rows,
        "reconciliation": {"status": "PASS", "quantity_tolerance": QUANTITY_TOLERANCE},
    }
    ownership_payload["content_hash"] = _hash(ownership_payload)
    _atomic_json(ledger_dir / "ownership_latest.json", ownership_payload)

    snapshots = _read_jsonl(ledger_dir / "account_snapshots.jsonl")
    account = next(
        (row for row in reversed(snapshots) if str(row.get("pulled_at_utc") or "") == as_of),
        None,
    )
    if account is None:
        raise CausalOwnershipError(
            "account and positions snapshots do not share one explicit as-of"
        )
    ownership_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in ownership_rows:
        ownership_by_symbol.setdefault(row["symbol"], []).append(row)
    valued_positions: list[dict[str, Any]] = []
    sleeve_values: dict[str, float] = {}
    for raw in sorted(broker_positions, key=lambda row: str(row.get("symbol") or "")):
        symbol = str(raw.get("symbol") or "").strip().upper()
        quantity = float(raw.get("qty") or 0.0)
        market_value = float(raw.get("market_value") or 0.0)
        owners = ownership_by_symbol.get(symbol, [])
        attributed = []
        remaining_value = market_value
        for position, owner in enumerate(owners):
            value = (
                remaining_value
                if position == len(owners) - 1
                else market_value * float(owner["quantity"]) / quantity
            )
            remaining_value -= value
            sleeve_id = str(owner["sleeve_id"])
            sleeve_values[sleeve_id] = sleeve_values.get(sleeve_id, 0.0) + value
            attributed.append(
                {
                    "sleeve_id": sleeve_id,
                    "quantity": float(owner["quantity"]),
                    "market_value": value,
                }
            )
        valued_positions.append(
            {
                "symbol": symbol,
                "quantity": quantity,
                "market_value": market_value,
                "current_price": float(raw.get("current_price") or 0.0),
                "cost_basis": float(raw.get("cost_basis") or 0.0),
                "unrealized_pl": float(raw.get("unrealized_pl") or 0.0),
                "ownership": attributed,
            }
        )
    equity = float(account.get("equity") or 0.0)
    cash = float(account.get("cash") or 0.0)
    positions_market_value = sum(
        float(row.get("market_value") or 0.0) for row in valued_positions
    )
    valuation_difference = equity - cash - positions_market_value
    valuation_tolerance = max(0.01, abs(equity) / 10_000.0)
    if abs(valuation_difference) > valuation_tolerance:
        raise CausalOwnershipError(
            "broker equity does not reconcile to cash plus current positions"
        )
    valuation = {
        "schema_version": VALUATION_SCHEMA,
        "as_of": as_of,
        "equity": equity,
        "cash": cash,
        "positions_market_value": positions_market_value,
        "positions": valued_positions,
        "sleeve_market_values": [
            {"sleeve_id": sleeve, "market_value": value}
            for sleeve, value in sorted(sleeve_values.items())
        ],
        "cash_ownership": {
            "sleeve_id": "portfolio_cash",
            "market_value": cash,
        },
        "sources": {
            "account_snapshot": "account_snapshots.jsonl",
            "positions_snapshot": "positions_latest.json",
            "ownership": "ownership_latest.json",
        },
        "reconciliation": {
            "status": "PASS",
            "equity_minus_cash_and_positions": valuation_difference,
            "tolerance": valuation_tolerance,
        },
    }
    valuation["content_hash"] = _hash(valuation)
    _atomic_json(ledger_dir / "valuation_latest.json", valuation)

    history_path = ledger_dir / "ownership_history.jsonl"
    existing_history = _read_jsonl(history_path)
    if not existing_history or existing_history[-1].get("content_hash") != ownership_payload["content_hash"]:
        with history_path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical(ownership_payload) + "\n")
    return {
        "status": "PASS",
        "as_of": as_of,
        "causal_epoch": causal_epoch.isoformat() if causal_epoch else None,
        "fills": len(causal_rows),
        "new_causal_fill_rows": len(additions),
        "ownership_positions": len(ownership_rows),
        "valuation_hash": valuation["content_hash"],
    }
