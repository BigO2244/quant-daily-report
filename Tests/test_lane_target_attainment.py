from core.lane_target_attainment import build_lane_target_attainment
from core.whole_share_feasibility import seal_whole_share_proof


def _plan() -> dict:
    return {
        "cash_target_weight": 0.10,
        "target_portfolio": [
            {"symbol": "AAA", "target_weight": 0.60},
            {"symbol": "BBB", "target_weight": 0.30},
        ],
    }


def test_target_attainment_is_account_scoped_and_reconciles_weights() -> None:
    payload = build_lane_target_attainment(
        plan=_plan(),
        post_snapshot={
            "account": {"equity": "1000", "cash": "100"},
            "positions": [
                {"symbol": "AAA", "market_value": "600"},
                {"symbol": "BBB", "market_value": "300"},
            ],
        },
        reconciliation={"status": "CLEAN"},
        run_id="paper-run",
        trade_date="2026-07-29",
        mode="paper",
        dry_run=False,
    )
    assert payload["status"] == "OK_TARGET_ATTAINED"
    assert payload["account_scope"] == "PAPER"
    assert payload["cash_target_drift"] == 0.0


def test_target_attainment_warns_on_material_cash_and_position_drift() -> None:
    payload = build_lane_target_attainment(
        plan=_plan(),
        post_snapshot={
            "account": {"equity": "1000", "cash": "400"},
            "positions": [{"symbol": "AAA", "market_value": "600"}],
        },
        reconciliation={"status": "CLEAN"},
        run_id="paper-run",
        trade_date="2026-07-29",
        mode="paper",
        dry_run=False,
    )
    assert payload["status"] == "WARN_TARGET_DRIFT"
    assert payload["cash_target_drift"] == 0.3
    assert payload["max_absolute_position_weight_drift"] == 0.3


def test_dry_run_does_not_claim_target_attainment() -> None:
    payload = build_lane_target_attainment(
        plan=_plan(),
        post_snapshot={"account": {}, "positions": []},
        reconciliation={"status": "DRY_RUN"},
        run_id="dry",
        trade_date="2026-07-29",
        mode="live_pilot",
        dry_run=True,
    )
    assert payload["status"] == "DRY_RUN_NOT_APPLICABLE"


def test_exact_nearest_feasible_quantity_is_clean_outside_fixed_weight_band() -> None:
    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    plan = {
        "cash_target_weight": 0.05,
        "target_attainment_policy": policy,
        "target_portfolio": [
            {"symbol": "AAA", "target_weight": 0.475},
            {"symbol": "BBB", "target_weight": 0.475},
        ],
        "approved_execution_package": {
            "content_hash": package_hash,
            "approved_cash_weight": 0.05,
            "approved_target_rows": [
                {"symbol": "AAA", "target_weight": 0.475},
                {"symbol": "BBB", "target_weight": 0.475},
            ],
            "constraints": {"target_attainment_policy": policy},
        },
    }
    proof = seal_whole_share_proof(
        {
            "schema_version": "caerus.whole_share_feasibility.v1",
            "status": "PASS",
            "approved_execution_package_hash": package_hash,
            "allocation": [
                {"symbol": "AAA", "target_quantity": 1},
                {"symbol": "BBB", "target_quantity": 1},
            ],
        }
    )
    payload = build_lane_target_attainment(
        plan=plan,
        post_snapshot={
            "account": {"equity": "1000", "cash": "26"},
            "positions": [
                {"symbol": "AAA", "qty": "1", "market_value": "600"},
                {"symbol": "BBB", "qty": "1", "market_value": "374"},
            ],
        },
        reconciliation={"status": "CLEAN"},
        run_id="paper-nearest",
        trade_date="2026-08-12",
        mode="paper",
        dry_run=False,
        feasibility_evidence=proof,
    )

    assert payload["status"] == "OK_NEAREST_FEASIBLE"
    assert payload["nearest_feasible_verified"] is True
    assert payload["achieved_cash_weight"] == 0.026


