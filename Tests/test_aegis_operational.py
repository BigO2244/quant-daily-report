from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from aiops.aegis.alpha_lab import AlphaLabImporter, FixtureAlphaLabAdapter
from aiops.aegis.api import handler
from aiops.aegis.brief import ExecutiveBriefGenerator
from aiops.aegis.domain import stable_id
from aiops.aegis.importers import FixtureGitHubAdapter, GitHubImporter, RepositoryStateImporter
from aiops.aegis.priority import PriorityEngine
from aiops.aegis.reconciliation import ReconciliationEngine
from aiops.aegis.operations import Operationalizer
from aiops.aegis.service import AIOPSRunnerAdapter, AegisService
from aiops.aegis.store import MIGRATIONS, AegisStore
from aiops.cli import main
from scripts.validate_aegis_boundaries import scan

AS_OF = "2026-08-02T12:00:00+00:00"


def make_service(tmp_path: Path) -> AegisService:
    return AegisService(AegisStore(tmp_path / "aegis.sqlite"))


def entity(entity_id: str, name: str, kind: str = "MISSION", status: str = "ACTIVE") -> dict:
    return {"id": entity_id, "entity_type": kind, "name": name, "status": status, "origin": "NATIVE", "source_record_id": entity_id, "metadata": {}}


def alpha_lab_snapshot() -> dict:
    return {
        "pr": {"number": 160, "title": "Add Alpha Lab CIO control plane", "state": "OPEN", "isDraft": True, "url": "https://github.test/pull/160", "headRefName": "project/alpha-lab", "headRefOid": "abc123", "updatedAt": AS_OF},
        "documents": {
            "CURRENT_STATE.md": "# State\n\nAs of 2026-07-24 UTC, evidence is frozen.\n",
            "STRATEGY_BACKLOG.md": "| Priority | Idea family | Research state | Initial class | Mechanism question | Immediate constraint |\n|---:|---|---|---|---|---|\n| 1 | Current Caerus decomposition | `FROZEN_BLOCKED_DATA` | `UNPROVEN` | Is there residual edge? | Certified decision tape |\n| 2 | Residual momentum | `FROZEN_EVALUATED_REVIEW` | `UNPROVEN` | Is residual momentum useful? | Review EXP-2 park verdict |\n| 3 | Short reversal | `FROZEN_EVALUATED_REVIEW` | `UNPROVEN` | Is reversal useful? | Review EXP-3 park verdict |\n",
            "EXPERIMENT_LEDGER.md": "| Experiment | Hypothesis | Frozen date | State | Primary classification | Evidence packet | Verdict |\n|---|---|---|---|---|---|---|\n| EXP-1 | HYP-2026-001 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-1.md` | `ITERATE` — `BLOCKED_DATA` |\n| EXP-2 | HYP-2026-002 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-2.md` | `PARK` — `NON_POSITIVE_VALIDATION` |\n| EXP-3 | HYP-2026-003 | 2026-07-23 | `REVIEW` | `UNPROVEN` | `evidence/EXP-3.md` | `PARK` — `COST_FAILURE` |\n",
            "DECISION_LOG.md": "| Date | Object | Decision | Evidence | Rationale | Next permitted state |\n|---|---|---|---|---|---|\n| 2026-07-14 | Alpha Lab project | `PURSUE` | Audit | Establish falsification process | Experiment registration |\n",
        },
    }


def test_forward_migration_upgrades_v1_and_enforces_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    connection = sqlite3.connect(path); connection.executescript(MIGRATIONS[0]); connection.execute("INSERT INTO schema_migrations VALUES (1)"); connection.commit(); connection.close()
    store = AegisStore(path)
    assert store.schema_version() == 2
    with store._connection() as checked:  # contract inspection
        assert checked.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            checked.execute("INSERT INTO hierarchy_links VALUES ('missing', 'also_missing', '{}', ?)", (AS_OF,))


