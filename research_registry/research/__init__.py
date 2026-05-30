"""Read-only research analytical modules used by the MCP tools.

These functions never write to disk, never call external services, and never
import the execution path. They consume artifacts produced by other parts of
the system (timing replay outputs, VIX regime history) and return structured
joins for the MCP tool layer to surface.
"""

from research_registry.research.attribution import (
    AttributionAnswer,
    analyse_attribution,
    attribution_summary_to_dict,
    select_latest_attribution_date,
)
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
from research_registry.research.promotion_readiness import (
    MIN_VALID_OBSERVATION_WINDOWS,
    StrategyPromotionReadinessAnswer,
    assess_strategy_readiness,
    strategy_promotion_readiness_to_dict,
)
from research_registry.research.strategy_behavior_differentiation import (
    BehaviorDifferentiationAnswer,
    analyse_behavior_differentiation,
    behavior_differentiation_to_dict,
)
from research_registry.research.strategy_differentiation import (
    StrategyDifferentiationAnswer,
    analyse_strategy_differentiation,
    differentiation_to_dict,
)
from research_registry.research.stable_window_evaluation import (
    INSUFFICIENT_SAMPLE_THRESHOLD as STABLE_WINDOW_INSUFFICIENT_SAMPLE,
    StableWindowAnswer,
    evaluate_stable_windows,
    stable_window_to_dict,
)
from research_registry.research.shadow_comparison import (
    KNOWN_STRATEGY_NAMES,
    ShadowComparisonAnswer,
    compare_shadow_strategies,
    parse_strategy_names,
    shadow_comparison_to_dict,
    strategy_slug,
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
from research_registry.research.timing_summary import (
    MIN_DAYS_FOR_RECOMMENDATION,
    TimingSummaryAnswer,
    parse_offset_highlights,
    summarise_timing,
    timing_summary_to_dict,
)

__all__ = [
    "ArtifactStatus",
    "AttributionAnswer",
    "CAPABILITY_REGISTRY",
    "Capability",
    "ClassificationResult",
    "INSUFFICIENT_SAMPLE_THRESHOLD",
    "MIN_VALID_OBSERVATION_WINDOWS",
    "STABLE_WINDOW_INSUFFICIENT_SAMPLE",
    "BehaviorDifferentiationAnswer",
    "StableWindowAnswer",
    "StrategyDifferentiationAnswer",
    "StrategyPromotionReadinessAnswer",
    "JoinedDay",
    "KNOWN_STRATEGY_NAMES",
    "MIN_DAYS_FOR_RECOMMENDATION",
    "RegimeAggregate",
    "RegimeHistoryFormatError",
    "ShadowComparisonAnswer",
    "TimingDay",
    "TimingSummaryAnswer",
    "aggregate_by_regime",
    "analyse_attribution",
    "analyse_behavior_differentiation",
    "analyse_strategy_differentiation",
    "answer_timing_by_regime_question",
    "assess_strategy_readiness",
    "attribution_summary_to_dict",
    "behavior_differentiation_to_dict",
    "differentiation_to_dict",
    "available_intents",
    "capability_summary",
    "check_artifacts",
    "classify_question",
    "closest_capabilities",
    "compare_shadow_strategies",
    "evaluate_stable_windows",
    "join_timing_to_regime",
    "load_timing_summaries",
    "load_vix_regime_history",
    "parse_offset_highlights",
    "parse_strategy_names",
    "select_latest_attribution_date",
    "select_timing_run",
    "shadow_comparison_to_dict",
    "stable_window_to_dict",
    "strategy_promotion_readiness_to_dict",
    "strategy_slug",
    "summarise_timing",
    "timing_summary_to_dict",
]
