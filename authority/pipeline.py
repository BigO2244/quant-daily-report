from __future__ import annotations

from typing import Any, Mapping, Sequence

from .contracts import (
    AuditPackage,
    AuthorityContractError,
    DecisionPackage,
    EvidencePackage,
    ExecutionPackage,
    RiskPackage,
    build_decision_package,
    build_evidence_package,
    build_risk_package,
)


def execution_package_from_dict(payload: Mapping[str, Any]) -> ExecutionPackage:
    """Rehydrate the persisted Trader handoff and recompute its immutable hash."""
    rows = payload.get("approved_target_rows")
    if not isinstance(rows, list):
        raise AuthorityContractError("execution package approved_target_rows must be a list")
    package = ExecutionPackage(
        schema_version=str(payload.get("schema_version") or ""),
        package_id=str(payload.get("package_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        risk_package_id=str(payload.get("risk_package_id") or ""),
        risk_hash=str(payload.get("risk_hash") or ""),
        approved_target_rows=tuple(rows),
        source_refs=tuple(payload.get("source_refs") or ()),
    )
    if str(payload.get("content_hash") or "") != package.content_hash:
        raise AuthorityContractError("execution package content_hash mismatch")
    return package


def execution_package_from_risk(risk: RiskPackage) -> ExecutionPackage:
    """Create the mechanical Trader handoff; no target construction occurs here."""
    return ExecutionPackage(
        schema_version="caerus.execution.v1",
        package_id=f"execution:{risk.package_id}",
        trade_date=risk.trade_date,
        risk_package_id=risk.package_id,
        risk_hash=risk.content_hash,
        approved_target_rows=risk.approved_target_rows,
        source_refs=(*risk.source_refs, f"risk:{risk.package_id}"),
    )


def audit_execution_package(
    execution: ExecutionPackage,
    observed_orders: Sequence[Mapping[str, Any]],
) -> AuditPackage:
    """Produce a read-only audit and flag orders outside approved symbols."""
    approved = {row["symbol"] for row in execution.approved_target_rows}
    observed = tuple(dict(row) for row in observed_orders)
    findings: list[str] = []
    for row in observed:
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        if symbol not in approved:
            findings.append(f"UNAPPROVED_SYMBOL:{symbol}")
        if str(row.get("status") or "").lower() in {"rejected", "canceled", "expired"}:
            findings.append(f"ORDER_{str(row.get('status')).upper()}:{symbol}")
    return AuditPackage(
        schema_version="caerus.audit.v1",
        package_id=f"audit:{execution.package_id}",
        trade_date=execution.trade_date,
        execution_package_id=execution.package_id,
        execution_hash=execution.content_hash,
        observed_orders=observed,
        findings=tuple(findings),
    )


def wrap_precompute_payload(
    payload: Mapping[str, Any],
    *,
    evidence_refs: Sequence[str],
    decision_id: str,
    risk_id: str,
    risk_constraints: Mapping[str, Any] | None = None,
) -> tuple[EvidencePackage, DecisionPackage, RiskPackage, ExecutionPackage]:
    """Wrap an existing precompute payload without recomputing its targets.

    The payload must explicitly carry ``target_portfolio`` or ``trades``. A
    downstream component cannot silently recover targets from another source.
    """
    trade_date = str(payload.get("trade_date") or "").strip()
    if not trade_date:
        raise AuthorityContractError("precompute payload trade_date is required")
    raw_targets = payload.get("target_portfolio")
    if raw_targets is None:
        raw_targets = payload.get("target_rows")
    if raw_targets is None:
        raw_targets = payload.get("trades")
    if not isinstance(raw_targets, list):
        raise AuthorityContractError("precompute payload lacks explicit target_portfolio/trades")
    observations = payload.get("evidence") or payload.get("signals") or raw_targets
    if not isinstance(observations, list):
        observations = raw_targets
    evidence = build_evidence_package(
        package_id=f"evidence:{decision_id}",
        trade_date=trade_date,
        source_refs=evidence_refs,
        observations=observations,
    )
    decision = build_decision_package(
        package_id=decision_id,
        trade_date=trade_date,
        evidence=evidence,
        target_rows=raw_targets,
        source_refs=evidence_refs,
    )
    risk = build_risk_package(
        package_id=risk_id,
        decision=decision,
        approved_target_rows=decision.target_rows,
        constraints=risk_constraints or {},
        source_refs=(f"decision:{decision.package_id}",),
    )
    return evidence, decision, risk, execution_package_from_risk(risk)
