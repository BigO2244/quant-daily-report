import copy
from decimal import Decimal
import json

import pytest

from Tests.fixtures.orion_registry import orion_registry
from Tests.test_exact_execution_choice2 import _plan, _rebuild_exact
from Tests.test_aquila_recovery_ownership import seal, write
from core.aquila_recovery_chain import build_aquila_recovery_chain
from core.aquila_recovery_ownership import build_orion_sell_recovery_bridge
from core.portfolio_operating_model import content_hash
from core.sleeve_ownership_transfer import net_demands
from core.submission_wal import OrderIntent, prepare_order_intent, validate_broker_order_evidence
from execution.exact_executor import _append_broker_observation, _persist_economic_reconciliation

pytestmark = pytest.mark.usefixtures("orion_registry")


def setup_chain(tmp_path, *, wrong_owner=False):
    initial = _plan(); day = initial.trade_date
    previous_epoch, new_epoch = day + "T1005ET", day + "T1200ET"
    owners = ("caerus_orion", "caerus_aquila")
    decisions = {owner: {"decision_id": "decision:" + owner, "decision_hash": "a"*64} for owner in owners}
    allocations = [{"sleeve_id": owner, "weight": .5, "capital_eligible": True} for owner in owners]
    start = {"INTC": 10.728862, "LRCX": 4., "MU": 2., "STX": 2., "WDC": 3.}
    book = seal({"account_id_hash": initial.account_id_hash, "opening_contract_hash": "e"*64,
                 "reconciliation": {"status": "PASS"}, "positions": [
                     {"symbol": s, "sleeve_id": owners[0], "quantity": q} for s,q in start.items()]})
    book_sha = write(tmp_path / "frozen.json", book)
    contract = seal({"trade_date": day, "ownership_snapshot_hash": book["content_hash"],
                     "ownership_snapshot_path": "frozen.json", "ownership_snapshot_sha256": book_sha})
    session = seal({"session_id": "session:chain"}); session_sha = write(tmp_path / "session.json", session)
    allocation = seal({"trade_date": day, "allocation_id": "allocation:chain", "session_id": session["session_id"],
                       "session_hash": session["content_hash"], "quantity_contracts": {"caerus_aquila": contract}})
    allocation_sha = write(tmp_path / "allocation.json", allocation)
    def exact_row(symbol, side, qty, owner):
        return {"symbol": symbol, "side": side, "quantity": qty, "order_type": "limit", "expected_price": 100,
                "limit_price": 100, "cap_enforcement_price": 100, "notional": qty*100,
                "sleeve_contributions": [{"sleeve_id": owner, "allocation_fraction": 1., **decisions[owner]}]}
    def make_plan(number, positions, cash, sells, buys, bridge=None):
        demands = {"MU": {"caerus_orion": -.35 if number == 0 else -.1, "caerus_aquila": .1}}
        residual, transfers = net_demands(demands, {"MU": 100}, decisions)
        qa = {"quantity_contract": contract, "ownership_snapshot_sha256": book_sha,
              "signed_sleeve_demands": demands, "broker_sleeve_demands": residual, "internal_transfers": transfers}
        constraints = {**initial.to_dict()["constraints"], "allow_fractional": True, "max_orders": 20, "paper_regime_owner": "caerus_orion", "aquila_quantity_authority": qa}
        if number:
            qa["recovery_ownership_bridge"] = bridge
            constraints.update(paper_drill_epoch=previous_epoch, paper_drill_live_eligible=False)
        quantities = {s: Decimal(str(q)) for s,q in positions.items()}; final_cash = Decimal(str(cash))
        for row in [*sells, *buys]:
            sign = -1 if row["side"] == "SELL" else 1
            quantities[row["symbol"]] = quantities.get(row["symbol"], Decimal(0)) + sign*Decimal(str(row["quantity"]))
            final_cash -= sign*Decimal(str(row["quantity"]))*100
        return _rebuild_exact(initial.to_dict(), run_id=f"parent{number}", constraints=constraints,
            sleeve_allocations=allocations, strategy_id="caerus_paper_portfolio", validate_current_allocator=False,
            starting_positions=[{"symbol": s, "quantity": q} for s,q in positions.items()], starting_cash=cash,
            sell_orders=sells, buy_orders=buys, expected_posttrade_positions=[{"symbol":s,"quantity":float(q)} for s,q in quantities.items() if q],
            expected_posttrade_cash=float(final_cash), source_artifact_hashes={"allocation.json":allocation_sha,"session.json":session_sha})
    def envelope(plan, name):
        obj = {"trade_date": day, "exact_execution_plan": plan.to_dict(), "exact_execution_plan_hash":plan.content_hash,
               "allocation_id":allocation["allocation_id"], "session_id":session["session_id"],"approved_target_hash":"d"*64,
               "source_portfolio_allocation":"allocation.json","source_portfolio_allocation_sha256":allocation_sha,
               "source_session_manifest":"session.json","source_session_manifest_sha256":session_sha}
        sha = write(tmp_path / name, obj)
        return {"exact_plan_path":name,"exact_plan_file_sha256":sha,"plan_id":plan.plan_id,"plan_hash":plan.content_hash}
    observations = {}; wal = tmp_path / "outputs/paper_lane/submission_wal"
    def fill_plan(plan, namespace, selected):
        quantities = {r["symbol"]:Decimal(str(r["quantity"])) for r in plan.starting_positions}
        cash = Decimal(str(plan.starting_cash)); intents=[]; rows=[]
        for order in selected:
            intent = OrderIntent(trade_date=day,plan_id=plan.plan_id,plan_hash=plan.content_hash,attempt_id="original",
                order_id=order["order_id"],client_order_id=order["client_order_id"],symbol=order["symbol"],side=order["side"],
                quantity=order["quantity"],order_type="limit",limit_price=100,created_at=plan.created_at,
                starting_state_hash=plan.starting_state_hash,paper_drill_epoch=previous_epoch if namespace != wal else "")
            intent=prepare_order_intent(namespace,intent).intent
            row={"id":"broker-"+intent.client_order_id,"client_order_id":intent.client_order_id,"symbol":intent.symbol,
                 "side":intent.side,"qty":str(intent.quantity),"status":"filled","filled_qty":str(intent.quantity),"filled_avg_price":"100"}
            observations[intent.client_order_id]=row
            _append_broker_observation(namespace,intent=intent,evidence=validate_broker_order_evidence(intent,row))
            intents.append(intent);rows.append(row);sign=-1 if intent.side=="SELL" else 1
            quantities[intent.symbol]=quantities.get(intent.symbol,Decimal(0))+sign*Decimal(str(intent.quantity));cash-=sign*Decimal(str(intent.quantity))*100
        positions=[{"symbol":s,"quantity":float(q)} for s,q in sorted(quantities.items()) if q]
        _persist_economic_reconciliation(plan=plan,wal_root=namespace,durable_intents=intents,observed_rows=rows,
            final_positions=positions,final_cash=float(cash),reconciliation_status="TERMINAL_FAILURE_STATE_RECONCILED")
        return positions,float(cash)
    sells=[exact_row(s,"SELL",q,owners[0]) for s,q in {"INTC":1.327061,"LRCX":.5,"MU":.25,"STX":.5,"WDC":.75}.items()]
    p0=make_plan(0,start,900,sells,[exact_row("AAPL","BUY",1,owners[1])]);binding0=envelope(p0,"p0.json")
    positions0,cash0=fill_plan(p0,wal,p0.sell_orders)
    single_kwargs=dict(book=book,contract=contract,allocation=allocation,repo_root=tmp_path,
        recovery_policy={"allowed_epochs":[previous_epoch],"ownership_bridge":{
            "prior_exact_plan_path":"p0.json","prior_exact_plan_file_sha256":binding0["exact_plan_file_sha256"],"approved_target_hash":"d"*64}},
        epoch=previous_epoch,account_hash=initial.account_id_hash,broker_positions=positions0,broker_cash=cash0,
        lookup_by_client_order_id=lambda cid:copy.deepcopy(observations[cid]),open_orders=[])
    _,bridge0=build_orion_sell_recovery_bridge(**single_kwargs)
    p1=make_plan(1,{r["symbol"]:r["quantity"] for r in positions0},cash0,
        [exact_row("INTC","SELL",.140728,owners[0])],
        [exact_row("AAPL","BUY",1.602608,owners[0] if wrong_owner else owners[1]),exact_row("AMZN","BUY",2.08181,owners[1]),exact_row("MSFT","BUY",1,owners[1])],bridge0)
    binding1=envelope(p1,"p1.json")
    selected=[*p1.sell_orders,*p1.buy_orders[:2]]
    positions1,cash1=fill_plan(p1,wal/"epochs"/previous_epoch,selected)
    binding0.update(wal_namespace="canonical",expected_client_order_ids=[r["client_order_id"] for r in p0.sell_orders])
    binding1.update(wal_namespace=previous_epoch,expected_client_order_ids=[r["client_order_id"] for r in selected])
    kwargs={**single_kwargs,"epoch":new_epoch,"broker_positions":positions1,"broker_cash":cash1,
        "recovery_policy":{"allowed_epochs":[new_epoch],"ownership_bridge_chain":{"parents":[binding0,binding1],"approved_target_hash":"d"*64}}}
    return kwargs,single_kwargs,observations


