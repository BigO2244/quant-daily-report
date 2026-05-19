from __future__ import annotations

from pathlib import Path
from typing import Any


CANONICAL_ROOTS = {
    "AGENTS.md",
    "README.md",
    "docs/OPERATIONS.md",
    "docs/runbook.md",
    "docs/deployment_workflow.md",
    "docs/documentation_governance.md",
    "docs/documentation_taxonomy.md",
    "docs/governance/fr_governance_model.md",
    "docs/governance/fr_active_backlog.md",
    "docs/governance/fr_registry.md",
}


def build_lineage(repo_root: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    existing_paths = {record["path"] for record in inventory.get("records", [])}
    edges: list[dict[str, str]] = []
    missing_refs: list[dict[str, str]] = []
    inbound: dict[str, int] = {path: 0 for path in existing_paths}
    for record in inventory.get("records", []):
        source = record["path"]
        for ref in record.get("references") or []:
            ref_path = _resolve_reference(repo_root, ref)
            if ref_path in existing_paths:
                edges.append({"from": source, "to": ref_path})
                inbound[ref_path] = inbound.get(ref_path, 0) + 1
            elif (repo_root / ref_path).exists():
                continue
            elif ref.startswith(("docs/", "scripts/", "core/", "Tests/", "AGENTS.md", "README.md")):
                missing_refs.append({"from": source, "missing_reference": ref})
    orphan_docs = sorted(
        path for path, count in inbound.items()
        if count == 0 and path not in CANONICAL_ROOTS and path.startswith("docs/")
    )
    return {
        "nodes": sorted(existing_paths),
        "edges": edges,
        "missing_references": missing_refs,
        "orphan_docs": orphan_docs,
        "canonical_roots": sorted(CANONICAL_ROOTS),
    }


def _resolve_reference(repo_root: Path, ref: str) -> str:
    clean = ref.strip().rstrip(").,;:")
    if clean.startswith("./"):
        clean = clean[2:]
    path = repo_root / clean
    try:
        if path.exists():
            return str(path.relative_to(repo_root))
    except Exception:
        pass
    return clean
