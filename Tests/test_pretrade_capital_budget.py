from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.operator_summary import format_execution_health_banner, write_operator_summary
from core.trading_day_summary import build_trading_day_summary
from paper.paper_broker import (
    PaperConfig,
    _apply_capital_budget_to_trades,
    _build_capital_budget,
    _build_shadow_orders,
    _compute_buy_budget,
    _split_orders_for_execution,
    _write_intended_orders_artifact,
)


def _cfg(*, allow_fractional: bool = True) -> PaperConfig:
    return PaperConfig(
        initial_equity=10000.0,
        benchmark_ticker="SPY",
        slippage_bps=0.0,
        allow_fractional=allow_fractional,
        min_trade_dollars=100.0,
    )


def _trade(ticker: str, side: str, shares: float, price: float, reason: str = "rebalance_to_target") -> dict:
    return {
        "ticker": ticker,
        "side": side,
        "shares": float(shares),
        "price": float(price),
        "slippage_cost": 0.0,
        "notional": float(abs(shares) * price),
        "reason": reason,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_negative_broker_cash_without_sell_proceeds_blocks_buys() -> None:
    trades = pd.DataFrame(
        [
            _trade("AAPL", "BUY", 5.0, 100.0),
            _trade("MSFT", "BUY", 10.0, 100.0),
        ]
    )
    capital_budget = _build_capital_budget(
        broker_cash=-500.0,
        broker_equity=25000.0,
        broker_buying_power=10000.0,
        expected_sell_proceeds=0.0,
        requested_buy_notional=1500.0,
    )

    clipped, meta = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)

    assert clipped.empty
    assert meta["allowed_buy_notional"] == 0.0
    assert meta["capital_constraint_triggered"] is True
    assert meta["clipped_or_deferred_buys_count"] == 2


def test_negative_cash_with_sell_proceeds_only_allows_conservative_amount() -> None:
    trades = pd.DataFrame(
        [
            _trade("XOM", "SELL", 30.0, 100.0),
            _trade("AAPL", "BUY", 10.0, 100.0),
            _trade("MSFT", "BUY", 7.0, 100.0),
        ]
    )
    capital_budget = _build_capital_budget(
        broker_cash=-500.0,
        broker_equity=25000.0,
        broker_buying_power=10000.0,
        expected_sell_proceeds=3000.0,
        requested_buy_notional=1700.0,
    )

    clipped, meta = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)

    assert meta["expected_sell_proceeds_conservative"] == 2850.0
    assert meta["allowed_buy_notional"] == 1700.0
    assert meta["capital_constraint_triggered"] is False
    assert meta["clipped_or_deferred_buys_count"] == 0
    buy_notional = float(
        clipped.loc[clipped["side"].astype(str).str.upper() == "BUY", "notional"].sum()
    )
    assert buy_notional == 1700.0


def test_positive_cash_with_reserve_allows_normal_buy_plan() -> None:
    trades = pd.DataFrame(
        [
            _trade("AAPL", "BUY", 10.0, 100.0),
            _trade("MSFT", "BUY", 10.0, 100.0),
        ]
    )
    capital_budget = _build_capital_budget(
        broker_cash=5000.0,
        broker_equity=50000.0,
        broker_buying_power=20000.0,
        expected_sell_proceeds=0.0,
        requested_buy_notional=2000.0,
    )

    clipped, meta = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)

    assert clipped.to_dict("records") == trades.to_dict("records")
    assert meta["allowed_buy_notional"] == 2000.0
    assert meta["capital_constraint_triggered"] is False


def test_postsell_buy_budget_targets_less_than_five_percent_cash() -> None:
    budget = _compute_buy_budget(
        {"cash": "2388.18", "equity": "9713.40", "buying_power": "2388.18"},
        _cfg(),
    )

    remaining_cash = 2388.18 - budget
    assert remaining_cash == 100.0
    assert remaining_cash / 9713.40 < 0.05


def test_reserve_policy_uses_max_of_minimum_and_equity_percent() -> None:
    budget_small = _build_capital_budget(
        broker_cash=5000.0,
        broker_equity=50000.0,
        broker_buying_power=5000.0,
        expected_sell_proceeds=0.0,
        requested_buy_notional=0.0,
    )
    budget_large = _build_capital_budget(
        broker_cash=5000.0,
        broker_equity=250000.0,
        broker_buying_power=5000.0,
        expected_sell_proceeds=0.0,
        requested_buy_notional=0.0,
    )

    assert budget_small["reserve_cash_policy"]["reserve_cash"] == 250.0
    assert budget_large["reserve_cash_policy"]["reserve_cash"] == 1250.0


def test_conservative_sell_haircut_is_applied() -> None:
    budget = _build_capital_budget(
        broker_cash=0.0,
        broker_equity=100000.0,
        broker_buying_power=0.0,
        expected_sell_proceeds=2000.0,
        requested_buy_notional=0.0,
    )

    assert budget["expected_sell_proceeds_conservative"] == 1900.0


def test_capital_budget_clipping_is_deterministic() -> None:
    trades = pd.DataFrame(
        [
            _trade("XOM", "SELL", 30.0, 100.0),
            _trade("AAPL", "BUY", 10.0, 100.0),
            _trade("MSFT", "BUY", 7.0, 100.0),
        ]
    )
    capital_budget = _build_capital_budget(
        broker_cash=-500.0,
        broker_equity=25000.0,
        broker_buying_power=10000.0,
        expected_sell_proceeds=3000.0,
        requested_buy_notional=1700.0,
    )

    clipped_a, meta_a = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)
    clipped_b, meta_b = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)

    assert clipped_a.to_dict("records") == clipped_b.to_dict("records")
    assert meta_a == meta_b


