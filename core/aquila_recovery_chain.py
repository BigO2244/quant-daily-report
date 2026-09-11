"""Closed two-parent ownership proof for one specifically approved PAPER recovery."""
from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
import json
from pathlib import Path
import re

from authority.exact_plan import compute_starting_state_hash, exact_execution_plan_from_dict
from core.aquila_recovery_ownership import (
    _body_hash, _build_orion_sell_recovery_bridge, _quantities, _require, _sha,
)
from core.paper_drill_epoch import plan_drill_epoch
from core.portfolio_operating_model import content_hash
from core.sleeve_ownership_transfer import validate_transfers
from core.submission_wal import (
    OrderIntent, canonical_broker_fill_evidence, economic_reconciliation_proof,
    read_resolutions, validate_broker_order_evidence,
)


def build_aquila_recovery_chain(*, book, contract, allocation, repo_root,
        recovery_policy, epoch, account_hash, broker_positions, broker_cash,
        lookup_by_client_order_id, open_orders):
    root = Path(repo_root).resolve()
    def path(value):
        item = Path(value)
        return (item if item.is_absolute() else root / item).resolve()
    config = recovery_policy.get("ownership_bridge_chain") or {}
    parents = config.get("parents")
    _require(isinstance(parents, list) and len(parents) == 2, "chain requires exactly two bound parents")
    _require(epoch in recovery_policy.get("allowed_epochs", []), "chain recovery epoch not approved")
    _require(isinstance(open_orders, list) and not open_orders, "chain prohibits open orders")
    p0, p1 = parents
    _require(p0.get("wal_namespace") == "canonical", "chain P0 must be canonical")
    prior_epoch = str(p1.get("wal_namespace") or "")
    _require(re.fullmatch(r"\d{4}-\d{2}-\d{2}T(?:[01]\d|2[0-3])[0-5]\dET", prior_epoch)
             and prior_epoch < str(epoch), "chain P1 epoch must precede recovery")
    plans, envelopes, id_sets, intent_sets = [], [], [], []
    wal = root / "outputs/paper_lane/submission_wal"
    for index, parent in enumerate(parents):
        ids = parent.get("expected_client_order_ids")
        _require(isinstance(ids, list) and len(ids) == (5 if index == 0 else 3)
                 and all(isinstance(v, str) and v for v in ids) and len(ids) == len(set(ids)),
                 "chain requires unique closed parent client IDs")
        source = path(parent["exact_plan_path"])
        _require(_sha(source) == parent.get("exact_plan_file_sha256"), "chain parent envelope file changed")
        envelope = json.loads(source.read_text())
        plan = exact_execution_plan_from_dict(envelope["exact_execution_plan"])
        _require(plan.plan_id == parent.get("plan_id") and plan.content_hash == parent.get("plan_hash")
                 and envelope.get("exact_execution_plan_hash") == plan.content_hash, "chain parent plan identity mismatch")
        _require(plan.account_scope == "PAPER" and plan.account_id_hash == account_hash == book.get("account_id_hash"), "chain account mismatch")
        _require(plan.trade_date == contract.get("trade_date") == allocation.get("trade_date") == envelope.get("trade_date")
                 and prior_epoch[:10] == plan.trade_date == str(epoch)[:10], "chain date mismatch")
        _require(envelope.get("approved_target_hash") == config.get("approved_target_hash") and bool(config.get("approved_target_hash")), "chain target mismatch")
        _require(envelope.get("allocation_id") == allocation.get("allocation_id") and envelope.get("session_id") == allocation.get("session_id"), "chain allocation/session mismatch")
        _require(plan_drill_epoch(plan) == (None if index == 0 else prior_epoch), "chain WAL epoch mismatch")
        namespace = wal if index == 0 else wal / "epochs" / prior_epoch
        files = sorted((namespace / plan.trade_date / "intents").glob("*.json"))
        _require({f.stem for f in files} == set(ids) and len(files) == len(ids), "chain durable client set mismatch")
        exact_by_client = {r["client_order_id"]: r for r in plan.orders}
        for intent_file in files:
            intent = OrderIntent.from_dict(json.loads(intent_file.read_text()))
            exact = exact_by_client.get(intent.client_order_id)
            _require(exact is not None and intent.client_order_id == intent_file.stem
                     and intent.plan_id == plan.plan_id and intent.plan_hash == plan.content_hash
                     and intent.starting_state_hash == plan.starting_state_hash,
                     "chain durable intent parent mismatch")
            _require(intent.order_id == exact["order_id"] and intent.symbol == exact["symbol"]
                     and intent.side == exact["side"] and Decimal(str(intent.quantity)) == Decimal(str(exact["quantity"]))
                     and intent.order_type == exact["order_type"]
                     and intent.time_in_force == exact.get("time_in_force", "day")
                     and intent.extended_hours == exact.get("extended_hours", False)
                     and intent.limit_price == exact.get("limit_price")
                     and intent.stop_price == exact.get("stop_price"), "chain durable order contract mismatch")
        plans.append(plan); envelopes.append(envelope); id_sets.append(set(ids)); intent_sets.append(files)
    _require(plans[0].plan_id != plans[1].plan_id and plans[0].content_hash != plans[1].content_hash
             and not id_sets[0].intersection(id_sets[1]), "chain parent identities overlap")
    day = plans[0].trade_date
    all_expected = {str(p.resolve()) for files in intent_sets for p in files}
    all_actual = {str(p.resolve()) for p in wal.glob(f"**/{day}/intents/*.json")}
    _require(all_actual == all_expected, "chain contains extra intent namespace")
    _require(all(p.name == prior_epoch for p in (wal / "epochs").glob(day + "*") if p.is_dir()), "chain contains extra epoch namespace")
    # Resolve the P0 terminal intermediate state without treating it as current
    # broker state. The P1 starting hash and final broker proof bind both edges.
    first = OrderIntent.from_dict(json.loads(intent_sets[0][0].read_text()))
    proof0 = economic_reconciliation_proof(first, read_resolutions(wal, trade_date=day, client_order_id=first.client_order_id))
    _require(proof0 is not None and proof0.final_state_hash == plans[1].starting_state_hash, "chain intermediate state mismatch")
    p0_policy = {"allowed_epochs": [prior_epoch], "ownership_bridge": {
        "prior_exact_plan_path": p0["exact_plan_path"], "prior_exact_plan_file_sha256": p0["exact_plan_file_sha256"],
        "approved_target_hash": config["approved_target_hash"]}}
    intermediate, evidence0 = _build_orion_sell_recovery_bridge(
        book=book, contract=contract, allocation=allocation, repo_root=root,
        recovery_policy=p0_policy, epoch=prior_epoch, account_hash=account_hash,
        broker_positions=plans[1].to_dict()["starting_positions"], broker_cash=plans[1].starting_cash,
        lookup_by_client_order_id=lookup_by_client_order_id, open_orders=[],
        allowed_epoch_intents={str(p.resolve()): _sha(p) for p in intent_sets[1]},
    )
    p0_cash = Decimal(str(plans[0].starting_cash)) + sum(
        Decimal(str(f["filled_quantity"])) * Decimal(str(f["fill_price"])) for f in evidence0["applied_fills"])
    _require(abs(p0_cash - Decimal(str(proof0.final_cash))) <= Decimal("0.01"), "chain unexplained P0 economic cash")
    plan, envelope = plans[1], envelopes[1]
    qa = plan.to_dict()["constraints"].get("aquila_quantity_authority") or {}
    _require(qa.get("quantity_contract") == contract and qa.get("ownership_snapshot_sha256") == contract.get("ownership_snapshot_sha256"), "chain frozen quantity parent mismatch")
    embedded = qa.get("recovery_ownership_bridge") or {}
    _require(_body_hash(embedded) == evidence0["content_hash"], "chain embedded P0 bridge mismatch")
    for label, supplied in (("portfolio_allocation", allocation), ("session_manifest", None)):
        key = "source_" + label
        source = path(envelope[key]); digest = _sha(source)
        aliases = [h for ref, h in plan.source_artifact_hashes.items() if path(ref) == source]
        _require(aliases and all(h == digest for h in aliases) and envelope[key + "_sha256"] == digest, "chain P1 source hash mismatch")
        obj = json.loads(source.read_text())
        _require(obj == supplied if supplied is not None else (_body_hash(obj) == allocation.get("session_hash") and obj.get("session_id") == allocation.get("session_id")), "chain P1 source content mismatch")
    planned = qa.get("internal_transfers") or []
    validate_transfers(qa)
    receipt_path = root / "outputs/paper_lane/ownership_transfers" / f"{plan.content_hash}.json"
    _require(not receipt_path.exists() and not receipt_path.is_symlink(), "chain matching transfer receipt")
    for source in sorted(receipt_path.parent.glob("*.json")):
        try:
            receipt = json.loads(source.read_text())
        except (ValueError, OSError):
            _require(False, "chain ambiguous transfer receipt")
        _require(isinstance(receipt, dict) and receipt.get("plan_id") and receipt.get("plan_hash"), "chain ambiguous transfer receipt")
        _require(receipt["plan_id"] not in {p.plan_id for p in plans} and receipt["plan_hash"] not in {p.content_hash for p in plans}, "chain matching transfer receipt")
    current = {s: {owner: Decimal(str(q)) for owner,q in owners.items()} for s,owners in intermediate.items()}
    orders = {r["client_order_id"]: r for r in plan.orders}
    expected_shapes = {("INTC", "SELL", "caerus_orion"), ("AAPL", "BUY", "caerus_aquila"), ("AMZN", "BUY", "caerus_aquila")}
    seen_shapes, applied, files, common, common_hash = set(), [], [], None, None
    namespace = wal / "epochs" / prior_epoch
    for source in intent_sets[1]:
        intent = OrderIntent.from_dict(json.loads(source.read_text()))
        _require(intent.plan_id == plan.plan_id and intent.plan_hash == plan.content_hash and intent.trade_date == day and intent.paper_drill_epoch == prior_epoch, "chain intent lineage mismatch")
        order = orders.get(intent.client_order_id)
        _require(order is not None and intent.order_id == order["order_id"] and intent.symbol == order["symbol"] and intent.side == order["side"] and Decimal(str(intent.quantity)) == Decimal(str(order["quantity"])), "chain intent economics mismatch")
        contributions = order.get("sleeve_contributions") or []
        _require(len(contributions) == 1 and Decimal(str(contributions[0].get("allocation_fraction"))) == 1, "chain mixed ownership")
        owner = contributions[0].get("sleeve_id"); shape = (intent.symbol, intent.side, owner)
        _require(shape in expected_shapes and shape not in seen_shapes, "chain unexpected fill owner/side/symbol")
        seen_shapes.add(shape)
        events = read_resolutions(namespace, trade_date=day, client_order_id=intent.client_order_id)
        proof = economic_reconciliation_proof(intent, events)
        _require(proof is not None and proof.starting_state_hash == plan.starting_state_hash and proof.paper_drill_epoch == prior_epoch, "chain missing P1 economic proof")
        digest = content_hash(asdict(proof))
        if common is None: common, common_hash = proof, digest
        _require(digest == common_hash, "chain inconsistent P1 proofs")
        observed = lookup_by_client_order_id(intent.client_order_id)
        evidence = validate_broker_order_evidence(intent, observed, resolution_events=events)
        _require(evidence.status == "filled" and Decimal(str(evidence.filled_quantity)) == Decimal(str(intent.quantity)), "chain partial/unfilled broker evidence")
        fill = canonical_broker_fill_evidence(intent, evidence)
        _require(fill in proof.broker_fills, "chain fresh fill differs from proof")
        owners = current.setdefault(intent.symbol, {}); quantity = Decimal(str(evidence.filled_quantity))
        prior = owners.get(owner, Decimal(0))
        _require(intent.side != "SELL" or prior >= quantity, "chain ownership underflow")
        owners[owner] = prior + quantity * (-1 if intent.side == "SELL" else 1)
        applied.append(fill)
        resolutions = sorted((namespace / day / "resolutions" / intent.client_order_id).glob("*.json"))
        _require(bool(resolutions), "chain missing persisted resolutions")
        files.append({"intent_path": str(source.relative_to(root)), "intent_sha256": _sha(source), "resolutions": [{"path": str(r.relative_to(root)), "sha256": _sha(r)} for r in resolutions]})
    _require(seen_shapes == expected_shapes and {f["client_order_id"] for f in common.broker_fills} == id_sets[1], "chain proof includes extra/missing fills")
    _require(id_sets[1] < set(orders) and any(r["side"] == "BUY" and r["client_order_id"] not in id_sets[1] for r in plan.orders), "chain completed/ambiguous transfer plan")
    aggregate = {s: sum(owners.values()) for s, owners in current.items() if sum(owners.values()) > 0}
    _require(aggregate == _quantities(common.final_positions) == _quantities(broker_positions), "chain final ownership state mismatch")
    _require(Decimal(str(common.final_cash)) == Decimal(str(broker_cash)) and compute_starting_state_hash(broker_positions, broker_cash) == common.final_state_hash, "chain final broker cash/state mismatch")
    economic_cash = Decimal(str(plan.starting_cash)) + sum(Decimal(str(f["filled_quantity"])) * Decimal(str(f["fill_price"])) * (1 if f["side"] == "SELL" else -1) for f in applied)
    _require(abs(economic_cash - Decimal(str(common.final_cash))) <= Decimal("0.01"), "chain unexplained economic cash")
    current = {s: {owner: float(q) for owner,q in owners.items()} for s,owners in current.items()}
    result = {"schema_version": "caerus.aquila_recovery_chain.v1", "epoch": epoch, "trade_date": day,
              "account_id_hash": account_hash, "frozen_ownership_hash": book["content_hash"],
              "frozen_ownership_sha256": contract["ownership_snapshot_sha256"], "quantity_contract_hash": contract["content_hash"],
              "allocation_hash": allocation["content_hash"], "allocation_id": allocation["allocation_id"],
              "session_hash": allocation["session_hash"], "session_id": allocation["session_id"],
              "approved_target_hash": config["approved_target_hash"], "parents": parents,
              "p0_bridge": evidence0, "p1_economic_proof": asdict(common), "p1_economic_proof_hash": common_hash,
              "p1_wal_files": files, "p1_applied_fills": applied, "planned_p1_transfers": planned,
              "internal_transfers_applied": False, "p1_transfer_receipt_absent_path": str(receipt_path.relative_to(root)),
              "original_plans_incomplete": True, "current_state_hash": common.final_state_hash,
              "current_positions": list(broker_positions), "current_cash": float(broker_cash), "derived_ownership": current}
    result["content_hash"] = content_hash(result)
    return current, result
