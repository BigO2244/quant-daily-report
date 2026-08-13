from __future__ import annotations

from pathlib import Path

import pytest

from authority.exact_plan import exact_execution_plan_from_dict
from core.failure_semantics import TerminalOutcome
from execution.exact_executor import execute_exact_plan
from scripts.authorize_exact_execution_plan import authorize_exact_execution_plan
from Tests.test_exact_execution_choice2 import (
    TrackingPaperBroker,
    _env,
    _finalize_direct_authorization,
)
from Tests.test_live_pilot_build_plan_from_precompute import _build, _bundle, _orion_shadow


REGIMES = (
    "risk_on_trending",
    "neutral_mixed",
    "risk_off_defensive",
    "high_volatility",
    "breadth_washout",
)
TARGETS = (
    {"AAPL": 0.95},
    {"AAPL": 0.475, "MSFT": 0.475},
    {"AAPL": 0.19, "MSFT": 0.19, "JNJ": 0.19, "PNC": 0.19, "SPG": 0.19},
)


class MatrixPaperBroker(TrackingPaperBroker):
    def __init__(self, *, old_quantity: int, target_price: float) -> None:
        super().__init__()
        self.cash = 1000.0 - (old_quantity * 100.0)
        self.positions = (
            [{"symbol": "OLD", "qty": str(old_quantity), "market_value": str(old_quantity * 100.0)}]
            if old_quantity
            else []
        )
        self.target_price = float(target_price)

    def get_latest_trades(self, symbols):
        return {
            str(symbol): {
                "symbol": str(symbol),
                "price": "100" if str(symbol) == "OLD" else str(self.target_price),
                "timestamp": "2026-08-12T13:35:00+00:00",
                "feed": "MATRIX_SIMULATION",
            }
            for symbol in symbols
        }


@pytest.mark.parametrize("composite_regime", REGIMES)
@pytest.mark.parametrize("target_weights", TARGETS)
@pytest.mark.parametrize("old_quantity", (0, 1, 5))
@pytest.mark.parametrize("target_price", (25.0, 100.0, 500.0))
def test_paper_chain_scenario_matrix(
    tmp_path: Path,
    composite_regime: str,
    target_weights: dict[str, float],
    old_quantity: int,
    target_price: float,
) -> None:
    """135 production-shaped PAPER scenarios; every broker call is simulated."""

    trade_date = "2026-08-12"
    payload_path = _bundle(
        tmp_path,
        signals=[],
        trade_date=trade_date,
        composite_regime=composite_regime,
    )
    _orion_shadow(tmp_path, trade_date=trade_date, weights=target_weights)
    prices = {symbol: target_price for symbol in target_weights}
    plan = _build(
        tmp_path,
        payload_path,
        prices=prices,
        approved_sleeve="caerus_orion",
        capital_cap=1000.0,
        lane="paper",
        shadow_root=tmp_path / "outputs" / "shadow_candidates",
        output_dir=tmp_path / "outputs" / "paper_lane" / "plans",
        state_dir=tmp_path / "outputs" / "paper_lane" / "state",
    )
    assert plan["status"] == "READY_FOR_MANUAL_APPROVAL"

    broker = MatrixPaperBroker(old_quantity=old_quantity, target_price=target_price)
    regime_root = tmp_path / "outputs" / "paper_lane" / "state" / "regime_authority"
    run_id = f"matrix-{composite_regime}-{len(target_weights)}-{old_quantity}-{target_price:g}"
    authorized = authorize_exact_execution_plan(
        plan=plan,
        broker=broker,
        env={**_env(), "CAERUS_LIVE_PILOT_PLANNING_EQUITY_CAP": "1000"},
        run_id=run_id,
        plan_path=Path(plan["json_path"]),
        created_at="2026-08-12T13:35:01+00:00",
        regime_state_root=regime_root,
    )
    authorized = _finalize_direct_authorization(regime_root, authorized)
    exact = exact_execution_plan_from_dict(authorized["exact_execution_plan"])
    assert exact.regime_state["observed_state"] == composite_regime.upper()

    result = execute_exact_plan(
        plan_payload=exact.to_dict(),
        broker=broker,
        env=_env(),
        wal_root=tmp_path / "outputs" / "paper_lane" / "submission_wal",
        attempt_id=f"{run_id}-simulated-submit",
        dry_run=False,
    )
    assert result.terminal_outcome is TerminalOutcome.RECONCILED_SUCCESS
    assert result.reconciliation_status == "CLEAN"
    assert broker.submit_calls == len(exact.orders)

