import copy
import hashlib
import json

import pytest

pytestmark = pytest.mark.usefixtures("orion_registry")

from core.aquila_recovery_ownership import build_orion_sell_recovery_bridge
from core.portfolio_operating_model import content_hash
from core.submission_wal import OrderIntent, prepare_order_intent, validate_broker_order_evidence
from execution.exact_executor import _append_broker_observation, _persist_economic_reconciliation
from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact
from Tests.fixtures.orion_registry import orion_registry


def seal(value):
    value.pop("content_hash", None)
    value["content_hash"] = content_hash(value)
    return value


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixture(tmp_path, *, owner="caerus_orion", proof=True, side="SELL", transfers=False, fractional=False, complete_plan=False, source_aliases="relative"):
    initial = _plan()
    if complete_plan:
        initial = _rebuild_exact(initial.to_dict(), buy_orders=[], expected_posttrade_positions=[], expected_posttrade_cash=1000)
    if fractional:
        sell = initial.to_dict()["sell_orders"][0]
        sell.update(quantity=1.327061, notional=132.7061)
        initial = _rebuild_exact(initial.to_dict(), constraints={**initial.to_dict()["constraints"], "allow_fractional": True},
                                 starting_positions=[{"symbol": "OLD", "quantity": 10.728862}],
                                 sell_orders=[sell], expected_posttrade_positions=[{"symbol": "OLD", "quantity": 9.401801}, {"symbol": "AAPL", "quantity": 2}],
                                 expected_posttrade_cash=932.7061)
    book = seal({"account_id_hash": initial.account_id_hash, "opening_contract_hash": "e"*64,
                 "reconciliation": {"status": "PASS"},
                 "positions": [{"symbol": "OLD", "sleeve_id": owner, "quantity": 10.728862 if fractional else 1.0}]})
    book_sha = write(tmp_path / "frozen.json", book)
    contract = seal({"trade_date": initial.trade_date, "ownership_snapshot_hash": book["content_hash"],
                     "ownership_snapshot_path": "frozen.json", "ownership_snapshot_sha256": book_sha})
    session = seal({"session_id": "session:test"})
    session_sha = write(tmp_path / "session.json", session)
    allocation = seal({"trade_date": initial.trade_date, "allocation_id": "allocation:test",
                       "session_id": session["session_id"], "session_hash": session["content_hash"],
                       "quantity_contracts": {"caerus_aquila": contract}})
    allocation_sha = write(tmp_path / "allocation.json", allocation)
    constraints = initial.to_dict()["constraints"]
    constraints["aquila_quantity_authority"] = {"quantity_contract": contract, "ownership_snapshot_sha256": book_sha, "internal_transfers": ["unsupported"] if transfers else []}
    if transfers == "valid":
        from core.sleeve_ownership_transfer import net_demands
        demands = {"OLD": {"caerus_orion": -1.5, "caerus_aquila": .5}}
        decisions = {owner: {"decision_id": "decision:" + owner, "decision_hash": "a"*64}
                     for owner in ("caerus_orion", "caerus_aquila")}
        residual, planned = net_demands(demands, {"OLD": 100}, decisions)
        constraints["aquila_quantity_authority"].update(internal_transfers=planned,
            signed_sleeve_demands=demands, broker_sleeve_demands=residual,
            ownership_snapshot_sha256=book_sha)
    source_hashes = {"allocation.json": allocation_sha, "session.json": session_sha}
    if source_aliases in {"absolute", "consistent", "conflicting"}:
        source_hashes = {str(tmp_path / name): digest for name, digest in source_hashes.items()}
    if source_aliases in {"consistent", "conflicting"}:
        source_hashes["allocation.json"] = allocation_sha if source_aliases == "consistent" else "f"*64
    plan = _rebuild_exact(initial.to_dict(), constraints=constraints, source_artifact_hashes=source_hashes)
    envelope = {"trade_date": plan.trade_date, "exact_execution_plan": plan.to_dict(),
                "exact_execution_plan_hash": plan.content_hash, "allocation_id": allocation["allocation_id"],
                "session_id": session["session_id"], "approved_target_hash": "d"*64,
                "source_portfolio_allocation": "allocation.json", "source_portfolio_allocation_sha256": allocation_sha,
                "source_session_manifest": "session.json", "source_session_manifest_sha256": session_sha}
    original_sha = write(tmp_path / "original.json", envelope)
    row = dict(plan.sell_orders[0] if side == "SELL" else plan.buy_orders[0])
    intent = OrderIntent(trade_date=plan.trade_date, plan_id=plan.plan_id, plan_hash=plan.content_hash,
                         attempt_id="original-run", order_id=row["order_id"], client_order_id=row["client_order_id"],
                         symbol=row["symbol"], side=row["side"], quantity=row["quantity"], order_type="limit",
                         limit_price=row["limit_price"], created_at=plan.created_at, starting_state_hash=plan.starting_state_hash)
    wal = tmp_path / "outputs/paper_lane/submission_wal"
    intent = prepare_order_intent(wal, intent).intent
    observed = {"id": "broker-order", "client_order_id": intent.client_order_id, "symbol": intent.symbol,
                "side": intent.side, "qty": str(intent.quantity), "status": "filled",
                "filled_qty": str(intent.quantity), "filled_avg_price": str(row["limit_price"])}
    evidence = validate_broker_order_evidence(intent, observed)
    _append_broker_observation(wal, intent=intent, evidence=evidence)
    positions = [] if side == "SELL" else [{"symbol": "OLD", "quantity": 1}, {"symbol": "AAPL", "quantity": 2}]
    cash = 1000 if side == "SELL" else 800
    if fractional:
        positions, cash = [{"symbol": "OLD", "quantity": 9.401801}], 1032.7061
    if proof:
        _persist_economic_reconciliation(plan=plan, wal_root=wal, durable_intents=[intent], observed_rows=[observed],
                                         final_positions=positions, final_cash=cash,
                                         reconciliation_status="TERMINAL_FAILURE_STATE_RECONCILED")
    epoch = plan.trade_date + "T1200ET"
    kwargs = dict(book=book, contract=contract, allocation=allocation, repo_root=tmp_path,
                  recovery_policy={"allowed_epochs": [epoch], "ownership_bridge": {
                      "prior_exact_plan_path": "original.json", "prior_exact_plan_file_sha256": original_sha,
                      "approved_target_hash": "d"*64}}, epoch=epoch, account_hash=plan.account_id_hash,
                  broker_positions=positions, broker_cash=cash,
                  lookup_by_client_order_id=lambda client: copy.deepcopy(observed), open_orders=[])
    return kwargs, observed


