"""End-of-day proof that one decision became one reconciled PAPER record."""

from __future__ import annotations

import csv
import datetime as dt
import math
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping


AUDIT_SCHEMA = "caerus.daily_portfolio_audit.v1"


class DailyPortfolioAuditError(RuntimeError):
    pass


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DailyPortfolioAuditError(f"audit source must be an object: {path}")
    return payload


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _content_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _require_relative_source(
    *, root: Path, relative: Any, boundary: Path, label: str
) -> Path:
    candidate = Path(str(relative or ""))
    if not str(candidate) or candidate.is_absolute():
        raise DailyPortfolioAuditError(
            f"daily portfolio audit failed: {label}_path_invalid"
        )
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(boundary.resolve()) or resolved.is_symlink():
        raise DailyPortfolioAuditError(
            f"daily portfolio audit failed: {label}_path_outside_boundary"
        )
    if not resolved.is_file():
        raise DailyPortfolioAuditError(
            f"daily portfolio audit failed: {label}_missing"
        )
    return resolved


def _resolve_exact_plan(
    *, root: Path, trade_date: str
) -> tuple[dict[str, Any], dict[str, Path], bool, str | None]:
    """Resolve either a legacy direct v3 plan or the canonical pointer/handoff."""

    plans_root = root / "outputs" / "paper_lane" / "plans"
    canonical = plans_root / f"exact_execution_plan_{trade_date}.json"
    if canonical.is_file():
        payload = _read(canonical)
        schema = payload.get("schema_version")
        if schema == "caerus.execution_plan.v3":
            return payload, {"exact_execution_plan": canonical}, False, None
        if schema != "caerus.exact_execution_plan_pointer.v1":
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_pointer_schema_invalid"
            )
        if payload.get("trade_date") != trade_date:
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_pointer_trade_date_mismatch"
            )
        handoff_path = _require_relative_source(
            root=root,
            relative=payload.get("json_path"),
            boundary=plans_root / "authority" / trade_date,
            label="exact_plan_handoff",
        )
        handoff = _read(handoff_path)
        if handoff.get("schema_version") != "caerus.authorized_execution_handoff.v1":
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_handoff_schema_invalid"
            )
        if handoff.get("trade_date") != trade_date:
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_handoff_trade_date_mismatch"
            )
        plan = handoff.get("exact_execution_plan")
        if not isinstance(plan, dict):
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_handoff_plan_missing"
            )
        plan_hash = str(plan.get("content_hash") or "")
        plan_id = str(plan.get("plan_id") or "")
        if not plan_hash or not plan_id:
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_identity_missing"
            )
        if (
            str(payload.get("plan_hash") or "") != plan_hash
            or str(handoff.get("exact_execution_plan_hash") or "") != plan_hash
            or str(payload.get("plan_id") or "") != plan_id
            or str(handoff.get("exact_execution_plan_id") or "") != plan_id
        ):
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_pointer_identity_mismatch"
            )
        authority_run_id = str(handoff.get("exact_execution_authority_run_id") or "")
        if not authority_run_id or str(plan.get("run_id") or "") != authority_run_id:
            raise DailyPortfolioAuditError(
                "daily portfolio audit failed: exact_plan_authority_run_mismatch"
            )
        return (
            plan,
            {
                "exact_execution_plan_pointer": canonical,
                "exact_execution_handoff": handoff_path,
            },
            True,
            authority_run_id,
        )

    legacy_candidates = sorted(
        plans_root.rglob(f"exact_execution_plan*{trade_date}*.json")
    )
    if not legacy_candidates:
        raise DailyPortfolioAuditError(
            "daily audit source is missing: exact_execution_plan"
        )
    plan_path = max(legacy_candidates, key=lambda path: path.stat().st_mtime_ns)
    return _read(plan_path), {"exact_execution_plan": plan_path}, False, None


