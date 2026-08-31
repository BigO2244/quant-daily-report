"""Fail-closed invariants for governed PAPER full-account target execution."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _close(left: Any, right: Any, *, tolerance: float = 0.01) -> bool:
    lhs = _finite(left)
    rhs = _finite(right)
    return bool(
        lhs is not None
        and rhs is not None
        and math.isclose(lhs, rhs, rel_tol=1e-9, abs_tol=tolerance)
    )


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def governed_full_account_policy(payload: Any) -> bool:
    """Return whether a valid policy requires governed full-account PAPER sizing."""

    if not isinstance(payload, Mapping):
        return False
    try:
        from core.target_attainment_policy import validate_target_attainment_policy

        validate_target_attainment_policy(payload)
    except (TypeError, ValueError):
        return False
    return True


def full_account_plan_invariant_error(plan: Any) -> str | None:
    """Validate that a governed exact plan was sized from one full-account NAV.

    The exact executor calls this before creating a WAL intent or submitting an
    order. Plans without a governed target-attainment policy remain outside this
    specialized contract. Whole-share plans additionally require their sealed
    integer proof; fractional plans require a matching fractional execution pin.
    """

    risk_state = plan.risk_state if isinstance(plan.risk_state, Mapping) else {}
    trade_meta = (
        risk_state.get("trade_meta")
        if isinstance(risk_state.get("trade_meta"), Mapping)
        else {}
    )
    proof = (
        trade_meta.get("whole_share_feasibility")
        if isinstance(trade_meta.get("whole_share_feasibility"), Mapping)
        else {}
    )
    explicit_policy = (
        risk_state.get("target_attainment_policy")
        if isinstance(risk_state.get("target_attainment_policy"), Mapping)
        else None
    )
    proof_policy = proof.get("policy") if isinstance(proof, Mapping) else None
    policy = explicit_policy or proof_policy
    if policy is None:
        return None
    if not governed_full_account_policy(policy):
        return "target_attainment_policy_invalid"
    from core.target_attainment_policy import (
        FRACTIONAL_SHARE_MODE,
        WHOLE_SHARE_MODE,
    )

    share_mode = str(policy.get("share_mode") or "").strip().upper()
    if explicit_policy is not None and proof_policy is not None:
        if _plain(explicit_policy) != _plain(proof_policy):
            return "target_attainment_policy_lineage_mismatch"
    if share_mode == WHOLE_SHARE_MODE:
        if str(proof.get("schema_version") or "") != "caerus.whole_share_feasibility.v1":
            return "whole_share_proof_schema_invalid"
        if str(proof.get("status") or "").strip().upper() != "PASS":
            return "whole_share_proof_status_invalid"
        proof_body = _plain(proof)
        proof_body.pop("proof_content_hash", None)
        try:
            computed_proof_hash = hashlib.sha256(
                json.dumps(
                    proof_body,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
        except (TypeError, ValueError):
            return "whole_share_proof_hash_invalid"
        if str(proof.get("proof_content_hash") or "") != computed_proof_hash:
            return "whole_share_proof_hash_invalid"
    elif share_mode == FRACTIONAL_SHARE_MODE:
        if plan.constraints.get("allow_fractional") is not True:
            return "fractional_policy_plan_pin_missing"
        if proof:
            return "fractional_policy_cannot_use_whole_share_proof"
    else:
        return "target_attainment_share_mode_invalid"

    nav_evidence = (
        risk_state.get("decision_nav_reconstruction")
        if isinstance(risk_state.get("decision_nav_reconstruction"), Mapping)
        else {}
    )
    if not nav_evidence:
        return "decision_nav_reconstruction_missing"
    authoritative_nav = _finite(nav_evidence.get("authoritative_account_nav"))
    if authoritative_nav is None or authoritative_nav <= 0.0:
        return "authoritative_account_nav_invalid"
    if not _close(plan.portfolio_nav, authoritative_nav):
        return "portfolio_nav_not_authoritative_account_nav"
    raw_planning_cap = nav_evidence.get("planning_equity_cap")
    if raw_planning_cap is not None:
        planning_cap = _finite(raw_planning_cap)
        if planning_cap is None or planning_cap + 0.01 < authoritative_nav:
            return "planning_equity_cap_reduces_authoritative_account_nav"
    if not _close(nav_evidence.get("planning_equity"), authoritative_nav):
        return "planning_equity_not_authoritative_account_nav"
    if not _close(nav_evidence.get("planning_cash"), plan.starting_cash):
        return "planning_cash_not_broker_starting_cash"
    if share_mode == WHOLE_SHARE_MODE and not _close(
        proof.get("equity_basis"), authoritative_nav
    ):
        return "whole_share_proof_equity_not_authoritative_account_nav"
    if not _close(plan.constraints.get("capital_cap_usd"), authoritative_nav):
        return "capital_cap_not_authoritative_account_nav"

    minimum_cash_weight = _finite(policy.get("minimum_cash_weight"))
    if minimum_cash_weight is None or minimum_cash_weight < 0.0:
        return "target_attainment_minimum_cash_weight_invalid"
    if float(plan.expected_posttrade_cash) + 0.01 < (
        authoritative_nav * minimum_cash_weight
    ):
        return "expected_posttrade_cash_below_governed_floor"

    if share_mode == FRACTIONAL_SHARE_MODE:
        for order in plan.orders:
            quantity = _finite(order.get("quantity"))
            if quantity is None or quantity <= 0.0:
                return "fractional_order_quantity_invalid"
        return None

    expected = {
        str(row.get("symbol") or "").strip().upper(): float(row.get("quantity") or 0.0)
        for row in plan.expected_posttrade_positions
    }
    allocation = proof.get("allocation")
    if not isinstance(allocation, (list, tuple)) or not allocation:
        return "whole_share_proof_allocation_missing"
    seen: set[str] = set()
    for row in allocation:
        if not isinstance(row, Mapping):
            return "whole_share_proof_allocation_malformed"
        symbol = str(row.get("symbol") or "").strip().upper()
        quantity = _finite(row.get("target_quantity"))
        if not symbol or symbol in seen or quantity is None or quantity < 0.0:
            return "whole_share_proof_allocation_malformed"
        seen.add(symbol)
        if not math.isclose(
            float(expected.get(symbol, 0.0)), quantity, rel_tol=0.0, abs_tol=1e-9
        ):
            return f"whole_share_quantity_not_exact_plan:{symbol}"
    return None
