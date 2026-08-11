from __future__ import annotations

import math

import pandas as pd

from authority.contracts import (
    build_decision_package,
    build_evidence_package,
    build_risk_package,
)
from authority.pipeline import execution_package_from_risk
from core.whole_share_feasibility import (
    build_nearest_feasible_whole_share_trades,
    whole_share_proof_content_hash,
)
from execution.core import (
    ExecutionRequest,
    SynchronousTestAdapter,
    execute_lifecycle,
    paper_execution_config,
)
from paper.paper_broker import PaperConfig


POLICY = {
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


def _paper_config() -> PaperConfig:
    return PaperConfig(
        initial_equity=10_000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=False,
        allow_fractional_sells=True,
        min_trade_dollars=1.0,
        max_trades_per_day=50,
    )


def _approved_package(rows: list[dict]) -> dict:
    evidence = build_evidence_package(
        package_id="evidence:whole-share",
        trade_date="2026-08-12",
        source_refs=["orion.json"],
        observations=rows,
    )
    decision = build_decision_package(
        package_id="decision:whole-share",
        trade_date="2026-08-12",
        evidence=evidence,
        target_rows=rows,
        target_cash_weight=0.05,
        source_refs=["orion.json"],
    )
    risk = build_risk_package(
        package_id="risk:whole-share",
        decision=decision,
        approved_target_rows=rows,
        approved_cash_weight=0.05,
        constraints={"target_attainment_policy": POLICY},
        source_refs=["decision:whole-share"],
    )
    return execution_package_from_risk(risk).to_dict()


def test_optimizer_is_deterministic_and_proves_cash_floor() -> None:
    targets = pd.DataFrame(
        [
            {"ticker": "AAA", "target_weight": 0.19},
            {"ticker": "BBB", "target_weight": 0.19},
            {"ticker": "CCC", "target_weight": 0.19},
            {"ticker": "DDD", "target_weight": 0.19},
            {"ticker": "EEE", "target_weight": 0.19},
        ]
    )
    prices = pd.Series(
        {"AAA": 225.0, "BBB": 151.0, "CCC": 101.0, "DDD": 76.0, "EEE": 51.0}
    )
    kwargs = {
        "holdings": pd.DataFrame(columns=["ticker", "shares"]),
        "targets": targets,
        "prices": prices,
        "total_equity": 10_500.0,
        "cfg": _paper_config(),
        "policy": POLICY,
        "max_orders": 50,
    }

    trades_a, proof_a = build_nearest_feasible_whole_share_trades(**kwargs)
    trades_b, proof_b = build_nearest_feasible_whole_share_trades(**kwargs)

    assert proof_a == proof_b
    assert trades_a.to_dict("records") == trades_b.to_dict("records")
    assert (
        proof_a["proof_method"]
        == "EXHAUSTIVE_PROVABLY_BOUNDED_INTEGER_CARTESIAN"
    )
    assert proof_a["candidate_count_evaluated"] == proof_a[
        "bounded_search_space_candidate_count"
    ]
    assert proof_a["projected_cash_weight"] >= 0.025
    assert proof_a["proof_content_hash"] == whole_share_proof_content_hash(proof_a)
    assert all(float(row["shares"]).is_integer() for _, row in trades_a.iterrows())


def test_buy_only_lifecycle_rebuilds_under_governed_cash_floor() -> None:
    rows = [{"symbol": "AAA", "target_weight": 0.95, "price": 100.0}]
    holdings = pd.DataFrame([{"ticker": "AAA", "shares": 85.0}])
    account = {"cash": "600", "equity": "10000", "buying_power": "600"}
    cfg = _paper_config()
    config = paper_execution_config(
        cfg,
        target_cash_weight=0.05,
        ledger_enabled=False,
    )
    result = execute_lifecycle(
        request=ExecutionRequest(
            holdings=holdings,
            targets=pd.DataFrame([{"ticker": "AAA", "target_weight": 0.95}]),
            prices=pd.Series({"AAA": 100.0}),
            total_equity=10_000.0,
            starting_cash=600.0,
            target_cash_weight=0.05,
            planning_account=account,
            run_id="buy-only-budget",
            approved_execution_package=_approved_package(rows),
        ),
        adapter=SynchronousTestAdapter(
            holdings=holdings,
            starting_cash=600.0,
            planning_account=account,
            post_sell_account=account,
        ),
        config=config,
    )

    assert result.sell_trades.empty
    assert result.rebudget_meta["reason_codes"][0] == "no_sell_orders_rebudgeted"
    assert float(result.rebuilt_buy_trades["notional"].sum()) <= 350.0
    assert float(result.rebuilt_buy_trades["shares"].sum()) == 3.0
    assert 600.0 - float(result.rebuilt_buy_trades["notional"].sum()) >= 250.0


def test_optimizer_proves_global_integer_solution_beyond_floor_and_ceiling() -> None:
    targets = pd.DataFrame(
        [
            {"ticker": "EXPENSIVE", "target_weight": 0.50},
            {"ticker": "CHEAP", "target_weight": 0.45},
        ]
    )
    prices = pd.Series({"EXPENSIVE": 1998.0, "CHEAP": 10.0})

    trades, proof = build_nearest_feasible_whole_share_trades(
        holdings=pd.DataFrame(columns=["ticker", "shares"]),
        targets=targets,
        prices=prices,
        total_equity=10_000.0,
        cfg=_paper_config(),
        policy=POLICY,
        max_orders=10,
    )

    quantities = {
        row["symbol"]: row["target_quantity"] for row in proof["allocation"]
    }
    assert quantities == {"CHEAP": 500, "EXPENSIVE": 2}
    assert quantities["CHEAP"] > math.ceil(0.45 * 10_000.0 / 10.0)
    assert (
        proof["proof_method"]
        == "EXHAUSTIVE_PROVABLY_BOUNDED_INTEGER_CARTESIAN"
    )
    assert proof["candidate_count_evaluated"] == proof[
        "bounded_search_space_candidate_count"
    ]
    assert set(trades["ticker"]) == {"CHEAP", "EXPENSIVE"}


def test_optimizer_cleans_fractional_remainder_before_whole_share_buy() -> None:
    trades, proof = build_nearest_feasible_whole_share_trades(
        holdings=pd.DataFrame([{"ticker": "AAA", "shares": 10.5}]),
        targets=pd.DataFrame([{"ticker": "AAA", "target_weight": 0.12}]),
        prices=pd.Series({"AAA": 100.0}),
        total_equity=10_000.0,
        cfg=_paper_config(),
        policy={**POLICY, "target_cash_weight": 0.88},
        max_orders=10,
    )

    assert proof["allocation"][0]["target_quantity"] == 12
    assert trades[["side", "shares"]].to_dict("records") == [
        {"side": "SELL", "shares": 0.5},
        {"side": "BUY", "shares": 2.0},
    ]
