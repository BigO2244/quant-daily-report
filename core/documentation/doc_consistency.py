from __future__ import annotations

from pathlib import Path
from typing import Any


GOVERNANCE_CATEGORIES = {"ARC", "OPS", "DOC", "HOTFIX", "FR"}
LIFECYCLE_STATES = {
    "BACKLOG",
    "READY",
    "READY_VALIDATED",
    "IN_PROGRESS",
    "DONE",
    "PROMOTION_READY",
    "DEPLOYED_OBSERVING",
    "DEPLOYED",
    "REVIEWED_DEFERRED",
}
CANONICAL_HIERARCHY = {
    "AGENTS.md": "operational runtime truth and agent handoff",
    "README.md": "onboarding and high-level architecture",
    "docs/OPERATIONS.md": "operator procedures",
    "docs/runbook.md": "incident and recovery operations",
    "docs/deployment_workflow.md": "deployment lifecycle",
    "docs/governance/": "governance and process",
    "docs/architecture/": "long-lived architecture",
    "docs/documentation/": "documentation governance",
}


def build_consistency_report(repo_root: Path, inventory: dict[str, Any], lineage: dict[str, Any]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    existing = {record["path"] for record in inventory.get("records", [])}
    for required in ("AGENTS.md", "README.md", "docs/OPERATIONS.md", "docs/runbook.md", "docs/deployment_workflow.md"):
        if required not in existing:
            findings.append({"severity": "FAIL", "code": "missing_canonical_doc", "detail": required})
    if not (repo_root / "docs" / "architecture").exists():
        findings.append({"severity": "WARN", "code": "missing_architecture_directory", "detail": "docs/architecture/"})
    if not (repo_root / "docs" / "documentation").exists():
        findings.append({"severity": "WARN", "code": "missing_documentation_directory", "detail": "docs/documentation/"})
    for missing in lineage.get("missing_references", []):
        findings.append({"severity": "WARN", "code": "missing_reference", "detail": f"{missing['from']} -> {missing['missing_reference']}"})
    agents = repo_root / "AGENTS.md"
    if agents.exists():
        text = agents.read_text(encoding="utf-8", errors="replace")
        if "VM" not in text or "cron" not in text:
            findings.append({"severity": "WARN", "code": "agents_runtime_semantics_sparse", "detail": "AGENTS.md should describe VM cron runtime ownership"})
        if "docs/governance/fr_active_backlog.md" not in text:
            findings.append({"severity": "WARN", "code": "agents_missing_fr_backlog_reference", "detail": "AGENTS.md should point to active governance backlog"})
    return {
        "governance_categories": sorted(GOVERNANCE_CATEGORIES),
        "lifecycle_states": sorted(LIFECYCLE_STATES),
        "canonical_hierarchy": CANONICAL_HIERARCHY,
        "findings": findings,
        "fail_count": sum(1 for finding in findings if finding["severity"] == "FAIL"),
        "warn_count": sum(1 for finding in findings if finding["severity"] == "WARN"),
    }

