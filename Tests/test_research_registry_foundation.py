from __future__ import annotations

import pytest

from research_registry.confidence import ConfidenceEngine
from research_registry.governance import GovernanceEngine
from research_registry.models import (
    AttributionRun,
    ChainStatus,
    ConfidenceLevel,
    ExposureSnapshot,
    GovernanceFR,
    GovernanceState,
    MetadataEnvelopeBuilder,
    NAVSurface,
    ObjectType,
    PortfolioSnapshot,
    RegimeAssessment,
    ResearchArtifact,
    ResearchObjectEnvelope,
    Strategy,
    SurfaceType,
    ValidationRun,
)
from research_registry.provenance import ProvenanceGraph
from research_registry.registry import SQLiteResearchRegistry
from research_registry.replay import DeterministicRebuilder
from research_registry.temporal import TemporalFence
from research_registry.validation import (
    RegistryValidationError,
    assert_surface_operation_allowed,
    surface_compatibility,
    validate_envelope,
)


AS_OF = "2026-01-02T12:00:00Z"


def _builder() -> MetadataEnvelopeBuilder:
    return MetadataEnvelopeBuilder()


def _strategy() -> ResearchObjectEnvelope:
    return _builder().build(
        object_type=ObjectType.STRATEGY.value,
        data=Strategy(
            strategy_id="caerus_polaris",
            display_name="Caerus Polaris",
            promotion_state="PAPER",
            governance_classification="active_control",
        ).to_data(),
        strategy_ref="caerus_polaris",
        trade_date=None,
        surface_ref=None,
        schema_id="caerus.strategy",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=AS_OF,
        produced_by="Tests.test_research_registry_foundation",
        produced_at=AS_OF,
        source_paths=["memory://strategy"],
        input_object_ids=[],
        transformation="registry.test.raw_strategy",
        deterministic=True,
        source_state_hash=None,
        parent_refs=[],
        parent_chain_hashes=[],
        confidence_level=ConfidenceLevel.BROKER_AUTHORITATIVE.value,
        confidence_rationale="raw deterministic strategy fixture",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=None,
        execution_realism=None,
        chain_status=ChainStatus.NOT_APPLICABLE.value,
    )


def _nav(parent: ResearchObjectEnvelope, *, confidence: str = ConfidenceLevel.LOW.value) -> ResearchObjectEnvelope:
    data = NAVSurface(
        surface_id="polaris_shadow_2026_01_02",
        nav_surface_type=SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        confidence_level=confidence,
        execution_realism="MODEL_PORTFOLIO_NO_BROKER_FILLS",
        point_in_time_validity="POINT_IN_TIME_RESEARCH",
        source_path="memory://nav",
        strategy_ref="caerus_polaris",
        temporal_window={"start": "2026-01-02", "end": "2026-01-02"},
        chain_status=ChainStatus.NO_PRIOR.value,
        metrics={"nav": 100.0, "daily_return": 0.0},
    ).to_data()
    return _builder().build(
        object_type=ObjectType.NAV_SURFACE.value,
        data=data,
        strategy_ref="caerus_polaris",
        trade_date="2026-01-02",
        surface_ref="polaris_shadow_2026_01_02",
        schema_id="caerus.nav_surface",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=AS_OF,
        produced_by="Tests.test_research_registry_foundation",
        produced_at=AS_OF,
        source_paths=["memory://nav"],
        input_object_ids=[parent.object_id],
        transformation="registry.test.derive_nav",
        deterministic=True,
        source_state_hash=None,
        parent_refs=[parent.object_id],
        parent_chain_hashes=[parent.lineage["transformation_chain_hash"]],
        confidence_level=confidence,
        confidence_rationale="NO_PRIOR shadow chain is low confidence",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        execution_realism="MODEL_PORTFOLIO_NO_BROKER_FILLS",
        chain_status=ChainStatus.NO_PRIOR.value,
        downgrade_reasons=["CHAIN_NO_PRIOR"],
    )


def _attribution(parent: ResearchObjectEnvelope) -> ResearchObjectEnvelope:
    return _builder().build(
        object_type=ObjectType.ATTRIBUTION_RUN.value,
        data=AttributionRun(
            run_id="attr_polaris_2026_01_02",
            trade_date="2026-01-02",
            strategy_ref="caerus_polaris",
            nav_surface_ref=parent.object_id,
            contribution_report="memory://contribution",
            factor_exposure="memory://factor",
            regime_analysis="memory://regime",
            concentration_analysis="memory://concentration",
            decision_attribution=None,
            confidence_level=ConfidenceLevel.LOW.value,
            governance_state=GovernanceState.UNGOVERNED.value,
        ).to_data(),
        strategy_ref="caerus_polaris",
        trade_date="2026-01-02",
        surface_ref="polaris_shadow_2026_01_02",
        schema_id="caerus.attribution_run",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=AS_OF,
        produced_by="Tests.test_research_registry_foundation",
        produced_at=AS_OF,
        source_paths=["memory://attribution"],
        input_object_ids=[parent.object_id],
        transformation="registry.test.derive_attribution",
        deterministic=True,
        source_state_hash=None,
        parent_refs=[parent.object_id],
        parent_chain_hashes=[parent.lineage["transformation_chain_hash"]],
        confidence_level=ConfidenceLevel.LOW.value,
        confidence_rationale="inherits low confidence from NAV parent",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        execution_realism="MODEL_PORTFOLIO_NO_BROKER_FILLS",
        chain_status=ChainStatus.NOT_APPLICABLE.value,
    )


