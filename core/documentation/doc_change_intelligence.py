from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DocumentationDomain:
    name: str
    match_prefixes: tuple[str, ...]
    match_exact: tuple[str, ...]
    impacted_docs: tuple[str, ...]
    review_owner: str
    rationale: str


DOMAIN_RULES = (
    DocumentationDomain(
        name="execution_runtime",
        match_prefixes=("scripts/cron_", "scripts/run_precomputed_alpaca_execution.py", "brokers/", "reconciliation.py"),
        match_exact=("scripts/crontab.txt", "daily_quant_report.py"),
        impacted_docs=("AGENTS.md", "docs/OPERATIONS.md", "docs/runbook.md", "docs/deployment_workflow.md"),
        review_owner="operations",
        rationale="Runtime, scheduler, broker, or reconciliation semantics changed.",
    ),
    DocumentationDomain(
        name="deployment_governance",
        match_prefixes=("deploy/", "scripts/deploy_", "scripts/validate_cron_commands.py"),
        match_exact=("docs/deployment_workflow.md",),
        impacted_docs=("AGENTS.md", "docs/OPERATIONS.md", "docs/deployment_workflow.md", "docs/governance/fr_governance_model.md"),
        review_owner="operations",
        rationale="Deployment or validation semantics changed.",
    ),
    DocumentationDomain(
        name="recovery_lifecycle",
        match_prefixes=("core/recovery/", "scripts/simulate_interrupted_recovery.py", "Tests/fixtures/interrupted_runs/"),
        match_exact=(),
        impacted_docs=("AGENTS.md", "docs/runbook.md", "docs/architecture/architecture_lineage.md", "docs/governance/governance_taxonomy.md"),
        review_owner="governance",
        rationale="Recovery lifecycle, settlement, or interruption intelligence changed.",
    ),
    DocumentationDomain(
        name="documentation_governance",
        match_prefixes=("core/documentation/", "scripts/validate_documentation_governance.py", "Tests/test_documentation_governance.py"),
        match_exact=(),
        impacted_docs=("AGENTS.md", "docs/documentation/canonical_hierarchy.md", "docs/documentation/metadata_standard.md", "docs/governance/governance_taxonomy.md"),
        review_owner="documentation",
        rationale="Documentation governance tooling or validation semantics changed.",
    ),
    DocumentationDomain(
        name="strategy_research",
        match_prefixes=("alpha_stack/", "overnight_agents/", "quant_research_agent/", "config/"),
        match_exact=("data/universe.csv",),
        impacted_docs=("AGENTS.md", "README.md", "docs/architecture/architecture_lineage.md", "docs/OPERATIONS.md"),
        review_owner="research",
        rationale="Strategy, signal, research, universe, or configuration semantics changed.",
    ),
    DocumentationDomain(
        name="dashboard_reporting",
        match_prefixes=("web/dashboard/", "scripts/refresh_quant_dashboard.py", "scripts/research/build_dashboard_v1.py"),
        match_exact=("deploy/caerus-dashboard.nginx",),
        impacted_docs=("AGENTS.md", "docs/OPERATIONS.md", "docs/runbook.md", "docs/architecture/architecture_lineage.md"),
        review_owner="operations",
        rationale="Dashboard, reporting, or operator visibility semantics changed.",
    ),
    DocumentationDomain(
        name="governance_process",
        match_prefixes=("docs/governance/",),
        match_exact=(),
        impacted_docs=("AGENTS.md", "docs/governance/governance_taxonomy.md", "docs/governance/fr_governance_model.md"),
        review_owner="governance",
        rationale="Governance source material changed and canonical references may need review.",
    ),
)

CANONICAL_TARGETS = {
    "AGENTS.md",
    "README.md",
    "docs/OPERATIONS.md",
    "docs/runbook.md",
    "docs/deployment_workflow.md",
    "docs/governance/fr_governance_model.md",
    "docs/governance/governance_taxonomy.md",
    "docs/architecture/architecture_lineage.md",
    "docs/documentation/canonical_hierarchy.md",
    "docs/documentation/metadata_standard.md",
}


