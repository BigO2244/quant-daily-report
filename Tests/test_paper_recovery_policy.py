from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from core.paper_recovery_policy import (
    derive_weekly_rotation_guard_payload,
    factor_rotation_scale,
    validate_recovery_config,
)


def _factor_closes() -> pd.DataFrame:
    dates = pd.bdate_range("2026-06-01", periods=25)
    frame = pd.DataFrame(index=dates)
    frame["SPY"] = [100 + index * 0.2 for index in range(len(dates))]
    frame["RSP"] = [100 + index * 0.3 for index in range(len(dates))]
    frame["QQQ"] = [110 - index * 0.3 for index in range(len(dates))]
    frame["SMH"] = [120 - index * 0.8 for index in range(len(dates))]
    frame["MTUM"] = [105 - index * 0.4 for index in range(len(dates))]
    return frame


def test_config_must_be_explicitly_paper_only_and_approved() -> None:
    result = validate_recovery_config(
        {
            "enabled": True,
            "policy_id": "weekly_rotation_guard_v1",
            "approval_status": "APPROVED_FOR_PAPER_OBSERVATION",
            "paper_only": True,
            "live_eligible": False,
        },
        requested_policy="weekly_rotation_guard_v1",
    )
    assert result["status"] == "PASS"


def test_factor_guard_uses_strictly_prior_closes_and_locks() -> None:
    closes = _factor_closes()
    decision_date = str((closes.index[-1] + pd.Timedelta(days=1)).date())
    scale, meta = factor_rotation_scale(
        decision_date=decision_date,
        closes=closes,
    )
    assert scale == 0.25
    assert meta["uses_strictly_prior_closes"] is True
    assert meta["latest_input_date"] < decision_date


def test_weekly_policy_reuses_first_signal_and_routes_guard_residual_to_cash(
    tmp_path: Path,
) -> None:
    for date, ticker in (("2026-07-06", "AAA"), ("2026-07-07", "BBB")):
        path = tmp_path / date / "signals.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "snapshot_date": date,
                    "strategy_identity": {
                        "execution_target_strategy_id": "growth_engine_v4"
                    },
                    "signals": [
                        {
                            "ticker": ticker,
                            "target_weight": 0.95,
                            "sleeve": "sleeve_trend",
                        },
                        {"ticker": "CASH", "target_weight": 0.05},
                    ],
                }
            ),
            encoding="utf-8",
        )

    derived, meta = derive_weekly_rotation_guard_payload(
        precompute_root=tmp_path,
        trade_date="2026-07-08",
        factor_fetcher=lambda _start, _end: _factor_closes(),
    )

    by_ticker = {row["ticker"]: row for row in derived["signals"]}
    assert set(by_ticker) == {"AAA", "CASH"}
    assert by_ticker["AAA"]["target_weight"] == pytest.approx(0.2375)
    assert by_ticker["CASH"]["target_weight"] == pytest.approx(0.7625)
    assert meta["weekly_decision_date"] == "2026-07-06"
    assert meta["live_eligible"] is False
