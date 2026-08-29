"""Standard point-in-time regime diagnostics for Alpha Lab evaluators.

Regime results are secondary diagnostics.  They may describe where a frozen
technique appears useful, but they cannot turn a failed unconditional test into
a passing alpha claim or authorize dynamic allocation.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from typing import Any, Dict, Iterable, Mapping

from projects.alpha_lab.factory.canonical import parse_datetime
from projects.alpha_lab.factory.errors import ContractValidationError


SCHEMA_VERSION = "caerus_alpha_lab_regime_diagnostics_v1"
REGIME_LABELS = (
    "bull_trend",
    "bear_trend",
    "high_vol",
    "low_vol",
    "panic",
    "recovery",
    "neutral",
)
MIN_REGIME_OBSERVATIONS = 30
MIN_TOTAL_OBSERVATIONS = 252


def _finite_return(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ContractValidationError("{} must be numeric".format(field)) from exc
    if not math.isfinite(number) or number < -1.0:
        raise ContractValidationError(
            "{} must be finite and no less than -1".format(field)
        )
    return number


def _confidence(count: int, high_threshold: int) -> str:
    if count >= high_threshold:
        return "HIGH"
    if count >= 10:
        return "MEDIUM"
    return "LOW"


def summarize_regime_observations(
    observations: Iterable[Mapping[str, Any]],
    *,
    minimum_regime_observations: int = MIN_REGIME_OBSERVATIONS,
    minimum_total_observations: int = MIN_TOTAL_OBSERVATIONS,
) -> Dict[str, Any]:
    """Validate and summarize frozen observation units by known-at-decision regime.

    Required fields per row:

    ``observation_id``
        Unique issuer-event, rebalance-date, or other frozen inference unit.
    ``decision_at``
        Aware timestamp at which the technique made its decision.
    ``regime_available_at``
        Aware timestamp proving the regime label was available.
    ``return_start_at`` / ``return_end_at``
        Aware timestamps for the evaluated return interval.
    ``regime``
        One of the canonical seven regime labels.
    ``candidate_return`` / ``benchmark_return``
        Frozen-horizon simple returns.
    """

    if minimum_regime_observations < 1 or minimum_total_observations < 1:
        raise ContractValidationError("minimum observation thresholds must be positive")

    rows = []
    seen_ids: set[str] = set()
    for raw in observations:
        observation_id = str(raw.get("observation_id") or "").strip()
        if not observation_id:
            raise ContractValidationError("observation_id is required")
        if observation_id in seen_ids:
            raise ContractValidationError(
                "duplicate observation_id: {}".format(observation_id)
            )
        seen_ids.add(observation_id)

        regime = str(raw.get("regime") or "").strip()
        if regime not in REGIME_LABELS:
            raise ContractValidationError("unknown regime label: {}".format(regime))
        decision_at = parse_datetime(str(raw.get("decision_at") or ""))
        regime_available_at = parse_datetime(
            str(raw.get("regime_available_at") or "")
        )
        return_start_at = parse_datetime(str(raw.get("return_start_at") or ""))
        return_end_at = parse_datetime(str(raw.get("return_end_at") or ""))
        if regime_available_at > decision_at:
            raise ContractValidationError(
                "regime label was not available at decision time"
            )
        if return_start_at < decision_at:
            raise ContractValidationError(
                "return interval starts before the frozen decision"
            )
        if return_end_at <= return_start_at:
            raise ContractValidationError(
                "return interval must end after it starts"
            )

        candidate_return = _finite_return(
            raw.get("candidate_return"), "candidate_return"
        )
        benchmark_return = _finite_return(
            raw.get("benchmark_return"), "benchmark_return"
        )
        rows.append(
            {
                "observation_id": observation_id,
                "regime": regime,
                "active_return": candidate_return - benchmark_return,
            }
        )

    by_regime: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        by_regime[row["regime"]].append(row["active_return"])

    regimes = {}
    for label in REGIME_LABELS:
        values = by_regime[label]
        count = len(values)
        if not values:
            regimes[label] = {
                "regime": label,
                "observation_count": 0,
                "mean_active_return": None,
                "median_active_return": None,
                "active_return_volatility": None,
                "positive_active_return_rate": None,
                "confidence": "LOW",
                "decision_grade_for_regime": False,
                "reason_codes": ["no_observations"],
            }
            continue
        decision_grade = count >= minimum_regime_observations
        regimes[label] = {
            "regime": label,
            "observation_count": count,
            "mean_active_return": statistics.fmean(values),
            "median_active_return": statistics.median(values),
            "active_return_volatility": (
                statistics.stdev(values) if count > 1 else None
            ),
            "positive_active_return_rate": sum(value > 0 for value in values)
            / count,
            "confidence": _confidence(count, minimum_regime_observations),
            "decision_grade_for_regime": decision_grade,
            "reason_codes": (
                ["ok"]
                if decision_grade
                else ["regime_observations_below_{}".format(
                    minimum_regime_observations
                )]
            ),
        }

    total = len(rows)
    active_values = [row["active_return"] for row in rows]
    eligible_regimes = [
        label
        for label, payload in regimes.items()
        if payload["decision_grade_for_regime"]
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "classification": "RESEARCH_ONLY_NON_EXECUTIONAL",
        "observation_count": total,
        "minimum_total_observations": minimum_total_observations,
        "minimum_regime_observations": minimum_regime_observations,
        "total_coverage_sufficient": total >= minimum_total_observations,
        "overall_mean_active_return": (
            statistics.fmean(active_values) if active_values else None
        ),
        "regime_labels": list(REGIME_LABELS),
        "regimes": regimes,
        "decision_grade_regimes": eligible_regimes,
        "regime_selection_coverage_ready": bool(
            total >= minimum_total_observations and eligible_regimes
        ),
        "regime_selection_claim_permitted": False,
        "separate_regime_interaction_holdout_required": True,
        "primary_alpha_claim_permitted_from_regime_slice": False,
        "allocation_change_performed": False,
        "promotion_performed": False,
        "trading_behavior_changed": False,
        "interpretation": (
            "Regime slices are secondary diagnostics. A regime result requires "
            "a separately frozen holdout before it may inform allocation."
        ),
        "multiple_testing_boundary": (
            "Do not reinterpret technique-by-regime cells as independent primary "
            "hypotheses; apply the frozen family correction and a separate "
            "regime-interaction holdout."
        ),
    }
    return payload
