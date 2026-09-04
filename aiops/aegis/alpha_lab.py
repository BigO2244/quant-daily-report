"""Deterministic Alpha Lab operational-state import from a pinned GitHub PR."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from typing import Any, Protocol

from .domain import stable_id
from .importers import ImportResult, RepositoryStateImporter
from .store import AegisStore


ALPHA_LAB_FILES = (
    "CURRENT_STATE.md",
    "STRATEGY_BACKLOG.md",
    "EXPERIMENT_LEDGER.md",
    "DECISION_LOG.md",
)


class AlphaLabAdapter(Protocol):
    def fetch_snapshot(self) -> dict[str, Any]: ...


class FixtureAlphaLabAdapter:
    def __init__(self, snapshot: dict[str, Any]) -> None:
        self.snapshot = snapshot

    def fetch_snapshot(self) -> dict[str, Any]:
        return json.loads(json.dumps(self.snapshot))


class GitHubAlphaLabAdapter:
    """Read PR metadata and governance files at the immutable PR head SHA."""

    def __init__(self, repository: str, pr_number: int = 160) -> None:
        self.repository = repository
        self.pr_number = pr_number

    @staticmethod
    def _run(arguments: list[str]) -> str:
        result = subprocess.run(arguments, capture_output=True, text=True, check=False)
        if result.returncode:
            raise RuntimeError(f"Alpha Lab GitHub import failed: {result.stderr.strip()}")
        return result.stdout

    def fetch_snapshot(self) -> dict[str, Any]:
        metadata = json.loads(self._run([
            "gh", "pr", "view", str(self.pr_number), "--repo", self.repository,
            "--json", "number,title,state,isDraft,url,headRefName,headRefOid,updatedAt",
        ]))
        revision = metadata["headRefOid"]
        documents = {}
        for name in ALPHA_LAB_FILES:
            documents[name] = self._run([
                "gh", "api", "-H", "Accept: application/vnd.github.raw+json",
                f"repos/{self.repository}/contents/projects/alpha_lab/{name}?ref={revision}",
            ])
        return {"pr": metadata, "documents": documents}


@dataclass(frozen=True)
class AlphaLabImportResult:
    import_result: ImportResult
    portfolio: list[dict[str, Any]]
    blockers: list[dict[str, Any]]
    decision_candidates: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "import": self.import_result.as_dict(),
            "portfolio": self.portfolio,
            "blockers": self.blockers,
            "decision_candidates": self.decision_candidates,
        }


class AlphaLabImporter:
    """Import only explicit Alpha Lab lifecycle records; never infer promotion."""

    def __init__(self, store: AegisStore, repository: str = "BigO2244/quant-daily-report") -> None:
        self.store = store
        self.repository = repository

    @staticmethod
    def _table(document: str, required: tuple[str, ...]) -> list[dict[str, str]]:
        lines = document.splitlines()
        for index, line in enumerate(lines):
            if not line.startswith("|"):
                continue
            headers = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if not set(required).issubset(headers) or index + 1 >= len(lines):
                continue
            rows = []
            for candidate in lines[index + 2:]:
                if not candidate.startswith("|"):
                    break
                cells = [cell.strip() for cell in candidate.strip().strip("|").split("|")]
                if len(cells) == len(headers):
                    rows.append(dict(zip(headers, cells)))
            return rows
        raise ValueError(f"Alpha Lab table not found: {required}")

    @staticmethod
    def _clean(value: str) -> str:
        return value.replace("`", "").strip()

    def import_state(self, adapter: AlphaLabAdapter, as_of: str, dry_run: bool = False) -> AlphaLabImportResult:
        snapshot = adapter.fetch_snapshot()
        pr = snapshot["pr"]
        documents = snapshot["documents"]
        missing = sorted(set(ALPHA_LAB_FILES) - set(documents))
        if missing:
            raise ValueError(f"Missing Alpha Lab source documents: {', '.join(missing)}")
        revision = str(pr["headRefOid"])
        source_url = str(pr["url"])
        source_id = stable_id("source", {"type": "alpha_lab_pr", "repository": self.repository, "pr": str(pr["number"])})
        snapshot_id = stable_id("snapshot", {"source": source_id, "payload": snapshot})
        initiative_id = RepositoryStateImporter._taxonomy_id("INITIATIVE", "Alpha Lab")
        pr_entity_id = stable_id("entity", {"source": "github", "repository": self.repository, "type": "PR", "id": str(pr["number"])})

        backlog = self._table(documents["STRATEGY_BACKLOG.md"], ("Priority", "Idea family", "Research state", "Immediate constraint"))
        ledger = self._table(documents["EXPERIMENT_LEDGER.md"], ("Experiment", "Hypothesis", "State", "Evidence packet", "Verdict"))
        owner_decisions = self._table(documents["DECISION_LOG.md"], ("Date", "Object", "Decision", "Evidence", "Rationale"))
        source_reported_match = re.search(r"As of (\d{4}-\d{2}-\d{2})", documents["CURRENT_STATE.md"])
        source_reported_as_of = source_reported_match.group(1) if source_reported_match else None

        entities: list[dict[str, Any]] = []
        hierarchy: list[tuple[str, str]] = []
        relationships: list[tuple[str, str, str, dict[str, Any], str | None]] = []
        provenance: list[dict[str, Any]] = []
        portfolio: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []

        existing_initiative = self.store.entity(initiative_id)
        initiative_metadata = {"source_pr": int(pr["number"]), "source_commit": revision, "source_reported_as_of": source_reported_as_of}
        if existing_initiative:
            initiative_metadata = {**existing_initiative["metadata"], **initiative_metadata}
        entities.append({
            "id": initiative_id, "entity_type": "INITIATIVE", "name": "Alpha Lab",
            "status": "SOURCE_REPORTED_ACTIVE_RESEARCH", "origin": "NATIVE",
            "source_record_id": "taxonomy:alpha_lab",
            "metadata": initiative_metadata,
        })
        existing_pr = self.store.entity(pr_entity_id)
        pr_metadata = {"repository": self.repository, "record_type": "PR", "external_id": str(pr["number"]), "branch": pr.get("headRefName"), "commit": revision, "updated_at": pr.get("updatedAt"), "alpha_lab_scope": True}
        if existing_pr:
            pr_metadata = {**existing_pr["metadata"], **pr_metadata}
        entities.append({
            "id": pr_entity_id, "entity_type": "EXTERNAL_RECORD", "name": str(pr["title"]),
            "status": "DRAFT" if pr.get("isDraft") else str(pr["state"]), "origin": "IMPORTED",
            "source_record_id": f"github:{self.repository}:PR:{pr['number']}",
            "metadata": pr_metadata,
        })
        relationships.append((initiative_id, pr_entity_id, "TRACKED_BY_PR", {"evidence": source_url, "source_commit": revision}, None))

        mission_by_hypothesis: dict[str, str] = {}
        for row in backlog:
            family = self._clean(row["Idea family"])
            priority = self._clean(row["Priority"])
            status = self._clean(row["Research state"])
            constraint = self._clean(row["Immediate constraint"])
            hypothesis = f"HYP-2026-{int(priority):03d}" if priority.isdigit() else None
            entity_id = stable_id("entity", {"source": "alpha_lab_backlog", "family": family})
            metadata = {
                "alpha_lab_record_type": "RESEARCH_FAMILY", "priority": priority,
                "initial_class": self._clean(row.get("Initial class", "")),
                "mechanism_question": self._clean(row.get("Mechanism question", "")),
                "next_action": constraint, "hypothesis_id": hypothesis,
                "source_path": "projects/alpha_lab/STRATEGY_BACKLOG.md", "source_commit": revision, "source_reported_as_of": source_reported_as_of,
            }
            entities.append({"id": entity_id, "entity_type": "MISSION", "name": family, "status": status, "origin": "IMPORTED", "source_record_id": f"alpha_lab:backlog:{family}", "metadata": metadata})
            hierarchy.append((initiative_id, entity_id))
            if hypothesis:
                mission_by_hypothesis[hypothesis] = entity_id
            portfolio.append({"id": entity_id, "family": family, "priority": priority, "status": status, "next_action": constraint, "hypothesis_id": hypothesis, "source": "projects/alpha_lab/STRATEGY_BACKLOG.md", "source_commit": revision, "source_reported_as_of": source_reported_as_of})
            provenance.append({"entity_id": entity_id, "source_url": source_url, "source_path": metadata["source_path"], "source_commit": revision, "source_row_key": family})
            if "BLOCKED" in status:
                blocker_id = stable_id("entity", {"source": "alpha_lab_constraint", "constraint": constraint})
                entities.append({"id": blocker_id, "entity_type": "DATASET", "name": constraint, "status": "REQUIRED_EVIDENCE_MISSING", "origin": "IMPORTED", "source_record_id": f"alpha_lab:constraint:{entity_id}", "metadata": {"alpha_lab_record_type": "BLOCKER", "source_path": metadata["source_path"], "source_commit": revision}})
                relationships.append((entity_id, blocker_id, "BLOCKED_BY", {"evidence": f"Explicit {status} row constraint", "source_path": metadata["source_path"], "source_commit": revision}, None))
                blockers.append({"mission_id": entity_id, "family": family, "status": status, "blocker_id": blocker_id, "blocker": constraint, "source": metadata["source_path"], "source_commit": revision})

        for row in ledger:
            experiment = self._clean(row["Experiment"])
            hypothesis = self._clean(row["Hypothesis"])
            state = self._clean(row["State"])
            verdict = self._clean(row["Verdict"])
            evidence_packet = self._clean(row["Evidence packet"])
            entity_id = stable_id("entity", {"source": "alpha_lab_experiment", "id": experiment})
            metadata = {"alpha_lab_record_type": "EXPERIMENT", "hypothesis_id": hypothesis, "classification": self._clean(row.get("Primary classification", "")), "verdict": verdict, "evidence_packet": evidence_packet, "source_path": "projects/alpha_lab/EXPERIMENT_LEDGER.md", "source_commit": revision}
            entities.append({"id": entity_id, "entity_type": "TASK", "name": experiment, "status": state, "origin": "IMPORTED", "source_record_id": f"alpha_lab:experiment:{experiment}", "metadata": metadata})
            parent = mission_by_hypothesis.get(hypothesis)
            if parent:
                hierarchy.append((parent, entity_id))
                relationships.append((entity_id, parent, "VALIDATES", {"evidence": f"Explicit ledger hypothesis {hypothesis}", "source_path": metadata["source_path"], "source_commit": revision}, None))
            provenance.append({"entity_id": entity_id, "source_url": source_url, "source_path": metadata["source_path"], "source_commit": revision, "source_row_key": experiment})
            if state == "REVIEW" and verdict.startswith("PARK"):
                decisions.append({
                    "decision_type": "PARK_RESEARCH", "question": f"Accept the frozen {experiment} PARK verdict?",
                    "recommended_action": "Accept PARK for this experiment only; do not retire or reweight production strategy behavior",
                    "alternatives": ["Request further evidence", "Authorize a new hypothesis revision"],
                    "evidence_links": [f"projects/alpha_lab/{evidence_packet}"], "confidence": "HIGH",
                    "risk_if_delayed": "The evaluated experiment remains in REVIEW and Alpha Lab ownership is ambiguous",
                    "risk_if_approved": "The experiment is parked; no production or capital behavior changes",
                    "alpha_lab": {"experiment_id": experiment, "hypothesis_id": hypothesis, "verdict": verdict, "source_commit": revision},
                })

        for row in owner_decisions:
            object_name = self._clean(row["Object"])
            decision = self._clean(row["Decision"])
            entity_id = stable_id("entity", {"source": "alpha_lab_decision_log", "date": row["Date"], "object": object_name, "decision": decision})
            entities.append({"id": entity_id, "entity_type": "DECISION", "name": f"{object_name}: {decision}", "status": "RECORDED_OWNER_DECISION", "origin": "IMPORTED", "source_record_id": f"alpha_lab:decision:{row['Date']}:{object_name}", "metadata": {"alpha_lab_record_type": "OWNER_DECISION", "date": row["Date"], "evidence": self._clean(row["Evidence"]), "rationale": self._clean(row["Rationale"]), "next_permitted_state": self._clean(row.get("Next permitted state", "")), "source_path": "projects/alpha_lab/DECISION_LOG.md", "source_commit": revision}})
            hierarchy.append((initiative_id, entity_id))
            provenance.append({"entity_id": entity_id, "source_url": source_url, "source_path": "projects/alpha_lab/DECISION_LOG.md", "source_commit": revision, "source_row_key": f"{row['Date']}:{object_name}"})

        import_result = ImportResult(source_id, snapshot_id, len(entities), len(entities), [], provenance, [item["id"] for item in entities], dry_run)
        if dry_run:
            return AlphaLabImportResult(import_result, portfolio, blockers, decisions)

        self.store.record_import_source({"id": source_id, "source_type": "GITHUB", "source_uri": source_url, "authoritative_scope": "Alpha Lab research planning and owner-review state only", "metadata": {"repository": self.repository, "pr": int(pr["number"]), "head_commit": revision, "source_files": list(ALPHA_LAB_FILES)}}, as_of)
        snapshot_id = self.store.record_snapshot(source_id, snapshot, as_of)
        changed = 0
        with self.store.transaction() as conn:
            for entity in entities:
                changed += int(self.store.upsert_entity(entity, as_of, conn))
        for parent, child in hierarchy:
            self.store.add_hierarchy(parent, child, {"evidence": "explicit Alpha Lab registry membership", "source_url": source_url, "source_commit": revision}, as_of)
        for source, target, kind, evidence, rule in relationships:
            self.store.add_relationship(source, target, kind, evidence, "HIGH", as_of, rule)
        self.store.save_stale_status(source_id, "CURRENT", f"pinned Alpha Lab PR snapshot {revision[:12]} captured", as_of)
        return AlphaLabImportResult(ImportResult(source_id, snapshot_id, len(entities), changed, [], provenance, [item["id"] for item in entities], False), portfolio, blockers, decisions)
