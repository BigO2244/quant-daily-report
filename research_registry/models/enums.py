"""Closed semantic enumerations frozen by SEM-001 through SEM-008."""

from __future__ import annotations

from enum import Enum


class StrictStrEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ObjectType(StrictStrEnum):
    STRATEGY = "Strategy"
    NAV_SURFACE = "NAVSurface"
    ATTRIBUTION_RUN = "AttributionRun"
    EXPOSURE_SNAPSHOT = "ExposureSnapshot"
    REGIME_ASSESSMENT = "RegimeAssessment"
    AUDIT_FINDING = "AuditFinding"
    GOVERNANCE_FR = "GovernanceFR"
    PROMOTION_ASSESSMENT = "PromotionAssessment"
    PORTFOLIO_SNAPSHOT = "PortfolioSnapshot"
    RESEARCH_ARTIFACT = "ResearchArtifact"
    VALIDATION_RUN = "ValidationRun"
    STABILITY_ASSESSMENT = "StabilityAssessment"
    FRAGILITY_ASSESSMENT = "FragilityAssessment"
    CONFIDENCE_ASSESSMENT = "ConfidenceAssessment"
    TEMPORAL_WINDOW = "TemporalWindow"
    LINEAGE_NODE = "LineageNode"


class ConfidenceLevel(StrictStrEnum):
    BROKER_AUTHORITATIVE = "BROKER_AUTHORITATIVE"
    HIGH = "HIGH"
    PARTIAL_CONFIDENCE = "PARTIAL_CONFIDENCE"
    LOW = "LOW"
    UNAVAILABLE = "UNAVAILABLE"


CONFIDENCE_RANK = {
    ConfidenceLevel.UNAVAILABLE: 0,
    ConfidenceLevel.LOW: 1,
    ConfidenceLevel.PARTIAL_CONFIDENCE: 2,
    ConfidenceLevel.HIGH: 3,
    ConfidenceLevel.BROKER_AUTHORITATIVE: 4,
}


class GovernanceState(StrictStrEnum):
    UNGOVERNED = "UNGOVERNED"
    GOVERNED_DRAFT = "GOVERNED_DRAFT"
    GOVERNED_DEPLOYED = "GOVERNED_DEPLOYED"
    GOVERNED_OBSERVING = "GOVERNED_OBSERVING"
    GOVERNED_DEFERRED = "GOVERNED_DEFERRED"


GOVERNANCE_PRECEDENCE = {
    GovernanceState.UNGOVERNED: 0,
    GovernanceState.GOVERNED_DRAFT: 1,
    GovernanceState.GOVERNED_DEPLOYED: 2,
    GovernanceState.GOVERNED_DEFERRED: 3,
    GovernanceState.GOVERNED_OBSERVING: 4,
}


class SurfaceType(StrictStrEnum):
    LIVE_BROKER_PAPER_NAV = "LIVE_BROKER_PAPER_NAV"
    OPERATIONAL_SHADOW_NAV = "OPERATIONAL_SHADOW_NAV"
    RESEARCH_BACKTEST_NAV = "RESEARCH_BACKTEST_NAV"


class ChainStatus(StrictStrEnum):
    OK = "OK"
    NO_PRIOR = "NO_PRIOR"
    BROKEN_CHAIN = "BROKEN_CHAIN"
    REPAIRED = "REPAIRED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class EdgeType(StrictStrEnum):
    DERIVED_FROM = "DERIVED_FROM"
    GOVERNED_BY = "GOVERNED_BY"
    VALIDATED_BY = "VALIDATED_BY"
    AUDITED_BY = "AUDITED_BY"
    SUPERSEDES = "SUPERSEDES"
    PROMOTED_FROM = "PROMOTED_FROM"
    REPAIRS = "REPAIRS"
    FR_DEPENDS_ON = "FR_DEPENDS_ON"
    FR_COVERS = "FR_COVERS"
    AUDIT_FINDS = "AUDIT_FINDS"
    AUDIT_BLOCKS = "AUDIT_BLOCKS"
    DEPLOY_INCLUDES = "DEPLOY_INCLUDES"
    ROLLBACK_REVERSES = "ROLLBACK_REVERSES"
    TRANSITION_CAUSED_BY = "TRANSITION_CAUSED_BY"


NAV_BEARING_OBJECTS = {
    ObjectType.NAV_SURFACE.value,
    ObjectType.ATTRIBUTION_RUN.value,
    ObjectType.PORTFOLIO_SNAPSHOT.value,
    ObjectType.VALIDATION_RUN.value,
}

DATED_OBJECTS = {
    ObjectType.NAV_SURFACE.value,
    ObjectType.ATTRIBUTION_RUN.value,
    ObjectType.EXPOSURE_SNAPSHOT.value,
    ObjectType.REGIME_ASSESSMENT.value,
    ObjectType.PROMOTION_ASSESSMENT.value,
    ObjectType.PORTFOLIO_SNAPSHOT.value,
    ObjectType.VALIDATION_RUN.value,
    ObjectType.STABILITY_ASSESSMENT.value,
    ObjectType.FRAGILITY_ASSESSMENT.value,
    ObjectType.CONFIDENCE_ASSESSMENT.value,
    ObjectType.TEMPORAL_WINDOW.value,
}

STRATEGY_SCOPED_OBJECTS = {
    ObjectType.STRATEGY.value,
    ObjectType.NAV_SURFACE.value,
    ObjectType.ATTRIBUTION_RUN.value,
    ObjectType.EXPOSURE_SNAPSHOT.value,
    ObjectType.PROMOTION_ASSESSMENT.value,
    ObjectType.PORTFOLIO_SNAPSHOT.value,
    ObjectType.VALIDATION_RUN.value,
    ObjectType.STABILITY_ASSESSMENT.value,
    ObjectType.FRAGILITY_ASSESSMENT.value,
}

RAW_ROOT_OBJECTS = {
    ObjectType.STRATEGY.value,
    ObjectType.GOVERNANCE_FR.value,
    ObjectType.AUDIT_FINDING.value,
    ObjectType.RESEARCH_ARTIFACT.value,
    ObjectType.TEMPORAL_WINDOW.value,
}
