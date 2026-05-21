"""Typed v1 payload schemas for canonical Caerus research objects."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


class PayloadModel:
    def to_data(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class TemporalWindow(PayloadModel):
    start: str
    end: str
    timezone: str = "America/New_York"
    window_type: str = "TRADE_DATE"


@dataclass(frozen=True)
class LineageNode(PayloadModel):
    node_id: str
    object_ref: str
    parent_refs: list[str] = field(default_factory=list)
    transformation_chain_hash: str = ""


@dataclass(frozen=True)
class Strategy(PayloadModel):
    strategy_id: str
    display_name: str
    promotion_state: str
    governance_classification: str
    sleeve_composition: list[dict[str, Any]] = field(default_factory=list)
    regime_sensitivity: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NAVSurface(PayloadModel):
    surface_id: str
    nav_surface_type: str
    confidence_level: str
    execution_realism: str
    point_in_time_validity: str
    source_path: str
    strategy_ref: str
    temporal_window: dict[str, Any]
    chain_status: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AttributionRun(PayloadModel):
    run_id: str
    trade_date: str
    strategy_ref: str
    nav_surface_ref: str
    contribution_report: str
    factor_exposure: str
    regime_analysis: str
    concentration_analysis: str
    decision_attribution: str | None
    confidence_level: str
    governance_state: str


@dataclass(frozen=True)
class ExposureSnapshot(PayloadModel):
    snapshot_id: str
    trade_date: str
    strategy_ref: str
    exposure_summary: dict[str, Any]
    factor_risk_flags: list[str]
    concentration_monitor: dict[str, Any]
    exposure_drift_summary: dict[str, Any]
    temporal_validity: dict[str, Any]
    confidence_level: str


@dataclass(frozen=True)
class RegimeAssessment(PayloadModel):
    assessment_id: str
    trade_date: str
    dimensions: dict[str, str]
    regime_performance_breakdown: dict[str, Any]
    regime_fragility_report: dict[str, Any]
    regime_exposure_matrix: dict[str, Any]
    regime_transition_analysis: dict[str, Any]
    temporal_validity: dict[str, Any]
    confidence_level: str


@dataclass(frozen=True)
class AuditFinding(PayloadModel):
    finding_id: str
    audit_type: str
    severity: str
    finding_summary: str
    affected_objects: list[str]
    remediation_state: str
    evidence_refs: list[str]
    discovered_date: str


@dataclass(frozen=True)
class GovernanceFR(PayloadModel):
    fr_id: str
    category: str
    status: str
    blast_radius: str
    observation_criteria: str
    rollback_reference: str
    validation_summary: str
    affected_objects: list[str]
    deployed_date: str | None = None
    dependencies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PromotionAssessment(PayloadModel):
    assessment_id: str
    strategy_ref: str
    current_state: str
    target_state: str
    gate_results: list[dict[str, Any]]
    blocking_findings: list[str]
    confidence_assessment: dict[str, Any]
    governance_readiness: bool
    assessed_date: str


@dataclass(frozen=True)
class PortfolioSnapshot(PayloadModel):
    snapshot_id: str
    trade_date: str
    strategy_ref: str
    holdings: list[dict[str, Any]]
    weights: dict[str, float]
    cash_weight: float
    nav_surface_ref: str
    source_path: str
    temporal_validity: dict[str, Any]


@dataclass(frozen=True)
class ResearchArtifact(PayloadModel):
    artifact_id: str
    artifact_type: str
    source_uri: str
    content_hash: str
    summary: str
    produced_by: str


@dataclass(frozen=True)
class ValidationRun(PayloadModel):
    run_id: str
    hypothesis_ref: str | None
    run_type: str
    date_range: dict[str, Any]
    metrics: dict[str, float]
    nav_surface_ref: str
    confidence_level: str
    artifacts: list[str]


@dataclass(frozen=True)
class StabilityAssessment(PayloadModel):
    assessment_id: str
    strategy_ref: str
    window: dict[str, Any]
    regime_dependency_flags: list[str]
    fragility_classification: str
    beta_amplification: dict[str, Any]
    concentration_amplification: dict[str, Any]
    confidence_level: str


@dataclass(frozen=True)
class FragilityAssessment(PayloadModel):
    assessment_id: str
    strategy_ref: str
    window: dict[str, Any]
    fragility_flags: list[str]
    fragility_score: float
    blocking_conditions: list[str]
    confidence_level: str


@dataclass(frozen=True)
class ConfidenceAssessment(PayloadModel):
    assessment_id: str
    assesses: str
    assessed_confidence: str
    contributing_factors: list[str]
    downgrade_reasons: list[str]
    assessed_date: str
    temporal_validity: dict[str, Any]
    assessor: str
