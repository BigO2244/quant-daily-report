from __future__ import annotations

import pandas as pd

from research.alpha_lab_v2.engine import StrategySpec
from research.canonical_decision_tape import build_decision_tape_for_sleeve


def test_decision_tape_schema_and_security_id_targets() -> None:
    signals = pd.DataFrame(
        [
            {"date": "2020-01-02", "ticker": "SHARADAR:1", "close": 10.0, "momentum_score": 0.3,
             "momentum_rank": 1.0, "signal_ready": True},
            {"date": "2020-01-02", "ticker": "SHARADAR:2", "close": 10.0, "momentum_score": 0.2,
             "momentum_rank": 2.0, "signal_ready": True},
            {"date": "2020-01-03", "ticker": "SHARADAR:1", "close": 11.0, "momentum_score": 0.1,
             "momentum_rank": 2.0, "signal_ready": True},
            {"date": "2020-01-03", "ticker": "SHARADAR:2", "close": 12.0, "momentum_score": 0.4,
             "momentum_rank": 1.0, "signal_ready": True},
            {"date": "2020-01-06", "ticker": "SHARADAR:1", "close": 12.0, "momentum_score": 0.2,
             "momentum_rank": 2.0, "signal_ready": True},
            {"date": "2020-01-06", "ticker": "SHARADAR:2", "close": 13.0, "momentum_score": 0.5,
             "momentum_rank": 1.0, "signal_ready": True},
        ]
    )
    tape, summary = build_decision_tape_for_sleeve(
        signals,
        {"SHARADAR:1": "AAA", "SHARADAR:2": "BBB"},
        sleeve="caerus_test",
        spec=StrategySpec(
            name="test_top1",
            hypothesis_id="TEST",
            description="test",
            top_n=1,
            rebalance_mode="daily",
            transaction_cost_bps=0.0,
        ),
        start_date="2020-01-02",
        end_date="2020-01-06",
    )
    assert list(tape.columns) == [
        "trade_date", "security_id", "ticker", "sleeve", "candidate", "rank", "score", "target_weight"
    ]
    assert "SHARADAR:1" in set(tape["security_id"])
    assert set(tape["ticker"]) == {"AAA", "BBB"}
    selected = tape[tape["target_weight"] > 0]
    assert selected.groupby("trade_date")["security_id"].count().to_dict() == {
        "2020-01-02": 1,
        "2020-01-03": 1,
    }
    assert summary["selected_row_count"] == 2
    assert summary["average_selected_count"] == 1.0
