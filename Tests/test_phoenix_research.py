from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.phoenix.artifacts import write_phoenix_research_artifacts
from research.phoenix.strategy import PHOENIX_STRATEGY_ID, PhoenixConfig, build_phoenix_snapshot


def _row(date: pd.Timestamp, ticker: str, close: float, volume: float = 1_000_000.0) -> dict[str, object]:
    return {
        "date": date,
        "ticker": ticker,
        "open": close * 1.005,
        "high": close * 1.015,
        "low": close * 0.985,
        "close": close,
        "volume": volume,
    }


def _phoenix_panel(*, include_future: bool = False) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=70)
    trade_idx = 59
    records: list[dict[str, object]] = []
    for i, date in enumerate(dates):
        spy_close = 100.0 + i * 0.05
        aaa_close = 80.0 + i * 0.08
        bbb_close = 60.0 + i * 0.03
        ccc_close = 50.0 + i * 0.02

        if trade_idx - 5 < i <= trade_idx:
            spy_close *= 0.96
            aaa_close *= 0.82
            bbb_close *= 0.95
        if i > trade_idx and include_future:
            bbb_close *= 1.60
            ccc_close *= 0.60

        aaa_volume = 4_000_000.0 if trade_idx - 2 <= i <= trade_idx else 1_000_000.0
        bbb_volume = 1_700_000.0 if trade_idx - 1 <= i <= trade_idx else 1_000_000.0
        records.extend(
            [
                _row(date, "SPY", spy_close, 3_000_000.0),
                _row(date, "AAA", aaa_close, aaa_volume),
                _row(date, "BBB", bbb_close, bbb_volume),
                _row(date, "CCC", ccc_close, 1_000_000.0),
            ]
        )
    return pd.DataFrame(records)


def _test_config(**overrides: object) -> PhoenixConfig:
    params = {
        "top_n": 2,
        "max_gross": 0.80,
        "max_weight": 0.40,
        "min_median_dollar_volume_20d": 0.0,
        "min_history_days": 40,
        "local_stress_return_5d": -0.03,
        "local_stress_volume_shock": 1.1,
        "market_stress_spy_return_5d": -0.02,
        "max_position_vol_ann": 10.0,
    }
    params.update(overrides)
    return PhoenixConfig(**params)


def test_phoenix_selection_is_deterministic() -> None:
    panel = _phoenix_panel()
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())
    config = _test_config()

    first = build_phoenix_snapshot(panel, trade_date=trade_date, config=config)
    second = build_phoenix_snapshot(panel.sample(frac=1.0, random_state=17), trade_date=trade_date, config=config)

    assert first["strategy_id"] == PHOENIX_STRATEGY_ID
    assert first["strategy_slug"] == PHOENIX_STRATEGY_ID
    assert first["target_weights"] == second["target_weights"]
    assert first["holdings"] == second["holdings"]
    assert first["holdings"][0]["ticker"] == "AAA"
    assert first["governance_label"] == "RESEARCH_ONLY"
    assert first["execution_impact"] == "NON_EXECUTIONAL"


def test_phoenix_snapshot_ignores_future_rows_for_selection() -> None:
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())
    config = _test_config()

    point_in_time = build_phoenix_snapshot(_phoenix_panel(), trade_date=trade_date, config=config)
    with_future_rows = build_phoenix_snapshot(_phoenix_panel(include_future=True), trade_date=trade_date, config=config)

    assert point_in_time["target_weights"] == with_future_rows["target_weights"]
    assert point_in_time["rank_table"] == with_future_rows["rank_table"]


def test_phoenix_empty_and_insufficient_data_outputs_are_valid() -> None:
    columns = ["date", "ticker", "open", "high", "low", "close", "volume"]
    empty = build_phoenix_snapshot(pd.DataFrame(columns=columns), trade_date="2026-03-26", config=_test_config())
    assert empty["status"] == "NO_DATA"
    assert empty["target_weights"] == {}
    assert empty["holdings"] == []
    assert empty["cash_weight"] == 1.0

    short_panel = _phoenix_panel().groupby("ticker", group_keys=False).head(10)
    insufficient = build_phoenix_snapshot(short_panel, trade_date="2026-01-15", config=_test_config())
    assert insufficient["status"] == "NO_ELIGIBLE_NAMES"
    assert insufficient["holdings"] == []
    assert insufficient["cash_weight"] == 1.0
    assert isinstance(insufficient["rank_table"], list)


def test_phoenix_artifacts_have_review_and_attribution_shape(tmp_path: Path) -> None:
    panel = _phoenix_panel()
    trade_date = str(pd.bdate_range("2026-01-01", periods=70)[59].date())

    manifest = write_phoenix_research_artifacts(
        panel=panel,
        trade_date=trade_date,
        start_date="2026-01-01",
        output_dir=tmp_path,
        config=_test_config(),
    )

    assert manifest["strategy_id"] == PHOENIX_STRATEGY_ID
    assert manifest["status"] == "OK"
    for artifact_path in manifest["artifacts"].values():
        assert Path(artifact_path).exists()

    holdings = json.loads(Path(manifest["artifacts"]["holdings"]).read_text(encoding="utf-8"))
    attribution = json.loads(Path(manifest["artifacts"]["attribution_inputs"]).read_text(encoding="utf-8"))
    decision_trace = json.loads(Path(manifest["artifacts"]["decision_trace"]).read_text(encoding="utf-8"))

    assert holdings["schema_version"] == "phoenix_snapshot_v1"
    assert holdings["strategy_id"] == PHOENIX_STRATEGY_ID
    assert holdings["strategy_slug"] == PHOENIX_STRATEGY_ID
    assert holdings["benchmark_symbol"] == "SPY"
    assert holdings["estimated_holding_period_days"] == "5-20"
    assert holdings["weight_concentration"]["holdings_count"] == len(holdings["holdings"])
    assert holdings["signal_diagnostics"]["diagnostic_fields"]
    assert attribution["return_convention"] == "weights_as_of_t"
    assert attribution["weights"] == holdings["target_weights"]
    assert decision_trace["non_goals"] == [
        "no broker submission",
        "no paper execution changes",
        "no live execution changes",
        "no Polaris/Orion/Lyra behavior changes",
    ]