def test_hierarchy_and_relationship_cycles_are_rejected(tmp_path: Path) -> None:
    store = AegisStore(tmp_path / "graph.sqlite")
    for value in (entity("a", "A", "DOMAIN"), entity("b", "B", "PROGRAM"), entity("c", "C", "INITIATIVE")): store.upsert_entity(value, AS_OF)
    store.add_hierarchy("a", "b", {"evidence": "test"}, AS_OF); store.add_hierarchy("b", "c", {"evidence": "test"}, AS_OF)
    with pytest.raises(ValueError, match="cycle"): store.add_hierarchy("c", "a", {"evidence": "test"}, AS_OF)
    store.add_relationship("a", "b", "DEPENDS_ON", {"evidence": "test"}, "HIGH", AS_OF)
    with pytest.raises(ValueError, match="cycle"): store.add_relationship("b", "a", "DEPENDS_ON", {"evidence": "test"}, "HIGH", AS_OF)
    assert store.add_relationship("a", "b", "DEPENDS_ON", {"evidence": "duplicate"}, "HIGH", AS_OF) == stable_id("edge", {"source": "a", "target": "b", "type": "DEPENDS_ON"})


def test_task_dag_cycle_is_rejected(tmp_path: Path) -> None:
    service = make_service(tmp_path); mission = service.create_mission("DAG validation")
    with pytest.raises(ValueError, match="cycle"): service.store.add_task_edge(mission["id"], mission["tasks"][-1]["id"], mission["tasks"][0]["id"])


def test_github_import_is_idempotent_and_preserves_provenance(tmp_path: Path) -> None:
    store = AegisStore(tmp_path / "github.sqlite")
    records = [{"record_type": "PR", "number": 12, "title": "Implement Alpha Lab", "state": "OPEN", "isDraft": True, "headRefName": "agent/a", "headRefOid": "abc", "baseRefName": "main", "labels": [{"name": "research"}], "assignees": [{"login": "owner"}], "createdAt": AS_OF, "updatedAt": AS_OF, "url": "https://github.test/pr/12", "body": "Related to #7"}, {"record_type": "ISSUE", "number": 7, "title": "Alpha Lab", "state": "OPEN", "isDraft": False, "labels": [], "assignees": [], "createdAt": AS_OF, "updatedAt": AS_OF, "url": "https://github.test/issues/7", "body": ""}]
    importer = GitHubImporter(store, "owner/repo"); first = importer.import_records(FixtureGitHubAdapter(records), AS_OF); events = len(store.events()); second = importer.import_records(FixtureGitHubAdapter(records), AS_OF)
    assert first.records_seen == 2 and second.records_changed == 0
    assert len(store.events()) == events
    assert store.external_references()[0]["metadata"]["repository"] == "owner/repo"
    assert store.relationships(relationship_type="TRACKED_BY_ISSUE")[0]["inference_rule"] == "github_explicit_link_v1"
    dry = importer.import_records(FixtureGitHubAdapter(records), AS_OF, dry_run=True); assert dry.dry_run and len(store.external_references()) == 2


def test_failed_transaction_rolls_back_import_batch(tmp_path: Path) -> None:
    store = AegisStore(tmp_path / "rollback.sqlite")
    with pytest.raises(ValueError):
        with store.transaction() as connection:
            store.upsert_entity(entity("valid", "Valid"), AS_OF, connection)
            store.upsert_entity({**entity("invalid", "Invalid"), "entity_type": "NOT_A_TYPE"}, AS_OF, connection)
    assert store.entity("valid") is None


