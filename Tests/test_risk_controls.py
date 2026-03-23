from __future__ import annotations

from pathlib import Path

import pytest
import pandas as pd

from core.risk_controls import RiskControls, read_peak_equity_state, update_peak_equity_state


def _targets(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ticker", "sleeve", "target_weight"])


def test_circuit_breaker_scales_targets_and_adds_cash() -> None:
    controls = RiskControls()
    result = controls.apply_to_targets(
        _targets(
            [
                ("AAA", "trend", 0.10),
                ("BBB", "value", 0.10),
                ("CCC", "value", 0.10),
                ("DDD", "value", 0.10),
            ]
        ),
        cash_target_weight=0.60,
        current_equity=85.0,
        peak_equity=100.0,
    )

    assert result.metrics["circuit_breaker_triggered"] is True
    assert result.cash_target_weight == pytest.approx(0.80)
    assert result.weights["target_weight"].sum() == pytest.approx(0.20)


def test_position_cap_trims_single_name_to_ten_percent() -> None:
    controls = RiskControls()
    result = controls.apply_to_targets(
        _targets([("AAA", "trend", 0.20), ("BBB", "value", 0.05)]),
        cash_target_weight=0.75,
    )

    aaa = result.weights.loc[result.weights["ticker"] == "AAA", "target_weight"].iloc[0]
    assert aaa == pytest.approx(0.10)
    assert result.cash_target_weight == pytest.approx(0.85)


def test_sector_cap_scales_sector_to_thirty_percent() -> None:
    controls = RiskControls()
    result = controls.apply_to_targets(
        _targets(
            [
                ("AAA", "trend", 0.10),
                ("BBB", "trend", 0.10),
                ("CCC", "value", 0.10),
                ("DDD", "value", 0.10),
                ("EEE", "value", 0.05),
            ]
        ),
        cash_target_weight=0.55,
        sector_map={"AAA": "Tech", "BBB": "Tech", "CCC": "Tech", "DDD": "Tech", "EEE": "Health"},
    )

    tech_weight = (
        result.weights.assign(
            sector=result.weights["ticker"].map(
                {"AAA": "Tech", "BBB": "Tech", "CCC": "Tech", "DDD": "Tech", "EEE": "Health"}
            )
        )
        .groupby("sector")["target_weight"]
        .sum()["Tech"]
    )
    assert tech_weight == pytest.approx(0.30)
    assert result.cash_target_weight == pytest.approx(0.65)


def test_exposure_cap_scales_non_cash_to_ninety_five_percent() -> None:
    controls = RiskControls()
    result = controls.apply_to_targets(
        _targets(
            [
                ("AAA", "trend", 0.10),
                ("BBB", "value", 0.10),
                ("CCC", "value", 0.10),
                ("DDD", "value", 0.10),
                ("EEE", "value", 0.10),
                ("FFF", "value", 0.10),
                ("GGG", "value", 0.10),
                ("HHH", "value", 0.10),
                ("III", "value", 0.10),
                ("JJJ", "value", 0.10),
            ]
        ),
        cash_target_weight=0.0,
    )

    assert result.metrics["net_long_weight"] == pytest.approx(0.95)
    assert result.cash_target_weight == pytest.approx(0.05)


def test_peak_equity_state_persists_high_water_mark(tmp_path: Path) -> None:
    peak_path = tmp_path / "peak_equity.json"

    first = update_peak_equity_state(
        current_equity=100.0,
        trade_date="2026-03-23",
        source="broker",
        path=peak_path,
    )
    second = update_peak_equity_state(
        current_equity=90.0,
        trade_date="2026-03-24",
        source="broker",
        path=peak_path,
    )
    saved = read_peak_equity_state(peak_path)

    assert first["peak_equity"] == pytest.approx(100.0)
    assert second["peak_equity"] == pytest.approx(100.0)
    assert second["drawdown_pct"] == pytest.approx(0.10)
    assert saved["peak_equity"] == pytest.approx(100.0)
