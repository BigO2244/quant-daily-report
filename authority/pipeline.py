from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import (
    AuditPackage,
    AuthorityContractError,
    DecisionPackage,
    EvidencePackage,
    ExecutionPackage,
    RiskPackage,
    EXECUTION_SCHEMA_VERSION,
    LEGACY_EXECUTION_SCHEMA_VERSION,
    EVIDENCE_SCHEMA_VERSION,
    DECISION_SCHEMA_VERSION,
    RISK_SCHEMA_VERSION,
    build_decision_package,
    build_evidence_package,
    build_risk_package,
)


def evidence_package_from_dict(payload: Mapping[str, Any]) -> EvidencePackage:
    package = EvidencePackage(
        schema_version=str(payload.get("schema_version") or ""),
        package_id=str(payload.get("package_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        source_refs=tuple(payload.get("source_refs") or ()),
        observations=tuple(payload.get("observations") or ()),
    )
    if str(payload.get("content_hash") or "") != package.content_hash:
        raise AuthorityContractError("evidence package content_hash mismatch")
    return package


def decision_package_from_dict(payload: Mapping[str, Any]) -> DecisionPackage:
    package = DecisionPackage(
        schema_version=str(payload.get("schema_version") or ""),
        package_id=str(payload.get("package_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        evidence_package_id=str(payload.get("evidence_package_id") or ""),
        evidence_hash=str(payload.get("evidence_hash") or ""),
        authority=str(payload.get("authority") or ""),
        target_cash_weight=payload.get("target_cash_weight"),
        target_rows=tuple(payload.get("target_rows") or ()),
        source_refs=tuple(payload.get("source_refs") or ()),
    )
    if str(payload.get("content_hash") or "") != package.content_hash:
        raise AuthorityContractError("decision package content_hash mismatch")
    return package


def risk_package_from_dict(payload: Mapping[str, Any]) -> RiskPackage:
    package = RiskPackage(
        schema_version=str(payload.get("schema_version") or ""),
        package_id=str(payload.get("package_id") or ""),
        trade_date=str(payload.get("trade_date") or ""),
        decision_package_id=str(payload.get("decision_package_id") or ""),
        decision_hash=str(payload.get("decision_hash") or ""),
        approved_cash_weight=payload.get("approved_cash_weight"),
        approved_target_rows=tuple(payload.get("approved_target_rows") or ()),
        constraints=dict(payload.get("constraints") or {}),
        source_refs=tuple(payload.get("source_refs") or ()),
    )
    if str(payload.get("content_hash") or "") != package.content_hash:
        raise AuthorityContractError("risk package content_hash mismatch")
    return package


def validate_persisted_authority_chain(
    *,
    paths: Mapping[str, str],
    embedded_execution: Mapping[str, Any],
    trade_date: str,
    required_source_hash: str,
) -> tuple[EvidencePackage, DecisionPackage, RiskPackage, ExecutionPackage]:
    """Load and validate the complete immutable Evidence→Execution chain."""
    required = {"evidence", "decision", "risk", "execution"}
    if set(paths) != required:
        raise AuthorityContractError("all four authority_package_paths are required")
    payloads: dict[str, dict[str, Any]] = {}
    for name in sorted(required):
        path = Path(str(paths[name]))
        if not path.is_file():
            raise AuthorityContractError(f"authority {name} package is missing")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise AuthorityContractError(f"authority {name} package must be an object")
        payloads[name] = payload
    evidence = evidence_package_from_dict(payloads["evidence"])
    decision = decision_package_from_dict(payloads["decision"])
    risk = risk_package_from_dict(payloads["risk"])
    execution = execution_package_from_dict(payloads["execution"])
    if any(
        package.trade_date != trade_date
        for package in (evidence, decision, risk, execution)
    ):
        raise AuthorityContractError("authority package trade_date mismatch")
    if decision.evidence_package_id != evidence.package_id or decision.evidence_hash != evidence.content_hash:
        raise AuthorityContractError("Decision does not bind the persisted Evidence")
    if risk.decision_package_id != decision.package_id or risk.decision_hash != decision.content_hash:
        raise AuthorityContractError("Risk does not bind the persisted Decision")
    if execution.risk_package_id != risk.package_id or execution.risk_hash != risk.content_hash:
        raise AuthorityContractError("Execution does not bind the persisted Risk")
    expected_decision = build_decision_package(
        package_id=decision.package_id,
        trade_date=trade_date,
        evidence=evidence,
        target_rows=decision.target_rows,
        source_refs=decision.source_refs,
        target_cash_weight=decision.target_cash_weight,
    )
    if expected_decision.content_hash != decision.content_hash:
        raise AuthorityContractError("Decision semantic lineage mismatch")
    expected_risk = build_risk_package(
        package_id=risk.package_id,
        decision=decision,
        approved_target_rows=risk.approved_target_rows,
        approved_cash_weight=risk.approved_cash_weight,
        constraints=risk.constraints,
        source_refs=risk.source_refs,
    )
    if expected_risk.content_hash != risk.content_hash:
        raise AuthorityContractError("Risk exceeds or diverges from Decision")
    expected_execution = execution_package_from_risk(risk)
    if expected_execution.content_hash != execution.content_hash:
        raise AuthorityContractError("Execution diverges from Risk")
    if dict(embedded_execution) != payloads["execution"]:
        raise AuthorityContractError("embedded execution package differs from persisted authority")
    source_token = f"sha256:{required_source_hash}"
    if source_token not in evidence.source_refs or source_token not in decision.source_refs:
        raise AuthorityContractError("validated Orion sleeve source is not bound to Decision")
    return evidence, decision, risk, execution


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
        approved_cash_weight=payload.get("approved_cash_weight"),
        approved_target_rows=tuple(rows),
        constraints=(
            dict(payload.get("constraints") or {})
            if str(payload.get("schema_version") or "") != LEGACY_EXECUTION_SCHEMA_VERSION
            else {}
        ),
        source_refs=tuple(payload.get("source_refs") or ()),
    )
    if str(payload.get("content_hash") or "") != package.content_hash:
        raise AuthorityContractError("execution package content_hash mismatch")
    return package


def execution_package_from_risk(risk: RiskPackage) -> ExecutionPackage:
    """Create the mechanical Trader handoff; no target construction occurs here."""
    return ExecutionPackage(
        schema_version=EXECUTION_SCHEMA_VERSION,
        package_id=f"execution:{risk.package_id}",
        trade_date=risk.trade_date,
        risk_package_id=risk.package_id,
        risk_hash=risk.content_hash,
        approved_cash_weight=risk.approved_cash_weight,
        approved_target_rows=risk.approved_target_rows,
        constraints=risk.constraints,
        source_refs=(*risk.source_refs, f"risk:{risk.package_id}"),
    )


def audit_execution_package(
    execution: ExecutionPackage,
    observed_orders: Sequence[Mapping[str, Any]],
    *,
    authorized_exit_symbols: Sequence[str] = (),
) -> AuditPackage:
    """Produce a read-only audit and preserve zero-target exit authority."""
    approved = {row["symbol"] for row in execution.approved_target_rows}
    authorized_exits = {
        str(symbol).strip().upper() for symbol in authorized_exit_symbols
    }
    observed = tuple(dict(row) for row in observed_orders)
    findings: list[str] = []
    for row in observed:
        symbol = str(row.get("symbol") or row.get("ticker") or "").upper()
        side = str(row.get("side") or "").strip().upper()
        if symbol not in approved and not (
            side == "SELL" and symbol in authorized_exits
        ):
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

    The payload must explicitly carry portfolio target weights. A
    downstream component cannot silently recover targets from another source.
    """
    trade_date = str(payload.get("trade_date") or "").strip()
    if not trade_date:
        raise AuthorityContractError("precompute payload trade_date is required")
    raw_targets = payload.get("target_portfolio")
    if raw_targets is None:
        raw_targets = payload.get("target_rows")
    if raw_targets is None:
        raw_targets = payload.get("signals")
    if raw_targets is None:
        candidate_trades = payload.get("trades")
        if isinstance(candidate_trades, list) and all(
            isinstance(row, Mapping) and "target_weight" in row
            for row in candidate_trades
        ):
            raw_targets = candidate_trades
    if not isinstance(raw_targets, list):
        raise AuthorityContractError("precompute payload lacks explicit portfolio targets")
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
        target_cash_weight=float(payload.get("cash_target_weight") or 0.0),
    )
    risk = build_risk_package(
        package_id=risk_id,
        decision=decision,
        approved_target_rows=decision.target_rows,
        constraints=risk_constraints or {},
        source_refs=(f"decision:{decision.package_id}",),
        approved_cash_weight=float(payload.get("cash_target_weight") or 0.0),
    )
    return evidence, decision, risk, execution_package_from_risk(risk)
