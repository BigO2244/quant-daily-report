from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.target_book_metrics import (
    build_target_book_metrics,
    latest_prior_signals,
    target_weights,
)


def test_target_weights_adds_cash_and_normalises() -> None:
    weights = target_weights(
        {
            "signals": [
                {"ticker": "AAA", "target_weight": 0.6},
                {"ticker": "BBB", "target_weight": 0.3},
            ]
        }
    )
    assert weights["CASH"] == pytest.approx(0.1)
    assert sum(weights.values()) == pytest.approx(1.0)


def test_metrics_report_one_way_turnover_including_cash() -> None:
    previous = {
        "signals": [
            {"ticker": "AAA", "target_weight": 0.5},
            {"ticker": "BBB", "target_weight": 0.5},
        ]
    }
    current = {
        "signals": [
            {"ticker": "AAA", "target_weight": 0.5},
            {"ticker": "CCC", "target_weight": 0.4},
            {"ticker": "CASH", "target_weight": 0.1},
        ]
    }
    metrics = build_target_book_metrics(
        current_payload=current,
        current_source="current",
        previous_payload=previous,
        previous_source="previous",
    )
    assert metrics["status"] == "AVAILABLE"
    assert metrics["desired_one_way_turnover_pct"] == pytest.approx(0.5)
    assert metrics["desired_gross_l1_turnover_pct"] == pytest.approx(1.0)
    assert metrics["name_overlap_count"] == 1


def test_latest_prior_signals_ignores_same_day(tmp_path: Path) -> None:
    for date in ("2026-07-01", "2026-07-02"):
        path = tmp_path / date / "signals.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps({"signals": [{"ticker": date[-2:], "target_weight": 1.0}]}),
            encoding="utf-8",
        )
    payload, source = latest_prior_signals(root=tmp_path, trade_date="2026-07-02")
    assert payload is not None
    assert source is not None and "2026-07-01" in source
