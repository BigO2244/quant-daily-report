from __future__ import annotations

import json
from pathlib import Path

from scripts import governance_hygiene_agent as gha


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _fixture_repo(root: Path) -> None:
    _write(root / "AGENTS.md", "# Agents\n")
    _write(root / "README.md", "# Readme\n")
    _write(root / "docs/governance/caerus_investment_doctrine.md", "# Doctrine\n")
    _write(root / "docs/governance/README.md", "# Governance README\n")
    _write(root / "docs/governance/CURRENT_RESEARCH_ROADMAP.md", "# Roadmap\n")
    _write(root / "docs/governance/fr_registry.md", """# FR Registry

## FR File Location Audit

| FR | Title | Registry status | Current location | Lifecycle | Notes |
|---|---|---|---|---|---|
| FR-001 | Active Missing | READY | docs/governance/fr_active/fr_001_active.md | ACTIVE | Active spec retained. |
| FR-002 | Duplicate A | ACTIVE_RESEARCH | docs/governance/fr_archive/fr_002_archive.md | ARCHIVED | Archived copy retained; folder location is navigational only. |
| FR-002 | Duplicate B | ACTIVE_RESEARCH | docs/governance/fr_archive/fr_002_archive.md | ARCHIVED | Archived copy retained; folder location is navigational only. |
| FR-004 | Archive Active | ACTIVE_RESEARCH | docs/governance/fr_archive/fr_004_archive.md | ARCHIVED | Archived copy retained; current work tracked in backlog. |
| FR-005 | Missing Path | PROPOSED | docs/governance/fr_active/fr_005_missing.md | PROPOSED | Proposed FR; no file exists yet. |
""")
    _write(root / "docs/governance/fr_active_backlog.md", """# FR Active Backlog

## Current Active Summary

| FR | Phase | Status | Blast Radius | Dependencies | Observation Status | Current State | Rollback Reference |
|---|---|---|---|---|---|---|---|
| FR-001 Active Missing | Governance Automation | READY | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
| FR-002 Duplicate A | Governance Automation | READY | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
| FR-003 Backlog Missing Registry | Governance Automation | PROPOSED | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
| FR-004 Archive Active | Governance Automation | READY | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
| FR-005 Missing Path | Governance Automation | READY | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
""")
    _write(root / "docs/governance/fr_active/fr_001_active.md", "# FR-001 — Active Missing\n\nStatus: READY\n")
    _write(root / "docs/governance/fr_active/fr_006_active.md", "# FR-006 — Active Missing Registry\n\nStatus: READY\n")
    _write(root / "docs/governance/fr_archive/fr_002_archive.md", "# FR-002 — Duplicate A\n\nStatus: ACTIVE_RESEARCH\n")
    _write(root / "docs/governance/fr_archive/fr_004_archive.md", "# FR-004 — Archive Active\n\nStatus: ACTIVE_RESEARCH\n")


def _run_audit(root: Path, *, date: str = "2026-06-11", fail_on_fail: bool = False) -> tuple[int, Path, Path]:
    argv = [
        "--date",
        date,
        "--output-dir",
        "outputs/governance_hygiene",
        "--repo-root",
        str(root),
    ]
    if fail_on_fail:
        argv.append("--fail-on-fail")
    exit_code = gha.main(argv)
    run_dir = root / "outputs" / "governance_hygiene" / date
    return exit_code, run_dir / "governance_hygiene.json", run_dir / "governance_hygiene.md"


def _finding_categories(payload: dict[str, object]) -> list[tuple[str, str]]:
    return [(finding["category"], finding["severity"]) for finding in payload["findings"]]


def test_detects_registry_and_backlog_gaps(tmp_path: Path) -> None:
    _fixture_repo(tmp_path)

    payload = gha._build_audit(tmp_path, gha._today_from_args("2026-06-11"))
    categories = _finding_categories(payload)

    assert ("registry_coverage", "FAIL") in categories
    assert ("backlog_registry_gap", "FAIL") in categories


def test_extract_fr_number_supports_suffixes() -> None:
    assert gha._extract_fr_number("FR-036a") == "FR-036a"
    assert gha._extract_fr_number("FR-036d MCP conformance audit") == "FR-036d"


def test_collect_table_rows_allows_blank_lines_between_rows(tmp_path: Path) -> None:
    table = tmp_path / "table.md"
    _write(
        table,
        """| FR | Title | Registry status |
|---|---|---|
| FR-001 | First | READY |

| FR-002 | Second | RESEARCH_ONLY |

## Next Section
""",
    )

    tables = gha._collect_table_rows(table)

    assert len(tables) == 1
    assert [row.cells[0] for row in tables[0][1]] == ["FR-001", "FR-002"]


def test_detects_registry_path_duplicate_and_archive_warning(tmp_path: Path) -> None:
    _fixture_repo(tmp_path)

    payload = gha._build_audit(tmp_path, gha._today_from_args("2026-06-11"))
    findings = payload["findings"]

    assert any(finding["category"] == "registry_path" and finding["severity"] == "FAIL" for finding in findings)
    assert any(finding["category"] == "registry_duplicate" and finding["severity"] == "FAIL" for finding in findings)
    assert any(finding["category"] == "archive_registry_status" and finding["severity"] == "WARN" for finding in findings)


