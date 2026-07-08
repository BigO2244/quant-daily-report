from __future__ import annotations

"""Live-core parity harness.

Builds a live-pilot ``plan`` + broker ``pre_snapshot`` from a shared parity scenario,
feeds them through the LIVE request builder
(``scripts.live_pilot_execute._build_core_request``), and runs the SAME shared core
(``execution.core.compute_transition_trades``) that paper uses. This lets a test prove
that the live full-rebalance path reconstructs the identical ExecutionRequest inputs
and therefore produces identical trades to paper for the same
target/holdings/equity/prices/cash-weight -- the FR-104 "no drift" guarantee.
"""

from typing import Any

import pandas as pd

from execution.core import ExecutionRequest, compute_transition_trades, paper_execution_config
from scripts.live_pilot_execute import _build_core_request
from Tests.parity.scenarios import (
    PaperParityScenario,
    holdings_frame,
    make_config,
    prices_series,
    targets_frame,
)


def plan_from_scenario(scenario: PaperParityScenario, *, approved_sleeve: str = "growth_engine_v4") -> dict[str, Any]:
    """A schema caerus.transition_target.v2 plan mirroring the builder's output."""
    prices = scenario.prices
    rows: list[dict[str, Any]] = []
    for target in scenario.targets:
        symbol = str(target["ticker"]).upper()
        rows.append(
            {
                "symbol": symbol,
                "ticker": symbol,
                "target_weight": float(target["target_weight"]),
                "price": float(prices[symbol]),
                "order_type": "market",
                "sleeve": approved_sleeve,
                "source_signal_sleeve": target.get("sleeve"),
            }
        )
    rows.sort(key=lambda r: (-(float(r["target_weight"])), str(r["symbol"])))
    return {
        "schema_version": "live_pilot_plan_from_precompute.v2",
        "trade_date": "parity",
        "approved_sleeve": approved_sleeve,
        "target_portfolio_schema": "caerus.transition_target.v2",
        "target_portfolio": rows,
        "cash_target_weight": float(scenario.target_cash_weight),
    }


def snapshot_from_scenario(scenario: PaperParityScenario) -> dict[str, Any]:
    """A broker pre-snapshot: account equity/cash + positions (symbol, qty, market_value)."""
    account = scenario.planning_account
    equity = float(account["equity"])
    cash = float(account["cash"])
    positions: list[dict[str, Any]] = []
    for holding in scenario.holdings:
        symbol = str(holding["ticker"]).upper()
        qty = float(holding["shares"])
        price = float(scenario.prices[symbol])
        positions.append(
            {
                "symbol": symbol,
                "qty": qty,
                "market_value": qty * price,
                "current_price": price,
            }
        )
    return {
        "account": {
            "equity": equity,
            "portfolio_value": equity,
            "cash": cash,
            "buying_power": account.get("buying_power"),
            "status": account.get("status") or "ACTIVE",
        },
        "positions": positions,
        "open_orders": [],
    }


def live_core_request(scenario: PaperParityScenario) -> ExecutionRequest:
    plan = plan_from_scenario(scenario)
    snapshot = snapshot_from_scenario(scenario)
    request, malformed = _build_core_request(pre_snapshot=snapshot, plan=plan, run_id=f"live-parity:{scenario.name}")
    if malformed:
        raise AssertionError(f"unexpected malformed holdings in parity snapshot: {malformed}")
    if request is None:
        raise AssertionError("live core request was None (equity unavailable)")
    return request


def paper_core_request(scenario: PaperParityScenario) -> ExecutionRequest:
    return ExecutionRequest(
        holdings=holdings_frame(scenario),
        targets=targets_frame(scenario),
        prices=prices_series(scenario),
        total_equity=float(scenario.total_equity),
        starting_cash=float(scenario.starting_cash),
        target_cash_weight=float(scenario.target_cash_weight),
        planning_account=scenario.planning_account,
        run_id=f"paper-parity:{scenario.name}",
        price_basis=scenario.price_basis,
    )


def _config(scenario: PaperParityScenario):
    # Identical config for both engines so the ONLY variable is request construction.
    return paper_execution_config(make_config(scenario), target_cash_weight=float(scenario.target_cash_weight))


def _normalized_trades(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    out: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        out.append(
            {
                "ticker": str(row.get("ticker") or row.get("symbol") or "").upper(),
                "side": str(row.get("side") or "").upper(),
                "shares": round(float(row.get("shares") or 0.0), 9),
                "price": round(float(row.get("price") or 0.0), 9),
                "notional": round(float(row.get("notional") or 0.0), 9),
                "reason": str(row.get("reason") or ""),
            }
        )
    out.sort(key=lambda r: (r["side"], r["ticker"]))
    return out


def compute_trades(request: ExecutionRequest, scenario: PaperParityScenario) -> list[dict[str, Any]]:
    raw, _meta = compute_transition_trades(request=request, config=_config(scenario))
    return _normalized_trades(raw)