def test_bridge_subtracts_only_proven_sells_and_is_read_only(tmp_path):
    kwargs, _ = fixture(tmp_path)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    current, evidence = build_orion_sell_recovery_bridge(**kwargs)
    assert current == {"OLD": {"caerus_orion": 0.0}}
    assert evidence["applied_fills"][0]["side"] == "SELL"
    assert evidence["content_hash"] == content_hash({k:v for k,v in evidence.items() if k != "content_hash"})
    assert before == {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("case", ["missing_proof", "account", "target", "cash", "positions", "partial", "buy", "underflow", "transfer", "corrupt", "epoch", "open", "source_corrupt"])
def test_bridge_rejects_unproven_transitions(tmp_path, case):
    kwargs, observed = fixture(tmp_path, proof=case != "missing_proof", side="BUY" if case == "buy" else "SELL",
                               owner="caerus_aquila" if case == "underflow" else "caerus_orion", transfers=case == "transfer")
    if case == "account": kwargs["account_hash"] = "a"*64
    if case == "target": kwargs["recovery_policy"]["ownership_bridge"]["approved_target_hash"] = "f"*64
    if case == "cash": kwargs["broker_cash"] += 1
    if case == "positions": kwargs["broker_positions"] = [{"symbol": "EXTRA", "quantity": 1}]
    if case == "partial": observed.update(status="partially_filled", filled_qty="0.5")
    if case == "corrupt": (tmp_path / "original.json").write_text("{}")
    if case == "epoch": kwargs["epoch"] = "2026-08-12T1300ET"
    if case == "open": kwargs["open_orders"] = [{"id": "unexpected"}]
    if case == "source_corrupt": (tmp_path / "session.json").write_text("{}")
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        build_orion_sell_recovery_bridge(**kwargs)


def test_bridge_rejects_unknown_frozen_owner(tmp_path):
    kwargs, _ = fixture(tmp_path, owner="unknown_sleeve")
    with pytest.raises(RuntimeError, match="unknown/invalid frozen owner"):
        build_orion_sell_recovery_bridge(**kwargs)


def test_bridge_rejects_mixed_contribution_even_with_rehashed_envelope(tmp_path):
    kwargs, _ = fixture(tmp_path)
    original = tmp_path / "original.json"
    envelope = json.loads(original.read_text())
    row = envelope["exact_execution_plan"]["sell_orders"][0]
    row["sleeve_contributions"][0]["allocation_fraction"] = .5
    row["sleeve_contributions"].append({**row["sleeve_contributions"][0], "sleeve_id": "caerus_aquila"})
    seal(envelope["exact_execution_plan"])
    envelope["exact_execution_plan_hash"] = envelope["exact_execution_plan"]["content_hash"]
    kwargs["recovery_policy"]["ownership_bridge"]["prior_exact_plan_file_sha256"] = write(original, envelope)
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        build_orion_sell_recovery_bridge(**kwargs)


def test_bridge_rejects_any_extra_epoch_intent(tmp_path):
    kwargs, _ = fixture(tmp_path)
    write(tmp_path / "outputs/paper_lane/submission_wal/epochs/2026-08-12T1000ET/2026-08-12/intents/extra.json", {})
    with pytest.raises(RuntimeError, match="extra epoch intents"):
        build_orion_sell_recovery_bridge(**kwargs)


def test_bridge_rejects_tampered_original_intent(tmp_path):
    kwargs, _ = fixture(tmp_path)
    intent_file = next((tmp_path / "outputs/paper_lane/submission_wal/2026-08-12/intents").glob("*.json"))
    data = json.loads(intent_file.read_text())
    data["quantity"] = 100
    write(intent_file, data)
    with pytest.raises((RuntimeError, ValueError, TypeError)):
        build_orion_sell_recovery_bridge(**kwargs)


def test_bridge_preserves_exact_six_decimal_ownership_and_persisted_resolution_hashes(tmp_path):
    kwargs, _ = fixture(tmp_path, fractional=True)
    assert 10.728862 - 1.327061 != 9.401801  # Actual binary-float failure reproduced.
    current, evidence = build_orion_sell_recovery_bridge(**kwargs)
    assert current == {"OLD": {"caerus_orion": 9.401801}}
    assert evidence["wal_files"]
    for receipt in evidence["wal_files"]:
        assert receipt["resolutions"]
        for resolution in receipt["resolutions"]:
            path = tmp_path / resolution["path"]
            assert path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == resolution["sha256"]
    # No epsilon gate: even a tiny genuinely different decimal position fails.
    kwargs["broker_positions"][0]["quantity"] = 9.401801000001
    with pytest.raises(RuntimeError, match="unexplained final ownership state"):
        build_orion_sell_recovery_bridge(**kwargs)


def test_uncommitted_planned_transfer_is_bound_but_never_applied(tmp_path):
    kwargs, _ = fixture(tmp_path, transfers="valid")
    current, evidence = build_orion_sell_recovery_bridge(**kwargs)
    assert current == {"OLD": {"caerus_orion": 0.0}}
    assert evidence["planned_internal_transfers"][0]["quantity"] == .5
    assert evidence["internal_transfers_applied"] is False
    assert evidence["original_plan_incomplete"] is True
    assert not (tmp_path / evidence["transfer_receipt_absent_path"]).exists()


@pytest.mark.parametrize("receipt", ["canonical", "misplaced", "malformed"])
def test_planned_transfer_bridge_rejects_committed_or_ambiguous_receipt(tmp_path, receipt):
    kwargs, _ = fixture(tmp_path, transfers="valid")
    original = json.loads((tmp_path / "original.json").read_text())["exact_execution_plan"]
    receipt_root = tmp_path / "outputs/paper_lane/ownership_transfers"
    receipt_root.mkdir(parents=True)
    if receipt == "canonical":
        (receipt_root / (original["content_hash"] + ".json")).write_text("{}")
    elif receipt == "misplaced":
        write(receipt_root / "elsewhere.json", {"plan_id": original["plan_id"], "plan_hash": original["content_hash"]})
    else:
        (receipt_root / "unknown.json").write_text("not-json")
    with pytest.raises(RuntimeError, match="transfer receipt"):
        build_orion_sell_recovery_bridge(**kwargs)


def test_completed_transfer_plan_without_receipt_is_rejected(tmp_path):
    kwargs, _ = fixture(tmp_path, transfers="valid", complete_plan=True)
    with pytest.raises(RuntimeError, match="completed or ambiguous transfer plan"):
        build_orion_sell_recovery_bridge(**kwargs)


@pytest.mark.parametrize("aliases", ["absolute", "consistent"])
def test_relative_envelope_resolves_absolute_source_hash_aliases(tmp_path, aliases):
    kwargs, _ = fixture(tmp_path, source_aliases=aliases)
    current, evidence = build_orion_sell_recovery_bridge(**kwargs)
    assert current == {"OLD": {"caerus_orion": 0.0}}
    assert evidence["parent_source_sha256"]["portfolio_allocation"] == hashlib.sha256((tmp_path / "allocation.json").read_bytes()).hexdigest()


def test_conflicting_source_hash_alias_is_rejected(tmp_path):
    kwargs, _ = fixture(tmp_path, source_aliases="conflicting")
    with pytest.raises(RuntimeError, match="original source file lineage mismatch"):
        build_orion_sell_recovery_bridge(**kwargs)