def test_operator_and_trading_day_summary_include_capital_budget_fields(tmp_path: Path) -> None:
    run_root = tmp_path / "outputs" / "runs" / "run-capital"
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True, exist_ok=True)

    write_operator_summary(
        run_root,
        run_id="run-capital",
        trade_date="2026-03-16",
        mode="ALPACA",
        broker_cash_at_planning=-500.0,
        broker_equity_at_planning=25000.0,
        broker_buying_power_at_planning=10000.0,
        reserve_cash_policy={
            "min_cash_dollars": 1000.0,
            "equity_reserve_pct": 0.01,
            "sell_proceeds_haircut": 0.95,
            "reserve_cash": 1000.0,
            "available_for_buys": 1350.0,
        },
        expected_sell_proceeds=3000.0,
        expected_sell_proceeds_conservative=2850.0,
        requested_buy_notional=1700.0,
        allowed_buy_notional=1350.0,
        capital_constraint_triggered=True,
        clipped_or_deferred_buys_count=1,
    )

    _write_json(
        broker_dir / "pretrade_account_snapshot.json",
        {
            "account": {
                "status": "ACTIVE",
                "cash": "-500.00",
                "equity": "25000.00",
                "buying_power": "10000.00",
            }
        },
    )
    _write_json(broker_dir / "pretrade_positions.json", {"positions_count": 2})
    _write_json(broker_dir / "posttrade_account_snapshot.json", {"cash": 850.0, "equity": 24990.0})
    _write_json(broker_dir / "posttrade_positions.json", {"positions_count": 2})

    op_payload = json.loads((run_root / "operator_summary.json").read_text(encoding="utf-8"))
    assert op_payload["capital_constraint_triggered"] is True
    assert op_payload["allowed_buy_notional"] == 1350.0
    assert op_payload["clipped_or_deferred_buys_count"] == 1

    banner = format_execution_health_banner(op_payload)
    assert "capital_constraint=TRIGGERED" in banner
    assert "requested_buys=1700.0" in banner
    assert "allowed_buys=1350.0" in banner

    summary = build_trading_day_summary(
        run_root=run_root,
        run_id="run-capital",
        trade_date="2026-03-16",
        workspace_root=tmp_path,
        audit_dir=tmp_path / "outputs" / "execution_audit",
    )

    broker_context = summary["broker_context"]
    assert broker_context["broker_cash_at_planning"] == -500.0
    assert broker_context["broker_equity_at_planning"] == 25000.0
    assert broker_context["broker_buying_power_at_planning"] == 10000.0
    assert broker_context["reserve_cash_policy"]["reserve_cash"] == 1000.0
    assert broker_context["expected_sell_proceeds"] == 3000.0
    assert broker_context["expected_sell_proceeds_conservative"] == 2850.0
    assert broker_context["requested_buy_notional"] == 1700.0
    assert broker_context["allowed_buy_notional"] == 1350.0
    assert broker_context["capital_constraint_triggered"] is True
    assert broker_context["clipped_or_deferred_buys_count"] == 1


def test_intended_orders_artifact_includes_capital_budget_context(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("RUN_OUTPUT_ROOT", str(tmp_path))
    trades = pd.DataFrame([_trade("AAPL", "BUY", 3.5, 100.0, "rebalance_to_target_capital_clipped")])
    capital_budget = _build_capital_budget(
        broker_cash=-500.0,
        broker_equity=25000.0,
        broker_buying_power=10000.0,
        expected_sell_proceeds=3000.0,
        requested_buy_notional=1700.0,
    )
    capital_budget["allowed_buy_notional"] = 1350.0
    capital_budget["capital_constraint_triggered"] = True
    capital_budget["clipped_or_deferred_buys_count"] = 1

    out = _write_intended_orders_artifact(
        run_date="2026-03-16",
        run_id="run-capital",
        execution_trades=trades,
        execution_enabled=True,
        block_reasons=[],
        capital_budget=capital_budget,
    )

    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["capital_budget"]["broker_cash_at_planning"] == -500.0
    assert payload["capital_budget"]["expected_sell_proceeds_conservative"] == 2850.0
    assert payload["capital_budget"]["capital_constraint_triggered"] is True
    assert payload["capital_budget"]["clipped_or_deferred_buys_count"] == 1


def test_sell_before_buy_sequencing_remains_intact_after_clipping() -> None:
    trades = pd.DataFrame(
        [
            _trade("XOM", "SELL", 5.0, 100.0),
            _trade("AAPL", "BUY", 4.0, 100.0),
        ]
    )
    capital_budget = _build_capital_budget(
        broker_cash=1000.0,
        broker_equity=10000.0,
        broker_buying_power=5000.0,
        expected_sell_proceeds=500.0,
        requested_buy_notional=400.0,
    )

    clipped, _meta = _apply_capital_budget_to_trades(trades, _cfg(), capital_budget)
    orders = _build_shadow_orders(clipped, "run-capital")
    sell_orders, buy_orders = _split_orders_for_execution(orders)

    assert clipped["side"].tolist() == ["SELL", "BUY"]
    assert orders[0]["side"] == "SELL"
    assert len(sell_orders) == 1
    assert len(buy_orders) == 1
