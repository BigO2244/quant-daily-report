#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
DOCTRINE_REF = "docs/governance/caerus_investment_doctrine.md"
FR_ACTIVE_DIR = Path("docs/governance/fr_active")
FR_ARCHIVE_DIR = Path("docs/governance/fr_archive")
GOVERNANCE_DOCS = [
    Path("AGENTS.md"),
    Path("README.md"),
    Path("docs/governance/caerus_investment_doctrine.md"),
    Path("docs/governance/README.md"),
    Path("docs/governance/CURRENT_RESEARCH_ROADMAP.md"),
    Path("docs/governance/fr_registry.md"),
    Path("docs/governance/fr_active_backlog.md"),
]
OPEN_REGISTRY_STATUSES = {
    "ACTIVE_RESEARCH",
    "DRAFT_RESEARCH",
    "DESIGN",
    "PROPOSED",
    "IN_PROGRESS",
    "READY",
    "READY_VALIDATED",
    "PROMOTION_READY",
    "DEPLOYED_OBSERVING",
    "PHASES_1_3_COMPLETE",
    "SHELVED",
}
CLOSED_REGISTRY_STATUSES = {
    "COMPLETED",
    "CLOSED_PASS",
    "REVIEWED_DEFERRED",
    "SUPERSEDED",
    "DEPLOYED",
}
EXECUTION_TERMS = (
    "execution",
    "broker",
    "order",
    "buy",
    "sell",
    "trade",
    "cash",
    "allocation",
)
WINDOW_TERMS = (
    "weekend",
    "maintenance window",
    "scheduled maintenance",
    "after-hours",
    "outside active market hours",
    "operator approval",
)
DOCTRINE_CONFLICT_TERMS = (
    "sharpe",
    "min-vol",
    "minimum variance",
    "balanced portfolio",
    "risk parity",
    "max sharpe",
    "primary objective",
)

ROW_SEVERITY_ORDER = {"FAIL": 0, "WARN": 1, "INFO": 2}
TABLE_HEADER_RE = re.compile(r"^\|\s*FR\s*\|")
FR_FILE_RE = re.compile(r"^FR-(\d{3})")
FR_PATH_RE = re.compile(r"fr_(\d{3})_[A-Za-z0-9_]+\.md")
REF_RE = re.compile(
    r"(?P<ref>(?:\.\./)?(?:docs/governance/|fr_active/|fr_archive/|docs/|AGENTS\.md|README\.md)[A-Za-z0-9_./\-]+\.md|(?:\.\./)?(?:AGENTS\.md|README\.md))"
)


@dataclass(frozen=True)
class Finding:
    severity: str
    category: str
    file: str
    line: int | None
    message: str
    suggested_action: str


@dataclass(frozen=True)
class TableRow:
    file: str
    line: int
    cells: list[str]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only governance hygiene auditor.")
    parser.add_argument("--date", type=str, default=None, help="Report date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("outputs/governance_hygiene"),
        help="Base output directory for report artifacts.",
    )
    parser.add_argument("--fail-on-warn", action="store_true", help="Exit non-zero when WARN findings exist.")
    parser.add_argument("--fail-on-fail", action="store_true", help="Exit non-zero when FAIL findings exist.")
    parser.add_argument("--json-only", action="store_true", help="Write JSON only and skip the markdown artifact.")
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Override repository root. Intended for tests and local validation.",
    )
    return parser.parse_args(argv)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _extract_fr_number(text: str) -> str | None:
    match = re.search(r"\bFR-(\d{3}[a-z]?)\b", text, re.IGNORECASE)
    if match:
        return f"FR-{match.group(1).lower()}"
    return None


def _clean_status(text: str) -> str:
    clean = text.strip().strip("`")
    match = re.match(r"([A-Z0-9_]+)", clean)
    if match:
        return match.group(1)
    return clean


def _resolve_repo_relative_path(repo_root: Path, ref: str) -> Path:
    clean = ref.strip().strip("`\"'")
    if clean.startswith("./"):
        clean = clean[2:]
    if clean.startswith("../"):
        return (repo_root / clean).resolve()
    return (repo_root / clean).resolve()


