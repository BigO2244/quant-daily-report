"""Read-only GitHub and repository-state import adapters."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .domain import canonical_json, stable_id
from .store import AegisStore


class GitHubAdapter(Protocol):
    def fetch_open_records(self) -> list[dict[str, Any]]: ...


class FixtureGitHubAdapter:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records

    def fetch_open_records(self) -> list[dict[str, Any]]:
        return json.loads(json.dumps(self.records))


class GitHubCLIAdapter:
    """Optional read-only adapter using the operator's authenticated `gh` session."""

    FIELDS = "number,title,state,isDraft,headRefName,headRefOid,baseRefName,labels,assignees,createdAt,updatedAt,url,body"

    def __init__(self, repository: str) -> None:
        self.repository = repository

    def _run(self, kind: str, fields: str) -> list[dict[str, Any]]:
        result = subprocess.run(
            ["gh", kind, "list", "--repo", self.repository, "--state", "open", "--limit", "1000", "--json", fields],
            capture_output=True, text=True, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"GitHub import failed: {result.stderr.strip()}")
        return json.loads(result.stdout)

    def fetch_open_records(self) -> list[dict[str, Any]]:
        prs = self._run("pr", self.FIELDS)
        issues = self._run("issue", "number,title,state,labels,assignees,createdAt,updatedAt,url,body")
        return sorted(
            [{**item, "record_type": "PR"} for item in prs] + [{**item, "record_type": "ISSUE", "isDraft": False} for item in issues],
            key=lambda item: (item["record_type"], int(item["number"])),
        )


@dataclass(frozen=True)
class ImportResult:
    source_id: str
    snapshot_id: str
    records_seen: int
    records_changed: int
    unresolved: list[dict[str, Any]]
    provenance: list[dict[str, Any]]
    entities: list[str]
    dry_run: bool

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "snapshot_id": self.snapshot_id, "records_seen": self.records_seen,
                "records_changed": self.records_changed, "unresolved": self.unresolved, "provenance": self.provenance,
                "entities": self.entities, "dry_run": self.dry_run}


class GitHubImporter:
    _LINK_PATTERN = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?|related\s+to)\s+#(\d+)\b", re.IGNORECASE)

    def __init__(self, store: AegisStore, repository: str) -> None:
        self.store = store
        self.repository = repository

    def import_records(self, adapter: GitHubAdapter, as_of: str, dry_run: bool = False) -> ImportResult:
        records = adapter.fetch_open_records()
        source_id = stable_id("source", {"type": "github", "uri": self.repository})
        snapshot_id = stable_id("snapshot", {"source": source_id, "payload": records})
        entities, references, relations, provenance = [], [], [], []
        for record in records:
            record_type = str(record["record_type"]).upper()
            number = str(record["number"])
            entity_id = stable_id("entity", {"source": "github", "repository": self.repository, "type": record_type, "id": number})
            state = "DRAFT" if record_type == "PR" and record.get("isDraft") else "READY" if record_type == "PR" else "OPEN"
            source_metadata = {
                "repository": self.repository, "external_id": number, "record_type": record_type,
                "labels": sorted(label.get("name", "") for label in record.get("labels", [])),
                "assignees": sorted(assignee.get("login", "") for assignee in record.get("assignees", [])),
                "created_at": record.get("createdAt"), "updated_at": record.get("updatedAt"),
                "draft": bool(record.get("isDraft")), "branch": record.get("headRefName"),
                "commit": record.get("headRefOid"), "base_branch": record.get("baseRefName"),
            }
            existing = self.store.entity(entity_id)
            entity_metadata = {**existing["metadata"], **source_metadata} if existing else source_metadata
            entities.append({"id": entity_id, "entity_type": "EXTERNAL_RECORD", "name": str(record["title"]), "status": state,
                             "origin": "IMPORTED", "source_record_id": f"github:{self.repository}:{record_type}:{number}", "metadata": entity_metadata})
            references.append({"id": stable_id("external", {"source": source_id, "type": record_type, "id": number}), "entity_id": entity_id,
                               "source_id": source_id, "external_type": record_type, "external_id": number, "url": record.get("url"), "state": state, "metadata": source_metadata})
            provenance.append({"entity_id": entity_id, "source_id": source_id, "source_url": record.get("url"), "fields": sorted(source_metadata)})
            for linked_number in sorted(set(self._LINK_PATTERN.findall(record.get("body") or "")), key=int):
                target_id = stable_id("entity", {"source": "github", "repository": self.repository, "type": "ISSUE", "id": linked_number})
                relations.append((entity_id, target_id, "TRACKED_BY_ISSUE", {"evidence": f"explicit body reference to #{linked_number}", "source_url": record.get("url")}, "HIGH", "github_explicit_link_v1"))
        if dry_run:
            return ImportResult(source_id, snapshot_id, len(records), len(records), [], provenance, [item["id"] for item in entities], True)
        self.store.record_import_source({"id": source_id, "source_type": "GITHUB", "source_uri": f"https://github.com/{self.repository}", "authoritative_scope": "planning metadata only", "metadata": {"repository": self.repository}}, as_of)
        snapshot_id = self.store.record_snapshot(source_id, records, as_of)
        changed = 0
        with self.store.transaction() as conn:
            for entity, reference in zip(entities, references):
                entity_changed = self.store.upsert_entity(entity, as_of, conn)
                reference_changed = self.store.upsert_external_reference(reference, as_of, conn)
                changed += int(entity_changed or reference_changed)
        known = {item["id"] for item in entities} | {item["id"] for item in self.store.entities()}
        for source, target, kind, evidence, certainty, rule in relations:
            if target in known:
                self.store.add_relationship(source, target, kind, evidence, certainty, as_of, rule)
        self.store.save_stale_status(source_id, "CURRENT", "snapshot captured during import", as_of)
        return ImportResult(source_id, snapshot_id, len(records), changed, [], provenance, [item["id"] for item in entities], False)


