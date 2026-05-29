"""Read-only research analytical modules used by the MCP tools.

These functions never write to disk, never call external services, and never
import the execution path. They consume artifacts produced by other parts of
the system (timing replay outputs, VIX regime history) and return structured
joins for the MCP tool layer to surface.
"""

from research_registry.research.capabilities import (
    CAPABILITY_REGISTRY,
    ArtifactStatus,
    Capability,
    ClassificationResult,
    available_intents,
    capability_summary,
    check_artifacts,
    classify_question,
    closest_capabilities,
)
from research_registry.research.timing_regime import (
    INSUFFICIENT_SAMPLE_THRESHOLD,
    JoinedDay,
    RegimeAggregate,
    RegimeHistoryFormatError,
    TimingDay,
    aggregate_by_regime,
    answer_timing_by_regime_question,
    join_timing_to_regime,
    load_timing_summaries,
    load_vix_regime_history,
    select_timing_run,
)

__all__ = [
    "ArtifactStatus",
    "CAPABILITY_REGISTRY",
    "Capability",
    "ClassificationResult",
    "INSUFFICIENT_SAMPLE_THRESHOLD",
    "JoinedDay",
    "RegimeAggregate",
    "RegimeHistoryFormatError",
    "TimingDay",
    "aggregate_by_regime",
    "answer_timing_by_regime_question",
    "available_intents",
    "capability_summary",
    "check_artifacts",
    "classify_question",
    "closest_capabilities",
    "join_timing_to_regime",
    "load_timing_summaries",
    "load_vix_regime_history",
    "select_timing_run",
]
