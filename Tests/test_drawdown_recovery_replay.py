from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.research.build_drawdown_recovery_replay import (
    _rotation_guard_scale,
    build_artifact,
    discover_decisions,
)


def _write_signal(root: Path, date: str, weights: dict[str, float]) -> None:
    path = root / "outputs" / "precompute" / date / "signals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "snapshot_date": date,
        "signals": [
            {"ticker": ticker, "target_weight": weight}
            for ticker, weight in weights.items()
        ],
        "strategy_identity": {
            "live_strategy_id": "growth_engine_v4",
            "live_tracks_shadow_baseline": False,
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _price_panel(root: Path, dates: pd.DatetimeIndex, tickers: list[str]) -> Path:
    rows = []
    for date_index, date in enumerate(dates):
        for ticker_index, ticker in enumerate(tickers):
            if ticker == "SPY":
                close = 100.0 + date_index * 0.2
            else:
                close = 100.0 + ticker_index - max(0, date_index - 21) * 1.5
            rows.append({"date": date, "ticker": ticker, "close": close})
    path = root / "prices.parquet"
    pd.DataFrame(rows).to_parquet(path, index=False)
    return path


def test_discover_decisions_preserves_stored_identity_and_cash(tmp_path: Path) -> None:
    _write_signal(tmp_path, "2026-07-01", {"AAA": 0.6, "BBB": 0.3})
    decisions, inventory = discover_decisions(
        tmp_path,
        start="2026-07-01",
        end="2026-07-01",
    )

    decision = decisions[pd.Timestamp("2026-07-01")]
    assert decision.weights["CASH"] == pytest.approx(0.1)
    assert inventory[0]["equity_name_count"] == 2
    assert inventory[0]["live_strategy_id"] == "growth_engine_v4"


def test_rotation_guard_uses_only_prior_closes() -> None:
    dates = pd.bdate_range("2026-06-01", periods=25)
    close = pd.DataFrame(index=dates, columns=["SPY", "AAA", "BBB", "CCC"], dtype=float)
    close["SPY"] = range(100, 125)
    close.loc[:, ["AAA", "BBB", "CCC"]] = 100.0
    close.loc[dates[-10]:, ["AAA", "BBB", "CCC"]] = [
        [100.0 - row] * 3 for row in range(10)
    ]
    target = pd.Series({"AAA": 0.3, "BBB": 0.3, "CCC": 0.3, "CASH": 0.1})

    scale, meta = _rotation_guard_scale(
        date=dates[-1] + pd.Timedelta(days=1),
        target=target,
        close=close,
    )

    assert scale in {0.25, 0.5}
    assert meta["below_sma20_fraction"] == 1.0
    assert meta["cohort_relative_return"] < -0.03


def test_build_artifact_flags_dynamic_broad_reconstruction_as_unavailable(
    tmp_path: Path,
) -> None:
    dates = pd.bdate_range("2026-06-01", periods=30)
    tickers = [f"T{idx:02d}" for idx in range(18)]
    broad = {ticker: 1.0 / len(tickers) for ticker in tickers}
    concentrated = {ticker: 0.19 for ticker in tickers[:5]}
    _write_signal(tmp_path, str(dates[20].date()), broad)
    _write_signal(tmp_path, str(dates[21].date()), concentrated)
    _write_signal(tmp_path, str(dates[22].date()), concentrated)
    price_path = _price_panel(tmp_path, dates, ["SPY", *tickers])

    payload, artifact = build_artifact(
        repo=tmp_path,
        artifact_date="2026-07-28",
        start=str(dates[20].date()),
        end=str(dates[-2].date()),
        signals_root=Path("outputs/precompute"),
        price_panel=price_path,
        factor_panel=None,
        output_root=Path("outputs/research/drawdown_recovery"),
        cost_bps=10.0,
    )

    assert artifact.exists()
    assert payload["broad_anchor"]["equity_name_count"] == 18
    assert payload["evidence_assessment"]["decision_grade"] is False
    assert any(
        "dynamic daily 18-name" in claim
        for claim in payload["evidence_assessment"]["prohibited_claims"]
    )
    policy_ids = {row["policy_id"] for row in payload["metrics"]}
    assert "observed_daily_targets" in policy_ids
    assert "observed_weekly_targets" in policy_ids
    assert "last_exact_broad_book_buy_hold" in policy_ids
