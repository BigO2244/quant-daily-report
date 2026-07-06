from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.candidate_trade_lifecycle import write_candidate_trade_lifecycle


def _row(payload: dict, ticker: str, side: str) -> dict:
    for candidate in payload["candidates"]:
        if candidate["ticker"] == ticker and candidate["side"] == side:
            return candidate
    raise AssertionError(f"missing lifecycle row for {ticker} {side}")


def test_candidate_trade_lifecycle_explains_filter_rebudget_and_fills(tmp_path: Path) -> None:
    trade_date = "2026-06-25"
    run_id = "2026-06-25T093508-0400_7b9af94"
    run_root = tmp_path / "outputs" / "runs" / run_id
    broker_dir = run_root / "broker"
    broker_dir.mkdir(parents=True)

    planned_payload = {
        "trade_date": trade_date,
        "trades": [
            {"ticker": "MAR", "side": "SELL", "shares": 1, "price": 382.6011, "notional": 382.6011},
            {"ticker": "MO", "side": "SELL", "shares": 1, "price": 71.8898, "notional": 125.9755},
            {"ticker": "NEE", "side": "SELL", "shares": 1, "price": 87.4010, "notional": 109.0394},
            {"ticker": "NSC", "side": "BUY", "shares": 1, "price": 252.0, "notional": 252.0},
            {
                "ticker": "SPG",
                "side": "BUY",
                "shares": 2,
                "price": 222.15,
                "notional": 444.30,
                "sleeve_id": "sleeve_quality",
                "strategy_id": "growth_engine_v4",
                "candidate_rank": 4,
                "conviction_score": 0.77,
            },
            {"ticker": "UNH", "side": "BUY", "shares": 1, "price": 308.0, "notional": 308.0},
            {"ticker": "UNP", "side": "BUY", "shares": 1, "price": 227.0, "notional": 227.0},
            {"ticker": "VZ", "side": "BUY", "shares": 3, "price": 44.0, "notional": 132.0},
        ],
    }
    (broker_dir / f"intended_orders_{trade_date}.json").write_text(
        json.dumps(
            {
                "orders_intended_count": 6,
                "orders_intended": [
                    {"ticker": "MAR", "side": "SELL", "shares": 1, "price": 382.6011, "notional": 382.6011},
                    {"ticker": "NSC", "side": "BUY", "shares": 1, "price": 252.0, "notional": 252.0},
                    {"ticker": "SPG", "side": "BUY", "shares": 2, "price": 222.15, "notional": 444.30},
                    {"ticker": "UNH", "side": "BUY", "shares": 1, "price": 308.0, "notional": 308.0},
                    {"ticker": "UNP", "side": "BUY", "shares": 1, "price": 227.0, "notional": 227.0},
                    {"ticker": "VZ", "side": "BUY", "shares": 3, "price": 44.0, "notional": 132.0},
                ],
                "execution_blocked": False,
            }
        ),
        encoding="utf-8",
    )
    paper_summary = {
        "min_trade_dollars": 100.0,
        "posttrade_recon_status": "PASS",
        "post_sell_rebudget": {
            "enabled": True,
            "status": "REBUILT",
            "final_buy_orders_submitted": [
                {
                    "ticker": "SPG",
                    "side": "BUY",
                    "quantity": 1.7121539920765638,
                    "price": 222.15,
                    "notional": 380.35499888965,
                    "reason": "post_sell_rebudget_capital_clipped",
                }
            ],
            "skipped_buy_orders": [
                {"ticker": ticker, "side": "BUY", "block_reason": "buy_blocked_insufficient_buying_power"}
                for ticker in ("NSC", "UNH", "UNP", "VZ")
            ],
        },
        "alpaca_submissions": [
            {"ticker": "MAR", "side": "SELL", "quantity": 1, "status": "FILLED", "filled_qty": 1},
            {
                "ticker": "SPG",
                "side": "BUY",
                "quantity": 1.7121539920765638,
                "status": "FILLED",
                "filled_qty": 1.7121539920765638,
            },
        ],
    }

    artifact_path, lifecycle = write_candidate_trade_lifecycle(
        run_id=run_id,
        trade_date=trade_date,
        run_root=run_root,
        planned_payload=planned_payload,
        paper_summary=paper_summary,
        allow_fractional=False,
        min_trade_dollars=100.0,
    )

    assert artifact_path.exists()
    assert lifecycle["counts"]["precompute_candidates"] == 8
    assert lifecycle["counts"]["candidate_rows"] == 8
    assert lifecycle["counts"]["passed_executable_filter"] == 6
    assert lifecycle["counts"]["intended_orders"] == 6
    assert lifecycle["counts"]["submitted"] == 2
    assert lifecycle["counts"]["accepted"] == 2
    assert lifecycle["counts"]["filled"] == 2
    assert lifecycle["counts"]["rejected"] == 0
    assert lifecycle["counts"]["filtered_executable"] == 2
    assert lifecycle["counts"]["suppressed"] == 6
    assert lifecycle["counts"]["clipped"] == 1
    assert lifecycle["counts"]["suppression_reason_counts"] == {
        "buy_blocked_insufficient_buying_power": 4,
        "min_notional": 2,
    }
    assert lifecycle["counts"]["clipping_reason_counts"] == {
        "post_sell_rebudget_capital_clipped": 1,
    }
    assert "sleeve_id" in lifecycle["provenance_fields"]
    assert {(row["ticker"], row["side"]) for row in lifecycle["candidates"]} == {
        ("MAR", "SELL"),
        ("MO", "SELL"),
        ("NEE", "SELL"),
        ("NSC", "BUY"),
        ("SPG", "BUY"),
        ("UNH", "BUY"),
        ("UNP", "BUY"),
        ("VZ", "BUY"),
    }

    mar = _row(lifecycle, "MAR", "SELL")
    assert mar["passed_min_notional"] is True
    assert mar["reached_intended_orders"] is True
    assert mar["post_sell_rebudget_status"] == "not_applicable"
    assert mar["submitted"] is True
    assert mar["accepted"] is True
    assert mar["filled"] is True
    assert mar["clipped"] is False
    assert mar["suppression_or_clipping_reason"] is None
    assert mar["final_submitted_shares"] == pytest.approx(1.0)
    assert mar["final_filled_shares"] == pytest.approx(1.0)

    for ticker, expected_notional in {"MO": 71.8898, "NEE": 87.4010}.items():
        row = _row(lifecycle, ticker, "SELL")
        assert row["passed_min_notional"] is False
        assert row["reached_intended_orders"] is False
        assert row["submitted"] is False
        assert row["accepted"] is False
        assert row["filled"] is False
        assert row["suppression_or_clipping_reason"] == "min_notional"
        assert row["decision_stage"] == "executable_filter"
        assert row["responsible_code_path"]["function"] == "_normalize_and_filter_executable_trades"
        assert row["estimated_unexecuted_notional"] == pytest.approx(expected_notional)
        assert row["opportunity_cost_basis"] == "normalized_notional:min_notional"

    for ticker, expected_notional in {"NSC": 252.0, "UNH": 308.0, "UNP": 227.0, "VZ": 132.0}.items():
        row = _row(lifecycle, ticker, "BUY")
        assert row["passed_min_notional"] is True
        assert row["reached_intended_orders"] is True
        assert row["post_sell_rebudget_status"] == "skipped"
        assert row["submitted"] is False
        assert row["accepted"] is False
        assert row["filled"] is False
        assert row["suppression_or_clipping_reason"] == "buy_blocked_insufficient_buying_power"
        assert row["decision_stage"] == "post_sell_rebudget"
        assert row["responsible_code_path"]["function"] == "_rebuild_post_sell_buy_trades"
        assert row["estimated_unexecuted_notional"] == pytest.approx(expected_notional)
        assert row["opportunity_cost_basis"] == "intended_order_notional"

    spg = _row(lifecycle, "SPG", "BUY")
    assert spg["passed_min_notional"] is True
    assert spg["reached_intended_orders"] is True
    assert spg["post_sell_rebudget_status"] == "submitted"
    assert spg["submitted"] is True
    assert spg["accepted"] is True
    assert spg["filled"] is True
    assert spg["clipped"] is True
    assert spg["suppression_or_clipping_reason"] == "post_sell_rebudget_capital_clipped"
    assert spg["decision_reason"] == "post_sell_rebudget_capital_clipped"
    assert spg["responsible_code_path"]["function"] == "_rebuild_post_sell_buy_trades"
    assert spg["final_submitted_shares"] == pytest.approx(1.7121539920765638)
    assert spg["final_filled_shares"] == pytest.approx(1.7121539920765638)
    assert spg["sleeve_id"] == "sleeve_quality"
    assert spg["strategy_id"] == "growth_engine_v4"
    assert spg["candidate_rank"] == 4
    assert spg["conviction_score"] == 0.77
    assert spg["estimated_unexecuted_notional"] == pytest.approx(63.94500111035)
    assert spg["opportunity_cost_basis"] == "intended_minus_submitted_notional"