def _collect_table_rows(path: Path) -> list[tuple[str, list[TableRow]]]:
    tables: list[tuple[str, list[TableRow]]] = []
    lines = _read_text(path).splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if TABLE_HEADER_RE.match(line.strip()):
            header = line.strip()
            rows: list[TableRow] = []
            idx += 1
            while idx < len(lines):
                row_line = lines[idx]
                stripped = row_line.strip()
                if not stripped:
                    break
                if stripped.startswith("#") and not stripped.startswith("|"):
                    break
                if stripped.startswith("|") and not stripped.startswith("|---"):
                    cells = _split_cells(row_line)
                    if cells and cells[0].startswith("FR-"):
                        rows.append(TableRow(file=str(path), line=idx + 1, cells=cells))
                idx += 1
            tables.append((header, rows))
        idx += 1
    return tables


def _normalize_repo_path(repo_root: Path, ref: str, source: Path | None = None) -> Path:
    clean = ref.strip().strip("`\"'")
    if clean.startswith("./"):
        clean = clean[2:]
    if clean.startswith("../") and source is not None:
        return (source.parent / clean).resolve()
    if clean.startswith(("fr_active/", "fr_archive/")):
        return _resolve_repo_relative_path(repo_root, f"docs/governance/{clean}")
    if clean.startswith(("docs/", "AGENTS.md", "README.md")):
        return _resolve_repo_relative_path(repo_root, clean)
    return _resolve_repo_relative_path(repo_root, clean)


def _extract_refs_from_line(line: str) -> list[str]:
    return [match.group("ref") for match in REF_RE.finditer(line)]


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return []
    if completed.returncode != 0:
        return []
    return [line for line in completed.stdout.splitlines() if line.strip()]


def _git_head(repo_root: Path) -> str:
    lines = _git_lines(repo_root, ["rev-parse", "HEAD"])
    return lines[0] if lines else "unknown"


def _git_status_short(repo_root: Path) -> list[str]:
    return _git_lines(repo_root, ["status", "--short"])


def _git_recent_commits(repo_root: Path, limit: int = 10) -> list[dict[str, object]]:
    lines = _git_lines(
        repo_root,
        ["log", f"-n{limit}", "--date=iso-strict", "--pretty=format:%H%x09%ad%x09%s"],
    )
    commits: list[dict[str, object]] = []
    for line in lines:
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        commits.append({"hash": parts[0], "date": parts[1], "subject": parts[2]})
    return commits


def _git_recent_governance_paths(repo_root: Path, limit: int = 10) -> list[str]:
    lines = _git_lines(
        repo_root,
        [
            "log",
            f"-n{limit}",
            "--name-only",
            "--date=iso-strict",
            "--pretty=format:commit%x09%H%x09%ad%x09%s",
            "--",
            "docs/governance",
        ],
    )
    paths: set[str] = set()
    for line in lines:
        if line.startswith("commit\t"):
            continue
        if line.startswith("docs/governance/"):
            paths.add(line.strip())
    return sorted(paths)


def _today_from_args(value: str | None) -> dt.date:
    if value is None:
        return dt.date.today()
    return dt.date.fromisoformat(value)


def _file_number(path: Path) -> str | None:
    match = FR_PATH_RE.search(path.name)
    if match:
        return f"FR-{match.group(1)}"
    return None


def _parse_fr_file(path: Path, repo_root: Path) -> dict[str, object]:
    text = _read_text(path)
    lines = text.splitlines()
    fr_number = None
    title = None
    status = None
    for line in lines:
        if fr_number is None:
            match = FR_FILE_RE.match(line.strip().lstrip("#").strip())
            if match:
                fr_number = f"FR-{match.group(1)}"
                if "—" in line:
                    title = line.split("—", 1)[1].strip()
                elif "-" in line:
                    title = line.split("-", 1)[1].strip()
        if status is None:
            stripped = line.strip()
            if stripped.startswith("Status:"):
                status = stripped.split("Status:", 1)[1].strip()
            elif stripped.startswith("- **Status:**"):
                status = stripped.split("- **Status:**", 1)[1].strip()
    return {
        "path": str(path.relative_to(repo_root)),
        "fr_number": fr_number or _file_number(path),
        "title": title,
        "status": status,
        "lines": lines,
    }


