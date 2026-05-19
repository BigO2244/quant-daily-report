from __future__ import annotations

import datetime as dt
from pathlib import Path

from core.documentation.doc_change_intelligence import build_change_intelligence
from core.documentation.doc_consistency import GOVERNANCE_CATEGORIES, build_consistency_report
from core.documentation.doc_freshness import build_freshness_report
from core.documentation.doc_inventory import build_inventory
from core.documentation.doc_lineage import build_lineage
from scripts.validate_documentation_governance import build_payload, write_reports


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _front_matter(title: str, *, reviewed: str = "2026-05-18", canonical: str = "true") -> str:
    return f"""---
last_reviewed: {reviewed}
owner: operations
category: documentation
criticality: medium
canonical: {canonical}
related_systems: [documentation]
---

# {title}
"""


def _minimal_docs_repo(root: Path) -> None:
    _write(root / "AGENTS.md", _front_matter("Agents") + "\nVM cron runtime. See docs/governance/fr_active_backlog.md\n")
    _write(root / "README.md", _front_matter("Readme"))
    _write(root / "docs" / "OPERATIONS.md", _front_matter("Operations"))
    _write(root / "docs" / "runbook.md", _front_matter("Runbook"))
    _write(root / "docs" / "deployment_workflow.md", _front_matter("Deployment"))
    _write(root / "docs" / "documentation_governance.md", _front_matter("Doc Governance"))
    _write(root / "docs" / "documentation_taxonomy.md", _front_matter("Doc Taxonomy"))
    _write(root / "docs" / "governance" / "fr_governance_model.md", _front_matter("FR Governance"))
    _write(root / "docs" / "governance" / "fr_active_backlog.md", _front_matter("FR Active"))
    _write(root / "docs" / "governance" / "fr_registry.md", _front_matter("FR Registry"))
    _write(root / "docs" / "architecture" / "index.md", _front_matter("Architecture"))
    _write(root / "docs" / "architecture" / "architecture_lineage.md", _front_matter("Architecture Lineage"))
    _write(root / "docs" / "documentation" / "index.md", _front_matter("Documentation"))
    _write(root / "docs" / "documentation" / "canonical_hierarchy.md", _front_matter("Canonical Hierarchy"))
    _write(root / "docs" / "documentation" / "metadata_standard.md", _front_matter("Metadata Standard"))
    _write(root / "docs" / "governance" / "governance_taxonomy.md", _front_matter("Governance Taxonomy"))


