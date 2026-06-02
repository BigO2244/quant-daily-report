from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.promotion_readiness_windows import build_promotion_readiness_windows


def _write_nav(root: Path, rows: list[dict]) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _nav_rows(days: int, *, lyra_step: float = 0.002, orion_step: float = 0.001, polaris_step: float = 0.0005, include_spy: bool = True) -> list[dict]:
    dates = pd.date_range("2026-01-01", periods=days, freq="B")
    rows = []
    for i, day in enumerate(dates):
        row = {
            "date": day.date().isoformat(),
            "caerus_polaris": 1.0 + polaris_step * i,
            "caerus_orion": 1.0 + orion_step * i,
            "caerus_lyra": 1.0 + lyra_step * i,
        }
        if include_spy:
            row["spy_benchmark"] = 1.0 + 0.0002 * i
        rows.append(row)
    return rows


def _write_comparison(root: Path, trade_date: str) -> None:
    strategies = {}
    for slug, count, weight in (("caerus_polaris", 10, 0.1), ("caerus_orion", 5, 0.2), ("caerus_lyra", 5, 0.2)):
        strategies[slug] = {
            "expected_turnover": 0.1,
            "weight_concentration": {"holdings_count": count, "top3_concentration": weight * 3},
            "holdings": [{"ticker": f"{slug[-1]}{i}", "target_weight": weight} for i in range(count)],
        }
    path = root / "outputs" / "shadow_candidates" / trade_date / "comparison.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trade_date": trade_date, "strategies": strategies}, sort_keys=True), encoding="utf-8")


def test_insufficient_windows_block_promotion(tmp_path):
    _write_nav(tmp_path, _nav_rows(10))

    payload = build_promotion_readiness_windows(trade_date="2026-01-14", repo_root=tmp_path)

    assert payload["promotion_recommendation"] == "NO_PROMOTION_RECOMMENDED"
    assert payload["strategies"]["caerus_lyra"]["windows"]["20"]["readiness_state"] == "NOT_READY"
    assert "insufficient_observation_count" in payload["strategies"]["caerus_lyra"]["windows"]["20"]["reason_codes"]


def test_lyra_cannot_promote_with_insufficient_observations(tmp_path):
    _write_nav(tmp_path, _nav_rows(21, lyra_step=0.01, orion_step=0.0, polaris_step=0.0))

    payload = build_promotion_readiness_windows(trade_date="2026-01-29", repo_root=tmp_path)

    assert payload["strategies"]["caerus_lyra"]["windows"]["40"]["readiness_state"] == "NOT_READY"
    assert payload["promotion_recommendation"] == "NO_PROMOTION_RECOMMENDED"


def test_missing_polaris_and_missing_spy_are_conservative(tmp_path):
    rows = _nav_rows(65, include_spy=False)
    for row in rows:
        row.pop("caerus_polaris")
    _write_nav(tmp_path, rows)

    payload = build_promotion_readiness_windows(trade_date=rows[-1]["date"], repo_root=tmp_path)

    lyra_60 = payload["strategies"]["caerus_lyra"]["windows"]["60"]
    assert lyra_60["readiness_state"] == "NOT_READY"
    assert "missing_polaris_benchmark" in lyra_60["reason_codes"]
    assert "missing_spy_benchmark" in lyra_60["reason_codes"]


def test_window_calculations_are_date_bounded_and_deterministic(tmp_path):
    rows = _nav_rows(70, lyra_step=0.001)
    future = dict(rows[-1])
    future["date"] = "2026-12-31"
    future["caerus_lyra"] = 100.0
    rows.append(future)
    _write_nav(tmp_path, rows)
    _write_comparison(tmp_path, rows[60]["date"])

    first = build_promotion_readiness_windows(trade_date=rows[60]["date"], repo_root=tmp_path)
    second = build_promotion_readiness_windows(trade_date=rows[60]["date"], repo_root=tmp_path)

    assert first == second
    assert first["strategies"]["caerus_lyra"]["windows"]["20"]["total_return"] < 1.0
    assert "holdings_metrics_missing" not in first["strategies"]["caerus_lyra"]["windows"]["20"]["reason_codes"]