def build_change_intelligence(
    repo_root: Path,
    inventory: dict[str, Any],
    lineage: dict[str, Any],
    freshness: dict[str, Any],
    *,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    normalized_changes = sorted({_normalize_path(path) for path in changed_files or [] if _normalize_path(path)})
    records = {record["path"]: record for record in inventory.get("records", [])}
    stale_paths = {item["path"] for item in freshness.get("stale_docs", [])}
    missing_metadata = set(freshness.get("missing_metadata", []))

    impacted_domains = _classify_domains(normalized_changes)
    impacted_docs = _build_impacted_docs(records, stale_paths, missing_metadata, impacted_domains)
    architecture_drift = _detect_architecture_drift(repo_root, records, normalized_changes, impacted_domains)
    semantic_drift = _detect_semantic_drift(records, normalized_changes, impacted_domains)
    review_queue = _build_review_queue(impacted_docs, architecture_drift, semantic_drift)
    debt = _build_debt_report(review_queue, impacted_docs, architecture_drift, semantic_drift)

    return {
        "changed_files": normalized_changes,
        "impacted_domains": [domain_to_artifact(domain) for domain in impacted_domains],
        "impacted_docs": impacted_docs,
        "likely_stale_canonical_docs": [
            item for item in impacted_docs
            if item["exists"] and (item["stale"] or item["missing_metadata"] or item["canonical"])
        ],
        "runtime_semantic_drift": semantic_drift,
        "architecture_drift": architecture_drift,
        "proposed_updates_markdown": build_proposed_updates_markdown(
            normalized_changes,
            impacted_domains,
            impacted_docs,
            architecture_drift,
            semantic_drift,
        ),
        "review_queue": review_queue,
        "documentation_debt": debt,
        "lineage_context": {
            "orphan_docs_count": len(lineage.get("orphan_docs") or []),
            "missing_references_count": len(lineage.get("missing_references") or []),
        },
        "mutation_policy": {
            "recommendations_only": True,
            "auto_mutation": False,
            "auto_commit": False,
            "human_approval_required": True,
        },
    }


def domain_to_artifact(domain: DocumentationDomain) -> dict[str, Any]:
    return {
        "name": domain.name,
        "impacted_docs": list(domain.impacted_docs),
        "review_owner": domain.review_owner,
        "rationale": domain.rationale,
    }


def build_proposed_updates_markdown(
    changed_files: list[str],
    impacted_domains: list[DocumentationDomain],
    impacted_docs: list[dict[str, Any]],
    architecture_drift: list[dict[str, str]],
    semantic_drift: list[dict[str, str]],
) -> str:
    lines = [
        "# Proposed Documentation Updates",
        "",
        "These are review drafts only. No documentation files were edited.",
        "",
        "## Changed Inputs",
    ]
    if changed_files:
        lines.extend(f"- `{path}`" for path in changed_files)
    else:
        lines.append("- No changed files were supplied.")

    lines.extend(["", "## Impacted Domains"])
    if impacted_domains:
        for domain in impacted_domains:
            lines.append(f"- `{domain.name}`: {domain.rationale}")
    else:
        lines.append("- No domain rules matched the supplied changes.")

    lines.extend(["", "## Proposed Review Notes"])
    for item in impacted_docs:
        lines.append(f"- `{item['path']}`: {item['reason']}")

    lines.extend(["", "## Draft Patch Blocks"])
    if not impacted_docs:
        lines.append("- No patch drafts generated.")
    for item in impacted_docs:
        lines.extend(
            [
                f"### `{item['path']}`",
                "",
                "```diff",
                f"+ Review required: {item['reason']}",
                f"+ Impacted domain(s): {', '.join(item['domains'])}",
                "+ Proposed update: add or revise the canonical operational note after human approval.",
                "```",
                "",
            ]
        )

    if architecture_drift or semantic_drift:
        lines.extend(["## Drift Findings"])
        for item in architecture_drift:
            lines.append(f"- `architecture`: {item['detail']}")
        for item in semantic_drift:
            lines.append(f"- `runtime`: {item['detail']}")
    return "\n".join(lines)


def _classify_domains(changed_files: list[str]) -> list[DocumentationDomain]:
    matched: list[DocumentationDomain] = []
    for domain in DOMAIN_RULES:
        if any(_matches_domain(path, domain) for path in changed_files):
            matched.append(domain)
    return sorted(matched, key=lambda domain: domain.name)


def _matches_domain(path: str, domain: DocumentationDomain) -> bool:
    return path in domain.match_exact or any(path.startswith(prefix) for prefix in domain.match_prefixes)


def _build_impacted_docs(
    records: dict[str, dict[str, Any]],
    stale_paths: set[str],
    missing_metadata: set[str],
    domains: list[DocumentationDomain],
) -> list[dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for domain in domains:
        for path in domain.impacted_docs:
            record = records.get(path)
            item = docs.setdefault(
                path,
                {
                    "path": path,
                    "domains": [],
                    "exists": record is not None,
                    "canonical": path in CANONICAL_TARGETS or (bool(record.get("canonical")) if record else False),
                    "stale": path in stale_paths,
                    "missing_metadata": path in missing_metadata or record is None,
                    "review_owner": domain.review_owner,
                    "reason": "",
                    "priority": "MEDIUM",
                },
            )
            item["domains"].append(domain.name)
            if domain.review_owner not in item["review_owner"]:
                item["review_owner"] = "cross_functional"
    for item in docs.values():
        item["domains"] = sorted(set(item["domains"]))
        item["priority"] = _priority_for_doc(item)
        item["reason"] = _reason_for_doc(item)
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    return sorted(docs.values(), key=lambda item: (priority_order.get(item["priority"], 4), item["path"]))


def _detect_architecture_drift(
    repo_root: Path,
    records: dict[str, dict[str, Any]],
    changed_files: list[str],
    domains: list[DocumentationDomain],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    domain_names = {domain.name for domain in domains}
    architecture_changes = [
        path for path in changed_files
        if path.startswith(("core/", "alpha_stack/", "overnight_agents/", "quant_research_agent/"))
    ]
    if architecture_changes and "docs/architecture/architecture_lineage.md" not in records:
        findings.append(
            {
                "severity": "HIGH",
                "code": "missing_architecture_lineage_doc",
                "detail": "Architecture code changed but docs/architecture/architecture_lineage.md is not present.",
            }
        )
    undocumented = [
        path for path in architecture_changes
        if not _is_referenced_by_docs(records, path) and (repo_root / path).exists()
    ]
    if undocumented:
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "architecture_change_lacks_doc_reference",
                "detail": ", ".join(sorted(undocumented)[:10]),
            }
        )
    if "recovery_lifecycle" in domain_names and "docs/runbook.md" not in records:
        findings.append(
            {
                "severity": "HIGH",
                "code": "recovery_change_without_runbook",
                "detail": "Recovery lifecycle changes need a canonical runbook reference.",
            }
        )
    return findings


def _detect_semantic_drift(
    records: dict[str, dict[str, Any]],
    changed_files: list[str],
    domains: list[DocumentationDomain],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    domain_names = {domain.name for domain in domains}
    if "execution_runtime" in domain_names and "AGENTS.md" not in records:
        findings.append({"severity": "HIGH", "code": "runtime_change_without_agents", "detail": "Runtime changes require AGENTS.md review."})
    if "deployment_governance" in domain_names and "docs/deployment_workflow.md" not in records:
        findings.append({"severity": "HIGH", "code": "deployment_change_without_workflow_doc", "detail": "Deployment changes require docs/deployment_workflow.md."})
    if any(path.startswith("scripts/cron_") or path == "scripts/crontab.txt" for path in changed_files):
        findings.append(
            {
                "severity": "MEDIUM",
                "code": "cron_semantics_review_required",
                "detail": "Cron or scheduled execution semantics changed; AGENTS.md, OPERATIONS, and deployment workflow need review.",
            }
        )
    return findings


def _build_review_queue(
    impacted_docs: list[dict[str, Any]],
    architecture_drift: list[dict[str, str]],
    semantic_drift: list[dict[str, str]],
) -> list[dict[str, Any]]:
    queue: list[dict[str, Any]] = []
    for item in impacted_docs:
        queue.append(
            {
                "type": "DOC_REVIEW",
                "priority": item["priority"],
                "path": item["path"],
                "owner": item["review_owner"],
                "reason": item["reason"],
                "requires_human_approval": True,
            }
        )
    for finding in architecture_drift + semantic_drift:
        queue.append(
            {
                "type": "DRIFT_REVIEW",
                "priority": finding["severity"],
                "path": None,
                "owner": "governance",
                "reason": f"{finding['code']}: {finding['detail']}",
                "requires_human_approval": True,
            }
        )
    priority_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    return sorted(queue, key=lambda item: (priority_order.get(item["priority"], 4), item["path"] or "", item["reason"]))


def _build_debt_report(
    review_queue: list[dict[str, Any]],
    impacted_docs: list[dict[str, Any]],
    architecture_drift: list[dict[str, str]],
    semantic_drift: list[dict[str, str]],
) -> dict[str, Any]:
    weights = {"CRITICAL": 25, "HIGH": 10, "MEDIUM": 4, "LOW": 1}
    score = sum(weights.get(item["priority"], 1) for item in review_queue)
    score += sum(3 for item in impacted_docs if item.get("missing_metadata"))
    score += 5 * len(architecture_drift)
    score += 5 * len(semantic_drift)
    if score >= 60:
        rating = "HIGH"
    elif score >= 25:
        rating = "MODERATE"
    elif score:
        rating = "LOW"
    else:
        rating = "NONE"
    return {
        "score": score,
        "rating": rating,
        "review_queue_count": len(review_queue),
        "impacted_doc_count": len(impacted_docs),
        "architecture_drift_count": len(architecture_drift),
        "runtime_semantic_drift_count": len(semantic_drift),
    }


def _priority_for_doc(item: dict[str, Any]) -> str:
    if not item["exists"]:
        return "HIGH"
    if "execution_runtime" in item["domains"] or "deployment_governance" in item["domains"]:
        return "HIGH"
    if item["stale"] or item["missing_metadata"]:
        return "MEDIUM"
    return "LOW"


def _reason_for_doc(item: dict[str, Any]) -> str:
    reasons: list[str] = []
    if not item["exists"]:
        reasons.append("canonical target is missing")
    if item["stale"]:
        reasons.append("document freshness threshold is exceeded")
    if item["missing_metadata"]:
        reasons.append("metadata is missing")
    if item["canonical"]:
        reasons.append("canonical source-of-truth review is required")
    if not reasons:
        reasons.append("impacted domain changed")
    return "; ".join(reasons)


def _is_referenced_by_docs(records: dict[str, dict[str, Any]], path: str) -> bool:
    for record in records.values():
        if path in set(record.get("references") or []):
            return True
    return False


def _normalize_path(path: str) -> str:
    clean = path.strip().replace("\\", "/")
    while clean.startswith("./"):
        clean = clean[2:]
    return clean