def test_canonical_payload_schemas_are_explicit() -> None:
    assert Strategy("caerus_orion", "Caerus Orion", "SHADOW", "primary_candidate").to_data()["strategy_id"] == "caerus_orion"
    assert GovernanceFR("FR-999", "FR", "READY", "LOW", "observe", "revert", "pending", []).to_data()["fr_id"] == "FR-999"
    assert ResearchArtifact("artifact-1", "json", "memory://x", "abc", "summary", "test").to_data()["content_hash"] == "abc"
    assert ExposureSnapshot("exposure-1", "2026-01-02", "caerus_polaris", {}, [], {}, {}, {}, "LOW").to_data()["strategy_ref"] == "caerus_polaris"
    assert RegimeAssessment("regime-1", "2026-01-02", {}, {}, {}, {}, {}, {}, "LOW").to_data()["assessment_id"] == "regime-1"
    assert PortfolioSnapshot("portfolio-1", "2026-01-02", "caerus_polaris", [], {}, 1.0, "nav-1", "memory://portfolio", {}).to_data()["cash_weight"] == 1.0
    assert ValidationRun("validation-1", None, "BACKTEST", {}, {}, "nav-1", "LOW", []).to_data()["run_type"] == "BACKTEST"


def test_envelope_validation_and_sqlite_registry_hydration(tmp_path) -> None:
    strategy = _strategy()
    nav = _nav(strategy)
    attribution = _attribution(nav)

    validate_envelope(strategy)
    validate_envelope(nav, parent_envelopes={strategy.object_id: strategy})
    validate_envelope(attribution, parent_envelopes={nav.object_id: nav})

    registry = SQLiteResearchRegistry(tmp_path / "registry.db")
    try:
        registry.ingest(strategy)
        registry.ingest(nav)
        registry.ingest(attribution)

        hydrated = registry.hydrate(attribution.object_id)
        assert hydrated.to_dict() == attribution.to_dict()
        assert registry.upstream(attribution.object_id) == [nav.object_id, strategy.object_id]
        assert registry.orphan_findings() == []
    finally:
        registry.close()


def test_confidence_engine_rejects_silent_shadow_upgrade() -> None:
    strategy = _strategy()
    inflated = _nav(strategy, confidence=ConfidenceLevel.HIGH.value)
    with pytest.raises(RegistryValidationError) as excinfo:
        validate_envelope(inflated, parent_envelopes={strategy.object_id: strategy})
    assert "CONFIDENCE_STAMP_MISMATCH" in str(excinfo.value)

    computed = ConfidenceEngine().compute(
        object_type=ObjectType.NAV_SURFACE.value,
        nav_surface_type=SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        chain_status=ChainStatus.NO_PRIOR.value,
        execution_realism="MODEL_PORTFOLIO_NO_BROKER_FILLS",
        governance_state=GovernanceState.UNGOVERNED.value,
        parent_confidences={strategy.object_id: strategy.confidence["level"]},
        deterministic=True,
        is_stale=False,
    )
    assert computed.level == ConfidenceLevel.LOW
    assert "CHAIN_NO_PRIOR" in computed.downgrade_reasons


def test_truth_surface_compatibility_matrix_enforces_incompatibility() -> None:
    cautious = surface_compatibility(
        SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        SurfaceType.RESEARCH_BACKTEST_NAV.value,
    )
    assert cautious.compatibility == "CAUTIOUS_OK"
    assert cautious.requires_annotation is True

    with pytest.raises(ValueError):
        assert_surface_operation_allowed(
            SurfaceType.LIVE_BROKER_PAPER_NAV.value,
            SurfaceType.OPERATIONAL_SHADOW_NAV.value,
            operation="aggregate",
        )


def test_provenance_graph_rejects_cycles() -> None:
    strategy = _strategy()
    nav = _nav(strategy)
    graph = ProvenanceGraph()
    graph.add_object(strategy)
    graph.add_object(nav)
    graph.add_edge(strategy.lineage["node_id"], nav.lineage["node_id"], "DERIVED_FROM")
    with pytest.raises(ValueError):
        graph.add_edge(nav.lineage["node_id"], strategy.lineage["node_id"], "DERIVED_FROM")


def test_temporal_fencing_excludes_future_information() -> None:
    strategy = _strategy()
    fence = TemporalFence()
    assert fence.admissible(strategy, "2026-01-02T12:00:00Z") is True
    assert fence.admissible(strategy, "2026-01-02T11:59:59Z") is False


def test_governance_inheritance_blocks_surface_boundary() -> None:
    result = GovernanceEngine().inherit(
        parent_governance={
            "parent": {
                "state": GovernanceState.GOVERNED_DEPLOYED.value,
                "governing_frs": ["FR-024"],
            }
        },
        deterministic=True,
        child_surface=SurfaceType.OPERATIONAL_SHADOW_NAV.value,
        parent_surfaces={"parent": SurfaceType.RESEARCH_BACKTEST_NAV.value},
        child_ontology_version="1.0.0",
        parent_ontology_versions={"parent": "1.0.0"},
    )
    assert result.state == GovernanceState.UNGOVERNED
    assert result.inheritance_blocked is True


def test_deterministic_rebuild_parity(tmp_path) -> None:
    strategy = _strategy()
    nav = _nav(strategy)
    attribution = _attribution(nav)
    rebuilder = DeterministicRebuilder()

    first = rebuilder.rebuild(db_path=tmp_path / "registry_a.db", envelopes=[attribution, nav, strategy])
    second = rebuilder.rebuild(db_path=tmp_path / "registry_b.db", envelopes=[strategy, nav, attribution])

    rebuilder.assert_parity(first, second)
    assert first.object_ids == sorted([strategy.object_id, nav.object_id, attribution.object_id])