def _audit_cutover_ownership(root: Path, ownership: dict[str, Any]) -> dict[str, Path]:
    """Independently replay the prospective book; never rewrite historical facts."""
    ledger = root / "outputs" / "ledger" / "paper"
    path = ledger / "ownership_cutover.json"
    if not path.is_file():
        if ownership.get("opening_contract_hash"):
            raise DailyPortfolioAuditError("ownership_cutover_missing")
        return {}

    def require(condition: bool, reason: str) -> None:
        if not condition:
            raise DailyPortfolioAuditError("ownership_cutover_" + reason)

    def verified(payload: dict[str, Any], field: str = "content_hash") -> bool:
        body = dict(payload)
        claimed = body.pop(field, None)
        return claimed == _content_hash(body)

    def timestamp(value: str) -> dt.datetime:
        raw = str(value).replace("Z", "+00:00")
        # Alpaca may emit five fractional digits, unsupported by Python 3.10.
        # Match the ledger's microsecond precision without altering source bytes.
        fractional = re.fullmatch(r"(.+T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})", raw)
        if fractional:
            raw = fractional[1] + "." + fractional[2][:6].ljust(6, "0") + fractional[3]
        parsed = dt.datetime.fromisoformat(raw)
        require(parsed.tzinfo is not None, "timestamp_invalid")
        return parsed

    contract = _read(path)
    require(verified(contract) and contract.get("schema_version") == "caerus.ownership_cutover.v1", "hash_invalid")
    identity = contract.get("account_id_hash")
    require(contract.get("account_scope") == "PAPER" and isinstance(identity, str)
            and len(identity) == 64 and all(c in "0123456789abcdef" for c in identity), "account_invalid")
    require(verified(ownership) and ownership.get("opening_contract_hash") == contract["content_hash"]
            and ownership.get("account_id_hash") == identity, "ownership_binding_invalid")
    epoch = timestamp(contract["effective_at"])
    history_path = ledger / "causal_fills.jsonl"
    raw = history_path.read_bytes()
    length = contract.get("history_prefix_bytes")
    require(isinstance(length, int) and length > 0, "prefix_invalid")
    prefix = raw[:length]
    require(prefix.endswith(b"\n") and hashlib.sha256(prefix).hexdigest() == contract.get("history_sha256"), "history_hash_invalid")
    old = [json.loads(line) for line in prefix.splitlines() if line.strip()]
    require(len(old) == contract.get("history_fill_count"), "history_count_invalid")
    opening = contract["opening_positions_snapshot"]
    account = contract["opening_account_snapshot"]
    require(_content_hash(opening) == contract.get("positions_snapshot_hash")
            and _content_hash(account) == contract.get("account_snapshot_hash")
            and account.get("account_id_hash") == identity
            and opening.get("pulled_at_utc") == account.get("pulled_at_utc")
            and timestamp(opening["pulled_at_utc"]) == epoch, "snapshot_binding_invalid")
    book: dict[tuple[str, str], float] = {}
    for row in contract["opening_book"]:
        key = (row["symbol"], row["sleeve_id"])
        qty = float(row["quantity"])
        require(key not in book and bool(key[0]) and bool(key[1]) and math.isfinite(qty) and qty > 0, "opening_book_invalid")
        book[key] = qty
    expected_open = {r["symbol"]: float(r["qty"]) for r in opening["positions"]}
    old_totals: dict[str, float] = {}
    for row in old:
        require(verified(row, "record_hash") and timestamp(row["transaction_time_utc"]) < epoch, "historical_record_invalid")
        old_totals[row["symbol"]] = old_totals.get(row["symbol"], 0) + sum(float(e["signed_quantity"]) for e in row["inventory_effects"])
    for symbol in set(expected_open) | set(old_totals) | {k[0] for k in book}:
        require(abs(expected_open.get(symbol, 0) - old_totals.get(symbol, 0)) <= 1e-6
                and abs(expected_open.get(symbol, 0) - sum(q for (s, _), q in book.items() if s == symbol)) <= 1e-6,
                "opening_quantity_mismatch")
    # Discover immutable nested plans by content identity, independently of the
    # ownership builder's client-order index and emitted ownership positions.
    plans = {}
    plans_root = root / "outputs" / "paper_lane" / "plans"
    for candidate in sorted(plans_root.rglob("*.json")):
        require(candidate.resolve().is_relative_to(plans_root.resolve()), "plan_path_escape")
        payload = _read(candidate)
        if payload.get("schema_version") == "caerus.authorized_execution_handoff.v1":
            nested = payload.get("exact_execution_plan") or {}
            require(payload.get("exact_execution_plan_hash") == nested.get("content_hash")
                    and payload.get("exact_execution_plan_id") == nested.get("plan_id"), "handoff_identity_invalid")
            payload = nested
        if payload.get("schema_version") == "caerus.execution_plan.v3":
            require(verified(payload), "plan_hash_invalid")
            plans[payload["content_hash"]] = payload
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    require(len({r["activity_id"] for r in rows}) == len(rows), "duplicate_fill")
    with (ledger / "fills.csv").open(newline="") as handle:
        fills = list(csv.DictReader(handle))
    require(len(fills) == len(rows) and {r["activity_id"] for r in fills} == {r["activity_id"] for r in rows}, "broker_fill_set_mismatch")
    fill_index = {r["activity_id"]: r for r in fills}
    for row in rows:
        fill = fill_index[row["activity_id"]]
        require(verified(row, "record_hash") and row["symbol"] == fill["symbol"]
                and row["side"] == fill["side"] and row["quantity"] == float(fill["qty"])
                and row["broker_order_id"] == fill["order_id"]
                and timestamp(row["transaction_time_utc"]) == timestamp(fill["transaction_time_utc"]), "fill_identity_invalid")
    broker_orders = {}
    for line in (ledger / "orders.jsonl").read_text().splitlines():
        order_row = json.loads(line)
        broker_orders[order_row["id"]] = order_row
    from core.sleeve_ownership_transfer import load_transfer_receipts
    transfers = load_transfer_receipts(receipt_root=plans_root.parent / "ownership_transfers",
        plans=plans, account_hash=identity, broker_orders=broker_orders, fills=fills, as_of=ownership["as_of"])
    require(ownership.get("internal_transfer_receipt_hashes", []) == [r["content_hash"] for r in transfers], "transfer_receipt_set_mismatch")
    pending_transfers = list(transfers)
    def transfer_until(stamp):
        while pending_transfers and timestamp(pending_transfers[0]["committed_at"]) <= stamp:
            receipt = pending_transfers.pop(0)
            for transfer in receipt["transfers"]:
                source = (transfer["symbol"], transfer["from_sleeve"])
                target = (transfer["symbol"], transfer["to_sleeve"])
                qty = float(transfer["quantity"])
                require(qty > 0 and book.get(source, 0) + 1e-6 >= qty, "transfer_inventory_invalid")
                book[source] = book.get(source, 0) - qty
                book[target] = book.get(target, 0) + qty
    for row in rows[len(old):]:
        transfer_until(timestamp(row["transaction_time_utc"]))
        require(timestamp(row["transaction_time_utc"]) >= epoch and row["attribution_status"] == "ATTRIBUTED", "forward_timestamp_or_status_invalid")
        require(broker_orders.get(row["broker_order_id"], {}).get("client_order_id") == row["client_order_id"], "broker_order_link_invalid")
        plan = plans.get(row["plan_hash"], {})
        require(plan.get("plan_id") == row["plan_id"] and plan.get("account_id_hash") == identity
                and plan.get("account_scope") == "PAPER", "forward_plan_identity_invalid")
        orders = [o for o in [*plan.get("sell_orders", []), *plan.get("buy_orders", [])]
                  if o.get("client_order_id") == row["client_order_id"]]
        require(len(orders) == 1, "forward_order_missing")
        order = orders[0]
        require(order["symbol"] == row["symbol"] and order["side"].lower() == row["side"]
                and order.get("allocation_id") == row.get("allocation_id"), "forward_order_identity_invalid")
        effects = {}
        sign = 1 if row["side"] == "buy" else -1
        require(row["side"] in {"buy", "sell"}, "forward_side_invalid")
        total = 0.0
        for contribution in order.get("sleeve_contributions", []):
            owner = contribution["sleeve_id"]
            fraction = float(contribution["allocation_fraction"])
            require(owner not in effects and math.isfinite(fraction) and fraction > 0, "forward_demand_invalid")
            total += fraction
            effects[owner] = sign * row["quantity"] * fraction
        require(abs(total - 1) <= 1e-9 and len(row["inventory_effects"]) == len(effects), "forward_demand_total_invalid")
        observed = {e["sleeve_id"]: float(e["signed_quantity"]) for e in row["inventory_effects"]}
        require(set(observed) == set(effects) and all(abs(observed[k] - v) <= 1e-6 for k, v in effects.items()), "forward_effect_mismatch")
        for owner, quantity in effects.items():
            key = (row["symbol"], owner)
            book[key] = book.get(key, 0) + quantity
            require(book[key] >= -1e-6, "negative_owner_inventory")
    transfer_until(timestamp(ownership["as_of"]))
    current = {(r["symbol"], r["sleeve_id"]): float(r["quantity"]) for r in ownership["positions"]}
    require(len(current) == len(ownership["positions"])
            and all(abs(current.get(k, 0) - book.get(k, 0)) <= 1e-6 for k in set(current) | set(book)), "replay_mismatch")
    broker = _read(ledger / "positions_latest.json")
    require(broker.get("pulled_at_utc") == ownership.get("as_of"), "broker_as_of_mismatch")
    broker_quantities = {r["symbol"]: float(r["qty"]) for r in broker["positions"]}
    require(all(abs(broker_quantities.get(s, 0) - sum(q for (sym, _), q in book.items() if sym == s)) <= 1e-6
                for s in set(broker_quantities) | {k[0] for k in book}), "broker_quantity_mismatch")
    accounts = [json.loads(line) for line in (ledger / "account_snapshots.jsonl").read_text().splitlines() if line.strip()]
    latest_account = next((r for r in reversed(accounts) if r.get("pulled_at_utc") == ownership.get("as_of")), {})
    require(latest_account.get("account_id_hash") == identity, "current_account_mismatch")
    return {"ownership_cutover": path, "broker_orders": ledger / "orders.jsonl",
            "account_snapshots": ledger / "account_snapshots.jsonl", "causal_fills": history_path,
            "broker_fills": ledger / "fills.csv", "broker_positions": ledger / "positions_latest.json"}