def test_documentation_inventory_extracts_metadata_and_references(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(
        tmp_path / "docs" / "example.md",
        _front_matter("Example") + "\nSee docs/OPERATIONS.md and scripts/validate_documentation_governance.py.\n",
    )

    inventory = build_inventory(tmp_path)
    records = {record["path"]: record for record in inventory["records"]}

    assert records["docs/example.md"]["has_metadata"] is True
    assert "docs/OPERATIONS.md" in records["docs/example.md"]["references"]
    assert "scripts/validate_documentation_governance.py" in records["docs/example.md"]["references"]


def test_lineage_detects_missing_references_and_orphans(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(tmp_path / "docs" / "orphan.md", _front_matter("Orphan"))
    _write(tmp_path / "docs" / "ref.md", _front_matter("Ref") + "\nSee docs/missing.md\n")

    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)

    assert {"from": "docs/ref.md", "missing_reference": "docs/missing.md"} in lineage["missing_references"]
    assert "docs/orphan.md" in lineage["orphan_docs"]


def test_freshness_reports_missing_metadata_and_stale_docs(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(tmp_path / "docs" / "stale.md", _front_matter("Stale", reviewed="2020-01-01"))
    _write(tmp_path / "docs" / "missing_meta.md", "# Missing Meta\n")

    freshness = build_freshness_report(build_inventory(tmp_path), today=dt.date(2026, 5, 18))

    assert "docs/missing_meta.md" in freshness["missing_metadata"]
    assert any(item["path"] == "docs/stale.md" for item in freshness["stale_docs"])


def test_consistency_requires_canonical_hierarchy(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    (tmp_path / "docs" / "OPERATIONS.md").unlink()
    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)

    consistency = build_consistency_report(tmp_path, inventory, lineage)

    assert consistency["fail_count"] == 1
    assert any(item["code"] == "missing_canonical_doc" and item["detail"] == "docs/OPERATIONS.md" for item in consistency["findings"])


def test_governance_taxonomy_categories_are_available() -> None:
    assert {"ARC", "OPS", "DOC", "HOTFIX", "FR"} <= GOVERNANCE_CATEGORIES


def test_validator_writes_expected_reports(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    payload = build_payload(tmp_path)
    out_dir = tmp_path / "outputs" / "documentation_validation"

    paths = write_reports(payload, out_dir)

    names = {path.name for path in paths}
    assert "documentation_validation_report.json" in names
    assert "documentation_inventory.json" in names
    assert "documentation_lineage.json" in names
    assert "canonicalization_summary.md" in names
    assert "governance_consistency_report.md" in names
    assert "impacted_docs_report.json" in names
    assert "proposed_doc_updates.md" in names
    assert "documentation_review_queue.json" in names
    assert "documentation_debt_report.json" in names


def test_change_intelligence_maps_impacted_docs_for_recovery_changes(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(tmp_path / "core" / "recovery" / "state_transitions.py", "STATE = 'RECOVERY_CANDIDATE'\n")
    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)
    freshness = build_freshness_report(inventory, today=dt.date(2026, 5, 18))

    report = build_change_intelligence(
        tmp_path,
        inventory,
        lineage,
        freshness,
        changed_files=["core/recovery/state_transitions.py"],
    )

    domain_names = {domain["name"] for domain in report["impacted_domains"]}
    impacted_paths = {item["path"] for item in report["impacted_docs"]}
    assert "recovery_lifecycle" in domain_names
    assert "docs/runbook.md" in impacted_paths
    assert "docs/architecture/architecture_lineage.md" in impacted_paths
    assert report["mutation_policy"]["auto_mutation"] is False


def test_change_intelligence_generates_governance_review_for_cron_changes(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(tmp_path / "scripts" / "cron_execute.sh", "#!/usr/bin/env bash\n")
    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)
    freshness = build_freshness_report(inventory, today=dt.date(2026, 5, 18))

    report = build_change_intelligence(
        tmp_path,
        inventory,
        lineage,
        freshness,
        changed_files=["scripts/cron_execute.sh"],
    )

    assert any(item["type"] == "DRIFT_REVIEW" and "cron_semantics_review_required" in item["reason"] for item in report["review_queue"])
    assert any(item["path"] == "docs/deployment_workflow.md" for item in report["review_queue"])
    assert report["documentation_debt"]["rating"] in {"LOW", "MODERATE", "HIGH"}


def test_change_intelligence_detects_architecture_drift_without_doc_reference(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    _write(tmp_path / "core" / "new_architecture" / "engine.py", "class Engine:\n    pass\n")
    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)
    freshness = build_freshness_report(inventory, today=dt.date(2026, 5, 18))

    report = build_change_intelligence(
        tmp_path,
        inventory,
        lineage,
        freshness,
        changed_files=["core/new_architecture/engine.py"],
    )

    assert any(item["code"] == "architecture_change_lacks_doc_reference" for item in report["architecture_drift"])


def test_change_intelligence_recommendations_are_deterministic(tmp_path: Path) -> None:
    _minimal_docs_repo(tmp_path)
    inventory = build_inventory(tmp_path)
    lineage = build_lineage(tmp_path, inventory)
    freshness = build_freshness_report(inventory, today=dt.date(2026, 5, 18))
    changed = ["core/documentation/doc_change_intelligence.py", "docs/governance/governance_taxonomy.md"]

    first = build_change_intelligence(tmp_path, inventory, lineage, freshness, changed_files=changed)
    second = build_change_intelligence(tmp_path, inventory, lineage, freshness, changed_files=list(reversed(changed)))

    assert first["impacted_docs"] == second["impacted_docs"]
    assert first["review_queue"] == second["review_queue"]
    assert first["proposed_updates_markdown"] == second["proposed_updates_markdown"]
