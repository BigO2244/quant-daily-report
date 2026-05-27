from __future__ import annotations

import json
from pathlib import Path

from research_registry.ingestion.families import ingest_artifact_family
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry
from scripts import research_registry_cli


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _run_fixture(root: Path) -> Path:
    run_root = root / "outputs" / "runs" / "run-20260527"
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run-20260527",
            "trade_date": "2026-05-27",
            "execution_status": "EXECUTED",
            "operator_execution_status": "executed",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run-20260527",
            "trade_date": "2026-05-27",
            "status": "EXECUTED",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
            "broker_responses": [
                {"ticker": "ELV", "side": "BUY", "status": "ACCEPTED"},
                {"ticker": "SLB", "side": "BUY", "status": "ACCEPTED"},
            ],
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "run_id": "run-20260527",
            "trade_date": "2026-05-27",
            "terminal_status": "success",
            "operator_execution_status": "executed",
            "execution_integrity_status": "OK",
            "updated_at": "2026-05-27T14:05:00Z",
        },
    )
    _write_json(
        run_root / "audit" / "execution_integrity.json",
        {
            "run_id": "run-20260527",
            "trade_date": "2026-05-27",
            "status": "OK",
            "pending_buy_count": 0,
            "missing_buy_orders": [],
            "findings": [],
        },
    )
    return run_root


def test_execution_run_and_integrity_adapters_ingest_realish_run_artifacts(tmp_path: Path) -> None:
    run_root = _run_fixture(tmp_path)
    registry = SQLiteResearchRegistry(tmp_path / "registry.db")
    try:
        run_result = ingest_artifact_family(
            family="execution_run",
            artifact_paths=[run_root],
            registry=registry,
        )
        integrity_result = ingest_artifact_family(
            family="execution_integrity",
            artifact_paths=[run_root / "audit" / "execution_integrity.json"],
            registry=registry,
        )

        assert run_result.findings == []
        assert integrity_result.findings == []
        query = RegistryQuery(registry)
        objects = query.list_objects()
        artifact_types = {obj.data.get("artifact_type") for obj in objects}
        assert "execution_run" in artifact_types
        assert "execution_integrity" in artifact_types
        assert any(obj.object_type == "AuditFinding" for obj in objects)
        assert query.registry_summary()["object_count"] == 3
    finally:
        registry.close()


def test_research_packet_and_governance_doc_adapters_ingest_realish_artifacts(tmp_path: Path) -> None:
    packet_root = tmp_path / "outputs" / "research_packets" / "2026-05-27"
    _write_json(
        packet_root / "packet.json",
        {
            "trade_date": "2026-05-27",
            "generated_at": "2026-05-27T22:00:00Z",
            "status": "READY",
            "confidence": "LOW",
        },
    )
    _write_json(packet_root / "summary.json", {"trade_date": "2026-05-27", "status": "READY"})
    _write_text(packet_root / "packet.md", "# Packet\n")

    governance_doc = tmp_path / "docs" / "governance" / "fr_active_backlog.md"
    _write_text(
        governance_doc,
        "\n".join(
            [
                "# FR Active Backlog",
                "| FR | Phase | Status | Blast Radius |",
                "|---|---|---|---|",
                "| FR-031 execution integrity contract | Execution Integrity | `PROMOTION_READY` | HIGH |",
                "| HOTFIX-2026-05-27 buy-leg suppression incident | HOTFIX | `DEPLOYED_OBSERVING` | HIGH |",
            ]
        )
        + "\n",
    )

    registry = SQLiteResearchRegistry(tmp_path / "registry.db")
    try:
        packet_result = ingest_artifact_family(
            family="research_packet",
            artifact_paths=[packet_root],
            registry=registry,
        )
        governance_result = ingest_artifact_family(
            family="governance_doc",
            artifact_paths=[governance_doc],
            registry=registry,
        )

        assert packet_result.findings == []
        assert governance_result.findings == []
        query = RegistryQuery(registry)
        objects = query.list_objects()
        artifact_types = {obj.data.get("artifact_type") for obj in objects}
        assert "research_packet" in artifact_types
        assert "governance_doc" in artifact_types
        governance = [obj for obj in objects if obj.object_type == "GovernanceFR"]
        assert {obj.data["fr_id"] for obj in governance} == {"FR-031", "HOTFIX-2026-05-27"}
        assert {obj.data["status"] for obj in governance} == {"PROMOTION_READY", "DEPLOYED_OBSERVING"}
    finally:
        registry.close()


def test_research_registry_cli_ingests_realish_artifact_roots(tmp_path: Path, capsys) -> None:
    run_root = _run_fixture(tmp_path)
    packet_root = tmp_path / "outputs" / "research_packets" / "2026-05-27"
    _write_json(packet_root / "packet.json", {"trade_date": "2026-05-27", "status": "READY"})
    governance_root = tmp_path / "docs" / "governance"
    _write_text(
        governance_root / "fr_registry.md",
        "| FR | Phase | Status |\n|---|---|---|\n| FR-031 execution integrity contract | Execution Integrity | `PROMOTION_READY` |\n",
    )
    db_path = tmp_path / "registry.db"

    assert research_registry_cli.main(
        ["ingest-runs", "--db", str(db_path), "--runs-root", str(run_root.parent), "--limit", "1"]
    ) == 0
    assert research_registry_cli.main(
        [
            "ingest-research-packets",
            "--db",
            str(db_path),
            "--packets-root",
            str(packet_root.parent),
            "--limit",
            "1",
        ]
    ) == 0
    assert research_registry_cli.main(
        ["ingest-governance", "--db", str(db_path), "--docs-root", str(governance_root)]
    ) == 0
    assert research_registry_cli.main(
        ["query", "--db", str(db_path), "--data-artifact-type", "execution_run"]
    ) == 0

    output = capsys.readouterr().out
    assert '"status": "INGESTED_RUNS"' in output
    assert '"status": "INGESTED_RESEARCH_PACKETS"' in output
    assert '"status": "INGESTED_GOVERNANCE"' in output
    assert '"object_count": 1' in output
