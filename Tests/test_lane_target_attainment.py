from core.lane_target_attainment import build_lane_target_attainment


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