def _parse_registry_location_audit(repo_root: Path, registry_path: Path) -> tuple[list[dict[str, object]], list[Finding]]:
    tables = _collect_table_rows(registry_path)
    rows: list[dict[str, object]] = []
    findings: list[Finding] = []
    for header, table_rows in tables:
        if "Registry status" not in header:
            continue
        seen: dict[str, list[TableRow]] = {}
        for row in table_rows:
            fr = _extract_fr_number(row.cells[0] if row.cells else "") or (row.cells[0] if row.cells else "")
            seen.setdefault(fr, []).append(row)
            if len(row.cells) < 6:
                findings.append(
                    Finding(
                        severity="FAIL",
                        category="registry_parse",
                        file=str(registry_path.relative_to(repo_root)),
                        line=row.line,
                        message=f"Registry row for {fr or 'unknown'} is missing expected columns.",
                        suggested_action="Repair the file-location audit table so each FR row has FR, title, status, location, lifecycle, and notes.",
                    )
                )
                continue
            fr = _extract_fr_number(row.cells[0]) or row.cells[0]
            status = _clean_status(row.cells[2]) if len(row.cells) > 2 else ""
            rows.append(
                {
                    "fr": fr,
                    "title": row.cells[1],
                    "status": status,
                    "location": row.cells[3],
                    "lifecycle": row.cells[4],
                    "notes": row.cells[5],
                    "line": row.line,
                }
            )
        for fr, dup_rows in seen.items():
            if fr and len(dup_rows) > 1:
                first = dup_rows[0]
                findings.append(
                    Finding(
                        severity="FAIL",
                        category="registry_duplicate",
                        file=str(registry_path.relative_to(repo_root)),
                        line=first.line,
                        message=f"Duplicate registry location-audit entries exist for {fr}.",
                        suggested_action="Deduplicate the registry file-location audit table so each FR appears once.",
                    )
                )
    return rows, findings


def _parse_duplicate_number_exceptions(registry_path: Path) -> set[str]:
    exceptions: set[str] = set()
    in_section = False
    for line in _read_text(registry_path).splitlines():
        stripped = line.strip()
        if stripped == "## FR Numbering Exceptions":
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if not in_section:
            continue
        for match in re.findall(r"FR-(\d{3})", stripped):
            exceptions.add(f"FR-{match}")
    return exceptions


def _parse_backlog_table(repo_root: Path, backlog_path: Path) -> list[dict[str, object]]:
    tables = _collect_table_rows(backlog_path)
    rows: list[dict[str, object]] = []
    for header, table_rows in tables:
        if "Rollback Reference" not in header:
            continue
        for row in table_rows:
            cells = row.cells
            if len(cells) < 8:
                rows.append({"fr": _extract_fr_number(cells[0] if cells else "") or (cells[0] if cells else ""), "line": row.line, "raw_cells": cells})
                continue
            rows.append(
                {
                    "fr": _extract_fr_number(cells[0]) or cells[0],
                    "phase": cells[1],
                    "status": cells[2],
                    "blast_radius": cells[3],
                    "dependencies": cells[4],
                    "observation_status": cells[5],
                    "current_state": cells[6],
                    "rollback_reference": cells[7],
                    "line": row.line,
                    "raw_cells": cells,
                }
            )
    return rows


