from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from types import MappingProxyType
from typing import Any, Mapping, Sequence

EVIDENCE_SCHEMA_VERSION = "caerus.evidence.v1"
DECISION_SCHEMA_VERSION = "caerus.decision.v1"
RISK_SCHEMA_VERSION = "caerus.risk.v1"
EXECUTION_SCHEMA_VERSION = "caerus.execution.v1"
AUDIT_SCHEMA_VERSION = "caerus.audit.v1"


class AuthorityContractError(ValueError):
    """Raised when an authority handoff is malformed or has broken lineage."""


def _canonical(value: Any) -> str:
    return json.dumps(_thaw(value), sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, set):
        return tuple(sorted((_freeze(item) for item in value), key=str))
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw(item) for item in value]
    return value


def _validate_trade_date(value: str) -> None:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise AuthorityContractError(f"invalid trade_date: {value!r}") from exc


def _cash_weight(value: Any, field_name: str) -> float:
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise AuthorityContractError(f"{field_name} must be numeric") from exc
    if not 0.0 <= weight <= 1.0:
        raise AuthorityContractError(f"{field_name} must be between 0 and 1")
    return weight


def _rows(rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    normalized = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            raise AuthorityContractError("package rows must be mappings")
        row = dict(raw)
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        if not symbol or symbol == "CASH":
            raise AuthorityContractError("target/evidence rows require a non-CASH symbol")
        row["symbol"] = symbol
        if "ticker" in row:
            row["ticker"] = symbol
        normalized.append(_freeze(row))
    return tuple(normalized)


def _refs(refs: Sequence[str]) -> tuple[str, ...]:
    out = tuple(sorted({str(ref).strip() for ref in refs if str(ref).strip()}))
    if not out:
        raise AuthorityContractError("at least one source artifact reference is required")
    return out


@dataclass(frozen=True)
class EvidencePackage:
    schema_version: str
    package_id: str
    trade_date: str
    source_refs: tuple[str, ...]
    observations: tuple[Mapping[str, Any], ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != EVIDENCE_SCHEMA_VERSION:
            raise AuthorityContractError("unsupported evidence schema version")
        if not self.package_id or not self.trade_date:
            raise AuthorityContractError("evidence package_id and trade_date are required")
        _validate_trade_date(self.trade_date)
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        object.__setattr__(self, "observations", _rows(self.observations))
        object.__setattr__(self, "content_hash", _hash(self.to_dict(include_hash=False)))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "trade_date": self.trade_date,
            "source_refs": list(self.source_refs),
            "observations": [_thaw(row) for row in self.observations],
        }
        if include_hash:
            result["content_hash"] = self.content_hash
        return result


@dataclass(frozen=True)
class DecisionPackage:
    schema_version: str
    package_id: str
    trade_date: str
    evidence_package_id: str
    evidence_hash: str
    authority: str
    target_cash_weight: float
    target_rows: tuple[Mapping[str, Any], ...]
    source_refs: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != DECISION_SCHEMA_VERSION:
            raise AuthorityContractError("unsupported decision schema version")
        if self.authority != "DECISION":
            raise AuthorityContractError("only Decision may author investment targets")
        if not self.package_id or not self.evidence_package_id or not self.evidence_hash:
            raise AuthorityContractError("decision lineage fields are required")
        _validate_trade_date(self.trade_date)
        object.__setattr__(self, "target_cash_weight", _cash_weight(self.target_cash_weight, "target_cash_weight"))
        object.__setattr__(self, "target_rows", _rows(self.target_rows))
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        object.__setattr__(self, "content_hash", _hash(self.to_dict(include_hash=False)))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "trade_date": self.trade_date,
            "evidence_package_id": self.evidence_package_id,
            "evidence_hash": self.evidence_hash,
            "authority": self.authority,
            "target_cash_weight": self.target_cash_weight,
            "target_rows": [_thaw(row) for row in self.target_rows],
            "source_refs": list(self.source_refs),
        }
        if include_hash:
            result["content_hash"] = self.content_hash
        return result


@dataclass(frozen=True)
class RiskPackage:
    schema_version: str
    package_id: str
    trade_date: str
    decision_package_id: str
    decision_hash: str
    approved_cash_weight: float
    approved_target_rows: tuple[Mapping[str, Any], ...]
    constraints: Mapping[str, Any]
    source_refs: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != RISK_SCHEMA_VERSION:
            raise AuthorityContractError("unsupported risk schema version")
        if not self.decision_package_id or not self.decision_hash:
            raise AuthorityContractError("risk package must bind to Decision")
        _validate_trade_date(self.trade_date)
        object.__setattr__(self, "approved_cash_weight", _cash_weight(self.approved_cash_weight, "approved_cash_weight"))
        object.__setattr__(self, "approved_target_rows", _rows(self.approved_target_rows))
        object.__setattr__(self, "constraints", _freeze(self.constraints))
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        object.__setattr__(self, "content_hash", _hash(self.to_dict(include_hash=False)))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "trade_date": self.trade_date,
            "decision_package_id": self.decision_package_id,
            "decision_hash": self.decision_hash,
            "approved_cash_weight": self.approved_cash_weight,
            "approved_target_rows": [_thaw(row) for row in self.approved_target_rows],
            "constraints": _thaw(self.constraints),
            "source_refs": list(self.source_refs),
        }
        if include_hash:
            result["content_hash"] = self.content_hash
        return result


