"""End-of-day proof that one decision became one reconciled PAPER record."""

from __future__ import annotations

import hashlib
import json
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
