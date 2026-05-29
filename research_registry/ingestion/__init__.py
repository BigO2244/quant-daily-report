from research_registry.ingestion.adapters import EnvelopeJsonAdapter
from research_registry.ingestion.families import (
    ArtifactFamilyAdapter,
    AttributionArtifactAdapter,
    AuditArtifactAdapter,
    ExecutionTimingArtifactAdapter,
    ExposureIntelligenceArtifactAdapter,
    GovernanceArtifactAdapter,
    HydrationFinding,
    HydrationResult,
    PerformanceVeracityArtifactAdapter,
    RegimeIntelligenceArtifactAdapter,
    ShadowEvaluationArtifactAdapter,
    ValidationArtifactAdapter,
    VixRegimeHistoryAdapter,
    ingest_artifact_family,
)

GrandfatheredArtifactAdapter = ArtifactFamilyAdapter

__all__ = [
    "ArtifactFamilyAdapter",
    "AttributionArtifactAdapter",
    "AuditArtifactAdapter",
    "EnvelopeJsonAdapter",
    "ExecutionTimingArtifactAdapter",
    "ExposureIntelligenceArtifactAdapter",
    "GovernanceArtifactAdapter",
    "GrandfatheredArtifactAdapter",
    "HydrationFinding",
    "HydrationResult",
    "PerformanceVeracityArtifactAdapter",
    "RegimeIntelligenceArtifactAdapter",
    "ShadowEvaluationArtifactAdapter",
    "ValidationArtifactAdapter",
    "VixRegimeHistoryAdapter",
    "ingest_artifact_family",
]
