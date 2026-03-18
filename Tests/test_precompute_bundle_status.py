from __future__ import annotations

from pathlib import Path

from core.precompute_contract import write_precompute_bundle
from scripts.precompute_bundle_status import describe_bundle_status


def test_bundle_status_missing_requires_rebuild(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    status = describe_bundle_status(report_date="2026-03-18")

    assert status["bundle_status"] == "MISSING"
    assert status["bundle_should_rebuild"] is True


def test_bundle_status_valid_reuses_existing_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_precompute_bundle(
        trade_date="2026-03-18",
        run_id="run123",
        mode="ALPACA",
        daily_snapshot={"asof": "2026-03-18", "proposed_trades": [{"ticker": "AAPL"}]},
        signals_payload={"weights": [{"ticker": "AAPL", "target_weight": 0.5}]},
        execution_payload={
            "trade_date": "2026-03-18",
            "execution_status": "PLANNED",
            "planner_intended_trades_count": 1,
            "execution_eligible_trades_count": 1,
            "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        },
    )

    status = describe_bundle_status(report_date="2026-03-18")

    assert status["bundle_status"] == "VALID"
    assert status["bundle_should_rebuild"] is False


def test_bundle_status_force_refresh_marks_refresh_requested(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    write_precompute_bundle(
        trade_date="2026-03-18",
        run_id="run123",
        mode="ALPACA",
        daily_snapshot={"asof": "2026-03-18", "proposed_trades": [{"ticker": "AAPL"}]},
        signals_payload={"weights": [{"ticker": "AAPL", "target_weight": 0.5}]},
        execution_payload={
            "trade_date": "2026-03-18",
            "execution_status": "PLANNED",
            "planner_intended_trades_count": 1,
            "execution_eligible_trades_count": 1,
            "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        },
    )

    status = describe_bundle_status(report_date="2026-03-18", force_refresh=True)

    assert status["bundle_status"] == "REFRESH_REQUESTED"
    assert status["bundle_should_rebuild"] is True
