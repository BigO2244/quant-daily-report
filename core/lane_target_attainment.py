"""Target-attainment evidence for the unified paper/live-pilot executor."""

from __future__ import annotations

import math
from typing import Any, Mapping


SCHEMA_VERSION = "caerus_lane_target_attainment_v2"
LEGACY_SCHEMA_VERSION = "caerus_lane_target_attainment_v1"


def _number(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def build_lane_target_attainment(
    *,
    plan: Mapping[str, Any],
    post_snapshot: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    run_id: str,
    trade_date: str,
    mode: str,
    dry_run: bool,
    drift_tolerance: float = 0.02,
    feasibility_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    package_payload = (
        plan.get("approved_execution_package")
        if isinstance(plan.get("approved_execution_package"), Mapping)
        else {}
    )
    package_target_rows = package_payload.get("approved_target_rows")
    target_rows = (
        package_target_rows
        if isinstance(package_target_rows, list)
        else plan.get("target_portfolio") or []
    )
    target = {
        str(row.get("symbol") or row.get("ticker") or "").strip().upper(): float(
            row.get("target_weight") or 0.0
        )
        for row in target_rows
        if isinstance(row, Mapping)
        and str(row.get("symbol") or row.get("ticker") or "").strip()
        and _number(row.get("target_weight")) is not None
    }
    target_cash = _number(package_payload.get("approved_cash_weight"))
    if target_cash is None:
        target_cash = _number(plan.get("cash_target_weight"))
    if target_cash is None:
        target_cash = max(0.0, 1.0 - sum(target.values()))

    account = (
        post_snapshot.get("account")
        if isinstance(post_snapshot.get("account"), Mapping)
        else {}
    )
    equity = _number(
        account.get("equity")
        or account.get("portfolio_value")
    )
    cash = _number(account.get("cash"))
    actual_values: dict[str, float] = {}
    actual_quantities: dict[str, float] = {}
    for row in post_snapshot.get("positions") or []:
        if not isinstance(row, Mapping):
            continue
        symbol = str(row.get("symbol") or row.get("ticker") or "").strip().upper()
        market_value = _number(row.get("market_value"))
        if symbol and market_value is not None:
            actual_values[symbol] = actual_values.get(symbol, 0.0) + market_value
        quantity = _number(
            row.get("qty")
            if row.get("qty") is not None
            else row.get("shares")
        )
        if symbol and quantity is not None:
            actual_quantities[symbol] = actual_quantities.get(symbol, 0.0) + quantity

    actual = (
        {symbol: value / equity for symbol, value in actual_values.items()}
        if equity is not None and equity > 0.0
        else {}
    )
    actual_cash = (
        cash / equity
        if cash is not None and equity is not None and equity > 0.0
        else None
    )
    symbols = sorted(set(target).union(actual))
    rows = [
        {
            "symbol": symbol,
            "target_weight": round(float(target.get(symbol, 0.0)), 10),
            "actual_weight": round(float(actual.get(symbol, 0.0)), 10),
            "actual_quantity": (
                round(float(actual_quantities[symbol]), 10)
                if symbol in actual_quantities
                else None
            ),
            "weight_drift": round(
                float(actual.get(symbol, 0.0) - target.get(symbol, 0.0)),
                10,
            ),
        }
        for symbol in symbols
    ]
    max_position_drift = (
        max(abs(float(row["weight_drift"])) for row in rows) if rows else 0.0
    )
    cash_drift = (
        actual_cash - target_cash if actual_cash is not None else None
    )
    recon_status = str(reconciliation.get("status") or "").strip().upper()
    package_constraints = (
        package_payload.get("constraints")
        if isinstance(package_payload, Mapping)
        and isinstance(package_payload.get("constraints"), Mapping)
        else {}
    )
    expected_package_hash = (
        str(package_payload.get("content_hash") or "")
        if isinstance(package_payload, Mapping)
        else ""
    )
    package_policy = package_constraints.get("target_attainment_policy")
    plan_policy = plan.get("target_attainment_policy")
    policy_payload = (
        package_policy
        if isinstance(package_policy, Mapping)
        else plan_policy
    )
    normalized_policy: dict[str, Any] | None = None
    policy_error: str | None = None
    if isinstance(policy_payload, Mapping) and policy_payload:
        try:
            from core.target_attainment_policy import validate_target_attainment_policy

            if (
                isinstance(plan_policy, Mapping)
                and plan_policy
                and not isinstance(package_policy, Mapping)
            ):
                raise ValueError(
                    "approved execution package omits target-attainment policy"
                )
            if (
                isinstance(package_policy, Mapping)
                and isinstance(plan_policy, Mapping)
                and dict(package_policy) != dict(plan_policy)
            ):
                raise ValueError(
                    "plan target-attainment policy diverges from approved package"
                )
            normalized_policy = validate_target_attainment_policy(
                policy_payload,
                expected_target_cash_weight=float(target_cash),
            )
        except Exception as exc:
            policy_error = str(exc)

    proof = dict(feasibility_evidence or {})
    proof_allocation = proof.get("allocation")
    proof_quantities = {
        str(row.get("symbol") or "").strip().upper(): _number(
            row.get("target_quantity")
        )
        for row in proof_allocation or []
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip()
    }
    proof_quantities = {
        symbol: float(quantity)
        for symbol, quantity in proof_quantities.items()
        if quantity is not None
    }
    quantity_mismatches = [
        {
            "symbol": symbol,
            "approved_quantity": round(float(quantity), 10),
            "actual_quantity": round(float(actual_quantities.get(symbol, 0.0)), 10),
        }
        for symbol, quantity in sorted(proof_quantities.items())
        if not math.isclose(
            float(actual_quantities.get(symbol, 0.0)),
            float(quantity),
            abs_tol=1e-6,
        )
    ]
    unapproved_symbols = sorted(
        symbol
        for symbol in set(actual_values).union(actual_quantities)
        if symbol not in target
        and (
            abs(float(actual_values.get(symbol, 0.0))) > 0.01
            or abs(float(actual_quantities.get(symbol, 0.0))) > 1e-9
        )
    )
    cash_floor = (
        float(normalized_policy["minimum_cash_weight"])
        if normalized_policy is not None
        else None
    )
    from core.whole_share_feasibility import whole_share_proof_content_hash

    proof_hash_valid = bool(
        proof
        and str(proof.get("proof_content_hash") or "")
        == whole_share_proof_content_hash(proof)
    )
    proof_lineage_valid = bool(
        expected_package_hash
        and str(proof.get("approved_execution_package_hash") or "")
        == expected_package_hash
    )
    market_values_complete = all(
        symbol in actual_values or math.isclose(quantity, 0.0, abs_tol=1e-9)
        for symbol, quantity in proof_quantities.items()
    )
    proof_valid = bool(
        normalized_policy is not None
        and policy_error is None
        and proof.get("schema_version") == "caerus.whole_share_feasibility.v1"
        and str(proof.get("status") or "").upper() == "PASS"
        and set(proof_quantities) == set(target)
        and proof_hash_valid
        and proof_lineage_valid
    )
    nearest_feasible_verified = bool(
        proof_valid
        and not quantity_mismatches
        and not unapproved_symbols
        and market_values_complete
        and actual_cash is not None
        and cash_floor is not None
        and actual_cash + 1e-12 >= cash_floor
    )
    if dry_run:
        status = "DRY_RUN_NOT_APPLICABLE"
        reason = "dry_run_has_no_posttrade_target_attainment"
    elif equity is None or cash is None:
        status = "UNKNOWN_INSUFFICIENT_BROKER_SNAPSHOT"
        reason = "posttrade_equity_or_cash_missing"
    elif recon_status != "CLEAN":
        status = "FAIL_EXECUTION_INCOMPLETE"
        reason = f"reconciliation_{recon_status.lower() or 'unknown'}"
    elif policy_error:
        status = "FAIL_POLICY_INVALID"
        reason = f"target_attainment_policy_invalid:{policy_error}"
    elif normalized_policy is not None and not proof_valid:
        status = "FAIL_FEASIBILITY_PROOF_INVALID"
        reason = "governed_whole_share_feasibility_proof_missing_or_incomplete"
    elif normalized_policy is not None and not nearest_feasible_verified:
        status = "FAIL_NEAREST_FEASIBLE_MISMATCH"
        reason = "posttrade_does_not_exactly_match_proven_whole_share_allocation"
    elif (
        max_position_drift > float(drift_tolerance)
        or cash_drift is None
        or abs(cash_drift) > float(drift_tolerance)
    ):
        if nearest_feasible_verified:
            status = "OK_NEAREST_FEASIBLE"
            reason = "posttrade_matches_proven_nearest_feasible_whole_share_allocation"
        else:
            status = "WARN_TARGET_DRIFT"
            reason = "posttrade_weights_outside_tolerance_without_exact_feasibility_proof"
    else:
        status = "OK_TARGET_ATTAINED"
        reason = "posttrade_weights_within_tolerance"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "trade_date": trade_date,
        "account_scope": str(mode or "").strip().upper(),
        "status": status,
        "reason_code": reason,
        "drift_tolerance": float(drift_tolerance),
        "target_cash_weight": round(float(target_cash), 10),
        "achieved_cash_weight": (
            round(float(actual_cash), 10) if actual_cash is not None else None
        ),
        "cash_target_drift": (
            round(float(cash_drift), 10) if cash_drift is not None else None
        ),
        "max_absolute_position_weight_drift": round(max_position_drift, 10),
        "target_equity_name_count": len(target),
        "actual_equity_name_count": len(actual),
        "posttrade_equity": equity,
        "posttrade_cash": cash,
        "reconciliation_status": recon_status or None,
        "positions": rows,
        "target_attainment_policy": normalized_policy,
        "policy_validation_error": policy_error,
        "whole_share_feasibility": proof or None,
        "whole_share_feasibility_valid": proof_valid,
        "whole_share_feasibility_hash_valid": proof_hash_valid,
        "whole_share_feasibility_lineage_valid": proof_lineage_valid,
        "posttrade_market_values_complete": market_values_complete,
        "approved_execution_package_hash": expected_package_hash or None,
        "nearest_feasible_verified": nearest_feasible_verified,
        "quantity_mismatches": quantity_mismatches,
        "unapproved_symbols": unapproved_symbols,
        "source_artifacts": {
            "plan": "source plan passed to unified lane executor",
            "posttrade_snapshot": "live_pilot_broker_snapshot_post.json",
            "reconciliation": "live_pilot_reconciliation.json",
        },
    }
