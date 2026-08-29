"""Generic, bounded adapter contract for heterogeneous research techniques."""

from __future__ import annotations

import ast
import importlib
import inspect
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from projects.alpha_lab.factory.canonical import canonical_hash, require_non_empty
from projects.alpha_lab.factory.errors import ContractValidationError, ResearchBoundaryError


_ALLOWED_MODULE_PREFIX = "projects.alpha_lab.evaluators."
_FORBIDDEN_IMPORT_ROOTS = {
    "alpha_stack",
    "brokers",
    "core",
    "daily_quant_report",
    "deploy",
    "reconciliation",
    "scripts",
}
_FORBIDDEN_CALLS = {
    "cancel_order",
    "submit_market_order",
    "submit_option_limit_order",
    "submit_option_market_order",
}


class TechniqueFamily(str, Enum):
    CROSS_SECTIONAL = "CROSS_SECTIONAL"
    TIME_SERIES = "TIME_SERIES"
    EVENT_STUDY = "EVENT_STUDY"
    MACHINE_LEARNING = "MACHINE_LEARNING"
    PORTFOLIO_CONSTRUCTION = "PORTFOLIO_CONSTRUCTION"
    OPTIONS_INFORMATION = "OPTIONS_INFORMATION"
    EXECUTION_RESEARCH = "EXECUTION_RESEARCH"
    OTHER = "OTHER"


class EvaluationPhase(str, Enum):
    DISCOVERY = "DISCOVERY"
    CHALLENGE = "CHALLENGE"


@dataclass(frozen=True)
class EvaluatorSpec:
    hypothesis_id: str
    evaluator_id: str
    technique_family: TechniqueFamily
    module: str
    callable_name: str
    maximum_variants: int
    primary_metric: str
    data_contract_ids: Tuple[str, ...]
    challenge_period: str
    spec_hash: str
    schema_version: str = "caerus_alpha_lab_evaluator_spec_v1"

    def __post_init__(self) -> None:
        for name in (
            "hypothesis_id",
            "evaluator_id",
            "module",
            "callable_name",
            "primary_metric",
            "challenge_period",
        ):
            require_non_empty(getattr(self, name), name)
        if not self.module.startswith(_ALLOWED_MODULE_PREFIX):
            raise ResearchBoundaryError(
                "evaluator module must live below projects.alpha_lab.evaluators"
            )
        if self.maximum_variants < 1:
            raise ContractValidationError("maximum_variants must be positive")
        if not self.data_contract_ids:
            raise ContractValidationError("data_contract_ids cannot be empty")
        unsigned = self.unsigned_dict()
        if canonical_hash(unsigned) != self.spec_hash:
            raise ContractValidationError("evaluator spec_hash mismatch")

    def unsigned_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "hypothesis_id": self.hypothesis_id,
            "evaluator_id": self.evaluator_id,
            "technique_family": self.technique_family.value,
            "module": self.module,
            "callable_name": self.callable_name,
            "maximum_variants": self.maximum_variants,
            "primary_metric": self.primary_metric,
            "data_contract_ids": self.data_contract_ids,
            "challenge_period": self.challenge_period,
        }

    def to_dict(self) -> Dict[str, Any]:
        result = self.unsigned_dict()
        result["spec_hash"] = self.spec_hash
        return result

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "EvaluatorSpec":
        return cls(
            hypothesis_id=value["hypothesis_id"],
            evaluator_id=value["evaluator_id"],
            technique_family=TechniqueFamily(value["technique_family"]),
            module=value["module"],
            callable_name=value["callable_name"],
            maximum_variants=int(value["maximum_variants"]),
            primary_metric=value["primary_metric"],
            data_contract_ids=tuple(value["data_contract_ids"]),
            challenge_period=value["challenge_period"],
            spec_hash=value["spec_hash"],
            schema_version=value.get("schema_version", "caerus_alpha_lab_evaluator_spec_v1"),
        )