def test_closed_chain_applies_only_five_sells_then_one_sell_two_buys(tmp_path):
    kwargs,single,_=setup_chain(tmp_path)
    before={str(p):p.read_bytes() for p in tmp_path.rglob("*.json")}
    current,evidence=build_aquila_recovery_chain(**kwargs)
    assert current["INTC"]=={"caerus_orion":9.261073}
    assert current["AAPL"]=={"caerus_aquila":1.602608}
    assert current["AMZN"]=={"caerus_aquila":2.08181}
    assert current["MU"]=={"caerus_orion":1.75}  # Both planned transfers remain unapplied.
    assert "MSFT" not in current
    assert evidence["content_hash"]==content_hash({k:v for k,v in evidence.items() if k!="content_hash"})
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob("*.json")}
    with pytest.raises(RuntimeError,match="extra epoch intents"):
        build_orion_sell_recovery_bridge(**single)


@pytest.mark.parametrize("case",["plan","extra_epoch","missing_proof","state","account","target","partial","owner","receipt","misplaced_receipt","malformed_receipt","decimal","ids","epoch","embedded"])
def test_closed_chain_rejects_any_ambiguity(tmp_path,case):
    kwargs,_,observations=setup_chain(tmp_path, wrong_owner=case=="owner")
    parents=kwargs["recovery_policy"]["ownership_bridge_chain"]["parents"]
    if case=="plan": parents[1]["plan_id"]="other-plan"
    if case=="extra_epoch": write(tmp_path/"outputs/paper_lane/submission_wal/epochs/2026-08-12T1100ET/2026-08-12/intents/extra.json",{})
    if case=="missing_proof":
        for p in (tmp_path/"outputs/paper_lane/submission_wal/epochs").rglob("resolutions/*/*.json"):
            if json.loads(p.read_text())["state"]=="ECONOMICALLY_RECONCILED":p.unlink()
    if case=="state": kwargs["broker_cash"]+=1
    if case=="account": kwargs["account_hash"]="f"*64
    if case=="target": kwargs["recovery_policy"]["ownership_bridge_chain"]["approved_target_hash"]="f"*64
    if case=="partial": observations[parents[1]["expected_client_order_ids"][1]].update(status="partially_filled",filled_qty="1")
    if case in {"receipt","misplaced_receipt","malformed_receipt"}:
        receipt_root=tmp_path/"outputs/paper_lane/ownership_transfers"
        filename=parents[1]["plan_hash"]+".json" if case=="receipt" else "elsewhere.json"
        write(receipt_root/filename,{"plan_id":parents[1]["plan_id"],"plan_hash":parents[1]["plan_hash"]})
        if case=="malformed_receipt":(receipt_root/filename).write_text("invalid")
    if case=="decimal": kwargs["broker_positions"][0]["quantity"]+=.000000000001
    if case=="ids": parents[1]["expected_client_order_ids"][0]=parents[1]["expected_client_order_ids"][1]
    if case=="epoch": kwargs["epoch"]=parents[1]["wal_namespace"]
    if case=="embedded":
        p=tmp_path/"p1.json";obj=json.loads(p.read_text());obj["exact_execution_plan"]["constraints"]["aquila_quantity_authority"]["recovery_ownership_bridge"]["current_cash"]+=1
        parents[1]["exact_plan_file_sha256"]=write(p,obj)
    with pytest.raises((RuntimeError,ValueError)):
        build_aquila_recovery_chain(**kwargs)