def test_repository_import_marks_absent_initiative_unresolved(tmp_path: Path) -> None:
    (tmp_path / "config/research").mkdir(parents=True); (tmp_path / "docs/governance").mkdir(parents=True); (tmp_path / "research/alpha_lab_v1").mkdir(parents=True)
    (tmp_path / "config/research/strategy_registry.json").write_text(json.dumps({"schema_version": "v1", "strategies": [{"strategy_id": "caerus_orion", "display_name": "Orion", "status": "shadow"}]}), encoding="utf-8")
    (tmp_path / "docs/governance/fr_registry.md").write_text("| FR-900 | Test Research | `ACTIVE_RESEARCH` | `spec.md` | ACTIVE |\n", encoding="utf-8")
    store = AegisStore(tmp_path / "aegis.sqlite"); result = RepositoryStateImporter(store, tmp_path).import_state(AS_OF)
    assert any(item["name"] == "Atlas" for item in result.unresolved)
    assert any(item["source_record_id"] == "fr_registry:FR-900" for item in store.entities("MISSION"))
    assert RepositoryStateImporter(store, tmp_path).import_state(AS_OF).records_changed == 0


def test_alpha_lab_import_is_pinned_idempotent_and_evidence_backed(tmp_path: Path) -> None:
    store = AegisStore(tmp_path / "alpha.sqlite")
    importer = AlphaLabImporter(store, "owner/repo")
    adapter = FixtureAlphaLabAdapter(alpha_lab_snapshot())
    dry = importer.import_state(adapter, AS_OF, dry_run=True)
    assert dry.import_result.dry_run and store.entities() == []
    first = importer.import_state(adapter, AS_OF)
    second = importer.import_state(adapter, AS_OF)
    assert len(first.portfolio) == 3 and len(first.blockers) == 1
    assert [item["alpha_lab"]["experiment_id"] for item in first.decision_candidates] == ["EXP-2", "EXP-3"]
    assert second.import_result.records_changed == 0
    assert all(item["source_commit"] == "abc123" for item in first.portfolio)
    assert all(item["source_reported_as_of"] == "2026-07-24" for item in first.portfolio)
    assert store.relationships(relationship_type="BLOCKED_BY")[0]["provenance"]["source_commit"] == "abc123"


def test_operationalizer_creates_isolated_alpha_lab_mission(tmp_path: Path) -> None:
    (tmp_path / "config/research").mkdir(parents=True)
    (tmp_path / "docs/governance").mkdir(parents=True)
    (tmp_path / "research/alpha_lab_v1").mkdir(parents=True)
    (tmp_path / "config/research/strategy_registry.json").write_text('{"strategies": []}', encoding="utf-8")
    (tmp_path / "docs/governance/fr_registry.md").write_text("# Empty fixture\n", encoding="utf-8")
    store = AegisStore(tmp_path / "operational.sqlite")
    operationalizer = Operationalizer(store, tmp_path)
    result = operationalizer.run(AS_OF, FixtureGitHubAdapter([]), alpha_lab_adapter=FixtureAlphaLabAdapter(alpha_lab_snapshot()))
    alpha = result["alpha_lab"]
    assert alpha["mission"]["state"] == "APPROVAL_REQUIRED"
    assert alpha["mission"]["source_record_id"] == "alpha_lab:github:PR:160"
    assert len(alpha["decisions"]) == 2 and len(alpha["blockers"]) == 1
    assert {item["mission_id"] for item in alpha["decisions"]} == {alpha["mission"]["id"]}
    assert len(store.traverse(alpha["mission"]["id"], "out", "TRACKED_BY_PR")) == 1
    repeated = operationalizer.run(AS_OF, FixtureGitHubAdapter([]), alpha_lab_adapter=FixtureAlphaLabAdapter(alpha_lab_snapshot()))
    assert all(item["records_changed"] == 0 for item in repeated["imports"])


