from __future__ import annotations

import json
from pathlib import Path

from research.target_attainment import build_target_attainment


TRADE_DATE = "2026-06-09"
RUN_ID = "2026-06-09T093507-0400_fixture"


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_fixture(
    root: Path,
    *,
    target_cash: float = 500.0,
    actual_cash: float = 500.0,
    actual_positions: dict[str, float] | None = None,
    sell_timeout: bool = False,
    capital_constrained: bool = False,
    fractional: bool = False,
    post_sell_rebudget: bool = False,
) -> None:
    actual_positions = actual_positions or {"AAA": 50.0, "BBB": 50.0}
    target_positions = [
        {"symbol": "AAA", "shares": 50.0, "price": 100.0, "market_value": 5000.0, "target_weight": 0.5},
        {"symbol": "BBB", "shares": 50.0, "price": 90.0, "market_value": 4500.0, "target_weight": 0.45},
    ]
    if fractional:
        target_positions = [
            {"symbol": "AAA", "shares": 50.5, "price": 100.0, "market_value": 5050.0, "target_weight": 0.505},
            {"symbol": "BBB", "shares": 49.444444, "price": 90.0, "market_value": 4450.0, "target_weight": 0.445},
        ]
        actual_positions = {"AAA": 50.5, "BBB": 49.444444}
    _write_json(
        root / "outputs" / "operational_drag" / TRADE_DATE / "intended_nav.json",
        {
            "available": True,
            "date": TRADE_DATE,
            "intended_cash": target_cash,
            "intended_equity_value": 10000.0,
            "intended_gross_exposure": 0.95,
            "intended_positions": target_positions,
            "reason_codes": ["ok"],
        },
    )
    _write_json(
        root / "outputs" / "operational_drag" / TRADE_DATE / "actual_nav.json",
        {
            "available": True,
            "date": TRADE_DATE,
            "actual_cash": actual_cash,
            "actual_equity_value": 10000.0,
            "actual_gross_exposure": max(0.0, 1.0 - (actual_cash / 10000.0)),
            "actual_positions": [
                {"symbol": symbol, "shares": shares, "price": None, "market_value": None}
                for symbol, shares in sorted(actual_positions.items())
            ],
            "reason_codes": ["actual_positions_from_reconciled_posttrade"],
        },
    )
    run = root / "outputs" / "runs" / RUN_ID
    _write_json(
        run / "execution_payload.json",
        {
            "trade_date": TRADE_DATE,
            "cash_target_weight": 0.05,
            "submitted_count": 3,
            "accepted_count": 3,
            "rejected_count": 0,
            "sell_phase_status": "TIMEOUT" if sell_timeout else "CONFIRMED",
            "buy_phase_block_reason": "sell_phase_timeout" if sell_timeout else None,
            "trades": [
                {"ticker": "AAA", "side": "BUY", "shares": 10.25 if fractional else 10.0, "entry_price": 100.0},
                {"ticker": "BBB", "side": "BUY", "shares": 5.0, "entry_price": 90.0},
            ],
        },
    )
    _write_json(
        run / "broker" / f"intended_orders_{TRADE_DATE}.json",
        {
            "report_date": TRADE_DATE,
            "capital_budget": {
                "capital_constraint_triggered": capital_constrained,
                "clipped_or_deferred_buys_count": 1 if capital_constrained else 0,
            },
            "orders_intended": [
                {"ticker": "AAA", "side": "BUY", "shares": 10.25 if fractional else 10.0, "price": 100.0},
                {"ticker": "BBB", "side": "BUY", "shares": 5.0, "price": 90.0},
            ],
        },
    )
    _write_json(
        run / "broker" / f"recon_posttrade_{TRADE_DATE}.json",
        {
            "trade_date": TRADE_DATE,
            "drift_status": "OK_RECONCILED",
            "broker_cash": actual_cash,
            "broker_equity": 10000.0,
            "actual_positions": actual_positions,
        },
    )
    if post_sell_rebudget:
        _write_json(
            run / "broker" / f"post_sell_rebudget_{TRADE_DATE}.json",
            {
                "trade_date": TRADE_DATE,
                "status": "REBUILT",
                "original_precomputed_buy_notional": 1000.0,
                "recomputed_buy_notional": 2500.0,
            },
        )
    _write_json(
        root / "outputs" / "operational_drag" / TRADE_DATE / "price_hydration.json",
        {
            "date": TRADE_DATE,
            "prices": [
                {"symbol": "AAA", "date": TRADE_DATE, "close": 100.0},
                {"symbol": "BBB", "date": TRADE_DATE, "close": 90.0},
            ],
        },
    )


def test_perfect_target_attainment(tmp_path: Path) -> None:
    _write_fixture(tmp_path)

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    assert payload["summary"]["actual_cash_pct"] == 0.05
    assert payload["summary"]["cash_gap_pct"] == 0.0
    assert payload["summary"]["exposure_gap_pct"] == 0.0
    assert payload["reason_codes"] == ["ok"]
    assert payload["attainment_score"] == 100.0


def test_cash_and_exposure_drift_are_measured(tmp_path: Path) -> None:
    _write_fixture(tmp_path, actual_cash=2500.0, actual_positions={"AAA": 50.0, "BBB": 50.0})

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    assert payload["summary"]["actual_cash_pct"] == 0.25
    assert payload["summary"]["cash_gap_pct"] == 0.2
    assert payload["summary"]["exposure_gap_pct"] == 0.2
    assert payload["summary"]["excess_cash"] == 2000.0
    assert "cash_above_target" in payload["reason_codes"]
    assert "exposure_below_target" in payload["reason_codes"]


def test_sell_confirmation_shortfall_is_attributed(tmp_path: Path) -> None:
    _write_fixture(tmp_path, actual_cash=2500.0, actual_positions={"AAA": 40.0, "BBB": 40.0}, sell_timeout=True)

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    assert "sell_confirmation_constraint" in payload["reason_codes"]
    assert "sell_confirmation_constraint" in payload["drift_attribution"]["classifications"]


def test_capital_budget_shortfall_is_attributed(tmp_path: Path) -> None:
    _write_fixture(tmp_path, actual_cash=1800.0, capital_constrained=True)

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    assert "capital_budget_constraint" in payload["reason_codes"]


def test_fractional_share_scenario_preserves_attainment(tmp_path: Path) -> None:
    _write_fixture(tmp_path, fractional=True)

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    aaa = next(row for row in payload["weights"] if row["symbol"] == "AAA")
    assert aaa["actual_shares"] == 50.5
    assert payload["reason_codes"] == ["ok"]


def test_post_sell_rebudget_telemetry_is_reported(tmp_path: Path) -> None:
    _write_fixture(tmp_path, post_sell_rebudget=True)

    payload = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    assert "post_sell_rebudget_applied" in payload["reason_codes"]
    assert payload["source_artifacts"]["post_sell_rebudget"].endswith(f"post_sell_rebudget_{TRADE_DATE}.json")


def test_artifact_output_is_deterministic(tmp_path: Path) -> None:
    _write_fixture(tmp_path, actual_cash=2500.0, sell_timeout=True)

    first = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)
    second = build_target_attainment(trade_date=TRADE_DATE, repo_root=tmp_path)

    first.pop("artifact_path", None)
    second.pop("artifact_path", None)
    assert first == second
