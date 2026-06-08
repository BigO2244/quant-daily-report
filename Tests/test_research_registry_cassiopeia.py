from __future__ import annotations

import json
from pathlib import Path

from research_registry.research.cassiopeia import build_cassiopeia_model_selection


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _summary(cagr: float, excess: float) -> dict:
    return {
        "strategy_name": "Strategy",
        "summary": {
            "n_days": 300,
            "n_years": 1.2,
            "cagr": cagr,
            "sharpe": 1.1,
            "max_drawdown": -0.1,
            "hit_rate": 0.55,
            "avg_turnover": 0.1,
            "excess_return_vs_spy": excess,
        },
    }


def _base_fixture(root: Path, *, regime_obs: int = 60, governance: dict[str, str] | None = None) -> None:
    trade_date = "2026-06-02"
    _write_json(root / "outputs" / "vix_regime" / "regime_current.json", {"date": trade_date, "regime": "ELEVATED", "vix": 24.0})
    _write_json(
        root / "outputs" / "shadow_candidates" / "performance" / "shadow_summary.json",
        {
            "trade_date": trade_date,
            "strategies": {
                "caerus_polaris": _summary(0.10, 0.02),
                "caerus_orion": _summary(0.20, 0.06),
                "caerus_lyra": _summary(0.30, 0.10),
                "spy_benchmark": _summary(0.08, 0.0),
            },
        },
    )
    _write_json(
        root / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json",
        {
            "date": trade_date,
            "strategies": {
                strategy: {"regimes": {"neutral": {"observation_count": regime_obs, "total_return": total, "hit_rate": 0.55, "max_drawdown": -0.1, "confidence": "HIGH", "reason_codes": ["ok"]}}}
                for strategy, total in {"caerus_polaris": 0.05, "caerus_orion": 0.12, "caerus_lyra": 0.2}.items()
            },
        },
    )
    governance = governance or {"caerus_polaris": "BLOCKED", "caerus_orion": "PASS", "caerus_lyra": "BLOCKED"}
    _write_json(
        root / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json",
        {
            "date": trade_date,
            "strategies": {
                strategy: {"decision": decision, "reason_codes": ["ok"] if decision == "PASS" else ["weak_differentiation"]}
                for strategy, decision in governance.items()
            },
        },
    )
    _write_json(
        root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json",
        {
            "date": trade_date,
            "strategies": {
                strategy: {"windows": {"60": {"readiness_state": "WATCH", "observation_count": 60, "reason_codes": ["ok"]}}}
                for strategy in ["caerus_polaris", "caerus_orion", "caerus_lyra"]
            },
        },
    )


def test_cassiopeia_recommends_only_decision_grade_strategy(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_cassiopeia_model_selection(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["leaderboard_winner"] == "caerus_lyra"
    assert payload["recommended_strategy"] == "caerus_orion"
    assert payload["decision_grade_recommendation"] is True
    assert (tmp_path / "outputs" / "model_quality" / "2026-06-02" / "cassiopeia_model_selection.json").exists()


def test_cassiopeia_no_decision_grade_when_all_blocked(tmp_path: Path) -> None:
    _base_fixture(tmp_path, governance={"caerus_polaris": "BLOCKED", "caerus_orion": "BLOCKED", "caerus_lyra": "BLOCKED"})

    payload = build_cassiopeia_model_selection(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["leaderboard_winner"] == "caerus_lyra"
    assert payload["recommended_strategy"] is None
    assert "NO_DECISION_GRADE_EVIDENCE" in payload["reason_codes"]


def test_cassiopeia_blocks_sparse_regime_evidence(tmp_path: Path) -> None:
    _base_fixture(tmp_path, regime_obs=3, governance={"caerus_polaris": "PASS", "caerus_orion": "PASS", "caerus_lyra": "PASS"})

    payload = build_cassiopeia_model_selection(trade_date="2026-06-02", repo_root=tmp_path)

    assert payload["recommended_strategy"] is None
    assert any(code.startswith("INSUFFICIENT_REGIME_OBSERVATIONS") for row in payload["eligible_strategies"] for code in row["reason_codes"])


def test_cassiopeia_meta_model_is_excluded_from_direct_portfolio_set(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_cassiopeia_model_selection(trade_date="2026-06-02", repo_root=tmp_path)

    cassiopeia = [row for row in payload["excluded_strategies"] if row["strategy"] == "caerus_cassiopeia"][0]
    assert cassiopeia["reason_codes"] == ["META_MODEL_RECOMMENDATION_ONLY"]
