"""Pure implementations of the four frozen signal compositions.

Cross-sectional ranks are inputs because they must be calculated within the
frozen point-in-time eligible population.  These functions reject missing,
non-finite, or out-of-range ranks instead of imputing them.
"""

from __future__ import annotations

import math
from typing import Iterable, Literal, Tuple

from projects.alpha_lab.factory.errors import ContractValidationError


def _rank(value: float, field_name: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractValidationError("{} must be finite".format(field_name))
    numeric = float(value)
    if numeric < 0.0 or numeric > 1.0:
        raise ContractValidationError("{} must be in [0, 1]".format(field_name))
    return numeric


def earnings_revision_score(
    *,
    breadth_rank: float,
    median_eps_revision_rank: float,
    revenue_consensus_change_rank: float,
    positive_dispersion_reduction_rank: float,
) -> float:
    """Return the HYP-2026-002 frozen 40/30/15/15 composite."""

    return (
        0.40 * _rank(breadth_rank, "breadth_rank")
        + 0.30 * _rank(median_eps_revision_rank, "median_eps_revision_rank")
        + 0.15
        * _rank(revenue_consensus_change_rank, "revenue_consensus_change_rank")
        + 0.15
        * _rank(
            positive_dispersion_reduction_rank,
            "positive_dispersion_reduction_rank",
        )
    )


def insider_conviction_score(
    *,
    distinct_buyer_count_rank: float,
    purchase_value_to_market_cap_rank: float,
    average_role_score: float,
) -> float:
    """Return the HYP-2026-003 frozen 50/30/20 composite."""

    return (
        0.50 * _rank(distinct_buyer_count_rank, "distinct_buyer_count_rank")
        + 0.30
        * _rank(
            purchase_value_to_market_cap_rank,
            "purchase_value_to_market_cap_rank",
        )
        + 0.20 * _rank(average_role_score, "average_role_score")
    )


TradeSide = Literal["BUYER", "SELLER", "AMBIGUOUS"]


def classify_option_trade(*, price: float, bid: float, ask: float) -> TradeSide:
    """Apply the HYP-2026-004 midpoint plus/minus 10% spread rule."""

    values = {"price": price, "bid": bid, "ask": ask}
    for field_name, value in values.items():
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ContractValidationError("{} must be finite".format(field_name))
    if bid <= 0 or ask <= bid:
        raise ContractValidationError("option NBBO must have positive bid and ask > bid")
    midpoint = (float(bid) + float(ask)) / 2.0
    threshold = 0.10 * (float(ask) - float(bid))
    if price >= midpoint + threshold:
        return "BUYER"
    if price <= midpoint - threshold:
        return "SELLER"
    return "AMBIGUOUS"


def options_information_score(
    *,
    signed_delta_imbalance_rank: float,
    strike_displacement_rank: float,
    call_minus_put_iv_change_rank: float,
) -> float:
    """Return the HYP-2026-004 frozen 50/30/20 composite."""

    return (
        0.50
        * _rank(signed_delta_imbalance_rank, "signed_delta_imbalance_rank")
        + 0.30 * _rank(strike_displacement_rank, "strike_displacement_rank")
        + 0.20
        * _rank(call_minus_put_iv_change_rank, "call_minus_put_iv_change_rank")
    )


def supply_chain_raw_score(
    shocks: Iterable[Tuple[float, float, float]],
) -> float:
    """Return the HYP-2026-005 one-hop dependency-weighted raw supplier score.

    Each tuple is ``(customer_shock, dependency_fraction, confidence)``.
    Dependency is capped at 50%; the frozen minimum 10% eligibility is enforced.
    """

    total = 0.0
    count = 0
    positive_count = 0
    for customer_shock, dependency, confidence in shocks:
        values = (customer_shock, dependency, confidence)
        if not all(isinstance(value, (int, float)) for value in values):
            raise ContractValidationError("supply-chain score inputs must be numeric")
        if not all(math.isfinite(float(value)) for value in values):
            raise ContractValidationError("supply-chain score inputs must be finite")
        if customer_shock == 0:
            raise ContractValidationError("customer shock cannot be zero")
        if dependency < 0.10 or dependency > 1.0:
            raise ContractValidationError("dependency must be in [0.10, 1.0]")
        if confidence <= 0.0 or confidence > 1.0:
            raise ContractValidationError("confidence must be in (0, 1]")
        total += float(customer_shock) * min(float(dependency), 0.50) * float(confidence)
        if customer_shock > 0:
            positive_count += 1
        count += 1
    if count == 0:
        raise ContractValidationError("at least one eligible one-hop edge is required")
    if positive_count == 0:
        raise ContractValidationError("at least one positive customer shock is required")
    if total <= 0:
        raise ContractValidationError(
            "eligible negative customer shocks offset the positive aggregate"
        )
    return total
