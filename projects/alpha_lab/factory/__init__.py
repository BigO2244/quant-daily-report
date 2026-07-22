"""Research-only contracts and evidence persistence for Caerus Alpha Lab.

This package deliberately has no dependency on broker, execution, scheduler,
allocation, or production runtime modules.
"""

from .canonical import canonical_hash, canonical_json
from .contracts import (
    ExperimentDesign,
    HypothesisClassification,
    HypothesisManifest,
    Observation,
    RunManifest,
    RunState,
)
from .errors import (
    ContractValidationError,
    EventStoreIntegrityError,
    ProviderNotReadyError,
    ResearchBoundaryError,
)
from .providers import (
    ProviderGateResult,
    ProviderReadiness,
    ProviderRequirement,
    ProviderStatus,
    evaluate_provider_readiness,
    require_provider_ready,
)
from .store import AppendOnlyJSONLEventStore, EventRecord

__all__ = [
    "AppendOnlyJSONLEventStore",
    "ContractValidationError",
    "EventRecord",
    "EventStoreIntegrityError",
    "ExperimentDesign",
    "HypothesisClassification",
    "HypothesisManifest",
    "Observation",
    "ProviderGateResult",
    "ProviderNotReadyError",
    "ProviderReadiness",
    "ProviderRequirement",
    "ProviderStatus",
    "ResearchBoundaryError",
    "RunManifest",
    "RunState",
    "canonical_hash",
    "canonical_json",
    "evaluate_provider_readiness",
    "require_provider_ready",
]