def test_authorized_no_trade_passes_only_with_exact_nearest_feasible_proof() -> None:
    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    plan = {
        "cash_target_weight": 0.05,
        "target_attainment_policy": policy,
        "target_portfolio": [
            {"symbol": "AAA", "target_weight": 0.475},
            {"symbol": "BBB", "target_weight": 0.475},
        ],
        "approved_execution_package": {
            "content_hash": package_hash,
            "approved_cash_weight": 0.05,
            "approved_target_rows": [
                {"symbol": "AAA", "target_weight": 0.475},
                {"symbol": "BBB", "target_weight": 0.475},
            ],
            "constraints": {"target_attainment_policy": policy},
        },
        "exact_execution_plan": {"portfolio_nav": 1000.0},
    }
    proof = seal_whole_share_proof(
        {
            "schema_version": "caerus.whole_share_feasibility.v1",
            "status": "PASS",
            "approved_execution_package_hash": package_hash,
            "equity_basis": 1000.0,
            "allocation": [
                {"symbol": "AAA", "target_quantity": 1},
                {"symbol": "BBB", "target_quantity": 1},
            ],
        }
    )
    snapshot = {
        "account": {"equity": "1000", "cash": "26"},
        "positions": [
            {"symbol": "AAA", "qty": "1", "market_value": "600"},
            {"symbol": "BBB", "qty": "1", "market_value": "374"},
        ],
    }

    payload = build_lane_target_attainment(
        plan=plan,
        post_snapshot=snapshot,
        reconciliation={"status": "NOT_APPLICABLE_NO_TRADE"},
        run_id="paper-authorized-no-trade",
        trade_date="2026-08-20",
        mode="paper",
        dry_run=False,
        feasibility_evidence=proof,
    )

    assert payload["status"] == "OK_NEAREST_FEASIBLE"
    assert payload["reason_code"] == (
        "authorized_no_trade_matches_proven_nearest_feasible_allocation"
    )
    assert payload["nearest_feasible_verified"] is True

    mismatched = build_lane_target_attainment(
        plan=plan,
        post_snapshot={
            **snapshot,
            "positions": [
                {"symbol": "AAA", "qty": "2", "market_value": "600"},
                {"symbol": "BBB", "qty": "1", "market_value": "374"},
            ],
        },
        reconciliation={"status": "NOT_APPLICABLE_NO_TRADE"},
        run_id="paper-authorized-no-trade-mismatch",
        trade_date="2026-08-20",
        mode="paper",
        dry_run=False,
        feasibility_evidence=proof,
    )

    assert mismatched["status"] == "FAIL_EXECUTION_INCOMPLETE"
    assert mismatched["nearest_feasible_verified"] is False


def test_governed_policy_fails_closed_without_complete_quantity_proof() -> None:
    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    payload = build_lane_target_attainment(
        plan={
            "cash_target_weight": 0.05,
            "target_attainment_policy": policy,
            "target_portfolio": [
                {"symbol": "AAA", "target_weight": 0.95},
            ],
            "approved_execution_package": {
                "content_hash": package_hash,
                "approved_cash_weight": 0.05,
                "approved_target_rows": [
                    {"symbol": "AAA", "target_weight": 0.95},
                ],
                "constraints": {"target_attainment_policy": policy},
            },
        },
        post_snapshot={
            "account": {"equity": "1000", "cash": "50"},
            "positions": [
                {"symbol": "AAA", "qty": "10", "market_value": "950"},
            ],
        },
        reconciliation={"status": "CLEAN"},
        run_id="paper-missing-proof",
        trade_date="2026-08-12",
        mode="paper",
        dry_run=False,
        feasibility_evidence=seal_whole_share_proof(
            {
                "schema_version": "caerus.whole_share_feasibility.v1",
                "status": "PASS",
                "approved_execution_package_hash": package_hash,
                "allocation": [],
            }
        ),
    )

    assert payload["status"] == "FAIL_FEASIBILITY_PROOF_INVALID"
    assert payload["whole_share_feasibility_valid"] is False


def test_nearest_feasible_proof_cannot_use_a_smaller_account_denominator() -> None:
    package_hash = "approved-package-hash"
    policy = {
        "schema_version": "caerus.target_attainment_policy.v1",
        "account_scope": "PAPER",
        "share_mode": "WHOLE_SHARES",
        "target_cash_weight": 0.05,
        "minimum_cash_weight": 0.025,
        "fixed_drift_tolerance": 0.02,
        "nearest_feasible_required": True,
        "comparison_epoch_policy": "FIRST_CLEAN_POST_FIX_PAPER_RUN",
        "strict_green_propagation": True,
        "owner_approved_at": "2026-08-11",
    }
    plan = {
        "approved_execution_package": {
            "content_hash": package_hash,
            "approved_cash_weight": 0.05,
            "approved_target_rows": [
                {"symbol": "AAA", "target_weight": 0.95},
            ],
            "constraints": {"target_attainment_policy": policy},
        },
        "exact_execution_plan": {"portfolio_nav": 1200.0},
    }
    proof = seal_whole_share_proof(
        {
            "schema_version": "caerus.whole_share_feasibility.v1",
            "status": "PASS",
            "approved_execution_package_hash": package_hash,
            "equity_basis": 1000.0,
            "allocation": [{"symbol": "AAA", "target_quantity": 10}],
        }
    )

    payload = build_lane_target_attainment(
        plan=plan,
        post_snapshot={
            "account": {"equity": "1200", "cash": "200"},
            "positions": [
                {"symbol": "AAA", "qty": "10", "market_value": "1000"},
            ],
        },
        reconciliation={"status": "CLEAN"},
        run_id="wrong-denominator",
        trade_date="2026-08-17",
        mode="paper",
        dry_run=False,
        feasibility_evidence=proof,
    )

    assert payload["status"] == "FAIL_FEASIBILITY_PROOF_INVALID"
    assert payload["expected_execution_equity_basis"] == 1200.0
    assert payload["whole_share_feasibility_equity_basis"] == 1000.0
    assert payload["whole_share_feasibility_equity_basis_valid"] is False
    assert payload["nearest_feasible_verified"] is False
