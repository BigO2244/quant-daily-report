from __future__ import annotations

"""FR-104 live full-rebalance parity.

Proves the live full-rebalance path (``_build_core_request`` -> shared core) produces
the SAME trades as paper for the same target/holdings/equity/prices/cash-weight, and
that both match an INDEPENDENT hand-derivation (verification is not the same code
checking itself). Real money places real sells against the operator's positions, so
this is the "no drift" guarantee and it is proven, not asserted.
"""

import pandas as pd
import pytest

from execution.core import compute_transition_trades
from Tests.parity.live_core_harness import (
    _config,
    compute_trades,
    live_core_request,
    paper_core_request,
    plan_from_scenario,
)
from Tests.parity.scenarios import scenario_by_name

FULL_REBALANCE_SCENARIOS = ["full_rebalance_mixed"]


@pytest.mark.parametrize("scenario_name", FULL_REBALANCE_SCENARIOS)
def test_live_core_trades_match_paper(scenario_name: str) -> None:
    scenario = scenario_by_name(scenario_name)
    live = compute_trades(live_core_request(scenario), scenario)
    paper = compute_trades(paper_core_request(scenario), scenario)
    assert live == paper, f"live-core trades diverged from paper for {scenario_name}"


@pytest.mark.parametrize("scenario_name", FULL_REBALANCE_SCENARIOS)
def test_live_request_reconstructs_paper_inputs(scenario_name: str) -> None:
    scenario = scenario_by_name(scenario_name)
    live = live_core_request(scenario)
    paper = paper_core_request(scenario)

    assert float(live.total_equity) == pytest.approx(float(paper.total_equity))
    # The drift-point-1 fix: live must carry the risk-adjusted cash weight, not 0.0.
    assert float(live.target_cash_weight) == pytest.approx(float(paper.target_cash_weight))
    assert float(live.target_cash_weight) == pytest.approx(0.30)

    live_h = {r.ticker: r.shares for r in live.holdings.itertuples()}
    paper_h = {r.ticker: r.shares for r in paper.holdings.itertuples()}
    assert live_h == paper_h

    live_t = {r.ticker: round(float(r.target_weight), 9) for r in live.targets.itertuples()}
    paper_t = {r.ticker: round(float(r.target_weight), 9) for r in paper.targets.itertuples()}
    assert live_t == paper_t

    for symbol in set(paper.prices.index) | set(live.prices.index):
        assert float(live.prices.loc[symbol]) == pytest.approx(float(paper.prices.loc[symbol])), symbol


def test_independent_hand_derivation() -> None:
    """Hand-computed expected trades, asserted against BOTH engines.

    equity 10000; prices OVR=100 UND=50 GONE=200 NEW=25.
    Holdings OVR=40, UND=20, GONE=5. Targets OVR 0.20, UND 0.30, NEW 0.20; GONE absent.
      OVR: target 0.20*10000/100 = 20 sh; held 40 -> SELL 20 ($2000)
      GONE: absent -> SELL 5 ($1000)
      UND: target 0.30*10000/50 = 60 sh; held 20 -> BUY 40 ($2000)
      NEW: target 0.20*10000/25 = 80 sh; held 0 -> BUY 80 ($2000)
    """
    expected = [
        {"ticker": "GONE", "side": "SELL", "shares": 5.0, "price": 200.0, "notional": 1000.0, "reason": "removed_from_targets"},
        {"ticker": "OVR", "side": "SELL", "shares": 20.0, "price": 100.0, "notional": 2000.0, "reason": "rebalance_to_target"},
        {"ticker": "NEW", "side": "BUY", "shares": 80.0, "price": 25.0, "notional": 2000.0, "reason": "rebalance_to_target"},
        {"ticker": "UND", "side": "BUY", "shares": 40.0, "price": 50.0, "notional": 2000.0, "reason": "rebalance_to_target"},
    ]
    expected.sort(key=lambda r: (r["side"], r["ticker"]))

    scenario = scenario_by_name("full_rebalance_mixed")
    live = compute_trades(live_core_request(scenario), scenario)
    paper = compute_trades(paper_core_request(scenario), scenario)
    assert live == expected
    assert paper == expected


def test_legacy_plan_without_cash_weight_defaults_to_zero() -> None:
    """A pre-v2 plan (no top-level cash_target_weight) must not crash and defaults 0.0."""
    scenario = scenario_by_name("full_rebalance_mixed")
    plan = plan_from_scenario(scenario)
    plan.pop("cash_target_weight")
    from Tests.parity.live_core_harness import snapshot_from_scenario
    from scripts.live_pilot_execute import _build_core_request

    request, malformed = _build_core_request(
        pre_snapshot=snapshot_from_scenario(scenario), plan=plan, run_id="legacy"
    )
    assert not malformed
    assert request is not None
    assert float(request.target_cash_weight) == pytest.approx(0.0)
