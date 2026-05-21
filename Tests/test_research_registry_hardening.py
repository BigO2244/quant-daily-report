from __future__ import annotations

import json

import pytest

from research_registry.ingestion import (
    ArtifactFamilyAdapter,
    AuditArtifactAdapter,
    GovernanceArtifactAdapter,
)
from research_registry.models import ConfidenceLevel, ResearchObjectEnvelope
from research_registry.observability import ConformanceAuditor, RegistryInspector
from research_registry.registry import SQLiteResearchRegistry
from research_registry.replay import DeterministicParityValidator, ReplayValidator
from research_registry.runtime import RuntimeReadinessCheck
from research_registry.validation import RegistryValidationError, assert_surface_operation_allowed, validate_envelope
from research_registry.models.enums import SurfaceType

from Tests.test_research_registry_foundation import _attribution, _nav, _strategy


def test_grandfathered_artifact_hydration_is_low_confidence(tmp_path) -> None:
    artifact = tmp_path / "contribution_report.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": "caerus_position_attribution_v1",
                "trade_date": "2026-04-30",
                "strategies": {"caerus_polaris": {"return": 0.01}},
            }
        ),
        encoding="utf-8",
    )

    result = ArtifactFamilyAdapter().hydrate_path(artifact)

    assert result.findings == []
    assert len(result.envelopes) == 1
    envelope = result.envelopes[0]
    assert envelope.confidence["level"] == ConfidenceLevel.LOW.value
    assert envelope.annotations["grandfathered"]["lineage_inferred"] is True
    validate_envelope(envelope)


def test_audit_artifact_adapter_derives_audit_finding_with_inferred_lineage(tmp_path) -> None:
    artifact = tmp_path / "audit_summary.json"
    artifact.write_text(
        json.dumps(
            {
                "generated_at": "2026-05-21T14:36:29+00:00",
                "overall_classification": "INVALIDATED",
                "key_findings": ["same-date timing risk"],
            }
        ),
        encoding="utf-8",
    )

    result = AuditArtifactAdapter().hydrate_path(artifact)

    assert result.findings == []
    assert len(result.envelopes) == 2
    root, finding = result.envelopes
    assert finding.object_type == "AuditFinding"
    assert finding.lineage["parent_refs"] == [root.object_id]
    assert finding.confidence["level"] == ConfidenceLevel.LOW.value
    validate_envelope(finding, parent_envelopes={root.object_id: root})


def test_governance_artifact_adapter_derives_governance_fr(tmp_path) -> None:
    artifact = tmp_path / "fr.json"
    artifact.write_text(
        json.dumps(
            {
                "fr_id": "FR-999",
                "status": "READY",
                "category": "FR",
                "blast_radius": "LOW",
                "observation_criteria": "read-only validation",
                "rollback_reference": "remove derived registry",
                "validation_summary": "pending",
                "affected_objects": [],
            }
        ),
        encoding="utf-8",
    )

    result = GovernanceArtifactAdapter().hydrate_path(artifact)

    assert result.findings == []
    assert [envelope.object_type for envelope in result.envelopes] == ["ResearchArtifact", "GovernanceFR"]


def test_malformed_artifact_is_reported_not_hydrated(tmp_path) -> None:
    artifact = tmp_path / "bad.json"
    artifact.write_text("{not-json", encoding="utf-8")

    result = ArtifactFamilyAdapter().hydrate_path(artifact)

    assert result.envelopes == []
    assert result.findings[0].code == "ARTIFACT_MALFORMED"


def test_reopened_sqlite_registry_preserves_graph_traversal(tmp_path) -> None:
    strategy = _strategy()
    nav = _nav(strategy)
    attribution = _attribution(nav)
    db_path = tmp_path / "registry.db"

    registry = SQLiteResearchRegistry(db_path)
    registry.ingest(strategy)
    registry.ingest(nav)
    registry.ingest(attribution)
    registry.close()

    reopened = SQLiteResearchRegistry(db_path)
    try:
        assert reopened.upstream(attribution.object_id) == [nav.object_id, strategy.object_id]
        assert RegistryInspector().inspect(reopened).status == "PASS"
        assert ConformanceAuditor().audit_registry(reopened).status == "PASS"
    finally:
        reopened.close()


def test_replay_validator_detects_future_information_and_payload_divergence() -> None:
    strategy = _strategy()
    mutated = ResearchObjectEnvelope.from_dict(
        {**strategy.to_dict(), "data": {**strategy.data, "display_name": "Changed"}}
    )

    result = ReplayValidator().validate_canonical_replay(
        strategy,
        mutated,
        anchor="2026-01-02T11:59:59Z",
    )

    assert result.replay_result == "DIVERGENT"
    assert "PAYLOAD_DIVERGENCE" in result.findings
    assert any(finding.startswith("FUTURE_INFORMATION") for finding in result.findings)


def test_parity_validator_is_order_stable(tmp_path) -> None:
    strategy = _strategy()
    nav = _nav(strategy)
    attribution = _attribution(nav)

    report = DeterministicParityValidator().compare_rebuilds(
        first_db_path=tmp_path / "a.db",
        second_db_path=tmp_path / "b.db",
        envelopes=[attribution, strategy, nav],
    )

    assert report.status == "PASS"
    assert report.first_digest == report.second_digest


def test_chain_hash_instability_is_rejected() -> None:
    strategy = _strategy()
    broken = ResearchObjectEnvelope.from_dict(
        {
            **strategy.to_dict(),
            "lineage": {
                **strategy.lineage,
                "transformation_chain_hash": "0" * 64,
            },
        }
    )

    with pytest.raises(RegistryValidationError) as excinfo:
        validate_envelope(broken)
    assert "CHAIN_HASH_INCONSISTENCY" in str(excinfo.value)


def test_surface_incompatibility_requires_explicit_override() -> None:
    with pytest.raises(ValueError):
        assert_surface_operation_allowed(
            SurfaceType.LIVE_BROKER_PAPER_NAV.value,
            SurfaceType.RESEARCH_BACKTEST_NAV.value,
            operation="compare",
        )

    allowed = assert_surface_operation_allowed(
        SurfaceType.LIVE_BROKER_PAPER_NAV.value,
        SurfaceType.RESEARCH_BACKTEST_NAV.value,
        operation="compare",
        override_rationale="governance-reviewed diagnostic",
        override_audit_ref="audit_finding__example",
    )
    assert allowed.compatibility == "INCOMPATIBLE_OVERRIDE"


def test_runtime_readiness_checks_dependencies_without_broker_env(monkeypatch) -> None:
    for key in ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_API_SECRET", "ALPACA_BASE_URL"]:
        monkeypatch.delenv(key, raising=False)

    report = RuntimeReadinessCheck().run()

    assert report.status == "PASS"
    assert report.checks["networkx_dependency"] == "PASS"
    assert report.checks["broker_env_absent"] == "PASS"