def inspect_evaluator_boundary(source_path: Path) -> Dict[str, Any]:
    """Statically reject direct production imports and order-submission calls."""

    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    findings = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                    findings.append("forbidden_import:{}".format(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _FORBIDDEN_IMPORT_ROOTS:
                findings.append("forbidden_import:{}".format(node.module))
        elif isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            if name in _FORBIDDEN_CALLS:
                findings.append("forbidden_call:{}".format(name))
    return {
        "schema_version": "caerus_alpha_lab_evaluator_boundary_v1",
        "source_path": str(source_path),
        "status": "PASS" if not findings else "FAIL",
        "findings": sorted(set(findings)),
        "source_sha256": __import__("hashlib").sha256(source_path.read_bytes()).hexdigest(),
    }


def run_evaluator(
    *,
    spec: EvaluatorSpec,
    input_packet: Mapping[str, Any],
    phase: EvaluationPhase,
    challenge_access_authorized: bool,
) -> Dict[str, Any]:
    """Run a frozen evaluator and normalize its result without changing lifecycle state."""

    if phase is EvaluationPhase.CHALLENGE and not challenge_access_authorized:
        raise ContractValidationError("challenge phase requires explicit access authorization")
    if phase is EvaluationPhase.DISCOVERY and challenge_access_authorized:
        raise ContractValidationError("discovery phase cannot authorize challenge access")
    if input_packet.get("data_gate_status") != "READY_FOR_FROZEN_EVALUATOR":
        raise ContractValidationError("frozen evaluator requires a ready data gate")
    if input_packet.get("hypothesis_id") != spec.hypothesis_id:
        raise ContractValidationError("input hypothesis does not match evaluator spec")
    packet_assets = input_packet.get("assets")
    if not isinstance(packet_assets, Mapping):
        raise ContractValidationError("evaluator input requires certified assets")
    missing_contracts = sorted(
        set(spec.data_contract_ids) - {str(item) for item in packet_assets}
    )
    if missing_contracts:
        raise ContractValidationError(
            "evaluator input is missing frozen data contracts: {}".format(
                ",".join(missing_contracts)
            )
        )

    module = importlib.import_module(spec.module)
    source_path = Path(inspect.getsourcefile(module) or "").resolve()
    boundary = inspect_evaluator_boundary(source_path)
    if boundary["status"] != "PASS":
        raise ResearchBoundaryError("evaluator production boundary failed")
    function = getattr(module, spec.callable_name, None)
    if not callable(function):
        raise ContractValidationError("evaluator callable is missing")
    raw = function(dict(input_packet), phase=phase.value)
    if not isinstance(raw, Mapping):
        raise ContractValidationError("evaluator must return a mapping")
    variant_count = raw.get("variant_count")
    if not isinstance(variant_count, int) or variant_count < 1:
        raise ContractValidationError("evaluator result requires positive variant_count")
    if variant_count > spec.maximum_variants:
        raise ContractValidationError("evaluator exceeded frozen maximum_variants")
    if raw.get("primary_metric_name") != spec.primary_metric:
        raise ContractValidationError("evaluator primary metric changed from frozen spec")
    if raw.get("orders_submitted") is not False:
        raise ResearchBoundaryError("evaluator must attest that no orders were submitted")
    result = {
        "schema_version": "caerus_alpha_lab_evaluator_result_v1",
        "hypothesis_id": spec.hypothesis_id,
        "evaluator_id": spec.evaluator_id,
        "technique_family": spec.technique_family.value,
        "phase": phase.value,
        "spec_hash": spec.spec_hash,
        "input_packet_hash": canonical_hash(input_packet),
        "boundary_attestation": boundary,
        "result": dict(raw),
        "promotion_performed": False,
        "trading_behavior_changed": False,
    }
    result["result_hash"] = canonical_hash(result)
    return result


def load_spec(path: Path) -> EvaluatorSpec:
    return EvaluatorSpec.from_dict(json.loads(path.read_text(encoding="utf-8")))
