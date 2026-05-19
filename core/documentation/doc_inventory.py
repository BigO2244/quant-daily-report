from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DOC_EXTENSIONS = {".md", ".MD", ".rst"}
REFERENCE_RE = re.compile(r"(?P<path>(?:AGENTS\.md|README\.md|docs/[^\s),;:]+|scripts/[^\s),;:]+|core/[^\s),;:]+|Tests/[^\s),;:]+))")
FRONT_MATTER_RE = re.compile(r"\A---\n(?P<body>.*?)\n---\n", re.DOTALL)


@dataclass(frozen=True)
class DocRecord:
    path: str
    title: str | None
    category: str | None
    owner: str | None
    last_reviewed: str | None
    criticality: str | None
    canonical: bool | None
    related_systems: list[str] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    has_metadata: bool = False

    def to_artifact(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "category": self.category,
            "owner": self.owner,
            "last_reviewed": self.last_reviewed,
            "criticality": self.criticality,
            "canonical": self.canonical,
            "related_systems": self.related_systems,
            "references": self.references,
            "has_metadata": self.has_metadata,
        }


def _parse_metadata(text: str) -> tuple[dict[str, Any], str]:
    match = FRONT_MATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for raw_line in match.group("body").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            items = [item.strip().strip('"').strip("'") for item in value[1:-1].split(",") if item.strip()]
            meta[key.strip()] = items
        elif value.lower() in {"true", "false"}:
            meta[key.strip()] = value.lower() == "true"
        else:
            meta[key.strip()] = value.strip('"').strip("'")
    return meta, text[match.end() :]


def _title_from_body(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _normalize_ref(ref: str) -> str:
    clean = ref.strip().strip("`\"'")
    for marker in ("]", "[", "("):
        if marker in clean:
            clean = clean.split(marker, 1)[0]
    return clean.rstrip(").,;:`\"'")


def _extract_references(body: str) -> list[str]:
    refs = {_normalize_ref(match.group("path")) for match in REFERENCE_RE.finditer(body)}
    return sorted(ref for ref in refs if ref)


def discover_documentation(repo_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for root_name in ("AGENTS.md", "README.md"):
        root_path = repo_root / root_name
        if root_path.exists():
            candidates.append(root_path)
    docs_dir = repo_root / "docs"
    if docs_dir.exists():
        for path in docs_dir.rglob("*"):
            if "__pycache__" in path.parts or path.name == ".DS_Store":
                continue
            if path.is_file() and path.suffix in DOC_EXTENSIONS:
                candidates.append(path)
    return sorted(candidates, key=lambda item: str(item.relative_to(repo_root)))


def build_inventory(repo_root: Path) -> dict[str, Any]:
    records: list[DocRecord] = []
    for path in discover_documentation(repo_root):
        rel = str(path.relative_to(repo_root))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            text = ""
        meta, body = _parse_metadata(text)
        records.append(
            DocRecord(
                path=rel,
                title=_title_from_body(body),
                category=meta.get("category"),
                owner=meta.get("owner"),
                last_reviewed=meta.get("last_reviewed"),
                criticality=meta.get("criticality"),
                canonical=meta.get("canonical"),
                related_systems=list(meta.get("related_systems") or []),
                references=_extract_references(body),
                has_metadata=bool(meta),
            )
        )
    return {
        "doc_count": len(records),
        "records": [record.to_artifact() for record in records],
    }
