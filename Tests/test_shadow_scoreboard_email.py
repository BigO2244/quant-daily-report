from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from core.shadow_scoreboard import build_shadow_scoreboard
from core.execution_payload import STATUS_EXECUTED, STATUS_HALTED
from scripts import send_trading_confirmation_email as confirmation


ET = ZoneInfo("America/New_York")


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_shadow_artifacts(root: Path, trade_date: str = "2026-05-04") -> None:
    dated = root / "outputs" / "shadow_candidates" / trade_date
    _write_json(
        dated / "shadow_evaluation.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": {
                    "data_status": "OK",
                    "daily_return": 0.0068,
                    "cumulative_return": 0.0347,
                    "excess_return_vs_spy": -0.0765,
                    "constituent_change_count": 1,
                    "rolling_count_of_valid_days": 12,
                },
                "caerus_orion": {
                    "data_status": "OK",
                    "daily_return": 0.01,
                    "cumulative_return": 0.05,
                    "excess_return_vs_spy": -0.0611,
                    "constituent_change_count": 3,
                    "rolling_count_of_valid_days": 12,
                },
                "caerus_lyra": {
                    "data_status": "OK",
                    "daily_return": -0.002,
                    "cumulative_return": 0.02,
                    "excess_return_vs_spy": -0.0911,
                    "constituent_change_count": 5,
                    "rolling_count_of_valid_days": 8,
                },
                "spy_benchmark": {
                    "data_status": "OK",
                    "daily_return": 0.0028,
                    "cumulative_return": 0.1111,
                    "excess_return_vs_spy": 0.0,
                },
            },
        },
    )
    _write_json(
        dated / "comparison.json",
        {
            "trade_date": trade_date,
            "strategies": {
                slug: {
                    "expected_turnover": turnover,
                    "weight_concentration": {"top3_concentration": top3},
                }
                for slug, turnover, top3 in [
                    ("caerus_polaris", 0.12, 0.42),
                    ("caerus_orion", 0.18, 0.55),
                    ("caerus_lyra", 0.31, 0.61),
                ]
            },
        },
    )
    _write_json(
        dated / "feedback_loop_summary.json",
        {
            "trade_date": trade_date,
            "status": "PARTIAL",
            "strategies": {
                "polaris": {"learning_readiness": "MEDIUM"},
                "orion": {"learning_readiness": "HIGH"},
                "lyra": {"learning_readiness": "LOW"},
            },
            "system_learning_summary": {"ready_for_promotion_logic": False},
        },
    )


def test_shadow_scoreboard_renders_snapshot_when_artifacts_exist(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)

    scoreboard = build_shadow_scoreboard(tmp_path, "2026-05-04")

    assert scoreboard["status"] == "OK"
    assert "Shadow Strategy Snapshot" in scoreboard["text"]
    assert "Polaris:" in scoreboard["text"]
    assert "Data status: OK" in scoreboard["text"]
    assert "Today: +0.68%" in scoreboard["text"]
    assert "vs SPY: -7.65%" in scoreboard["text"]
    assert "Learning readiness: HIGH" in scoreboard["text"]


def test_shadow_scoreboard_unavailable_reason_when_missing(tmp_path: Path) -> None:
    scoreboard = build_shadow_scoreboard(tmp_path, "2026-05-04")

    assert scoreboard["status"] == "UNAVAILABLE"
    assert "Shadow snapshot unavailable: shadow directory missing for 2026-05-04" in scoreboard["text"]


