from __future__ import annotations

import pandas as pd
import pytest

from core.concentration import concentrate_targets


def _book(rows):
    return pd.DataFrame(rows, columns=["ticker", "target_weight", "sleeve"])


def test_top5_conviction_weighted_deploys_and_orders_by_conviction():
    book = _book([
        ("AAPL", 0.079, "trend"), ("PNC", 0.056, "mr"), ("QCOM", 0.055, "quality"),
        ("GM", 0.054, "value"), ("VZ", 0.052, "trend"), ("COST", 0.041, "mr"),
        ("FTNT", 0.039, "quality"), ("XYZ", 0.02, "value"),
    ])
    out = concentrate_targets(book, top_n=5, max_position_weight=0.50, target_cash_weight=0.05)
    assert list(out["ticker"]) == ["AAPL", "PNC", "QCOM", "GM", "VZ"]
    # Deploys ~95% (5% cash), none over the 50% cap.
    assert out["target_weight"].sum() == pytest.approx(0.95, abs=1e-6)
    assert (out["target_weight"] <= 0.50 + 1e-9).all()
    # Highest conviction gets the largest weight.
    assert out.iloc[0]["target_weight"] >= out.iloc[-1]["target_weight"]


def test_strong_conviction_runs_to_cap_and_spills_rest():
    # One dominant name + two small ones, top_n=3, cap 0.50.
    book = _book([("BIG", 0.80, "trend"), ("MID", 0.10, "quality"), ("SMALL", 0.05, "value")])
    out = concentrate_targets(book, top_n=3, max_position_weight=0.50, target_cash_weight=0.05)
    by = dict(zip(out["ticker"], out["target_weight"]))
    # BIG is clipped at the 50% cap; its overflow redistributes to MID/SMALL (not to cash
    # while uncapped names remain), so the full 95% deploys.
    assert by["BIG"] == pytest.approx(0.50, abs=1e-6)
    assert out["target_weight"].sum() == pytest.approx(0.95, abs=1e-6)
    assert by["MID"] > by["SMALL"]


def test_cap_binds_everywhere_leaves_cash():
    # 2 names, cap 0.40 -> max deployable 0.80 < 0.95 invested -> 0.15 stays cash.
    book = _book([("A", 0.5, "t"), ("B", 0.5, "t")])
    out = concentrate_targets(book, top_n=2, max_position_weight=0.40, target_cash_weight=0.05)
    assert out["target_weight"].sum() == pytest.approx(0.80, abs=1e-6)
    assert all(w == pytest.approx(0.40) for w in out["target_weight"])


def test_name_count_floored_so_budget_is_reachable():
    # top_n=1 with 50% cap cannot deploy 95%; must widen to ceil(0.95/0.5)=2 names.
    book = _book([("A", 0.6, "t"), ("B", 0.3, "t"), ("C", 0.1, "t")])
    out = concentrate_targets(book, top_n=1, max_position_weight=0.50, target_cash_weight=0.05)
    assert len(out) == 2
    assert out["target_weight"].sum() == pytest.approx(0.95, abs=1e-6)


def test_drops_cash_and_nonpositive_and_handles_fewer_names():
    book = _book([("CASH", 0.10, ""), ("A", 0.5, "t"), ("B", 0.0, "t"), ("C", 0.4, "t")])
    out = concentrate_targets(book, top_n=5, max_position_weight=0.50, target_cash_weight=0.05)
    assert set(out["ticker"]) == {"A", "C"}
    assert "CASH" not in set(out["ticker"])


def test_empty_and_validation():
    assert concentrate_targets(pd.DataFrame(columns=["ticker", "target_weight"])).empty
    with pytest.raises(ValueError):
        concentrate_targets(_book([("A", 1.0, "t")]), top_n=0)
    with pytest.raises(ValueError):
        concentrate_targets(_book([("A", 1.0, "t")]), max_position_weight=1.5)
