from __future__ import annotations

from research_registry.models import (
    ChainStatus,
    ConfidenceLevel,
    MetadataEnvelopeBuilder,
    NAVSurface,
    ObjectType,
    ResearchObjectEnvelope,
    SurfaceType,
)
from research_registry.models.enums import GovernanceState
from research_registry.query import RegistryQuery
from research_registry.registry import SQLiteResearchRegistry

from Tests.test_research_registry_foundation import AS_OF, _attribution, _nav, _strategy


def _live_nav(parent: ResearchObjectEnvelope) -> ResearchObjectEnvelope:
    return MetadataEnvelopeBuilder().build(
        object_type=ObjectType.NAV_SURFACE.value,
        data=NAVSurface(
            surface_id="polaris_live_2026_01_02",
            nav_surface_type=SurfaceType.LIVE_BROKER_PAPER_NAV.value,
            confidence_level=ConfidenceLevel.LOW.value,
            execution_realism="BROKER_PAPER_FILLS",
            point_in_time_validity="BROKER_TIMESTAMP_DEPENDENT",
            source_path="memory://broker",
            strategy_ref="caerus_polaris",
            temporal_window={"start": "2026-01-02", "end": "2026-01-02"},
            chain_status=ChainStatus.OK.value,
            metrics={"nav": 101.0},
        ).to_data(),
        strategy_ref="caerus_polaris",
        trade_date="2026-01-02",
        surface_ref="polaris_live_2026_01_02",
        schema_id="caerus.nav_surface",
        schema_version="1.0.0",
        ontology_version="1.0.0",
        as_of=AS_OF,
        produced_by="Tests.test_research_registry_query_layer",
        produced_at=AS_OF,
        source_paths=["memory://broker"],
        input_object_ids=[parent.object_id],
        transformation="registry.test.derive_live_nav",
        deterministic=True,
        source_state_hash=None,
        parent_refs=[parent.object_id],
        parent_chain_hashes=[parent.lineage["transformation_chain_hash"]],
        confidence_level=ConfidenceLevel.LOW.value,
        confidence_rationale="ungoverned critical fixture is low confidence",
        governance_state=GovernanceState.UNGOVERNED.value,
        governance_coverage_type="UNGOVERNED",
        nav_surface_type=SurfaceType.LIVE_BROKER_PAPER_NAV.value,
        execution_realism="BROKER_PAPER_FILLS",
        chain_status=ChainStatus.OK.value,
    )


def _registry(tmp_path, *, include_live: bool = False) -> tuple[SQLiteResearchRegistry, dict[str, ResearchObjectEnvelope]]:
    strategy = _strategy()
    nav = _nav(strategy)
    attribution = _attribution(nav)
    registry = SQLiteResearchRegistry(tmp_path / "registry.db")
    registry.ingest(strategy)
    registry.ingest(nav)
    registry.ingest(attribution)
    objects = {"strategy": strategy, "nav": nav, "attribution": attribution}
    if include_live:
        live = _live_nav(strategy)
        registry.ingest(live)
        objects["live"] = live
    return registry, objects


def test_deterministic_object_retrieval_and_listing(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)

        assert query.get_object(objects["nav"].object_id).to_dict() == objects["nav"].to_dict()
        listed = query.list_objects()
        assert [obj.object_id for obj in listed] == sorted(obj.object_id for obj in listed)
    finally:
        registry.close()


def test_typed_queries_are_sorted_and_side_effect_free(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)
        before_digest = registry.registry_digest()

        assert [obj.object_id for obj in query.query_by_type("NAVSurface")] == [objects["nav"].object_id]
        assert [obj.object_id for obj in query.query_by_surface("OPERATIONAL_SHADOW_NAV")] == [
            objects["attribution"].object_id,
            objects["nav"].object_id,
        ]
        assert [obj.object_id for obj in query.query_by_confidence("LOW")] == [objects["attribution"].object_id, objects["nav"].object_id]
        assert len(query.query_by_governance_state("UNGOVERNED")) == 3
        assert registry.registry_digest() == before_digest
    finally:
        registry.close()


def test_temporal_queries_preserve_fencing_and_trade_date_filters(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)

        assert query.query_as_of("2026-01-02T11:59:59Z") == []
        assert [obj.object_id for obj in query.query_as_of("2026-01-02T12:00:00Z")] == sorted(
            obj.object_id for obj in objects.values()
        )
        assert [obj.object_id for obj in query.query_trade_date("2026-01-02")] == [
            objects["attribution"].object_id,
            objects["nav"].object_id,
        ]
    finally:
        registry.close()


def test_lineage_traversal_is_deterministic(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)
        lineage = query.get_lineage(objects["nav"].object_id)

        assert lineage.parents == [objects["strategy"].object_id]
        assert lineage.children == [objects["attribution"].object_id]
        assert lineage.ancestors == [objects["strategy"].object_id]
        assert lineage.descendants == [objects["attribution"].object_id]
        assert query.get_parents(objects["attribution"].object_id) == [objects["nav"].object_id]
        assert query.get_children(objects["strategy"].object_id) == [objects["nav"].object_id]
    finally:
        registry.close()


def test_reconstruct_object_state_reports_canonical_presence_without_mutation(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)
        before_digest = registry.registry_digest()

        present = query.reconstruct_object_state(objects["nav"].object_id, "2026-01-02T12:00:00Z")
        absent = query.reconstruct_object_state(objects["nav"].object_id, "2026-01-02T11:59:59Z")

        assert present.reconstruction_status == "PRESENT_AT_ANCHOR"
        assert present.truth_mode == "CANONICAL"
        assert present.object.object_id == objects["nav"].object_id
        assert absent.reconstruction_status == "OBJECT_NOT_PRESENT_AT_ANCHOR"
        assert absent.excluded_reason == "TEMPORAL_FENCE"
        assert registry.registry_digest() == before_digest
    finally:
        registry.close()


def test_registry_summary_statistics_and_surface_conflict_detection(tmp_path) -> None:
    registry, objects = _registry(tmp_path, include_live=True)
    try:
        query = RegistryQuery(registry)
        summary = query.registry_summary()
        stats = query.registry_statistics()
        conflicts = query.detect_surface_conflicts()

        assert summary["object_count"] == 4
        assert summary["edge_count"] == 3
        assert stats.by_type["NAVSurface"] == 2
        assert stats.by_surface["LIVE_BROKER_PAPER_NAV"] == 1
        assert len(conflicts) == 1
        assert conflicts[0].strategy_ref == "caerus_polaris"
        assert conflicts[0].trade_date == "2026-01-02"
        assert "INCOMPATIBLE" in conflicts[0].compatibility.values()
    finally:
        registry.close()


def test_orphan_detection_reports_graph_orphans(tmp_path) -> None:
    registry, objects = _registry(tmp_path)
    try:
        query = RegistryQuery(registry)
        assert query.detect_orphans() == []

        registry.graph.add_object(objects["nav"])
        # Simulate a corrupted in-memory DAG by removing the parent edge only.
        registry.graph.graph.remove_edge(
            objects["strategy"].lineage["node_id"],
            objects["nav"].lineage["node_id"],
        )

        assert query.detect_orphans() == [objects["nav"].lineage["node_id"]]
    finally:
        registry.close()
