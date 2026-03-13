import json
from pathlib import Path

import reconciliation


def _load_report(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_post_execution_drift_ok_reconciled(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = reconciliation.write_post_execution_drift_report(
        run_date="2026-03-13",
        expected_positions={"AAPL": 5.0, "MSFT": 3.0},
        actual_positions={"AAPL": 5.0, "MSFT": 3.0},
    )

    report = _load_report(payload["report_path"])
    assert report["drift_status"] == "OK_RECONCILED"
    assert report["verdict"] == "PASS"
    assert report["unexpected_short_positions"] == []
    assert report["qty_mismatches"] == []
    assert report["missing_in_actual"] == []
    assert report["missing_in_expected"] == []
    assert sorted(report["matching_positions"]) == ["AAPL", "MSFT"]


def test_post_execution_drift_unexpected_short(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = reconciliation.write_post_execution_drift_report(
        run_date="2026-03-13",
        expected_positions={"HCA": 0.0, "ROST": 0.0},
        actual_positions={"HCA": -1.0, "ROST": -4.0},
    )

    report = _load_report(payload["report_path"])
    assert report["drift_status"] == "UNEXPECTED_SHORT"
    assert report["manual_intervention_required"] is True
    assert report["duplicate_fill_suspicions"] == ["HCA", "ROST"]
    assert report["repair_suggestions"] == ["BUY 1 HCA", "BUY 4 ROST"]
    assert [row["symbol"] for row in report["unexpected_short_positions"]] == ["HCA", "ROST"]


def test_post_execution_drift_qty_mismatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = reconciliation.write_post_execution_drift_report(
        run_date="2026-03-13",
        expected_positions={"AAPL": 5.0},
        actual_positions={"AAPL": 3.0},
    )

    report = _load_report(payload["report_path"])
    assert report["drift_status"] == "DRIFT_DETECTED"
    assert report["verdict"] == "WARN"
    assert report["qty_mismatches"] == [
        {
            "symbol": "AAPL",
            "expected_qty": 5.0,
            "actual_qty": 3.0,
            "abs_diff": 2.0,
        }
    ]


def test_post_execution_drift_extra_broker_position(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = reconciliation.write_post_execution_drift_report(
        run_date="2026-03-13",
        expected_positions={"AAPL": 5.0},
        actual_positions={"AAPL": 5.0, "NVDA": 2.0},
    )

    report = _load_report(payload["report_path"])
    assert report["drift_status"] == "DRIFT_DETECTED"
    assert report["missing_in_expected"] == ["NVDA"]
    assert report["unexpected_short_positions"] == []


def test_post_execution_drift_missing_expected_broker_position(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    payload = reconciliation.write_post_execution_drift_report(
        run_date="2026-03-13",
        expected_positions={"AAPL": 5.0, "MSFT": 1.0},
        actual_positions={"AAPL": 5.0},
    )

    report = _load_report(payload["report_path"])
    assert report["drift_status"] == "DRIFT_DETECTED"
    assert report["missing_in_actual"] == ["MSFT"]
    assert report["operator_message"] == "Post-execution broker drift detected; expected and actual positions differ."