@dataclass(frozen=True)
class ExecutionPackage:
    schema_version: str
    package_id: str
    trade_date: str
    risk_package_id: str
    risk_hash: str
    approved_cash_weight: float
    approved_target_rows: tuple[Mapping[str, Any], ...]
    source_refs: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != EXECUTION_SCHEMA_VERSION:
            raise AuthorityContractError("unsupported execution schema version")
        if not self.risk_package_id or not self.risk_hash:
            raise AuthorityContractError("execution package must bind to Risk")
        _validate_trade_date(self.trade_date)
        object.__setattr__(self, "approved_cash_weight", _cash_weight(self.approved_cash_weight, "approved_cash_weight"))
        object.__setattr__(self, "approved_target_rows", _rows(self.approved_target_rows))
        object.__setattr__(self, "source_refs", _refs(self.source_refs))
        object.__setattr__(self, "content_hash", _hash(self.to_dict(include_hash=False)))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "trade_date": self.trade_date,
            "risk_package_id": self.risk_package_id,
            "risk_hash": self.risk_hash,
            "approved_cash_weight": self.approved_cash_weight,
            "approved_target_rows": [_thaw(row) for row in self.approved_target_rows],
            "source_refs": list(self.source_refs),
        }
        if include_hash:
            result["content_hash"] = self.content_hash
        return result


@dataclass(frozen=True)
class AuditPackage:
    schema_version: str
    package_id: str
    trade_date: str
    execution_package_id: str
    execution_hash: str
    observed_orders: tuple[Mapping[str, Any], ...]
    findings: tuple[str, ...]
    content_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if self.schema_version != AUDIT_SCHEMA_VERSION:
            raise AuthorityContractError("unsupported audit schema version")
        if not self.execution_package_id or not self.execution_hash:
            raise AuthorityContractError("audit package must bind to Execution")
        _validate_trade_date(self.trade_date)
        object.__setattr__(self, "observed_orders", _rows(self.observed_orders) if self.observed_orders else ())
        object.__setattr__(self, "findings", tuple(str(f) for f in self.findings))
        object.__setattr__(self, "content_hash", _hash(self.to_dict(include_hash=False)))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "package_id": self.package_id,
            "trade_date": self.trade_date,
            "execution_package_id": self.execution_package_id,
            "execution_hash": self.execution_hash,
            "observed_orders": [_thaw(row) for row in self.observed_orders],
            "findings": list(self.findings),
        }
        if include_hash:
            result["content_hash"] = self.content_hash
        return result


def build_evidence_package(*, package_id: str, trade_date: str, source_refs: Sequence[str], observations: Sequence[Mapping[str, Any]]) -> EvidencePackage:
    return EvidencePackage(EVIDENCE_SCHEMA_VERSION, package_id, trade_date, tuple(source_refs), tuple(dict(r) for r in observations))


def build_decision_package(*, package_id: str, trade_date: str, evidence: EvidencePackage, target_rows: Sequence[Mapping[str, Any]], source_refs: Sequence[str], target_cash_weight: float = 0.0) -> DecisionPackage:
    if trade_date != evidence.trade_date:
        raise AuthorityContractError("Decision trade_date must match Evidence")
    return DecisionPackage(DECISION_SCHEMA_VERSION, package_id, trade_date, evidence.package_id, evidence.content_hash, "DECISION", target_cash_weight, tuple(dict(r) for r in target_rows), tuple(source_refs))


def build_risk_package(*, package_id: str, decision: DecisionPackage, approved_target_rows: Sequence[Mapping[str, Any]], constraints: Mapping[str, Any], source_refs: Sequence[str], approved_cash_weight: float | None = None) -> RiskPackage:
    decision_by_symbol = {str(row["symbol"]): row for row in decision.target_rows}
    approved = _rows(approved_target_rows)
    if not {row["symbol"] for row in approved}.issubset(decision_by_symbol):
        raise AuthorityContractError("Risk may constrain Decision targets but cannot invent symbols")
    for row in approved:
        decision_row = decision_by_symbol[str(row["symbol"])]
        approved_side = str(row.get("side") or "").upper()
        decision_side = str(decision_row.get("side") or "").upper()
        if approved_side and decision_side and approved_side != decision_side:
            raise AuthorityContractError("Risk may not reverse a Decision target side")
        for field_name in ("shares", "quantity", "notional", "target_weight", "weight"):
            if field_name not in row or field_name not in decision_row:
                continue
            try:
                approved_value = abs(float(row[field_name]))
                decision_value = abs(float(decision_row[field_name]))
            except (TypeError, ValueError) as exc:
                raise AuthorityContractError(f"non-numeric {field_name} in target rows") from exc
            if approved_value > decision_value + 1e-12:
                raise AuthorityContractError(
                    f"Risk may constrain but not increase Decision {field_name}"
                )
    risk_cash = decision.target_cash_weight if approved_cash_weight is None else _cash_weight(approved_cash_weight, "approved_cash_weight")
    if risk_cash + 1e-12 < decision.target_cash_weight:
        raise AuthorityContractError("Risk may constrain but not reduce Decision cash weight")
    return RiskPackage(RISK_SCHEMA_VERSION, package_id, decision.trade_date, decision.package_id, decision.content_hash, risk_cash, approved, constraints, tuple(source_refs))