def test_documented_numbering_exceptions_suppress_duplicate_file_numbers(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "# Agents\n")
    _write(tmp_path / "README.md", "# Readme\n")
    _write(tmp_path / "docs/governance/caerus_investment_doctrine.md", "# Doctrine\n")
    _write(
        tmp_path / "docs/governance/README.md",
        "# Governance README\n\nSee `docs/governance/caerus_investment_doctrine.md`.\n",
    )
    _write(
        tmp_path / "docs/governance/CURRENT_RESEARCH_ROADMAP.md",
        "# Roadmap\n\nSee `docs/governance/caerus_investment_doctrine.md`.\n",
    )
    _write(
        tmp_path / "docs/governance/fr_registry.md",
        """# FR Registry

## FR File Location Audit

| FR | Title | Registry status | Current location | Lifecycle | Notes |
|---|---|---|---|---|---|
| FR-002 | Duplicate Pair | READY | docs/governance/fr_active_backlog.md | ACTIVE | Canonical row retained for auditability. |

## FR Numbering Exceptions

- FR-002: intentional paired evidence retained for auditability.
""",
    )
    _write(
        tmp_path / "docs/governance/fr_active_backlog.md",
        """# FR Active Backlog

## Current Active Summary

| FR | Phase | Status | Blast Radius | Dependencies | Observation Status | Current State | Rollback Reference |
|---|---|---|---|---|---|---|---|
| FR-002 Duplicate Pair | Governance Automation | READY | LOW | docs/governance/fr_registry.md | not_started | Read-only audit plan. | Revert docs only. |
""",
    )
    _write(tmp_path / "docs/governance/fr_active/fr_002_active.md", "# FR-002 — Duplicate Pair\n\nStatus: READY\n")
    _write(tmp_path / "docs/governance/fr_archive/fr_002_archive.md", "# FR-002 — Duplicate Pair\n\nStatus: READY\n")

    payload = gha._build_audit(tmp_path, gha._today_from_args("2026-06-11"))

    assert not any(finding["category"] == "duplicate_fr_number" for finding in payload["findings"])


def test_detects_missing_doctrine_references(tmp_path: Path) -> None:
    _fixture_repo(tmp_path)

    payload = gha._build_audit(tmp_path, gha._today_from_args("2026-06-11"))
    doctrine_files = {
        finding["file"]
        for finding in payload["findings"]
        if finding["category"] == "doctrine_reference"
    }

    assert "docs/governance/README.md" in doctrine_files
    assert "docs/governance/CURRENT_RESEARCH_ROADMAP.md" in doctrine_files
    assert "docs/governance/fr_registry.md" in doctrine_files
    assert "docs/governance/fr_active_backlog.md" in doctrine_files
    assert "AGENTS.md" in doctrine_files


def test_registry_paths_resolve_from_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    assert gha._resolve_repo_relative_path(repo_root, "docs/governance/fr_archive/fr_050_phoenix_research_spec.md").exists()
    assert gha._resolve_repo_relative_path(repo_root, "docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md").exists()
    assert gha._resolve_repo_relative_path(repo_root, "research/pit_universe_architecture_2026-06-10.md").exists()


def test_live_repo_registry_paths_do_not_fail_for_existing_files(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    # The fixture repo gives us a small controlled baseline while the live repo
    # check below verifies the resolver against actual repo-root paths.
    _fixture_repo(tmp_path)
    _ = gha._build_audit(tmp_path, gha._today_from_args("2026-06-11"))

    for ref in [
        "docs/governance/fr_archive/fr_050_phoenix_research_spec.md",
        "docs/governance/fr_active/fr_069_research_lab_modular_sleeve_architecture.md",
        "research/pit_universe_architecture_2026-06-10.md",
    ]:
        assert gha._resolve_repo_relative_path(repo_root, ref).exists()


def test_writes_deterministic_outputs_and_preserves_sources(tmp_path: Path) -> None:
    _fixture_repo(tmp_path)
    source_snapshot = {
        path: (tmp_path / path).read_text(encoding="utf-8")
        for path in [
            "AGENTS.md",
            "README.md",
            "docs/governance/caerus_investment_doctrine.md",
            "docs/governance/README.md",
            "docs/governance/CURRENT_RESEARCH_ROADMAP.md",
            "docs/governance/fr_registry.md",
            "docs/governance/fr_active_backlog.md",
            "docs/governance/fr_active/fr_001_active.md",
            "docs/governance/fr_active/fr_006_active.md",
            "docs/governance/fr_archive/fr_002_archive.md",
            "docs/governance/fr_archive/fr_004_archive.md",
        ]
    }

    first_exit, first_json, first_md = _run_audit(tmp_path)
    first_json_text = first_json.read_text(encoding="utf-8")
    first_md_text = first_md.read_text(encoding="utf-8")

    second_exit, second_json, second_md = _run_audit(tmp_path)

    assert first_exit == 0
    assert second_exit == 0
    assert first_json_text == second_json.read_text(encoding="utf-8")
    assert first_md_text == second_md.read_text(encoding="utf-8")
    for rel_path, text in source_snapshot.items():
        assert (tmp_path / rel_path).read_text(encoding="utf-8") == text

    payload = json.loads(first_json_text)
    assert payload["status"] in {"WARN", "FAIL"}
    assert payload["generated_at"] == "2026-06-11T00:00:00Z"
    assert "files_scanned" in payload and payload["files_scanned"]


def test_respects_fail_on_fail(tmp_path: Path) -> None:
    _fixture_repo(tmp_path)

    exit_code, _, _ = _run_audit(tmp_path, fail_on_fail=True)

    assert exit_code == 1
