"""Read-only, hash-bound ownership projection for an approved Orion sell recovery."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from authority.exact_plan import compute_starting_state_hash, exact_execution_plan_from_dict
from core.portfolio_operating_model import content_hash
from core.sleeve_ownership_transfer import validate_transfers
from core.submission_wal import (
    OrderIntent, canonical_broker_fill_evidence, economic_reconciliation_proof,
    read_resolutions, validate_broker_order_evidence,
)


class OwnershipBridgeError(RuntimeError):
    pass


def _require(ok, reason):
    if not ok:
        raise OwnershipBridgeError(reason)


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _body_hash(value):
    body = dict(value)
    digest = body.pop("content_hash", None)
    _require(bool(digest) and content_hash(body) == digest, "invalid parent content hash")
    return digest


def _quantities(rows):
    result = {}
    for row in rows:
        symbol = str(row.get("symbol") or "")
        quantity = Decimal(str(row.get("quantity", row.get("qty"))))
        _require(symbol and symbol not in result and quantity.is_finite() and quantity >= 0,
                 "invalid aggregate position")
        result[symbol] = quantity
    return {s: q for s, q in result.items() if q > 0}


def build_orion_sell_recovery_bridge(*, book, contract, allocation, repo_root,
        recovery_policy, epoch, account_hash, broker_positions, broker_cash,
        lookup_by_client_order_id, open_orders):
    return _build_orion_sell_recovery_bridge(
        book=book, contract=contract, allocation=allocation, repo_root=repo_root,
        recovery_policy=recovery_policy, epoch=epoch, account_hash=account_hash,
        broker_positions=broker_positions, broker_cash=broker_cash,
        lookup_by_client_order_id=lookup_by_client_order_id, open_orders=open_orders,
        allowed_epoch_intents={},
    )


def _build_orion_sell_recovery_bridge(*, book, contract, allocation, repo_root,
        recovery_policy, epoch, account_hash, broker_positions, broker_cash,
        lookup_by_client_order_id, open_orders, allowed_epoch_intents):
    """Subtract only original-plan, fully proven Orion sells from the frozen book.

    All artifacts are read from their governed paths. The caller embeds the
    returned evidence in its new immutable exact plan; this helper writes none.
    """
    root = Path(repo_root).resolve()
    def path(value):
        candidate = Path(value)
        return candidate if candidate.is_absolute() else root / candidate
    _require(epoch in recovery_policy.get("allowed_epochs", []), "recovery epoch not approved")
    _require(isinstance(open_orders, list) and not open_orders, "open orders prohibit ownership bridge")
    config = recovery_policy.get("ownership_bridge") or {}
    original_path = path(config["prior_exact_plan_path"])
    _require(_sha(original_path) == config.get("prior_exact_plan_file_sha256"), "prior exact envelope hash mismatch")
    envelope = json.loads(original_path.read_text())
    plan = exact_execution_plan_from_dict(envelope["exact_execution_plan"])
    day = plan.trade_date
    _require(str(epoch).startswith(day + "T"), "recovery epoch date mismatch")
    _require(plan.account_scope == "PAPER" and plan.account_id_hash == account_hash == book.get("account_id_hash"), "account mismatch")
    _require(envelope.get("trade_date") == day == contract.get("trade_date") == allocation.get("trade_date"), "parent date mismatch")
    _require(envelope.get("exact_execution_plan_hash") == plan.content_hash, "envelope exact plan hash mismatch")
    _require(envelope.get("allocation_id") == allocation.get("allocation_id") and envelope.get("session_id") == allocation.get("session_id"), "allocation/session identity mismatch")
    _require(bool(config.get("approved_target_hash")) and envelope.get("approved_target_hash") == config["approved_target_hash"], "approved target mismatch")
    book_hash, contract_hash, allocation_hash = map(_body_hash, (book, contract, allocation))
    _require(book_hash == contract.get("ownership_snapshot_hash"), "frozen ownership content mismatch")
    book_path = path(contract["ownership_snapshot_path"])
    _require(_sha(book_path) == contract.get("ownership_snapshot_sha256") and json.loads(book_path.read_text()) == book, "frozen ownership file mismatch")
    _require(book.get("opening_contract_hash") and (book.get("reconciliation") or {}).get("status") == "PASS", "frozen book not reconciled")
    authority = plan.to_dict()["constraints"].get("aquila_quantity_authority") or {}
    _require(dict(authority.get("quantity_contract") or {}) == contract, "original quantity contract mismatch")
    _require(authority.get("ownership_snapshot_sha256") == contract["ownership_snapshot_sha256"], "original ownership parent mismatch")
    planned_transfers = authority.get("internal_transfers") or []
    transfer_receipt_path = root / "outputs/paper_lane/ownership_transfers" / f"{plan.content_hash}.json"
    if planned_transfers:
        validate_transfers(authority)
        _require(not transfer_receipt_path.exists() and not transfer_receipt_path.is_symlink(),
                 "committed or ambiguous transfer receipt prohibits bridge")
        # A misplaced matching receipt is still evidence of a possible commit.
        # Malformed receipts cannot establish that they belong to another plan.
        for receipt_path in sorted(transfer_receipt_path.parent.glob("*.json")):
            try:
                receipt = json.loads(receipt_path.read_text())
            except (ValueError, OSError) as exc:
                raise OwnershipBridgeError("ambiguous transfer receipt") from exc
            _require(isinstance(receipt, Mapping) and receipt.get("plan_hash") and receipt.get("plan_id"),
                     "ambiguous transfer receipt identity")
            _require(receipt["plan_hash"] != plan.content_hash and receipt["plan_id"] != plan.plan_id,
                     "matching transfer receipt prohibits bridge")
    _require((allocation.get("quantity_contracts") or {}).get("caerus_aquila") == contract, "allocation quantity contract mismatch")
    source_hashes = {}
    for label, supplied in (("portfolio_allocation", allocation), ("session_manifest", None)):
        key = "source_" + label
        source_path = path(envelope[key])
        digest = _sha(source_path)
        matching_hashes = [declared for source_ref, declared in plan.source_artifact_hashes.items()
                           if path(source_ref).resolve() == source_path.resolve()]
        _require(digest == envelope[key + "_sha256"] and bool(matching_hashes)
                 and all(declared == digest for declared in matching_hashes),
                 "original source file lineage mismatch")
        source = json.loads(source_path.read_text())
        if supplied is not None:
            _require(source == supplied, "original allocation differs")
        else:
            _require(_body_hash(source) == allocation.get("session_hash") and source.get("session_id") == allocation.get("session_id"), "session content mismatch")
        source_hashes[label] = digest
    current = {}
    for row in book.get("positions", []):
        symbol, owner = str(row.get("symbol") or ""), row.get("sleeve_id")
        quantity = Decimal(str(row.get("quantity")))
        _require(symbol and owner in {"caerus_orion", "caerus_aquila"} and quantity.is_finite() and quantity >= 0, "unknown/invalid frozen owner")
        _require(owner not in current.setdefault(symbol, {}), "duplicate frozen owner")
        current[symbol][owner] = quantity
    frozen_aggregate = {s: sum(owners.values()) for s, owners in current.items() if sum(owners.values()) > 0}
    _require(frozen_aggregate == _quantities(plan.starting_positions), "frozen book differs from original starting positions")
    wal = root / "outputs/paper_lane/submission_wal"
    intents = sorted((wal / day / "intents").glob("*.json"))
    _require(bool(intents), "original durable intents missing")
    # Epoch WALs must not hide another same-day economic transition.
    epoch_intents = {str(p.resolve()): _sha(p) for p in (wal / "epochs").glob(f"*/{day}/intents/*.json")}
    _require(epoch_intents == allowed_epoch_intents, "extra epoch intents prohibit bridge")
    orders = {r["client_order_id"]: r for r in plan.orders}
    applied, receipts, common, common_hash = [], [], None, None
    for intent_file in intents:
        intent = OrderIntent.from_dict(json.loads(intent_file.read_text()))
        _require(intent.plan_id == plan.plan_id and intent.plan_hash == plan.content_hash and intent.trade_date == day and not intent.paper_drill_epoch, "multiple or nonoriginal durable plans")
        order = orders.get(intent.client_order_id)
        _require(order is not None and order["order_id"] == intent.order_id and intent.side == "SELL" and order["side"] == "SELL", "nonoriginal or buy intent")
        contributions = order.get("sleeve_contributions") or []
        _require(len(contributions) == 1 and contributions[0].get("sleeve_id") == "caerus_orion" and float(contributions[0].get("allocation_fraction", 0)) == 1.0, "mixed or non-Orion contribution")
        _require(intent.symbol == order["symbol"] and float(intent.quantity) == float(order["quantity"]), "intent economics mismatch")
        events = read_resolutions(wal, trade_date=day, client_order_id=intent.client_order_id)
        proof = economic_reconciliation_proof(intent, events)
        _require(proof is not None and proof.starting_state_hash == plan.starting_state_hash, "missing original economic proof")
        proof_body = asdict(proof)
        proof_hash = content_hash(proof_body)
        if common is None:
            common, common_hash = proof, proof_hash
        _require(proof_hash == common_hash, "different economic proofs")
        observed = lookup_by_client_order_id(intent.client_order_id)
        evidence = validate_broker_order_evidence(intent, observed, resolution_events=events)
        _require(evidence.status == "filled" and evidence.filled_quantity == float(intent.quantity), "partial or nonfilled order")
        fill = canonical_broker_fill_evidence(intent, evidence)
        _require(fill in proof.broker_fills, "fresh fill differs from proof")
        available = current.get(intent.symbol, {}).get("caerus_orion", Decimal(0))
        filled_quantity = Decimal(str(evidence.filled_quantity))
        _require(available >= filled_quantity, "Orion ownership underflow")
        current[intent.symbol]["caerus_orion"] = available - filled_quantity
        applied.append(fill)
        resolution_files = sorted((wal / day / "resolutions" / intent.client_order_id).glob("*.json"))
        _require(bool(resolution_files), "persisted resolution files missing")
        receipts.append({"intent_path": str(intent_file.relative_to(root)), "intent_sha256": _sha(intent_file),
                         "resolutions": [{"path": str(p.relative_to(root)), "sha256": _sha(p)} for p in resolution_files]})
    _require({f["client_order_id"] for f in common.broker_fills} == {f["client_order_id"] for f in applied}, "extra or missing proof fills")
    if planned_transfers:
        durable_ids = {f["client_order_id"] for f in applied}
        buy_ids = {r["client_order_id"] for r in plan.buy_orders}
        _require(durable_ids < set(orders) and bool(buy_ids) and not durable_ids.intersection(buy_ids),
                 "completed or ambiguous transfer plan cannot bridge without receipt")
    aggregate = {s: sum(owners.values()) for s, owners in current.items() if sum(owners.values()) > 0}
    _require(aggregate == _quantities(common.final_positions) == _quantities(broker_positions), "unexplained final ownership state")
    _require(compute_starting_state_hash(broker_positions, broker_cash) == common.final_state_hash, "fresh broker state/cash differs from proof")
    expected_cash = plan.starting_cash + sum(f["filled_quantity"] * f["fill_price"] for f in applied)
    _require(abs(expected_cash - common.final_cash) <= 0.01, "unexplained proof cash")
    # Keep exact decimal arithmetic through every ownership comparison; only
    # convert at the caller/JSON boundary after the state has been proven.
    current = {s: {owner: float(q) for owner, q in owners.items()} for s, owners in current.items()}
    result = {"schema_version": "caerus.orion_sell_recovery_ownership.v1", "trade_date": day,
              "epoch": epoch, "account_id_hash": account_hash,
              "original_plan_id": plan.plan_id, "original_plan_hash": plan.content_hash,
              "original_exact_plan_path": str(config["prior_exact_plan_path"]),
              "opening_contract_hash": book["opening_contract_hash"],
              "original_envelope_sha256": config["prior_exact_plan_file_sha256"],
              "frozen_ownership_hash": book_hash, "frozen_ownership_sha256": contract["ownership_snapshot_sha256"],
              "quantity_contract_hash": contract_hash, "allocation_id": allocation["allocation_id"],
              "allocation_hash": allocation_hash, "session_id": allocation["session_id"],
              "session_hash": allocation["session_hash"], "approved_target_hash": config["approved_target_hash"],
              "parent_source_sha256": source_hashes, "economic_proof_hash": common_hash,
              "economic_proof": asdict(common), "wal_files": receipts, "applied_fills": applied,
              "current_state_hash": common.final_state_hash, "current_positions": list(broker_positions),
              "current_cash": float(broker_cash), "derived_ownership": current,
              "planned_internal_transfers": planned_transfers,
              "internal_transfers_applied": False,
              "transfer_receipt_absent_path": str(transfer_receipt_path.relative_to(root)) if planned_transfers else None,
              "original_plan_incomplete": len(applied) < len(plan.orders)}
    result["content_hash"] = content_hash(result)
    return current, result
