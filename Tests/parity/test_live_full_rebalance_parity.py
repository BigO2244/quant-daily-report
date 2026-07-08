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


def _small_account_request(min_trade_usd: float):
    """~$500 account, one off-target holding + a many-name target (each < $100)."""
    import pandas as pd
    from execution.core import (ExecutionRequest, live_pilot_execution_config,
                                 execute_lifecycle, SynchronousTestAdapter)

    prices = {"ABBV": 252.0}
    weights = [0.08, 0.075, 0.07, 0.065, 0.06, 0.06, 0.055, 0.05, 0.05, 0.05,
               0.045, 0.045, 0.04, 0.04, 0.04, 0.035, 0.03, 0.025]
    targets = []
    for i, w in enumerate(weights):
        t = f"T{i:02d}"; prices[t] = 50.0
        targets.append({"ticker": t, "sleeve": "live_pilot", "target_weight": w})
    holdings = pd.DataFrame([{"ticker": "ABBV", "sleeve": "live_pilot", "shares": 1.1862}],
                            columns=["ticker", "sleeve", "shares"])
    equity, cash = 500.15, 0.88
    acct = {"cash": str(cash), "equity": str(equity), "portfolio_value": str(equity),
            "buying_power": str(cash), "status": "ACTIVE"}
    cfg = live_pilot_execution_config(approved_cap_usd=equity, max_orders=50,
                                      allow_fractional=True, min_trade_usd=min_trade_usd,
                                      ledger_enabled=False)
    req = ExecutionRequest(holdings=holdings,
                           targets=pd.DataFrame(targets, columns=["ticker", "sleeve", "target_weight"]),
                           prices=pd.Series(prices, dtype=float), total_equity=equity,
                           starting_cash=cash, target_cash_weight=0.05,
                           planning_account=acct, run_id="small", price_basis="t")
    # post_sell_account WITHOUT buying_power => adapter fills it with post-sell cash,
    # modeling the live adapter re-snapshotting the broker after sells settle.
    adapter = SynchronousTestAdapter(holdings=holdings, starting_cash=cash,
        post_sell_account={"equity": str(equity), "status": "ACTIVE"},
        planning_account=acct, portfolio_id="live_pilot")
    return execute_lifecycle(request=req, adapter=adapter, config=cfg)


def test_small_account_min_trade_floor_blocks_all_buys() -> None:
    # Documents the failure the $100 default causes on a small account: every target
    # position is < $100, so the rebalance sells to cash and buys NOTHING.
    r = _small_account_request(min_trade_usd=100.0)
    assert len(r.final_buy_orders or []) == 0


def test_small_account_low_floor_fills_top_targets_within_budget() -> None:
    # With a low floor the weight-priority rebudget fills the TOP targets it can afford
    # and skips the unaffordable tail (operator's "fill what it can afford" intent).
    r = _small_account_request(min_trade_usd=10.0)
    buys = r.final_buy_orders or []
    assert len(buys) > 0
    filled = [str(o.get("ticker")) for o in buys]
    # Highest-weight names come first; the tail (lowest weight) is skipped.
    assert filled[0] == "T00"
    assert "T17" not in filled
    buy_notional = sum(float(o.get("notional") or 0.0) for o in buys)
    post_sell_cash = float(r.post_sell_budget_meta.get("post_sell_cash") or 0.0)
    assert buy_notional <= post_sell_cash + 1e-6
    # Deploys down toward the 5% cash target rather than leaving proceeds idle.
    assert (post_sell_cash - buy_notional) < 0.5 * post_sell_cash


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
