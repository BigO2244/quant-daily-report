"""Governed PAPER target-attainment policy helpers."""

from __future__ import annotations

import math
from typing import Any, Mapping


SCHEMA_VERSION = "caerus.target_attainment_policy.v1"
WHOLE_SHARE_MODE = "WHOLE_SHARES"
FRACTIONAL_SHARE_MODE = "FRACTIONAL_SHARES"
SUPPORTED_SHARE_MODES = frozenset({WHOLE_SHARE_MODE, FRACTIONAL_SHARE_MODE})
FIRST_CLEAN_EPOCH = "FIRST_CLEAN_POST_FIX_PAPER_RUN"
ACCEPTED_TARGET_STATUSES = frozenset(
    {"OK_TARGET_ATTAINED", "OK_NEAREST_FEASIBLE"}
)


class TargetAttainmentPolicyError(ValueError):
    """Raised when a target-attainment policy is malformed."""


def _finite(value: Any, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise TargetAttainmentPolicyError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number):
        raise TargetAttainmentPolicyError(f"{field_name} must be finite")
    return number


def validate_target_attainment_policy(
    payload: Mapping[str, Any] | None,
    *,
    expected_target_cash_weight: float | None = None,
) -> dict[str, Any]:
    """Return a normalized policy or fail closed.

    The policy is PAPER-only. Five percent remains the portfolio target; the
    lower cash value is a hard execution boundary. Whole-share mode requires a
    sealed nearest-feasible proof. Fractional mode requires direct post-trade
    target attainment and therefore must not claim a nearest-feasible waiver.
    """

    if not isinstance(payload, Mapping) or not payload:
        raise TargetAttainmentPolicyError("target-attainment policy is required")
    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        raise TargetAttainmentPolicyError("unsupported target-attainment policy schema")
    if str(payload.get("account_scope") or "").upper() != "PAPER":
        raise TargetAttainmentPolicyError("target-attainment policy must be PAPER-only")
    share_mode = str(payload.get("share_mode") or "").upper()
    if share_mode not in SUPPORTED_SHARE_MODES:
        raise TargetAttainmentPolicyError(
            "target-attainment policy share_mode is unsupported"
        )
    nearest_feasible_required = payload.get("nearest_feasible_required")
    if type(nearest_feasible_required) is not bool:
        raise TargetAttainmentPolicyError(
            "nearest_feasible_required must be boolean"
        )
    if share_mode == WHOLE_SHARE_MODE and not nearest_feasible_required:
        raise TargetAttainmentPolicyError(
            "whole-share target attainment requires a nearest-feasible proof"
        )
    if share_mode == FRACTIONAL_SHARE_MODE and nearest_feasible_required:
        raise TargetAttainmentPolicyError(
            "fractional target attainment cannot use a nearest-feasible waiver"
        )
    if not bool(payload.get("strict_green_propagation")):
        raise TargetAttainmentPolicyError("strict green propagation must be enabled")
    if str(payload.get("comparison_epoch_policy") or "").upper() != FIRST_CLEAN_EPOCH:
        raise TargetAttainmentPolicyError("unsupported live-vs-shadow comparison epoch policy")

    target_cash = _finite(payload.get("target_cash_weight"), "target_cash_weight")
    minimum_cash = _finite(payload.get("minimum_cash_weight"), "minimum_cash_weight")
    tolerance = _finite(payload.get("fixed_drift_tolerance"), "fixed_drift_tolerance")
    if not 0.0 <= minimum_cash <= target_cash < 1.0:
        raise TargetAttainmentPolicyError(
            "minimum_cash_weight must be between zero and target_cash_weight"
        )
    if not 0.0 <= tolerance < 1.0:
        raise TargetAttainmentPolicyError("fixed_drift_tolerance must be in [0, 1)")
    if expected_target_cash_weight is not None and not math.isclose(
        target_cash,
        float(expected_target_cash_weight),
        abs_tol=1e-12,
    ):
        raise TargetAttainmentPolicyError(
            "policy target_cash_weight does not match the approved cash target"
        )

    return {
        "schema_version": SCHEMA_VERSION,
        "account_scope": "PAPER",
        "share_mode": share_mode,
        "target_cash_weight": target_cash,
        "minimum_cash_weight": minimum_cash,
        "fixed_drift_tolerance": tolerance,
        "nearest_feasible_required": bool(nearest_feasible_required),
        "comparison_epoch_policy": FIRST_CLEAN_EPOCH,
        "strict_green_propagation": True,
        "owner_approved_at": str(payload.get("owner_approved_at") or ""),
    }


def target_status_passes(status: Any) -> bool:
    return str(status or "").strip().upper() in ACCEPTED_TARGET_STATUSES