def test_shadow_scoreboard_shows_no_data_daily_status(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "comparison.json"
    comparison = json.loads(comparison_path.read_text())
    comparison["status"] = "NO_DATA"
    comparison["reason_code"] = "PRICE_CACHE_STALE"
    comparison["data"] = {"coverage": {"end_date": "2026-05-03"}}
    _write_json(comparison_path, comparison)
    path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "shadow_evaluation.json"
    payload = json.loads(path.read_text())
    payload["strategies"]["caerus_polaris"]["data_status"] = "NO_DATA"
    payload["strategies"]["caerus_polaris"]["data_reason"] = "PRICE_CACHE_STALE"
    payload["strategies"]["caerus_polaris"]["daily_return"] = 0.0
    _write_json(path, payload)

    scoreboard = build_shadow_scoreboard(tmp_path, "2026-05-04")

    assert "Data status: NO_DATA" in scoreboard["text"]
    assert "Today: unavailable (PRICE_CACHE_STALE; cache coverage through 2026-05-03)" in scoreboard["text"]
    assert "Since inception: +3.47%" in scoreboard["text"]


def test_shadow_scoreboard_uses_completed_session_when_morning_artifact_is_false_stale(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, trade_date="2026-05-19")
    _write_shadow_artifacts(tmp_path, trade_date="2026-05-20")
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-20" / "comparison.json"
    comparison = json.loads(comparison_path.read_text())
    comparison["status"] = "NO_DATA"
    comparison["reason_code"] = "PRICE_CACHE_STALE"
    comparison["data"] = {"coverage": {"end_date": "2026-05-19"}}
    _write_json(comparison_path, comparison)
    evaluation_path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-20" / "shadow_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    for payload in evaluation["strategies"].values():
        payload["data_status"] = "NO_DATA"
        payload["data_reason"] = "PRICE_CACHE_STALE"
        payload["daily_return"] = 0.0
    _write_json(evaluation_path, evaluation)

    scoreboard = build_shadow_scoreboard(
        tmp_path,
        "2026-05-20",
        now=dt.datetime(2026, 5, 20, 9, 45, tzinfo=ET),
    )

    assert scoreboard["status"] == "OK"
    assert "Snapshot as of: 2026-05-19" in scoreboard["text"]
    assert "Data status: OK" in scoreboard["text"]
    assert "Today: +0.68%" in scoreboard["text"]
    assert "PRICE_CACHE_STALE" not in scoreboard["text"]
    assert "Data status: NO_DATA" not in scoreboard["text"]


def test_shadow_scoreboard_preserves_genuinely_stale_completed_session(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path, trade_date="2026-05-19")
    comparison_path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-19" / "comparison.json"
    comparison = json.loads(comparison_path.read_text())
    comparison["status"] = "NO_DATA"
    comparison["reason_code"] = "PRICE_CACHE_STALE"
    comparison["data"] = {"coverage": {"end_date": "2026-05-16"}}
    _write_json(comparison_path, comparison)
    evaluation_path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-19" / "shadow_evaluation.json"
    evaluation = json.loads(evaluation_path.read_text())
    evaluation["strategies"]["caerus_polaris"]["data_status"] = "NO_DATA"
    evaluation["strategies"]["caerus_polaris"]["data_reason"] = "PRICE_CACHE_STALE"
    evaluation["strategies"]["caerus_polaris"]["daily_return"] = 0.0
    _write_json(evaluation_path, evaluation)

    scoreboard = build_shadow_scoreboard(
        tmp_path,
        "2026-05-20",
        now=dt.datetime(2026, 5, 20, 9, 45, tzinfo=ET),
    )

    assert "Snapshot as of: 2026-05-19" in scoreboard["text"]
    assert "Data status: NO_DATA" in scoreboard["text"]
    assert "PRICE_CACHE_STALE" in scoreboard["text"]


def test_shadow_scoreboard_missing_artifact_is_explicit(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "comparison.json"
    path.unlink()

    scoreboard = build_shadow_scoreboard(tmp_path, "2026-05-04")

    assert scoreboard["status"] == "UNAVAILABLE"
    assert "missing comparison.json" in scoreboard["text"]


def test_shadow_scoreboard_schema_mismatch_is_degraded(tmp_path: Path) -> None:
    _write_shadow_artifacts(tmp_path)
    path = tmp_path / "outputs" / "shadow_candidates" / "2026-05-04" / "shadow_evaluation.json"
    payload = json.loads(path.read_text())
    payload["strategies"].pop("caerus_orion")
    _write_json(path, payload)

    scoreboard = build_shadow_scoreboard(tmp_path, "2026-05-04")

    assert scoreboard["status"] == "OK"
    assert "Orion:" in scoreboard["text"]
    assert "Artifact status: DEGRADED" in scoreboard["text"]
    assert "shadow_evaluation.json missing strategy caerus_orion" in scoreboard["text"]


def test_trading_confirmation_includes_shadow_snapshot_and_preserves_execution_status(tmp_path: Path, monkeypatch) -> None:
    _write_shadow_artifacts(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    results = {
        "trade_date": "2026-05-04",
        "run_id": "run-1",
        "mode": "PAPER",
        "status": STATUS_EXECUTED,
        "submitted_count": 9,
        "accepted_count": 9,
        "rejected_count": 0,
    }

    subject, body_text, body_html = confirmation._build_confirmation_email(results, tmp_path / "execution_results.json")

    assert "[EXECUTED]" in subject
    assert "Status: EXECUTED" in body_text
    assert "Submitted: 9" in body_text
    assert "Shadow Strategy Snapshot" in body_text
    assert "Orion:" in body_text
    assert "Dynamic Sleeve Inventory" in body_text
    assert "Live Pilot / Account" in body_text
    assert "Shadow Strategy Snapshot" in body_html


def test_trading_confirmation_shadow_snapshot_does_not_use_promotion_language(tmp_path: Path, monkeypatch) -> None:
    _write_shadow_artifacts(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)

    _, body_text, _ = confirmation._build_confirmation_email(
        {
            "trade_date": "2026-05-04",
            "run_id": "run-1",
            "mode": "PAPER",
            "status": STATUS_EXECUTED,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
        },
        tmp_path / "execution_results.json",
    )

    lowered = body_text.lower()
    assert "promote" not in lowered
    assert "replace" not in lowered
    assert "deploy capital" not in lowered


def test_trading_confirmation_renders_reconciliation_ok_with_no_operator_action(tmp_path: Path, monkeypatch) -> None:
    _write_shadow_artifacts(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    _write_json(
        tmp_path / "outputs" / "broker" / "recon_posttrade_2026-05-04.json",
        {"drift_status": "OK_RECONCILED", "share_deltas": []},
    )

    _, body_text, _ = confirmation._build_confirmation_email(
        {
            "trade_date": "2026-05-04",
            "run_id": "run-1",
            "mode": "PAPER",
            "status": STATUS_EXECUTED,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "filled_count": 1,
        },
        tmp_path / "execution_results.json",
    )

    assert "--- Execution Status ---" in body_text
    assert "Filled: 1" in body_text
    assert "--- Reconciliation Status ---" in body_text
    assert "Status: OK_RECONCILED" in body_text
    assert "--- Operator Action Required ---\n- None" in body_text


def test_trading_confirmation_renders_reconciliation_drift_details(tmp_path: Path, monkeypatch) -> None:
    _write_shadow_artifacts(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    _write_json(
        tmp_path / "outputs" / "broker" / "recon_posttrade_2026-05-04.json",
        {
            "drift_status": "DRIFT_DETECTED",
            "share_deltas": [
                {
                    "symbol": "GM",
                    "expected_qty": 11.0,
                    "broker_qty": 15.0,
                    "classification": "QTY_MISMATCH",
                }
            ],
        },
    )

    _, body_text, _ = confirmation._build_confirmation_email(
        {
            "trade_date": "2026-05-04",
            "run_id": "run-1",
            "mode": "PAPER",
            "status": STATUS_EXECUTED,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
        },
        tmp_path / "execution_results.json",
    )

    assert "Status: DRIFT_DETECTED" in body_text
    assert "- GM: expected 11.0, broker 15.0 (QTY_MISMATCH)" in body_text
    assert "Review reconciliation drift before next run." in body_text


def test_trading_confirmation_renders_stale_price_halt_separately(tmp_path: Path, monkeypatch) -> None:
    _write_shadow_artifacts(tmp_path)
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    _write_json(
        tmp_path / "outputs" / "broker" / "recon_posttrade_2026-05-04.json",
        {"drift_status": "OK_RECONCILED", "share_deltas": []},
    )

    _, body_text, _ = confirmation._build_confirmation_email(
        {
            "trade_date": "2026-05-04",
            "run_id": "run-1",
            "mode": "PAPER",
            "status": STATUS_HALTED,
            "submitted_count": 0,
            "accepted_count": 0,
            "rejected_count": 0,
            "halt_reason": "[HALT] stale_prices detected (last_price_date=2026-05-03)",
        },
        tmp_path / "execution_payload.json",
    )

    assert "--- Execution Status ---" in body_text
    assert "Status: HALTED" in body_text
    assert "stale_prices" in body_text
    assert "--- Reconciliation Status ---" in body_text
    assert "Review execution halt before next run." in body_text


def test_trading_confirmation_missing_shadow_diagnostics_does_not_imply_execution_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setattr(confirmation, "_load_performance_data", lambda _trade_date: None)
    _write_json(
        tmp_path / "outputs" / "broker" / "recon_posttrade_2026-05-04.json",
        {"drift_status": "OK_RECONCILED", "share_deltas": []},
    )

    _, body_text, _ = confirmation._build_confirmation_email(
        {
            "trade_date": "2026-05-04",
            "run_id": "run-1",
            "mode": "PAPER",
            "status": STATUS_EXECUTED,
            "submitted_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
        },
        tmp_path / "execution_results.json",
    )

    assert "Status: EXECUTED" in body_text
    assert "Shadow snapshot unavailable: shadow directory missing for 2026-05-04" in body_text
    assert "Review shadow artifact generation or data coverage." in body_text
    assert "Review execution halt before next run." not in body_text


def test_trading_confirmation_broker_fetch_fallback_disabled_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.delenv("ALLOW_CONFIRMATION_BROKER_FETCH", raising=False)

    def _fail_fetch(*_args, **_kwargs):
        raise AssertionError("live broker fetch should be disabled by default")

    monkeypatch.setattr(confirmation, "_fetch_live_broker_snapshot_inputs", _fail_fetch)

    results, path = confirmation._load_broker_results_fallback("2026-05-04", {"run_id": "run-1"})

    assert path == tmp_path / "outputs" / "broker_snapshot" / "broker_snapshot_2026-05-04.json"
    assert results["broker_snapshot_unavailable"] is True
    assert results["halt_reason"] == "broker_snapshot_unavailable_local_artifact_missing"


def test_trading_confirmation_broker_fetch_fallback_enabled_by_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(confirmation, "_REPO_ROOT", tmp_path)
    monkeypatch.setenv("ALLOW_CONFIRMATION_BROKER_FETCH", "1")
    called = {"fetch": False}

    def _fake_fetch(*, report_date: str, order_limit: int):
        called["fetch"] = True
        return (
            {"equity": "10000"},
            [],
            [{"symbol": "AAPL", "status": "filled"}],
            [],
            [],
            "fake",
        )

    def _fake_snapshot(**kwargs):
        return {
            "trade_date": kwargs["report_date"],
            "counts": {"orders_report_date": 1, "fills_report_date": 0},
            "orders_report_date": kwargs["orders_all"],
            "fills_report_date": [],
        }

    monkeypatch.setattr(confirmation, "_fetch_live_broker_snapshot_inputs", _fake_fetch)
    monkeypatch.setattr(confirmation, "_build_live_broker_snapshot_payload", _fake_snapshot)

    results, _path = confirmation._load_broker_results_fallback("2026-05-04", {"run_id": "run-1"})

    assert called["fetch"] is True
    assert results["broker_snapshot_fallback"] is True
    assert results["submitted_count"] == 1
