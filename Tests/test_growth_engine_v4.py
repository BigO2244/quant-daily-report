import json

import pandas as pd
import pytest

from core import benchmark_v4
from core.growth_engine_v4 import evaluate_stop_exits, is_monday_rebalance
from paper.paper_broker import load_targets


def test_benchmark_starts_at_10000_on_inception(tmp_path, monkeypatch):
    monkeypatch.setattr(
        benchmark_v4,
        "_fetch_spy_adj_close",
        lambda start, end: pd.Series(
            [500.0, 505.0],
            index=pd.to_datetime(["2026-02-23", "2026-02-24"]),
            dtype=float,
        ),
    )
    out = benchmark_v4.update_inception_nav_series(
        asof_date="2026-02-23", model_nav=10_000.0, output_path=str(tmp_path / "inception.csv")
    )
    assert not out.empty
    assert float(out.iloc[0]["model_nav"]) == pytest.approx(10_000.0)
    assert float(out.iloc[0]["spy_nav"]) == pytest.approx(10_000.0)


def test_rebalance_runs_on_mondays():
    assert is_monday_rebalance("2026-03-02") is True
    assert is_monday_rebalance("2026-03-03") is False


def test_stop_triggers_generate_exit_orders():
    positions = pd.DataFrame([{"ticker": "AAPL", "entry_price": 100.0}])
    prices = pd.DataFrame([{"ticker": "AAPL", "close": 84.0, "sma_100": 90.0}])
    exits = evaluate_stop_exits(positions, prices, asof_date="2026-03-03")
    assert len(exits) == 1
    assert exits[0].ticker == "AAPL"


def test_disallow_leveraged_or_inverse_etf(tmp_path):
    payload = {
        "snapshot_date": "2026-03-02",
        "signals": [
            {"ticker": "SPXL", "sleeve": "core", "target_weight": 0.5},
            {"ticker": "AAPL", "sleeve": "core", "target_weight": 0.5},
        ],
    }
    p = tmp_path / "signals.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_targets(str(p))
