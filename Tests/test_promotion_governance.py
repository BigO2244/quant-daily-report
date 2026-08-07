from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.promotion_governance import (  # noqa: E402
    DECISION_BLOCKED,
    DECISION_DEMOTE,
    DECISION_HOLD,
    DECISION_PROMOTE,
    DECISION_PROMOTION_CANDIDATE,
    DECISION_WATCH,
    SCHEMA_VERSION,
    build_promotion_governance,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _window(
    *,
    observation_count: int = 60,
    excess_return_vs_polaris: float = 0.05,
    hit_rate: float = 0.6,
    correlation: float = 0.6,
):
    return {
        "average_daily_contribution": 0.001,
        "average_position_count": 5.0,
        "average_top3_concentration": 0.3,
        "average_top5_concentration": 0.5,
        "confidence": "HIGH",
        "daily_return_correlation_vs_polaris": correlation,
        "excess_return_vs_polaris": excess_return_vs_polaris,
        "excess_return_vs_spy": 0.1,
        "hit_rate": hit_rate,
        "max_drawdown": -0.05,
        "missing_days": 0,
        "observation_count": observation_count,
        "readiness_state": "READY",
        "realized_volatility": 0.2,
        "reason_codes": ["ok"],
        "total_return": 0.1,
        "turnover": 0.0,
        "window_trading_days": observation_count,
    }


def _write_promotion_windows(
    root: Path,
    trade_date: str,
    *,
    lyra_max_obs: int = 60,
    lyra_excess: float = 0.05,
    polaris_excess: float = 0.0,
    orion_excess: float = 0.04,
) -> None:
    payload = {
        "available": True,
        "blockers": [],
        "confidence": "HIGH",
        "date": trade_date,
        "promotion_recommendation": "PROMOTION_REVIEW_READY:caerus_lyra",
        "reason_codes": ["ok"],
        "schema_version": "caerus_promotion_readiness_windows_v1",
        "source_artifacts": [],
        "strategies": {
            "caerus_polaris": {
                "windows": {
                    "20": _window(observation_count=20, excess_return_vs_polaris=polaris_excess),
                    "40": _window(observation_count=40, excess_return_vs_polaris=polaris_excess),
                    "60": _window(observation_count=60, excess_return_vs_polaris=polaris_excess),
                }
            },
            "caerus_orion": {
                "windows": {
                    "20": _window(observation_count=20, excess_return_vs_polaris=orion_excess),
                    "40": _window(observation_count=40, excess_return_vs_polaris=orion_excess),
                    "60": _window(observation_count=60, excess_return_vs_polaris=orion_excess),
                }
            },
            "caerus_lyra": {
                "windows": {
                    "20": _window(observation_count=min(20, lyra_max_obs), excess_return_vs_polaris=lyra_excess),
                    "40": _window(observation_count=min(40, lyra_max_obs), excess_return_vs_polaris=lyra_excess),
                    "60": _window(observation_count=lyra_max_obs, excess_return_vs_polaris=lyra_excess),
                }
            },
        },
        "windows": ["20", "40", "60"],
    }
    _write_json(root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", payload)


def _pair(left: str, right: str, *, flag: str = "STRONG", correlation: float = 0.6, active_share: float = 0.6):
    return {
        "average_active_share_proxy": active_share,
        "behavioral_differentiation_score": 0.5,
        "common_top_contributors": [],
        "common_top_detractors": [],
        "contribution_correlation": 0.2,
        "daily_return_correlation": correlation,
        "differentiation_readiness_flag": flag,
        "factor_exposure_similarity": 0.4,
        "holdings_overlap_percentage": 0.2,
        "left_strategy": left,
        "reason_codes": ["ok"],
        "right_strategy": right,
        "sector_overlap": 0.3,
        "top10_overlap": 0.2,
    }


def _write_differentiation(
    root: Path,
    trade_date: str,
    *,
    lyra_flag: str = "STRONG",
    lyra_correlation: float = 0.6,
    lyra_active_share: float = 0.6,
    orion_flag: str = "WEAK",
    orion_correlation: float = 0.92,
    orion_active_share: float = 0.4,
) -> None:
    """Default fixture mirrors the real-world stance: Lyra is the
    candidate with strong differentiation; Orion is too correlated
    with Polaris to be promoted independently of Lyra."""
    payload = {
        "available": True,
        "blockers": [],
        "confidence": "HIGH",
        "date": trade_date,
        "factor_exposure_available": True,
        "factor_exposure_source_artifacts": [],
        "pairs": [
            _pair("caerus_lyra", "caerus_polaris", flag=lyra_flag, correlation=lyra_correlation, active_share=lyra_active_share),
            _pair("caerus_lyra", "caerus_orion", flag=lyra_flag, correlation=lyra_correlation, active_share=lyra_active_share),
            _pair("caerus_orion", "caerus_polaris", flag=orion_flag, correlation=orion_correlation, active_share=orion_active_share),
        ],
        "position_contributions_available": True,
        "position_contribution_source_artifacts": [],
        "reason_codes": ["ok"],
        "schema_version": "caerus_strategy_differentiation_v1",
        "source_artifacts": [],
    }
    _write_json(root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", payload)


def _risk_strategy(
    *,
    available: bool = True,
    max_name: float = 0.08,
    top3: float = 0.30,
    top5: float = 0.50,
    sector: float = 0.30,
    risk_level: str = "LOW",
):
    return {
        "available": available,
        "cash_unallocated": 0.0,
        "confidence": "HIGH",
        "factor_concentration": {},
        "gross_exposure": 1.0,
        "max_single_name_weight": max_name,
        "missing_sector_coverage_count": 0,
        "net_exposure": 1.0,
        "position_count": 10,
        "reason_codes": ["ok"],
        "risk_level": risk_level,
        "sector_concentration": sector,
        "sector_exposure": {},
        "strategy": "",
        "top10_concentration": 0.7,
        "top3_concentration": top3,
        "top5_concentration": top5,
        "top_holdings": [],
    }


def _write_risk_coverage(
    root: Path,
    trade_date: str,
    *,
    available: bool = True,
    lyra_max_name: float = 0.08,
    lyra_top3: float = 0.30,
) -> None:
    payload = {
        "available": available,
        "confidence": "HIGH",
        "date": trade_date,
        "gross_exposure": 1.0,
        "holdings_source_date": trade_date,
        "max_single_name_weight": 0.10,
        "net_exposure": 1.0,
        "position_count": 30,
        "reason_codes": ["ok"],
        "risk_level": "LOW",
        "schema_version": "caerus_risk_coverage_v1",
        "source_artifacts": [],
        "strategies": {
            "caerus_polaris": {**_risk_strategy(), "strategy": "caerus_polaris"},
            "caerus_orion": {**_risk_strategy(), "strategy": "caerus_orion"},
            "caerus_lyra": {
                **_risk_strategy(max_name=lyra_max_name, top3=lyra_top3),
                "strategy": "caerus_lyra",
            },
        },
        "strategies_covered": ["caerus_polaris", "caerus_orion", "caerus_lyra"],
        "top10_concentration": 0.7,
        "top3_concentration": 0.3,
        "top5_concentration": 0.5,
    }
    _write_json(root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json", payload)


def _write_universe(root: Path, trade_date: str, *, available: bool = True, stale: bool = False) -> None:
    payload = {
        "alias_resolutions": [],
        "available": available,
        "blockers": [] if available else ["security_master_missing"],
        "confidence": "HIGH" if available else "LOW",
        "coverage_summary": {},
        "date": trade_date,
        "duplicate_symbols": [],
        "holdings_symbols": ["AAA", "BBB"],
        "planned_symbols": ["AAA", "BBB"],
        "reason_codes": ["ok"] if available else ["security_master_missing"],
        "schema_version": "caerus_universe_governance_v1",
        "security_master_asof_date": trade_date,
        "security_master_path": str(root / "data" / "security_master"),
        "source_artifacts": [],
        "stale_universe": stale,
        "symbol_checks": [],
    }
    _write_json(root / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json", payload)


def _write_execution_timing(root: Path, trade_date: str, *, available: bool = True, coverage_ratio: float = 0.9) -> None:
    payload = {
        "available": available,
        "baseline_offset": "T+5m",
        "baseline_time_et": "09:35",
        "best_offset_vs_baseline": "T+10m",
        "buy_notional_evaluated": 1000.0,
        "confidence": "HIGH" if available else "LOW",
        "coverage_ratio": coverage_ratio,
        "date": trade_date,
        "offsets_evaluated": ["T+5m"],
        "reason_codes": ["ok"] if available else ["no_planned_orders"],
        "schema_version": "caerus_execution_timing_counterfactual_v1",
        "sell_notional_evaluated": 1000.0,
        "source_artifacts": [],
        "symbols_evaluated": 5 if available else 0,
        "symbols_missing_bars": [],
        "trade_date": trade_date,
        "worst_offset_vs_baseline": "T+0m",
    }
    _write_json(root / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json", payload)


def _write_position_sizing(root: Path, trade_date: str) -> None:
    payload = {
        "available": True,
        "confidence": "HIGH",
        "date": trade_date,
        "holdings_source_date": trade_date,
        "notes": "",
        "reason_codes": ["ok"],
        "returns_source_date": trade_date,
        "schema_version": "caerus_position_sizing_research_v1",
        "source_artifacts": [],
        "strategies": {},
    }
    _write_json(root / "outputs" / "research" / "position_sizing" / trade_date / "position_sizing_research.json", payload)


def _write_clean_inputs(root: Path, trade_date: str) -> None:
    _write_promotion_windows(root, trade_date)
    _write_differentiation(root, trade_date)
    _write_risk_coverage(root, trade_date)
    _write_universe(root, trade_date)
    _write_execution_timing(root, trade_date)
    _write_position_sizing(root, trade_date)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_clean_candidate_becomes_promote(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["available"] is True
    assert payload["promotion_recommendation"] == "caerus_lyra"
    assert payload["strategies"]["caerus_lyra"]["decision"] == DECISION_PROMOTE
    assert payload["current_control_strategy"] == "caerus_polaris"
    assert payload["strategies"]["caerus_polaris"]["decision"] == DECISION_HOLD


def test_insufficient_60_day_observation_blocks_promote(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    _write_promotion_windows(tmp_path, trade_date, lyra_max_obs=40)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["promotion_recommendation"] == "NO_PROMOTION_RECOMMENDED"
    # With max_obs == 40 the strategy should at least be a candidate, not PROMOTE.
    assert payload["strategies"]["caerus_lyra"]["decision"] == DECISION_PROMOTION_CANDIDATE


def test_observation_below_watch_blocks(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    _write_promotion_windows(tmp_path, trade_date, lyra_max_obs=10)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["strategies"]["caerus_lyra"]["decision"] == DECISION_BLOCKED
    blocker_codes = payload["strategies"]["caerus_lyra"]["reason_codes"]
    assert any("insufficient_observation_window" in code for code in blocker_codes)


def test_weak_differentiation_blocks_promote(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    _write_differentiation(
        tmp_path,
        trade_date,
        lyra_flag="WEAK",
        lyra_correlation=0.95,
        lyra_active_share=0.2,
    )
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["strategies"]["caerus_lyra"]["decision"] != DECISION_PROMOTE
    reasons = payload["strategies"]["caerus_lyra"]["reason_codes"]
    assert any("weak_differentiation" in code for code in reasons)
    assert any("active_share_below_floor" in code for code in reasons)
    assert any("correlation_above_cap_vs_incumbent" in code for code in reasons)


def test_missing_risk_coverage_blocks_promote(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    # Replace risk_coverage with available=False
    _write_risk_coverage(tmp_path, trade_date, available=False)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    # missing risk coverage degrades to BLOCKED (insufficient data, not a perf signal)
    assert payload["strategies"]["caerus_lyra"]["decision"] == DECISION_BLOCKED
    reasons = payload["strategies"]["caerus_lyra"]["reason_codes"]
    assert any("risk" in code for code in reasons)


def test_risk_concentration_blocks_promote(tmp_path):
    """With FR-040 calibrated thresholds, blocking-on-concentration
    requires the measured concentration to exceed the design-aware cap.
    Lyra with position_count=10 is CONCENTRATED (max_name cap 0.15,
    top3 cap 0.60); push max_name and top3 above those new caps so the
    block still fires under calibrated semantics."""
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    _write_risk_coverage(tmp_path, trade_date, lyra_max_name=0.25, lyra_top3=0.70)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["strategies"]["caerus_lyra"]["decision"] != DECISION_PROMOTE
    reasons = payload["strategies"]["caerus_lyra"]["reason_codes"]
    assert any("single_name_concentration_above_calibrated_cap" in code for code in reasons)
    assert any("top3_concentration_above_calibrated_cap" in code for code in reasons)


def test_universe_blocker_blocks_promote(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    _write_universe(tmp_path, trade_date, available=False)
    payload = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert payload["strategies"]["caerus_lyra"]["decision"] != DECISION_PROMOTE
    reasons = payload["strategies"]["caerus_lyra"]["reason_codes"]
    assert any("universe" in code for code in reasons)


def test_missing_inputs_degrade_to_blocked(tmp_path):
    payload = build_promotion_governance(trade_date="2026-06-02", repo_root=tmp_path)
    assert payload["available"] is False
    assert payload["promotion_recommendation"] == "NO_PROMOTION_RECOMMENDED"
    for strategy in ("caerus_polaris", "caerus_orion", "caerus_lyra"):
        assert payload["strategies"][strategy]["decision"] == DECISION_BLOCKED
    assert "missing_promotion_readiness_windows" in payload["reason_codes"]
    assert "missing_risk_coverage" in payload["reason_codes"]


def test_deterministic_strategy_ordering(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    first = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    second = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    assert first == second
    # Challenger ranking is sorted deterministically: by rank_score asc,
    # then -max_observation_count, then strategy name. With the default
    # fixture Lyra (PROMOTE) outranks Orion (HOLD/blocked diff).
    ranking = first["challenger_rankings"]
    assert [row["strategy"] for row in ranking] == [
        "caerus_lyra",
        "caerus_orion",
        "caerus_orion_alpha",
        "caerus_polaris_alpha",
    ]
    assert [row["rank"] for row in ranking] == [1, 2, 3, 4]


def test_artifacts_are_written(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_inputs(tmp_path, trade_date)
    build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    json_path = tmp_path / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.json"
    md_path = tmp_path / "outputs" / "research" / "promotion_governance" / trade_date / "promotion_governance.md"
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "Promotion Governance" in md_path.read_text(encoding="utf-8")
