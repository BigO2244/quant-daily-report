from __future__ import annotations

import csv
import json
from pathlib import Path

from research_registry.research.phoenix_evidence_tracker import build_phoenix_evidence_tracker


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _phoenix(active: bool = True) -> dict:
    return {
        "date": "2026-06-01",
        "active": active,
        "reason_codes": ["ok"] if active else ["NO_CRISIS_REGIME"],
        "target_candidates": [
            {"ticker": "AAA", "target_weight": 0.1, "phoenix_score": 0.9, "reason_codes": ["crisis_reversal_dislocation"]}
        ]
        if active
        else [],
    }


def test_phoenix_evidence_tracker_current_date_no_future_return(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "model_quality" / "2026-06-08" / "phoenix_research.json", _phoenix())

    payload = build_phoenix_evidence_tracker(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["phoenix_active"] is True
    assert payload["realized_return_evidence"]["available"] is False
    assert "FORWARD_RETURN_NOT_YET_OBSERVABLE" in payload["reason_codes"]


def test_phoenix_evidence_tracker_historical_realized_return_when_observable(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "model_quality" / "2026-06-01" / "phoenix_research.json", _phoenix())
    _write_csv(
        tmp_path / "prices.csv",
        [
            {"date": "2026-06-01", "ticker": "AAA", "close": "100"},
            {"date": "2026-06-06", "ticker": "AAA", "close": "110"},
        ],
    )

    payload = build_phoenix_evidence_tracker(
        trade_date="2026-06-08",
        signal_date="2026-06-01",
        as_of_date="2026-06-08",
        repo_root=tmp_path,
        price_path=tmp_path / "prices.csv",
    )

    assert payload["realized_return_evidence"]["available"] is True
    assert payload["realized_return_evidence"]["returns"][0]["realized_return"] == 0.1


def test_phoenix_evidence_tracker_inactive_phoenix_still_logged(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "model_quality" / "2026-06-08" / "phoenix_research.json", _phoenix(active=False))

    payload = build_phoenix_evidence_tracker(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["phoenix_active"] is False
    assert payload["candidate_count"] == 0
    assert "NO_CRISIS_REGIME" in payload["reason_codes"]


def test_phoenix_evidence_tracker_missing_price_data_degrades(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "model_quality" / "2026-06-01" / "phoenix_research.json", _phoenix())

    payload = build_phoenix_evidence_tracker(
        trade_date="2026-06-08",
        signal_date="2026-06-01",
        as_of_date="2026-06-08",
        repo_root=tmp_path,
    )

    assert payload["realized_return_evidence"]["available"] is False
    assert "PRICE_SOURCE_MISSING" in payload["reason_codes"]


def test_phoenix_evidence_tracker_deterministic_output(tmp_path: Path) -> None:
    _write_json(tmp_path / "outputs" / "model_quality" / "2026-06-08" / "phoenix_research.json", _phoenix())

    first = build_phoenix_evidence_tracker(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_phoenix_evidence_tracker(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
