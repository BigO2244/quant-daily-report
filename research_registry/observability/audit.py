"""Frozen-semantic conformance audit helpers."""

from __future__ import annotations

from dataclasses import dataclass, field

from research_registry.observability.inspectors import RegistryInspector
from research_registry.registry import SQLiteResearchRegistry


@dataclass(frozen=True)
class ConformanceReport:
    status: str
    checks: dict[str, str]
    findings: list[str] = field(default_factory=list)


class ConformanceAuditor:
    def audit_registry(self, registry: SQLiteResearchRegistry) -> ConformanceReport:
        integrity = RegistryInspector().inspect(registry)
        checks = {
            "metadata_envelopes": "PASS" if not any("M00" in finding for finding in integrity.findings) else "FAIL",
            "provenance_dag": "PASS" if not any("DAG_INVALID" in finding for finding in integrity.findings) else "FAIL",
            "orphan_detection": "PASS" if not any("ORPHAN:" in finding for finding in integrity.findings) else "FAIL",
            "confidence_recomputation": "PASS" if not any("CONFIDENCE" in finding for finding in integrity.findings) else "FAIL",
            "chain_hash_integrity": "PASS" if not any("CHAIN_HASH" in finding for finding in integrity.findings) else "FAIL",
        }
        findings = list(integrity.findings)
        if any(status == "FAIL" for status in checks.values()):
            return ConformanceReport(status="FAIL", checks=checks, findings=findings)
        return ConformanceReport(status="PASS", checks=checks, findings=findings)