def build_daily_portfolio_audit(*, repo_root: Path, trade_date: str) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    bundle = root / "outputs" / "precompute" / trade_date
    package_path = bundle / "paper_target_package.json"
    allocation_path = bundle / "portfolio_allocation.json"
    session_path = bundle / "session_manifest.json"
    decisions_path = bundle / "sleeve_decisions.json"
    execution_path = root / "outputs" / "workflow" / trade_date / "execution.json"
    ownership_path = root / "outputs" / "ledger" / "paper" / "ownership_latest.json"
    valuation_path = root / "outputs" / "ledger" / "paper" / "valuation_latest.json"
    reporting_path = root / "outputs" / "portfolio_history" / "reporting_snapshot.json"
    required = [
        package_path,
        allocation_path,
        session_path,
        decisions_path,
        execution_path,
        ownership_path,
        valuation_path,
        reporting_path,
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise DailyPortfolioAuditError(
            "daily audit source is missing: "
            + ",".join(missing)
        )
    package = _read(package_path)
    allocation = _read(allocation_path)
    session = _read(session_path)
    decisions = _read(decisions_path)
    plan, plan_sources, canonical_handoff, _ = _resolve_exact_plan(
        root=root, trade_date=trade_date
    )
    execution = _read(execution_path)
    ownership = _read(ownership_path)
    valuation = _read(valuation_path)
    reporting = _read(reporting_path)

    cutover_sources = _audit_cutover_ownership(root, ownership)
    failures: list[str] = []
    plan_body = dict(plan)
    declared_plan_hash = str(plan_body.pop("content_hash", ""))
    if plan.get("schema_version") != "caerus.execution_plan.v3":
        failures.append("exact_plan_schema_invalid")
    if declared_plan_hash != _content_hash(plan_body):
        failures.append("exact_plan_content_hash_invalid")
    if package.get("session_id") != session.get("session_id"):
        failures.append("session_identity_mismatch")
    if package.get("allocation_id") != allocation.get("allocation_id"):
        failures.append("allocation_identity_mismatch")
    if package.get("approved_target_hash") != allocation.get("approved_target_hash"):
        # Allocation v1 does not duplicate the Decision hash; the plan source
        # hashes and target package bind it.  Only compare when supplied.
        if allocation.get("approved_target_hash") is not None:
            failures.append("target_identity_mismatch")
    if session.get("content_hash") != decisions.get("session_hash"):
        failures.append("decision_session_hash_mismatch")
    if allocation.get("content_hash") != package.get("allocation_content_hash"):
        failures.append("package_allocation_hash_mismatch")
    plan_source_hashes = plan.get("source_artifact_hashes") or {}
    required_source_hashes = {
        "allocation": _hash_file(allocation_path),
        "session": _hash_file(session_path),
        "decisions": _hash_file(decisions_path),
        "target": _hash_file(package_path),
    }
    observed_hashes = set(plan_source_hashes.values())
    for name, source_hash in required_source_hashes.items():
        if source_hash not in observed_hashes:
            failures.append(f"exact_plan_missing_{name}_hash")
    execution_result_path: Path | None = None
    if canonical_handoff:
        execution_result_path = _require_relative_source(
            root=root,
            relative=Path(str(execution.get("run_root") or ""))
            / "execution_results.json",
            boundary=root / "outputs" / "paper_lane" / "runs",
            label="execution_result",
        )
        execution_result = _read(execution_result_path)
        if execution_result.get("run_id") != execution.get("run_id"):
            failures.append("execution_result_run_mismatch")
        if execution_result.get("plan_id_received") != plan.get("plan_id"):
            failures.append("exact_plan_execution_plan_id_mismatch")
        if execution_result.get("plan_hash_received") != declared_plan_hash:
            failures.append("exact_plan_execution_plan_hash_mismatch")
        if execution_result.get("plan_hash_validated") is not True:
            failures.append("exact_plan_execution_hash_not_validated")
        if execution_result.get("authorization_validated") is not True:
            failures.append("exact_plan_execution_authority_not_validated")
    elif plan.get("run_id") != execution.get("run_id"):
        failures.append("exact_plan_execution_run_mismatch")
    exact_orders = [*(plan.get("sell_orders") or []), *(plan.get("buy_orders") or [])]
    for order in exact_orders:
        if not isinstance(order, Mapping):
            failures.append("exact_plan_order_malformed")
            continue
        if order.get("session_id") != session.get("session_id"):
            failures.append("exact_order_session_mismatch")
        if order.get("allocation_id") != allocation.get("allocation_id"):
            failures.append("exact_order_allocation_mismatch")
    if str(execution.get("status") or "").lower() not in {"success", "no_action"}:
        failures.append("execution_not_terminal_green")
    if (ownership.get("reconciliation") or {}).get("status") != "PASS":
        failures.append("ownership_reconciliation_failed")
    if (valuation.get("reconciliation") or {}).get("status") != "PASS":
        failures.append("valuation_reconciliation_failed")
    if reporting.get("status") != "PASS":
        failures.append("reporting_snapshot_not_green")
    if valuation.get("as_of") != ownership.get("as_of") or valuation.get(
        "as_of"
    ) != reporting.get("as_of"):
        failures.append("reporting_as_of_mismatch")
    if str(valuation.get("as_of") or "")[:10] != trade_date:
        failures.append("valuation_trade_date_mismatch")
    if reporting.get("report_date") != trade_date:
        failures.append("reporting_trade_date_mismatch")
    if failures:
        raise DailyPortfolioAuditError("daily portfolio audit failed: " + ",".join(failures))

    sources = {
        "session_manifest": session_path,
        "sleeve_decisions": decisions_path,
        "portfolio_allocation": allocation_path,
        "paper_target_package": package_path,
        "execution_pointer": execution_path,
        "ownership": ownership_path,
        "valuation": valuation_path,
        "reporting_snapshot": reporting_path,
    }
    sources.update(plan_sources)
    sources.update(cutover_sources)
    if execution_result_path is not None:
        sources["execution_result"] = execution_result_path
    result = {
        "schema_version": AUDIT_SCHEMA,
        "status": "PASS",
        "trade_date": trade_date,
        "as_of": valuation.get("as_of"),
        "session_id": session.get("session_id"),
        "allocation_id": allocation.get("allocation_id"),
        "approved_target_hash": package.get("approved_target_hash"),
        "exact_plan_id": plan.get("plan_id"),
        "execution_run_id": execution.get("run_id"),
        "sources": {
            name: {
                "path": str(path.relative_to(root)),
                "sha256": _hash_file(path),
            }
            for name, path in sources.items()
        },
        "checks": {
            "decision_to_execution": "PASS",
            "execution_to_ownership": "PASS",
            "ownership_to_valuation": "PASS",
            "valuation_to_reporting": "PASS",
            "single_as_of": "PASS",
        },
    }
    result["content_hash"] = _content_hash(result)
    output = root / "outputs" / "audit" / trade_date / "portfolio_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result
