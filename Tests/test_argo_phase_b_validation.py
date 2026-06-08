from __future__ import annotations

import json
from pathlib import Path

from research_registry.research.argo_phase_b_validation import build_argo_phase_b_validation


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _argo_payload(*, decision_grade: bool = True, recommended: str | None = "caerus_orion", source_date: str = "2026-06-08") -> dict:
    return {
        "date": "2026-06-08",
        "strategy_id": "caerus_argo",
        "current_regime": {"regime": "HIGH", "evidence_regime": "high_vol", "source_date": source_date, "reason_codes": ["ok"]},
        "leaderboard_winner": "caerus_lyra",
        "recommended_strategy": recommended,
        "confidence": "MEDIUM" if decision_grade else "LOW",
        "decision_grade_recommendation": decision_grade,
        "source_statuses": [
            {
                "name": "current_regime",
                "status": "PRESENT" if source_date == "2026-06-08" else "STALE",
                "path": "outputs/vix_regime/regime_current.json",
                "source_date": source_date,
                "target_date": "2026-06-08",
                "reason_codes": ["ok"] if source_date == "2026-06-08" else ["SOURCE_DATE_DIFFERS_FROM_TARGET"],
            }
        ],
        "reason_codes": ["ok"] if decision_grade else ["NO_DECISION_GRADE_EVIDENCE"],
    }


def _write_argo(root: Path, payload: dict | None = None) -> None:
    _write_json(root / "outputs" / "model_quality" / "2026-06-08" / "argo_regime_selection.json", payload or _argo_payload())


def _write_regimes(root: Path, regimes: list[tuple[str, str]]) -> None:
    for date, regime in regimes:
        _write_json(root / "outputs" / "vix_regime" / date / "regime_current.json", {"date": date, "regime": regime})


def test_argo_phase_b_stable_regime_fixture(tmp_path: Path) -> None:
    _write_argo(tmp_path)
    _write_regimes(tmp_path, [("2026-06-06", "HIGH"), ("2026-06-07", "HIGH"), ("2026-06-08", "HIGH")])

    payload = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["overlay_id"] == "caerus_argo"
    assert payload["stability_summary"]["stable_regime"] is True
    assert payload["transition_summary"]["transition_count"] == 0
    assert payload["decision_grade_recommendation"] is True


def test_argo_phase_b_regime_transition_fixture(tmp_path: Path) -> None:
    _write_argo(tmp_path)
    _write_regimes(tmp_path, [("2026-06-06", "LOW"), ("2026-06-07", "HIGH"), ("2026-06-08", "HIGH")])

    payload = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["stability_summary"]["stable_regime"] is False
    assert payload["transition_summary"]["transition_count"] == 1
    assert payload["transition_summary"]["transitions"][0]["from_regime"] == "LOW"


def test_argo_phase_b_stale_regime_data(tmp_path: Path) -> None:
    _write_argo(tmp_path, _argo_payload(source_date="2026-06-01"))
    _write_regimes(tmp_path, [("2026-06-01", "HIGH")])

    payload = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["input_freshness"]["status"] == "STALE"
    assert "STALE_REGIME_DATA" in payload["reason_codes"]
    assert payload["decision_grade_recommendation"] is False


def test_argo_phase_b_missing_model_selection_artifact(tmp_path: Path) -> None:
    _write_regimes(tmp_path, [("2026-06-08", "HIGH")])

    payload = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["current_recommendation"] is None
    assert payload["decision_grade_recommendation"] is False
    assert "ARGO_SELECTION_ARTIFACT_MISSING" in payload["reason_codes"]


def test_argo_phase_b_leaderboard_winner_without_decision_grade(tmp_path: Path) -> None:
    _write_argo(tmp_path, _argo_payload(decision_grade=False, recommended=None))
    _write_regimes(tmp_path, [("2026-06-08", "HIGH")])

    payload = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path)

    assert payload["leaderboard_winner"] == "caerus_lyra"
    assert payload["current_recommendation"] is None
    assert payload["decision_grade_recommendation"] is False
    assert "LEADERBOARD_WINNER_NOT_DECISION_GRADE_RECOMMENDATION" in payload["reason_codes"]


def test_argo_phase_b_deterministic_output(tmp_path: Path) -> None:
    _write_argo(tmp_path)
    _write_regimes(tmp_path, [("2026-06-08", "HIGH")])

    first = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path, write=False)
    second = build_argo_phase_b_validation(trade_date="2026-06-08", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
