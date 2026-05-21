from research_registry.ingestion.adapters import EnvelopeJsonAdapter
from research_registry.ingestion.families import (
    ArtifactFamilyAdapter,
    AttributionArtifactAdapter,
    AuditArtifactAdapter,
    ExposureIntelligenceArtifactAdapter,
    GovernanceArtifactAdapter,
    HydrationFinding,
    HydrationResult,
    PerformanceVeracityArtifactAdapter,
    RegimeIntelligenceArtifactAdapter,
    ShadowEvaluationArtifactAdapter,
    ValidationArtifactAdapter,
)

GrandfatheredArtifactAdapter = ArtifactFamilyAdapter

__all__ = [
    "ArtifactFamilyAdapter",
    "AttributionArtifactAdapter",
    "AuditArtifactAdapter",
    "EnvelopeJsonAdapter",
    "ExposureIntelligenceArtifactAdapter",
    "GovernanceArtifactAdapter",
    "GrandfatheredArtifactAdapter",
    "HydrationFinding",
    "HydrationResult",
    "PerformanceVeracityArtifactAdapter",
    "RegimeIntelligenceArtifactAdapter",
    "ShadowEvaluationArtifactAdapter",
    "ValidationArtifactAdapter",
]
