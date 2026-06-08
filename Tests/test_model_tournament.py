from __future__ import annotations

import json
from pathlib import Path

from research_registry.research.model_tournament import build_model_tournament


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _strategy_summary(excess: float, *, n_days: int = 300) -> dict:
    return {
        "strategy_name": "Strategy",
        "summary": {
            "n_days": n_days,
            "n_years": round(n_days / 252, 4),
            "cumulative_return": 1.0 + excess,
            "excess_return_vs_spy": excess,
            "hit_rate": 0.55,
            "max_drawdown": -0.1,
            "annualised_vol": 0.2,
            "avg_turnover": 0.1,
        },
    }


def _base_fixture(root: Path, *, n_days: int = 300, equal_excess: bool = False) -> None:
    trade_date = "2026-06-02"
    orion_excess = 0.2 if not equal_excess else 0.1
    _write_json(
        root / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": _strategy_summary(0.1, n_days=n_days),
                "caerus_orion": _strategy_summary(orion_excess, n_days=n_days),
                "spy_benchmark": _strategy_summary(0.0, n_days=n_days),
            },
        },
    )
    _write_json(
        root / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json",
        {"date": trade_date, "strategies": {"caerus_polaris": {"decision": "PASS", "reason_codes": ["ok"]}, "caerus_orion": {"decision": "PASS", "reason_codes": ["ok"]}}},
    )
    _write_json(
        root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json",
        {
            "date": trade_date,
            "strategies": {
                "caerus_polaris": {"windows": {"60": {"readiness_state": "WATCH", "observation_count": 60, "reason_codes": ["ok"]}}},
                "caerus_orion": {"windows": {"60": {"readiness_state": "WATCH", "observation_count": 60, "reason_codes": ["ok"]}}},
            },
        },
    )
    _write_json(
        root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json",
        {"date": trade_date, "strategies": {"caerus_polaris": {"top3_concentration": 0.3}, "caerus_orion": {"top3_concentration": 0.6}}},
    )


def test_model_tournament_handles_populated_strategy_records(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_model_tournament(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["current_leader"] == "caerus_orion"
    assert payload["decision_grade_leader"] == "caerus_orion"
    orion = [row for row in payload["strategies"] if row["strategy"] == "caerus_orion"][0]
    assert orion["metrics"]["excess_return_vs_spy"] == 0.2
    assert (tmp_path / "outputs" / "model_quality" / "2026-06-02" / "model_tournament.json").exists()


def test_model_tournament_handles_missing_strategy_history(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_model_tournament(trade_date="2026-06-02", repo_root=tmp_path)

    phoenix = [row for row in payload["strategies"] if row["strategy"] == "caerus_phoenix"][0]
    assert phoenix["status"] == "REGISTERED_INSUFFICIENT_HISTORY"
    assert phoenix["decision_grade"] is False


def test_model_tournament_handles_meta_model_strategy(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_model_tournament(trade_date="2026-06-02", repo_root=tmp_path)

    cassiopeia = [row for row in payload["strategies"] if row["strategy"] == "caerus_cassiopeia"][0]
    assert cassiopeia["status"] == "META_MODEL_RECOMMENDATION_ONLY"
    assert cassiopeia["rankable"] is False


def test_model_tournament_deterministic_ranking_tie_breaks_by_strategy(tmp_path: Path) -> None:
    _base_fixture(tmp_path, equal_excess=True)

    payload = build_model_tournament(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["current_leader"] == "caerus_orion"


def test_model_tournament_no_decision_grade_when_coverage_insufficient(tmp_path: Path) -> None:
    _base_fixture(tmp_path, n_days=20)

    payload = build_model_tournament(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["decision_grade_leader"] is None
    assert "NO_DECISION_GRADE_LEADER" in payload["reason_codes"]
