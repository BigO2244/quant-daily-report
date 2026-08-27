from __future__ import annotations

import json
from pathlib import Path

import pytest

from authority.exact_plan import exact_execution_plan_from_dict
from core.failure_semantics import TerminalOutcome
from execution.exact_executor import execute_exact_plan
from scripts.authorize_exact_execution_plan import authorize_exact_execution_plan
from Tests.test_exact_execution_choice2 import (
    TrackingPaperBroker,
    TEST_NOW_ET,
    _env,
    _finalize_direct_authorization,
)
from Tests.test_live_pilot_build_plan_from_precompute import (
    _build,
    _bundle,
    _orion_shadow,
)


@pytest.mark.parametrize(
    "composite_regime",
    [
        "risk_on_trending",
        "neutral_mixed",
        "risk_off_defensive",
        "high_volatility",
        "breadth_washout",
    ],
)
def test_real_builder_authorizer_executor_chain_needs_no_fixture_authority_injection(
    tmp_path: Path,
    composite_regime: str,
) -> None:
    """Exercise the production-shaped PAPER chain without fabricating Risk.

    This is the regression that was missing on 2026-08-13: the real builder's
    persisted Risk package must pass unchanged through exact authorization and
    simulated broker execution.
    """

    trade_date = "2026-08-12"
    payload_path = _bundle(
        tmp_path,
        signals=[],
        trade_date=trade_date,
        composite_regime=composite_regime,
    )
    _orion_shadow(
        tmp_path,
        trade_date=trade_date,
        weights={"AAPL": 0.475, "MSFT": 0.475},
    )
    plan = _build(
        tmp_path,
        payload_path,
        prices={"AAPL": 50.0, "MSFT": 50.0},
        approved_sleeve="caerus_orion",
        capital_cap=1000.0,
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
        output_dir=tmp_path / "outputs" / "paper_lane" / "plans",
        state_dir=tmp_path / "outputs" / "paper_lane" / "state",
    )
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"

    risk_path = Path(plan["authority_package_paths"]["risk"])
    persisted_risk = json.loads(risk_path.read_text(encoding="utf-8"))
    persisted_market = persisted_risk["constraints"]["market_state"]
    assert persisted_market == plan["risk_controls"]["market_state"]

    broker = TrackingPaperBroker()
    regime_root = tmp_path / "outputs" / "paper_lane" / "state" / "regime_authority"
    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id="real-builder-authorizer-executor",
        plan_path=Path(plan["json_path"]),
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_root,
    )
    authorized = _finalize_direct_authorization(regime_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    assert exact.market_state_id == persisted_market["market_state_id"]
    assert exact.regime_state["observed_state"] == composite_regime.upper()
    assert exact.orders

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        # This is a deterministic historical-chain replay, not a freshness
        # test. Keep the real executor freshness gate enabled while widening
        # only this fixture's maximum age so the fixed 2026-08-12 evidence does
        # not expire as wall-clock time advances.
        env={**_env(), "CAERUS_EXACT_MAX_PLAN_AGE_SECONDS": "315360000"},
        wal_root=tmp_path / "outputs" / "paper_lane" / "submission_wal",
        attempt_id="real-chain-simulated-submit",
        dry_run=False,
        now_et=TEST_NOW_ET,
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert result.reconciliation_status == "CLEAN"
    assert broker.submit_calls == len(exact.orders)