def _build_audit(repo_root: Path, today: dt.date) -> dict[str, object]:
    findings: list[Finding] = []
    required_docs = [
        "docs/governance/caerus_investment_doctrine.md",
        "docs/governance/README.md",
        "docs/governance/CURRENT_RESEARCH_ROADMAP.md",
        "docs/governance/fr_registry.md",
        "docs/governance/fr_active_backlog.md",
        "AGENTS.md",
    ]
    for rel in required_docs:
        if not (repo_root / rel).exists():
            findings.append(
                Finding(
                    severity="FAIL",
                    category="missing_governance_source",
                    file=rel,
                    line=None,
                    message=f"Required governance source is missing: {rel}.",
                    suggested_action="Restore the missing governance source before relying on the hygiene audit.",
                )
            )
    docs_to_scan = [repo_root / rel for rel in GOVERNANCE_DOCS]
    docs_to_scan.extend(sorted((repo_root / FR_ACTIVE_DIR).glob("*.md")))
    docs_to_scan.extend(sorted((repo_root / FR_ARCHIVE_DIR).glob("*.md")))
    docs_to_scan = [path for path in docs_to_scan if path.exists()]
    files_scanned = sorted({str(path.relative_to(repo_root)) for path in docs_to_scan})

    registry_path = repo_root / "docs/governance/fr_registry.md"
    backlog_path = repo_root / "docs/governance/fr_active_backlog.md"
    registry_rows, registry_findings = _parse_registry_location_audit(repo_root, registry_path)
    findings.extend(registry_findings)
    backlog_rows = _parse_backlog_table(repo_root, backlog_path)
    backlog_by_fr = {row["fr"]: row for row in backlog_rows if row.get("fr")}
    registry_tables = _collect_table_rows(registry_path)
    duplicate_number_exceptions = _parse_duplicate_number_exceptions(registry_path)
    registry_all_numbers: set[str] = set()
    registry_open_numbers: set[str] = set()
    for header, table_rows in registry_tables:
        for row in table_rows:
            fr = _extract_fr_number(row.cells[0] if row.cells else "") or (row.cells[0] if row.cells else "")
            if not fr:
                continue
            registry_all_numbers.add(fr)
            if len(row.cells) > 2:
                status = _clean_status(str(row.cells[2]))
                if status in OPEN_REGISTRY_STATUSES:
                    registry_open_numbers.add(fr)

    # Doctrine references in canonical governance docs.
    for rel in [
        "docs/governance/README.md",
        "docs/governance/CURRENT_RESEARCH_ROADMAP.md",
        "docs/governance/fr_registry.md",
        "docs/governance/fr_active_backlog.md",
        "AGENTS.md",
    ]:
        path = repo_root / rel
        if not path.exists():
            continue
        text = _read_text(path)
        if DOCTRINE_REF not in text:
            findings.append(
                Finding(
                    severity="WARN",
                    category="doctrine_reference",
                    file=rel,
                    line=None,
                    message=f"{rel} does not reference {DOCTRINE_REF}.",
                    suggested_action="Add a short canonical-doctrine note or link to the doctrine in this document.",
                )
            )

    # FR files must be represented in the registry.
    fr_files = {}
    for path in sorted((repo_root / FR_ACTIVE_DIR).glob("*.md")) + sorted((repo_root / FR_ARCHIVE_DIR).glob("*.md")):
        parsed = _parse_fr_file(path, repo_root)
        fr_number = parsed["fr_number"]
        if fr_number:
            fr_files.setdefault(fr_number, []).append(parsed)
        else:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="fr_file_parse",
                    file=str(path.relative_to(repo_root)),
                    line=1,
                    message="Could not parse an FR number from the file header.",
                    suggested_action="Add a canonical FR heading such as '# FR-072 — ...' to the file.",
                )
            )

    for fr, items in fr_files.items():
        if len(items) > 1 and fr not in duplicate_number_exceptions:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="duplicate_fr_number",
                    file=", ".join(item["path"] for item in items),
                    line=1,
                    message=f"Duplicate FR file numbers detected for {fr}.",
                    suggested_action="Ensure each FR number appears in only one active/archive FR spec file.",
                )
            )

    registry_map = {row["fr"]: row for row in registry_rows}
    for fr, items in fr_files.items():
        if fr not in registry_all_numbers:
            first = items[0]
            findings.append(
                Finding(
                    severity="FAIL",
                    category="registry_coverage",
                    file=first["path"],
                    line=1,
                    message=f"{fr} appears in active/archive FR files but is missing from fr_registry.md.",
                    suggested_action="Add the FR to the registry file-location audit table and preserve the authoritative status record.",
                )
            )

    # Registry rows must point to existing files and stay consistent with backlog coverage.
    for row in registry_rows:
        fr = row["fr"]
        location = row["location"]
        path = _resolve_repo_relative_path(repo_root, location)
        status = str(row["status"]).strip()
        notes = str(row["notes"]).strip().lower()
        lifecycle = str(row["lifecycle"]).strip()
        if not path.exists():
            findings.append(
                Finding(
                    severity="FAIL",
                    category="registry_path",
                    file="docs/governance/fr_registry.md",
                    line=row["line"],
                    message=f"Registry location for {fr} points to a missing file: {location}.",
                    suggested_action="Update the registry path or restore the canonical FR file at the referenced location.",
                )
            )
        if status in OPEN_REGISTRY_STATUSES and fr not in backlog_by_fr:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="registry_backlog_gap",
                    file="docs/governance/fr_registry.md",
                    line=row["line"],
                    message=f"{fr} is marked {status} in the registry but is absent from the active backlog.",
                    suggested_action="Add the FR to fr_active_backlog.md or close/defer the registry state explicitly.",
                )
            )
        if location.startswith("docs/governance/fr_archive/") and status in OPEN_REGISTRY_STATUSES:
            navigational_note = "navigational only" in notes or "archived copy retained" in notes or "tracked in the backlog" in notes or "archived" in notes
            severity = "WARN" if navigational_note else "FAIL"
            findings.append(
                Finding(
                    severity=severity,
                    category="archive_registry_status",
                    file="docs/governance/fr_registry.md",
                    line=row["line"],
                    message=f"{fr} lives under fr_archive/ but the registry still marks it {status}.",
                    suggested_action="Keep the archival path as navigational only and ensure the registry note makes that explicit.",
                )
            )
        if location.startswith("docs/governance/fr_active/") and status in CLOSED_REGISTRY_STATUSES:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="active_registry_status",
                    file="docs/governance/fr_registry.md",
                    line=row["line"],
                    message=f"{fr} lives under fr_active/ but the registry marks it {status}.",
                    suggested_action="Align the registry status with the active file location or move the spec to the archive tree.",
                )
            )

    # Backlog hygiene.
    for row in backlog_rows:
        fr = row.get("fr", "")
        if not fr:
            continue
        status = _clean_status(str(row.get("status", "")))
        blast_radius = str(row.get("blast_radius", "")).strip()
        rollback = str(row.get("rollback_reference", "")).strip()
        current_state = str(row.get("current_state", "")).strip()
        phase = str(row.get("phase", "")).strip()
        text = " ".join(str(row.get(k, "")) for k in ("phase", "status", "blast_radius", "dependencies", "observation_status", "current_state", "rollback_reference")).lower()

        if not status:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="backlog_hygiene",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} is missing a backlog status.",
                    suggested_action="Populate the backlog status with a concrete lifecycle state.",
                )
            )
        if not blast_radius:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="backlog_hygiene",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} is missing a backlog blast radius.",
                    suggested_action="Populate the blast-radius column with LOW, MEDIUM, HIGH, or NONE as appropriate.",
                )
            )
        if not rollback:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="backlog_hygiene",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} is missing a rollback reference.",
                    suggested_action="Add a rollback reference that points to the reversible change or documentation link set.",
                )
            )
        if status == "IN_PROGRESS" and ("deployed" in current_state.lower() or "observing" in current_state.lower() or "deployed" in text):
            findings.append(
                Finding(
                    severity="WARN",
                    category="status_drift",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} is still marked IN_PROGRESS but the current-state text also implies deployed/observing status.",
                    suggested_action="Reconcile the backlog status with the evidence text or add an explicit status-review note.",
                )
            )
        if (status in {"PROPOSED", "READY"} or str(blast_radius).upper() == "HIGH") and any(term in text for term in EXECUTION_TERMS):
            if not any(term in text for term in WINDOW_TERMS):
                findings.append(
                    Finding(
                        severity="WARN",
                        category="execution_window",
                        file=str(backlog_path.relative_to(repo_root)),
                        line=row["line"],
                        message=f"{fr} touches execution-adjacent language without an explicit deployment-window constraint.",
                        suggested_action="Add a weekend/maintenance-window constraint or downgrade the FR's scope/blast radius.",
                    )
                )
        if any(term in text for term in DOCTRINE_CONFLICT_TERMS) and DOCTRINE_REF not in _read_text(backlog_path):
            findings.append(
                Finding(
                    severity="WARN",
                    category="doctrine_conflict",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} uses portfolio-objective language that may conflict with the doctrine without an explicit doctrine reference.",
                    suggested_action="Point the FR at the doctrine and clarify any exception to the default strategic objective.",
                )
            )

    # Backlog rows should be represented in the registry.
    for fr, row in backlog_by_fr.items():
        if fr not in registry_open_numbers:
            findings.append(
                Finding(
                    severity="FAIL",
                    category="backlog_registry_gap",
                    file=str(backlog_path.relative_to(repo_root)),
                    line=row["line"],
                    message=f"{fr} appears in the active backlog but is missing from the registry.",
                    suggested_action="Add the FR to fr_registry.md so lifecycle status remains authoritative.",
                )
            )

    # Broken or stale links inside governance markdown files.
    fr_locations = {fr: {str(item["path"]) for item in items} for fr, items in fr_files.items()}
    governance_docs = [
        path
        for path in sorted((repo_root / "docs/governance").rglob("*.md"))
        if path.is_file()
    ]
    governance_docs.extend([repo_root / "AGENTS.md", repo_root / "README.md"])
    for path in governance_docs:
        if not path.exists():
            continue
        text_lines = _read_text(path).splitlines()
        for line_no, line in enumerate(text_lines, start=1):
            for ref in _extract_refs_from_line(line):
                resolved = _normalize_repo_path(repo_root, ref, source=path)
                if resolved.exists():
                    continue
                fr_match = FR_PATH_RE.search(Path(ref).name)
                if not fr_match:
                    fr_match = FR_PATH_RE.search(ref)
                if fr_match:
                    fr = f"FR-{fr_match.group(1)}"
                    if fr in fr_locations:
                        findings.append(
                            Finding(
                                severity="WARN",
                                category="stale_reference",
                                file=str(path.relative_to(repo_root)),
                                line=line_no,
                                message=f"Reference to {ref} appears stale; {fr} exists at a different path.",
                                suggested_action="Update the link target to the current FR file location.",
                            )
                        )
                        continue
                findings.append(
                    Finding(
                        severity="FAIL",
                        category="broken_link",
                        file=str(path.relative_to(repo_root)),
                        line=line_no,
                        message=f"Broken markdown or path reference: {ref}",
                        suggested_action="Fix the link target or remove the obsolete reference.",
                    )
                )

    # Sort and assign stable identifiers.
    ordered = sorted(
        findings,
        key=lambda finding: (
            ROW_SEVERITY_ORDER.get(finding.severity, 99),
            finding.category,
            finding.file,
            finding.line if finding.line is not None else -1,
            finding.message,
        ),
    )
    numbered_findings = []
    for index, finding in enumerate(ordered, start=1):
        numbered_findings.append(
            {
                "id": f"GH-{index:03d}",
                "severity": finding.severity,
                "category": finding.category,
                "file": finding.file,
                "line": finding.line,
                "message": finding.message,
                "suggested_action": finding.suggested_action,
            }
        )

    counts = {"INFO": 0, "WARN": 0, "FAIL": 0}
    for finding in numbered_findings:
        counts[finding["severity"]] += 1
    status = "FAIL" if counts["FAIL"] else "WARN" if counts["WARN"] else "OK"
    generated_at = f"{today.isoformat()}T00:00:00Z"
    dirty_tree = bool(_git_status_short(repo_root))
    return {
        "generated_at": generated_at,
        "repo_head": _git_head(repo_root),
        "dirty_tree": dirty_tree,
        "files_scanned": files_scanned,
        "recent_commits": _git_recent_commits(repo_root),
        "recent_governance_paths": _git_recent_governance_paths(repo_root),
        "summary": {"INFO": counts["INFO"], "WARN": counts["WARN"], "FAIL": counts["FAIL"], "total": len(numbered_findings)},
        "findings": numbered_findings,
        "status": status,
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_markdown(path: Path, payload: dict[str, object]) -> None:
    lines = [
        "# Governance Hygiene Report",
        "",
        f"- Generated at: `{payload['generated_at']}`",
        f"- Repo head: `{payload['repo_head']}`",
        f"- Dirty tree: `{str(payload['dirty_tree']).lower()}`",
        f"- Status: `{payload['status']}`",
        "",
        "## Executive Summary",
        f"- FAIL: {payload['summary']['FAIL']}",
        f"- WARN: {payload['summary']['WARN']}",
        f"- INFO: {payload['summary']['INFO']}",
        "",
        "## Critical Findings",
    ]
    critical = [finding for finding in payload["findings"] if finding["severity"] == "FAIL"]
    if critical:
        for finding in critical:
            location = f"{finding['file']}:{finding['line']}" if finding["line"] is not None else finding["file"]
            lines.append(f"- `{finding['id']}` `{location}`: {finding['message']} Suggested action: {finding['suggested_action']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Warnings"])
    warnings = [finding for finding in payload["findings"] if finding["severity"] == "WARN"]
    if warnings:
        for finding in warnings:
            location = f"{finding['file']}:{finding['line']}" if finding["line"] is not None else finding["file"]
            lines.append(f"- `{finding['id']}` `{location}`: {finding['message']} Suggested action: {finding['suggested_action']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Informational Notes"])
    infos = [finding for finding in payload["findings"] if finding["severity"] == "INFO"]
    if infos:
        for finding in infos:
            location = f"{finding['file']}:{finding['line']}" if finding["line"] is not None else finding["file"]
            lines.append(f"- `{finding['id']}` `{location}`: {finding['message']}")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Suggested Next Actions",
        ]
    )
    if payload["findings"]:
        for finding in payload["findings"][:10]:
            lines.append(f"- `{finding['suggested_action']}`")
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Files Scanned",
        ]
    )
    for file_path in payload["files_scanned"]:
        lines.append(f"- `{file_path}`")
    lines.extend(["", "## Recent Commits Reviewed"])
    commits = payload["recent_commits"]
    if commits:
        for commit in commits:
            lines.append(f"- `{commit['hash'][:7]}` {commit['date']} {commit['subject']}")
    else:
        lines.append("- None.")
    lines.extend(["", "## Recent Governance Paths",])
    governance_paths = payload["recent_governance_paths"]
    if governance_paths:
        for governance_path in governance_paths:
            lines.append(f"- `{governance_path}`")
    else:
        lines.append("- None.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def run(argv: list[str] | None = None) -> tuple[int, dict[str, object], list[Path]]:
    args = parse_args(argv)
    repo_root = args.repo_root.resolve()
    report_date = _today_from_args(args.date)
    output_root = args.output_dir if args.output_dir.is_absolute() else repo_root / args.output_dir
    payload = _build_audit(repo_root, report_date)
    run_dir = output_root / report_date.isoformat()
    json_path = run_dir / "governance_hygiene.json"
    _write_json(json_path, payload)
    written = [json_path]
    if not args.json_only:
        md_path = run_dir / "governance_hygiene.md"
        _write_markdown(md_path, payload)
        written.append(md_path)
    print(json.dumps({"status": payload["status"], "output_dir": str(run_dir)}, indent=2, sort_keys=True))
    exit_code = 0
    if payload["status"] == "FAIL" and args.fail_on_fail:
        exit_code = 1
    elif payload["status"] == "WARN" and args.fail_on_warn:
        exit_code = 1
    return exit_code, payload, written


def main(argv: list[str] | None = None) -> int:
    exit_code, _, _ = run(argv)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
