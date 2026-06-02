from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.regime_attribution import (  # noqa: E402
    REGIME_LABELS,
    SCHEMA_VERSION,
    WARMUP_DAYS,
    build_regime_attribution,
)


def _write_nav(root: Path, rows: list[dict]) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _series_steady(
    days: int,
    *,
    polaris_step: float = 0.0005,
    orion_step: float = 0.0008,
    lyra_step: float = 0.001,
    spy_step: float = 0.0005,
    start: str = "2024-01-02",
) -> list[dict]:
    dates = pd.date_range(start, periods=days, freq="B")
    rows = []
    for i, day in enumerate(dates):
        rows.append(
            {
                "date": day.date().isoformat(),
                "caerus_polaris": 1.0 + polaris_step * i,
                "caerus_orion": 1.0 + orion_step * i,
                "caerus_lyra": 1.0 + lyra_step * i,
                "spy_benchmark": 1.0 + spy_step * i,
            }
        )
    return rows


def _series_with_panic(days: int = 80) -> list[dict]:
    """Build a NAV series where day 40 onward has a deep 5-day SPY drop."""
    rows = _series_steady(days)
    # Tank SPY by ~6% across days 40..44.
    for offset, multiplier in enumerate([0.99, 0.985, 0.98, 0.975, 0.965]):
        rows[40 + offset]["spy_benchmark"] = rows[40 + offset - 1]["spy_benchmark"] * multiplier
    # Keep falling for a few more days so the bear/high_vol windows pick up.
    for i in range(45, 50):
        rows[i]["spy_benchmark"] = rows[i - 1]["spy_benchmark"] * 0.99
    return rows


def _series_with_recovery(days: int = 90) -> list[dict]:
    rows = _series_with_panic(days)
    # Sharp 6%+ bounce across days 60..64 after preceding drawdown.
    for offset, multiplier in enumerate([1.015, 1.012, 1.013, 1.011, 1.014]):
        rows[60 + offset]["spy_benchmark"] = rows[60 + offset - 1]["spy_benchmark"] * multiplier
    return rows


def _series_with_high_vol(days: int = 80) -> list[dict]:
    rows = _series_steady(days, spy_step=0.0)
    # Inject alternating +/-2.5% daily moves over days 25..70 to push the
    # 20-day annualized volatility above 25% without triggering bull/bear
    # 20-day return thresholds.
    for i in range(25, 70):
        sign = 1 if i % 2 == 0 else -1
        rows[i]["spy_benchmark"] = rows[i - 1]["spy_benchmark"] * (1.0 + 0.025 * sign)
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_deterministic_regime_classification(tmp_path):
    _write_nav(tmp_path, _series_steady(80))
    first = build_regime_attribution(trade_date="2024-04-25", repo_root=tmp_path)
    second = build_regime_attribution(trade_date="2024-04-25", repo_root=tmp_path)
    assert first == second
    assert first["schema_version"] == SCHEMA_VERSION
    assert first["available"] is True


def test_high_vol_regime_classified(tmp_path):
    rows = _series_with_high_vol(80)
    _write_nav(tmp_path, rows)
    payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert payload["regime_distribution"]["high_vol"] > 0


def test_panic_regime_classified(tmp_path):
    rows = _series_with_panic(80)
    _write_nav(tmp_path, rows)
    payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert payload["regime_distribution"]["panic"] > 0


def test_recovery_regime_classified(tmp_path):
    rows = _series_with_recovery(90)
    _write_nav(tmp_path, rows)
    payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert payload["regime_distribution"]["recovery"] > 0


def test_insufficient_observations_lowers_confidence(tmp_path):
    rows = _series_steady(WARMUP_DAYS - 5)
    _write_nav(tmp_path, rows)
    payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert payload["available"] is False
    assert payload["confidence"] == "LOW"
    assert "history_below_warmup_window" in payload["reason_codes"]


def test_no_future_date_leakage(tmp_path):
    rows = _series_steady(120)
    _write_nav(tmp_path, rows)
    # Filter to a midpoint date — strategy metrics must use only the
    # history through that date, not the full 120-day series.
    mid_date = rows[60]["date"]
    mid_payload = build_regime_attribution(trade_date=mid_date, repo_root=tmp_path)
    full_payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    mid_classified_days = mid_payload["history_window"]["classified_days"]
    full_classified_days = full_payload["history_window"]["classified_days"]
    assert mid_classified_days < full_classified_days
    # No future date should leak into the mid-date payload's per-strategy
    # observation_count totals.
    lyra_mid_total = sum(
        row.get("observation_count", 0)
        for row in mid_payload["strategies"]["caerus_lyra"]["regimes"].values()
    )
    lyra_full_total = sum(
        row.get("observation_count", 0)
        for row in full_payload["strategies"]["caerus_lyra"]["regimes"].values()
    )
    assert lyra_mid_total < lyra_full_total


def test_missing_price_panel_behavior(tmp_path):
    payload = build_regime_attribution(trade_date="2026-06-02", repo_root=tmp_path)
    assert payload["available"] is False
    assert payload["confidence"] == "LOW"
    assert "missing_shadow_nav_series" in payload["reason_codes"]


def test_per_strategy_regime_scoring(tmp_path):
    rows = _series_steady(80, lyra_step=0.002, orion_step=0.001, polaris_step=0.0005)
    _write_nav(tmp_path, rows)
    payload = build_regime_attribution(trade_date=rows[-1]["date"], repo_root=tmp_path)
    # Steady upward NAV → mostly bull_trend / low_vol.
    classified_days = payload["history_window"]["classified_days"]
    assert classified_days > 0
    lyra_regimes = payload["strategies"]["caerus_lyra"]["regimes"]
    total_obs = sum(int(row.get("observation_count") or 0) for row in lyra_regimes.values())
    assert total_obs > 0
    # Lyra's avg_return should be higher than Polaris's in regimes both observed.
    for regime in REGIME_LABELS:
        l_row = lyra_regimes[regime]
        p_row = payload["strategies"]["caerus_polaris"]["regimes"][regime]
        if l_row["observation_count"] > 0 and p_row["observation_count"] > 0:
            assert l_row["average_return"] >= p_row["average_return"]


def test_artifacts_are_written(tmp_path):
    rows = _series_steady(40)
    _write_nav(tmp_path, rows)
    trade_date = rows[-1]["date"]
    build_regime_attribution(trade_date=trade_date, repo_root=tmp_path)
    json_path = tmp_path / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json"
    md_path = tmp_path / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.md"
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "Regime Attribution" in md_path.read_text(encoding="utf-8")