class RepositoryStateImporter:
    """Evidence-preserving importer for taxonomy, strategy registry, and FR registry."""

    INITIATIVES = {
        "Core Platform": ["aiops", "core"], "Alpha Lab": ["research/alpha_lab_v1", "research/alpha_lab_v2"],
        "Atlas": ["projects/atlas", "atlas"], "Orion": ["config/research/strategy_registry.json"],
        "Lyra": ["config/research/strategy_registry.json"], "Polaris": ["config/research/strategy_registry.json"],
        "Cassiopeia": ["config/research/strategy_registry.json"], "Data and PIT": ["data/pit_universe", "research/pit_universe.py"],
        "Risk and Governance": ["docs/governance"], "Execution Infrastructure": ["execution"],
        "Dashboard and Operations": ["web/dashboard", "docs/quant_dashboard.md"],
    }
    STRATEGY_PARENT = {"caerus_orion": "Orion", "caerus_orion_alpha": "Orion", "caerus_lyra": "Lyra",
                       "caerus_polaris": "Polaris", "caerus_polaris_alpha": "Polaris", "caerus_cassiopeia": "Cassiopeia"}

    def __init__(self, store: AegisStore, repo_root: Path) -> None:
        self.store = store; self.repo_root = Path(repo_root)

    def import_state(self, as_of: str, dry_run: bool = False) -> ImportResult:
        registry_path = self.repo_root / "config/research/strategy_registry.json"
        fr_path = self.repo_root / "docs/governance/fr_registry.md"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        fr_text = fr_path.read_text(encoding="utf-8")
        payload = {"strategy_registry": registry, "fr_registry_sha256": stable_id("sha", fr_text), "repo_head": self._head()}
        source_id = stable_id("source", {"type": "repository", "uri": str(self.repo_root.resolve())})
        snapshot_id = stable_id("snapshot", {"source": source_id, "payload": payload})
        entities, hierarchy, provenance, unresolved = self._taxonomy(as_of, registry)
        for strategy in registry.get("strategies", []):
            strategy_id = str(strategy["strategy_id"])
            entity_id = stable_id("entity", {"source": "strategy_registry", "id": strategy_id})
            entities.append({"id": entity_id, "entity_type": "INITIATIVE", "name": strategy.get("display_name", strategy_id),
                             "status": f"SOURCE_REPORTED_{str(strategy.get('status', 'unresolved')).upper()}", "origin": "IMPORTED",
                             "source_record_id": f"strategy_registry:{strategy_id}", "metadata": strategy})
            parent_name = self.STRATEGY_PARENT.get(strategy_id, "Alpha Lab")
            hierarchy.append((self._taxonomy_id("INITIATIVE", parent_name), entity_id, {"evidence": str(registry_path.relative_to(self.repo_root)), "rule": "strategy_registry_mapping_v1"}))
            provenance.append({"entity_id": entity_id, "source": str(registry_path.relative_to(self.repo_root)), "source_record_id": strategy_id})
        for line in fr_text.splitlines():
            if not line.startswith("| FR-"):
                continue
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) < 4:
                continue
            fr_id, title, status, path = cells[:4]
            entity_id = stable_id("entity", {"source": "fr_registry", "id": fr_id})
            entities.append({"id": entity_id, "entity_type": "MISSION", "name": f"{fr_id}: {title}", "status": status.strip("`"), "origin": "IMPORTED",
                             "source_record_id": f"fr_registry:{fr_id}", "metadata": {"fr_id": fr_id, "source_path": path.strip("`"), "raw_status": status.strip("`")}})
            hierarchy.append((self._taxonomy_id("INITIATIVE", "Risk and Governance"), entity_id, {"evidence": str(fr_path.relative_to(self.repo_root)), "rule": "fr_registry_explicit_row_v1"}))
            provenance.append({"entity_id": entity_id, "source": str(fr_path.relative_to(self.repo_root)), "source_record_id": fr_id})
        for entity in entities:
            if entity["status"] == "STATUS_UNRESOLVED":
                unresolved.append({"entity_id": entity["id"], "name": entity["name"], "reason": "No current repository evidence found at configured evidence paths", "review_required": True})
        if dry_run:
            return ImportResult(source_id, snapshot_id, len(entities), len(entities), unresolved, provenance, [item["id"] for item in entities], True)
        self.store.record_import_source({"id": source_id, "source_type": "REPOSITORY", "source_uri": str(self.repo_root.resolve()), "authoritative_scope": "repository governance and research metadata", "metadata": {"head": payload["repo_head"]}}, as_of)
        snapshot_id = self.store.record_snapshot(source_id, payload, as_of)
        changed = 0
        with self.store.transaction() as conn:
            for entity in entities: changed += int(self.store.upsert_entity(entity, as_of, conn))
        for parent, child, evidence in hierarchy:
            self.store.add_hierarchy(parent, child, evidence, as_of)
        self.store.save_stale_status(source_id, "CURRENT", "local repository snapshot captured", as_of)
        return ImportResult(source_id, snapshot_id, len(entities), changed, unresolved, provenance, [item["id"] for item in entities], False)

    def _taxonomy(self, as_of: str, registry: dict[str, Any]) -> tuple[list[dict[str, Any]], list[tuple[str, str, dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
        entities = [
            {"id": self._taxonomy_id("DOMAIN", "Aegis"), "entity_type": "DOMAIN", "name": "Aegis", "status": "ACTIVE_REGISTRY", "origin": "NATIVE", "source_record_id": "taxonomy:aegis", "metadata": {}},
            {"id": self._taxonomy_id("PROGRAM", "Caerus"), "entity_type": "PROGRAM", "name": "Caerus", "status": "SOURCE_REPORTED_ACTIVE", "origin": "NATIVE", "source_record_id": "taxonomy:caerus", "metadata": {"evidence": "AGENTS.md"}},
        ]
        hierarchy = [(entities[0]["id"], entities[1]["id"], {"evidence": "AEG-002 taxonomy", "rule": "explicit_taxonomy_v1"})]
        provenance, unresolved = [], []
        registry_text = canonical_json(registry).casefold()
        for name, paths in self.INITIATIVES.items():
            evidence = [path for path in paths if (self.repo_root / path).exists()]
            mentioned = name.casefold().replace(" ", "_") in registry_text or name.casefold() in registry_text
            status = "SOURCE_EVIDENCE_PRESENT" if evidence or mentioned else "STATUS_UNRESOLVED"
            entity = {"id": self._taxonomy_id("INITIATIVE", name), "entity_type": "INITIATIVE", "name": name, "status": status, "origin": "NATIVE", "source_record_id": f"taxonomy:{name.casefold().replace(' ', '_')}", "metadata": {"evidence_paths": evidence, "registry_mention": mentioned}}
            existing = self.store.entity(entity["id"])
            if existing and existing["status"] == "SOURCE_REPORTED_ACTIVE_RESEARCH":
                entity["status"] = existing["status"]
                entity["metadata"] = {**entity["metadata"], **existing["metadata"]}
            entities.append(entity); hierarchy.append((entities[1]["id"], entity["id"], {"evidence": evidence or ["configured taxonomy target"], "rule": "explicit_taxonomy_v1"}))
            provenance.append({"entity_id": entity["id"], "evidence_paths": evidence})
        return entities, hierarchy, provenance, unresolved

    @staticmethod
    def _taxonomy_id(kind: str, name: str) -> str: return stable_id("entity", {"taxonomy": "aegis_caerus_v1", "type": kind, "name": name})

    def _head(self) -> str:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_root, capture_output=True, text=True, check=False)
        return result.stdout.strip() if result.returncode == 0 else "UNKNOWN"
