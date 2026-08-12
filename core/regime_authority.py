"""Deterministic regime persistence and emergency-risk authority.

This module does not select securities or allocate capital.  It gives the
orchestrator one explicit answer about whether the previously authorized regime
persists, a confirmed normal transition is allowed, or an acute risk signal
must veto new exposure immediately.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RegimeAuthorityDecision:
    previous_state: str
    observed_state: str
    effective_state: str
    action: str
    reason_code: str
    confidence: float
    bars_in_state: int
    consecutive_observations: int
    minimum_dwell_bars: int
    confirmation_bars: int
    emergency_response: bool
    risk_veto_buys: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def decide_regime_authority(
    *,
    previous_state: str,
    observed_state: str,
    confidence: float,
    bars_in_state: int,
    consecutive_observations: int,
    acute_risk: bool = False,
    minimum_dwell_bars: int = 5,
    confirmation_bars: int = 2,
    confidence_threshold: float = 0.60,
) -> RegimeAuthorityDecision:
    previous = str(previous_state or "UNKNOWN").strip().upper()
    observed = str(observed_state or previous).strip().upper()
    try:
        confidence_value = float(confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError("regime confidence must be numeric") from exc
    if not math.isfinite(confidence_value) or not 0.0 <= confidence_value <= 1.0:
        raise ValueError("regime confidence must be within [0, 1]")
    if min(bars_in_state, consecutive_observations) < 0:
        raise ValueError("regime persistence counters cannot be negative")
    if min(minimum_dwell_bars, confirmation_bars) <= 0:
        raise ValueError("regime persistence thresholds must be positive")

    if acute_risk:
        return RegimeAuthorityDecision(
            previous_state=previous,
            observed_state=observed,
            effective_state="EMERGENCY_RISK_OFF",
            action="EMERGENCY_RISK_RESPONSE",
            reason_code="acute_market_risk_bypasses_hysteresis",
            confidence=confidence_value,
            bars_in_state=int(bars_in_state),
            consecutive_observations=int(consecutive_observations),
            minimum_dwell_bars=int(minimum_dwell_bars),
            confirmation_bars=int(confirmation_bars),
            emergency_response=True,
            risk_veto_buys=True,
        )
    if observed == previous:
        action, reason, effective = "PERSIST", "regime_unchanged", previous
    elif confidence_value < confidence_threshold:
        action, reason, effective = "PERSIST", "regime_confidence_below_threshold", previous
    elif bars_in_state < minimum_dwell_bars:
        action, reason, effective = "PERSIST", "regime_minimum_dwell_not_met", previous
    elif consecutive_observations < confirmation_bars:
        action, reason, effective = "PERSIST", "regime_confirmation_not_met", previous
    else:
        action, reason, effective = "CONFIRMED_TRANSITION", "regime_hysteresis_confirmed", observed
    emergency_persists = effective == "EMERGENCY_RISK_OFF"
    return RegimeAuthorityDecision(
        previous_state=previous,
        observed_state=observed,
        effective_state=effective,
        action=action,
        reason_code=reason,
        confidence=confidence_value,
        bars_in_state=int(bars_in_state),
        consecutive_observations=int(consecutive_observations),
        minimum_dwell_bars=int(minimum_dwell_bars),
        confirmation_bars=int(confirmation_bars),
        emergency_response=emergency_persists,
        risk_veto_buys=emergency_persists,
    )
