from __future__ import annotations

import json

from research_registry.ingestion import ingest_artifact_family as package_ingest_artifact_family
from research_registry.ingestion.families import ingest_artifact_family
from research_registry.registry import SQLiteResearchRegistry


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_public_artifact_family_ingestion_imports_from_canonical_boundaries() -> None:
    assert ingest_artifact_family is package_ingest_artifact_family


def test_artifact_family_hydration_is_deterministic_for_bounded_json(tmp_path) -> None:
    first = tmp_path / "b.json"
    second = tmp_path / "a.json"
    _write_json(first, {"trade_date": "2026-04-30", "value": 2})
    _write_json(second, {"trade_date": "2026-04-29", "value": 1})

    run_a = ingest_artifact_family(family="generic", artifact_paths=[first, second])
    run_b = ingest_artifact_family(family="generic", artifact_paths=[second, first])

    assert run_a.findings == []
    assert run_b.findings == []
    assert [envelope.object_id for envelope in run_a.envelopes] == [
        envelope.object_id for envelope in run_b.envelopes
    ]
    assert [envelope.to_dict() for envelope in run_a.envelopes] == [
        envelope.to_dict() for envelope in run_b.envelopes
    ]


def test_artifact_family_ingests_into_supplied_registry(tmp_path) -> None:
    artifact = tmp_path / "audit_summary.json"
    _write_json(
        artifact,
        {
            "generated_at": "2026-05-21T14:36:29+00:00",
            "overall_classification": "INVALIDATED",
        },
    )
    registry = SQLiteResearchRegistry(tmp_path / "registry.db")
    try:
        result = ingest_artifact_family(
            family="performance_veracity",
            artifact_paths=[artifact],
            registry=registry,
        )

        assert result.findings == []
        assert len(result.envelopes) == 2
        assert registry.store.object_ids() == sorted(envelope.object_id for envelope in result.envelopes)
        assert registry.hydrate(result.envelopes[-1].object_id).object_type == "AuditFinding"
    finally:
        registry.close()


def test_unknown_artifact_family_returns_finding_without_side_effect(tmp_path) -> None:
    artifact = tmp_path / "x.json"
    _write_json(artifact, {"trade_date": "2026-04-30"})

    result = ingest_artifact_family(family="unknown", artifact_paths=[artifact])

    assert result.envelopes == []
    assert result.findings[0].code == "UNKNOWN_ARTIFACT_FAMILY"
