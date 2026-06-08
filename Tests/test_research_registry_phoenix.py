from __future__ import annotations

from pathlib import Path

import pandas as pd

from research.phoenix.strategy import PhoenixConfig
from research_registry.research.phoenix import build_phoenix_model_quality_research


def _row(date: pd.Timestamp, ticker: str, close: float, volume: float = 1_000_000.0) -> dict[str, object]:
    return {"date": date, "ticker": ticker, "open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": volume}


def _panel(*, crisis: bool = True, include_future: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=70)
    trade_idx = 59
    rows: list[dict[str, object]] = []
    for i, date in enumerate(dates):
        spy = 100.0 + i * 0.04
        aaa = 80.0 + i * 0.02
        bbb = 70.0 + i * 0.01
        if crisis and trade_idx - 5 < i <= trade_idx:
            spy *= 0.96
            aaa *= 0.82
        if include_future and i > trade_idx:
            bbb *= 2.0
        vol = 4_000_000.0 if crisis and trade_idx - 2 <= i <= trade_idx else 1_000_000.0
        rows.extend([_row(date, "SPY", spy), _row(date, "AAA", aaa, vol), _row(date, "BBB", bbb)])
    return pd.DataFrame(rows)


def _config() -> PhoenixConfig:
    return PhoenixConfig(
        top_n=1,
        max_weight=0.5,
        min_median_dollar_volume_20d=0.0,
        min_history_days=40,
        local_stress_return_5d=-0.03,
        local_stress_volume_shock=1.1,
        market_stress_spy_return_5d=-0.02,
        max_position_vol_ann=10.0,
    )


def test_phoenix_model_quality_research_is_deterministic(tmp_path: Path) -> None:
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())
    first = build_phoenix_model_quality_research(panel=_panel(), trade_date=trade_date, repo_root=tmp_path, config=_config())
    second = build_phoenix_model_quality_research(panel=_panel().sample(frac=1.0, random_state=1), trade_date=trade_date, repo_root=tmp_path, config=_config())

    assert first["target_weights"] == second["target_weights"]
    assert first["target_candidates"] == second["target_candidates"]
    assert first["governance_label"] == "RESEARCH_ONLY"
    assert first["execution_impact"] == "NON_EXECUTIONAL"


def test_phoenix_model_quality_crisis_gating_and_inactive_behavior(tmp_path: Path) -> None:
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())

    active = build_phoenix_model_quality_research(panel=_panel(crisis=True), trade_date=trade_date, repo_root=tmp_path, config=_config(), write=False)
    inactive = build_phoenix_model_quality_research(panel=_panel(crisis=False), trade_date=trade_date, repo_root=tmp_path, config=_config(), write=False)

    assert active["active"] is True
    assert active["target_candidates"]
    assert inactive["active"] is False
    assert "NO_CRISIS_REGIME" in inactive["reason_codes"]


def test_phoenix_model_quality_missing_data_degrades(tmp_path: Path) -> None:
    payload = build_phoenix_model_quality_research(panel=pd.DataFrame(), trade_date="2026-06-02", repo_root=tmp_path, config=_config())

    assert payload["status"] == "NO_DATA"
    assert payload["target_candidates"] == []
    assert "NO_PRICE_DATA" in payload["reason_codes"]


def test_phoenix_model_quality_selection_ignores_future_rows(tmp_path: Path) -> None:
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())

    point_in_time = build_phoenix_model_quality_research(panel=_panel(), trade_date=trade_date, repo_root=tmp_path, config=_config(), write=False)
    with_future = build_phoenix_model_quality_research(panel=_panel(include_future=True), trade_date=trade_date, repo_root=tmp_path, config=_config(), write=False)

    assert point_in_time["target_weights"] == with_future["target_weights"]
    assert point_in_time["rank_table"] == with_future["rank_table"]
