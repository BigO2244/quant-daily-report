from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from core.precompute_contract import (
    REASON_PRECOMPUTE_INCOMPLETE,
    REASON_PRECOMPUTE_MISSING,
    REASON_PRECOMPUTE_WRONG_TRADE_DATE,
    load_precompute_inputs,
    validate_precompute_contract,
    write_precompute_bundle,
)
from core.timing_policy import classify_timing
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def test_write_and_load_precompute_bundle(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    trade_date = "2026-03-17"
    contract_path = write_precompute_bundle(
        trade_date=trade_date,
        run_id="run123",
        mode="PAPER",
        daily_snapshot={
            "asof": trade_date,
            "signals_snapshot_path": "signals/2026-03-17.json",
            "proposed_trades": [{"ticker": "AAPL", "action": "BUY"}],
        },
        signals_payload={"trade_date": trade_date, "weights": [{"ticker": "AAPL", "target_weight": 0.5}]},
        execution_payload={
            "trade_date": trade_date,
            "execution_status": "PLANNED",
            "planner_intended_trades_count": 1,
            "execution_eligible_trades_count": 1,
            "trades": [
                {
                    "ticker": "AAPL",
                    "side": "BUY",
                    "shares": 10,
                    "entry_price": 100.0,
                    "notional": 1000.0,
                }
            ],
        },
    )
    assert contract_path.exists()

    snapshot, payload, contract, reason = load_precompute_inputs(trade_date=trade_date)
    assert reason is None
    assert contract is not None
    assert snapshot is not None
    assert payload is not None
    assert snapshot["signals_snapshot_path"].endswith("outputs/precompute/2026-03-17/signals.json")
    assert payload["trades"][0]["ticker"] == "AAPL"
    assert payload["live_strategy_id"] == "growth_engine_v4"
    assert payload["execution_target_strategy_id"] == "growth_engine_v4"
    assert payload["paper_governed_strategy_id"] == "caerus_polaris"
    assert payload["paper_mapping_status"] == "ENGINE_BASELINE_ALIAS"
    assert payload["live_pilot_governed_strategy_id"] == "caerus_orion"
    assert payload["live_pilot_tracks_approved_strategy"] is False
    assert payload["execution_target_source"] == "outputs/precompute/2026-03-17/signals.json"
    assert payload["execution_target_type"] == "precompute_signals"
    assert payload["shadow_baseline_strategy"] == "caerus_polaris"
    assert payload["shadow_baseline_source"] == "outputs/shadow_candidates/2026-03-17/caerus_polaris.json"
    assert payload["live_tracks_shadow_baseline"] is False
    assert payload["target_book_metrics"]["metric_scope"] == (
        "desired_target_book_not_executed_fills"
    )
    assert payload["desired_one_way_turnover_pct"] is None


def test_validate_precompute_contract_missing() -> None:
    valid, reason = validate_precompute_contract(None, expected_trade_date="2026-03-17")
    assert valid is False
    assert reason == REASON_PRECOMPUTE_MISSING


def test_nonfinite_execution_trade_writes_strict_json_and_invalid_contract(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    trade_date = "2026-03-17"
    contract_path = write_precompute_bundle(
        trade_date=trade_date,
        run_id="run-bad-price",
        mode="PAPER",
        daily_snapshot={"asof": trade_date, "last_price": float("nan")},
        signals_payload={"trade_date": trade_date},
        execution_payload={
            "trade_date": trade_date,
            "execution_status": "PLANNED",
            "trades": [
                {
                    "ticker": "KLAC",
                    "side": "BUY",
                    "shares": 1,
                    "entry_price": float("nan"),
                    "notional": float("nan"),
                }
            ],
        },
    )

    raw_payload = (
        contract_path.parent / "planned_execution_payload.json"
    ).read_text(encoding="utf-8")
    assert "NaN" not in raw_payload
    assert "Infinity" not in raw_payload
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    assert contract["status"] == "invalid"
    assert contract["validated_for_execution"] is False
    assert contract["validation_failures"]


def test_validate_precompute_contract_wrong_date() -> None:
    valid, reason = validate_precompute_contract(
        {
            "artifact_type": "alpaca_precompute_bundle",
            "trade_date": "2026-03-16",
            "mode": "PAPER",
            "status": "complete",
            "validated_for_execution": True,
            "files": {"daily_snapshot": "x", "signals": "y", "planned_execution_payload": "z"},
        },
        expected_trade_date="2026-03-17",
    )
    assert valid is False
    assert reason == REASON_PRECOMPUTE_WRONG_TRADE_DATE


def test_validate_precompute_contract_incomplete(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle_dir = Path("outputs/precompute/2026-03-17")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    contract = {
        "artifact_type": "alpaca_precompute_bundle",
        "trade_date": "2026-03-17",
        "mode": "PAPER",
        "status": "complete",
        "validated_for_execution": True,
        "files": {
            "daily_snapshot": "daily_snapshot.json",
            "signals": "signals.json",
            "planned_execution_payload": "planned_execution_payload.json",
        },
    }
    valid, reason = validate_precompute_contract(contract, expected_trade_date="2026-03-17")
    assert valid is False
    assert reason == REASON_PRECOMPUTE_INCOMPLETE


def test_validate_precompute_contract_accepts_legacy_alpaca_mode_alias(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    bundle_dir = Path("outputs/precompute/2026-03-17")
    bundle_dir.mkdir(parents=True, exist_ok=True)
    for name in ("daily_snapshot.json", "signals.json", "planned_execution_payload.json"):
        (bundle_dir / name).write_text("{}\n", encoding="utf-8")
    contract = {
        "artifact_type": "alpaca_precompute_bundle",
        "trade_date": "2026-03-17",
        "mode": "ALPACA",
        "status": "complete",
        "validated_for_execution": True,
        "files": {
            "daily_snapshot": "daily_snapshot.json",
            "signals": "signals.json",
            "planned_execution_payload": "planned_execution_payload.json",
        },
    }

    valid, reason = validate_precompute_contract(contract, expected_trade_date="2026-03-17", expected_mode="PAPER")

    assert valid is True
    assert reason is None


def test_timing_classification_retry_window() -> None:
    timing = classify_timing(
        now=dt.datetime(2026, 3, 17, 10, 15, tzinfo=ET),
        workflow_start_et=dt.datetime(2026, 3, 17, 10, 14, tzinfo=ET),
        retry_attempt=True,
    )
    assert timing["timing_status"] == "retry_window"


def test_timing_classification_degraded_late() -> None:
    timing = classify_timing(
        now=dt.datetime(2026, 3, 17, 9, 40, tzinfo=ET),
        first_submit_et=dt.datetime(2026, 3, 17, 9, 40, tzinfo=ET),
    )
    assert timing["timing_status"] == "degraded_late"