def test_reconciliation_priority_and_decision_queue_are_deterministic(tmp_path: Path) -> None:
    service = make_service(tmp_path); mission = service.create_mission("Alpha Lab consolidation", {"owner_capability": "mission_planning", "next_action": "review"})
    service.store.upsert_entity(entity("duplicate_a", "Alpha Lab consolidation"), AS_OF); service.store.upsert_entity(entity("duplicate_b", "alpha  lab consolidation", status="COMPLETED"), AS_OF)
    records = ReconciliationEngine(service.store).run(AS_OF)
    assert any(item["category"] == "EXACT_DUPLICATE" for item in records) and any(item["category"] == "STATE_CONFLICT" for item in records)
    inputs = {key: 1 for key in ("criticality", "blocker_impact", "dependency_count", "decision_urgency", "production_risk", "research_value", "data_readiness", "effort_remaining", "age", "executive_priority", "required_by_active", "incident_resolution", "evidence_readiness")}
    engine = PriorityEngine(service.store); first = engine.score(mission["id"], inputs, AS_OF); second = engine.score(mission["id"], inputs, AS_OF)
    assert first == second and engine.ranking()[0]["mission_id"] == mission["id"]
    decision = {"id": "decision_test", "mission_id": mission["id"], "decision_type": "APPROVE_MISSION", "question": "Approve?", "recommended_action": "Review", "alternatives": ["Reject"], "evidence_links": ["evidence.md"], "confidence": "MEDIUM", "risk_if_delayed": "Delay", "risk_if_approved": "Review risk", "decision_owner": "Brett", "status": "OPEN", "rationale": "", "final_decision_event": None}
    service.store.queue_decision(decision, AS_OF); assert service.store.decisions_queue()[0]["id"] == "decision_test"
    service.store.resolve_queue_decision("decision_test", "APPROVED", "reviewed", "Brett", AS_OF)
    assert service.store.decisions_queue()[0]["final_decision_event"]["owner"] == "Brett"


def test_decision_queue_does_not_pad_missing_evidence(tmp_path: Path) -> None:
    store = AegisStore(tmp_path / "aegis.sqlite")
    operationalizer = Operationalizer(store, tmp_path)
    mission = operationalizer.service.create_mission("Evidence-only decision queue")
    queued = operationalizer._queue_decisions(mission["id"], [], [], AS_OF)
    assert len(queued) == 1
    assert all("checkpoint" not in item["question"] for item in queued)


def test_brief_is_reproducible_for_same_snapshot(tmp_path: Path) -> None:
    service = make_service(tmp_path); service.create_mission("Brief mission", {"owner_capability": "engineering", "next_action": "validate"})
    generator = ExecutiveBriefGenerator(service.store); first_json, first_md = generator.generate(AS_OF); second_json, second_md = generator.generate(AS_OF)
    assert first_json == second_json and first_md == second_md and first_json["payload_sha256"] == second_json["payload_sha256"]
    assert "## Provenance and Generation Metadata" in first_md


def test_execution_adapter_requires_persisted_approval_and_valid_mission(tmp_path: Path) -> None:
    service = make_service(tmp_path); mission = service.create_mission("Governed dispatch")
    adapter = AIOPSRunnerAdapter(service.store)
    with pytest.raises(KeyError): adapter.command("missing", "task", "spec.md", "BUILD")
    with pytest.raises(PermissionError): adapter.command(mission["id"], mission["tasks"][0]["id"], "spec.md", "BUILD")
    service.approve(mission["id"], "explicit test approval")
    assert adapter.command(mission["id"], mission["tasks"][0]["id"], "spec.md", "BUILD") == ["aiops", "run-all", "--spec", "spec.md", "--mode", "BUILD"]


def test_api_is_read_only_and_cli_mission_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "aegis.sqlite"; assert main(["aegis", "--db", str(db), "mission", "create", "--objective", "CLI mission"]) == 0
    service = AegisService(AegisStore(db)); server = ThreadingHTTPServer(("127.0.0.1", 0), handler(service)); thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        health = json.loads(urllib.request.urlopen(base + "/health").read()); assert health["write_enabled"] is False
        request = urllib.request.Request(base + "/missions", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc: urllib.request.urlopen(request)
        assert exc.value.code == 405
    finally: server.shutdown(); server.server_close(); thread.join()


def test_boundary_scanner_finds_no_aegis_forbidden_imports() -> None:
    violations = [item for item in scan(Path(".").resolve(), "origin/agent/aegis-control-plane-166") if item.startswith("forbidden import")]
    assert violations == []
