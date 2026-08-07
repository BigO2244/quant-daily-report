from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.governance_calibration import (  # noqa: E402
    CALIBRATION_STATUS_CLEAN,
    CALIBRATION_STATUS_RISK,
    DESIGN_CONCENTRATED,
    DESIGN_DIVERSIFIED,
    DESIGN_MICRO,
    DESIGN_STANDARD,
    SCHEMA_VERSION_CALIBRATION,
    SCHEMA_VERSION_RECLASSIFICATION,
    build_governance_calibration,
    build_governance_reclassification,
    calibrated_thresholds_for,
    classify_design,
)
from research.governance_maturity import build_governance_maturity  # noqa: E402
from research.promotion_governance import build_promotion_governance  # noqa: E402
from core.strategy_registry import active_shadow_security_selection_ids  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_risk(root: Path, trade_date: str, strategies: dict[str, dict]) -> None:
    _write_json(
        root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json",
        {
            "available": True,
            "strategies": {
                name: {**{"available": True, "strategy": name}, **row}
                for name, row in strategies.items()
            },
        },
    )


# ---------------------------------------------------------------------------
# Design class classification
# ---------------------------------------------------------------------------

def test_design_class_boundaries():
    assert classify_design(1) == DESIGN_MICRO
    assert classify_design(5) == DESIGN_MICRO
    assert classify_design(6) == DESIGN_CONCENTRATED
    assert classify_design(10) == DESIGN_CONCENTRATED
    assert classify_design(11) == DESIGN_STANDARD
    assert classify_design(25) == DESIGN_STANDARD
    assert classify_design(26) == DESIGN_DIVERSIFIED
    assert classify_design(100) == DESIGN_DIVERSIFIED


def test_calibrated_thresholds_for_design_classes():
    micro = calibrated_thresholds_for(5)
    assert micro["max_single_name_allowed"] == 0.25
    assert micro["top3_allowed"] == 0.75
    concentrated = calibrated_thresholds_for(10)
    assert concentrated["max_single_name_allowed"] == 0.15
    standard = calibrated_thresholds_for(20)
    assert standard["max_single_name_allowed"] == 0.10
    diversified = calibrated_thresholds_for(50)
    assert diversified["max_single_name_allowed"] == 0.07


# ---------------------------------------------------------------------------
# Calibration evaluator
# ---------------------------------------------------------------------------

def test_micro_portfolio_equal_weight_passes_calibrated_limits(tmp_path):
    """5-position equal-weight portfolio: max_name=0.20, top3=0.60, top5=1.00.
    All within MICRO_PORTFOLIO calibrated caps (0.25 / 0.75 / 1.00)."""
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_lyra": {
            "position_count": 5,
            "max_single_name_weight": 0.20,
            "top3_concentration": 0.60,
            "top5_concentration": 1.00,
            "top10_concentration": 1.00,
        },
    })
    p = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    row = next(r for r in p["strategies"] if r["strategy"] == "caerus_lyra")
    assert row["design_class"] == DESIGN_MICRO
    assert row["calibration_status"] == CALIBRATION_STATUS_CLEAN
    assert row["reason_codes"] == ["design_consistent_concentration"]


def test_concentrated_portfolio_evaluated_correctly(tmp_path):
    """10-position equal-weight: max_name=0.10, top3=0.30 — well within
    CONCENTRATED caps (0.15 / 0.60 / 0.85)."""
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_polaris": {
            "position_count": 10,
            "max_single_name_weight": 0.10,
            "top3_concentration": 0.30,
            "top5_concentration": 0.50,
            "top10_concentration": 1.00,
        },
    })
    p = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    row = next(r for r in p["strategies"] if r["strategy"] == "caerus_polaris")
    assert row["design_class"] == DESIGN_CONCENTRATED
    assert row["calibration_status"] == CALIBRATION_STATUS_CLEAN


def test_diversified_portfolio_evaluated_correctly(tmp_path):
    """50-position equal-weight: max_name=0.02 — well within DIVERSIFIED."""
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_big": {
            "position_count": 50,
            "max_single_name_weight": 0.02,
            "top3_concentration": 0.06,
            "top5_concentration": 0.10,
            "top10_concentration": 0.20,
        },
    })
    p = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    row = next(r for r in p["strategies"] if r["strategy"] == "caerus_big")
    assert row["design_class"] == DESIGN_DIVERSIFIED
    assert row["calibration_status"] == CALIBRATION_STATUS_CLEAN


def test_concentration_beyond_calibrated_limit_still_fails(tmp_path):
    """5-position MICRO portfolio with max_name=0.40 (above the 0.25 cap)
    is a TRUE_CONCENTRATION_RISK even after calibration."""
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_lyra": {
            "position_count": 5,
            "max_single_name_weight": 0.40,
            "top3_concentration": 0.80,
            "top5_concentration": 1.00,
            "top10_concentration": 1.00,
        },
    })
    p = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    row = next(r for r in p["strategies"] if r["strategy"] == "caerus_lyra")
    assert row["calibration_status"] == CALIBRATION_STATUS_RISK
    assert any("max_single_name_weight_above_calibrated_cap" in code for code in row["reason_codes"])
    assert any("top3_concentration_above_calibrated_cap" in code for code in row["reason_codes"])


def test_deterministic_classification(tmp_path):
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_lyra": {"position_count": 5, "max_single_name_weight": 0.20, "top3_concentration": 0.60, "top5_concentration": 1.00, "top10_concentration": 1.00},
    })
    a = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    b = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    assert a == b


def test_artifacts_and_schema(tmp_path):
    trade_date = "2026-06-02"
    _write_risk(tmp_path, trade_date, {
        "caerus_lyra": {"position_count": 5, "max_single_name_weight": 0.20, "top3_concentration": 0.60, "top5_concentration": 1.00, "top10_concentration": 1.00},
    })
    p = build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION_CALIBRATION
    assert (tmp_path / "outputs" / "research" / "governance_calibration" / trade_date / "governance_calibration.json").exists()
    assert (tmp_path / "outputs" / "research" / "governance_calibration" / trade_date / "governance_calibration.md").exists()


def test_missing_risk_coverage_degrades_gracefully(tmp_path):
    p = build_governance_calibration(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["available"] is False
    assert "missing_risk_coverage" in p["reason_codes"]


# ---------------------------------------------------------------------------
# Promotion governance integration: 5-position MICRO portfolio must not
# emit a concentration blocker.
# ---------------------------------------------------------------------------

def _write_clean_promotion_inputs(root: Path, trade_date: str, *, lyra_positions: int = 5, lyra_max_name: float = 0.20, lyra_top3: float = 0.60) -> None:
    """Minimal upstream artifacts so promotion_governance can fire all six gates."""
    _write_json(root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", {
        "available": True,
        "strategies": {
            s: {"windows": {
                "20": {"observation_count": 20, "hit_rate": 0.6, "excess_return_vs_polaris": 0.05},
                "40": {"observation_count": 40, "hit_rate": 0.6, "excess_return_vs_polaris": 0.05},
                "60": {"observation_count": 60, "hit_rate": 0.6, "excess_return_vs_polaris": 0.05},
            }} for s in ("caerus_polaris", "caerus_orion", "caerus_lyra")
        },
    })
    _write_json(root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True,
        "pairs": [
            {"left_strategy": "caerus_lyra", "right_strategy": "caerus_polaris", "differentiation_readiness_flag": "STRONG", "daily_return_correlation": 0.5, "average_active_share_proxy": 0.7},
            {"left_strategy": "caerus_lyra", "right_strategy": "caerus_orion", "differentiation_readiness_flag": "STRONG", "daily_return_correlation": 0.5, "average_active_share_proxy": 0.7},
        ],
    })
    _write_risk(root, trade_date, {
        "caerus_polaris": {"position_count": 10, "max_single_name_weight": 0.10, "top3_concentration": 0.30, "top5_concentration": 0.50, "top10_concentration": 1.00, "sector_concentration": 0.30, "risk_level": "LOW"},
        "caerus_orion": {"position_count": 5, "max_single_name_weight": 0.20, "top3_concentration": 0.60, "top5_concentration": 1.00, "top10_concentration": 1.00, "sector_concentration": 0.40, "risk_level": "LOW"},
        "caerus_lyra": {"position_count": lyra_positions, "max_single_name_weight": lyra_max_name, "top3_concentration": lyra_top3, "top5_concentration": 1.00, "top10_concentration": 1.00, "sector_concentration": 0.40, "risk_level": "LOW"},
    })
    _write_json(root / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json", {
        "available": True, "blockers": [], "stale_universe": False, "reason_codes": ["ok"],
    })
    _write_json(root / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json", {
        "available": True, "coverage_ratio": 0.9, "symbols_missing_bars": [], "symbols_evaluated": 50,
    })


def test_promotion_governance_does_not_block_micro_portfolio_for_concentration(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_promotion_inputs(tmp_path, trade_date)
    p = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    risk_gate = p["strategies"]["caerus_lyra"]["gates"]["risk"]
    assert risk_gate["status"] == "PASS"
    assert risk_gate["calibration_status"] == CALIBRATION_STATUS_CLEAN
    assert risk_gate["design_class"] == DESIGN_MICRO
    # No "_above_calibrated_cap" reasons.
    assert all(not r.endswith("_above_calibrated_cap") for r in risk_gate["reason_codes"])


def test_promotion_governance_still_blocks_when_calibrated_cap_exceeded(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_promotion_inputs(tmp_path, trade_date, lyra_positions=5, lyra_max_name=0.40, lyra_top3=0.85)
    p = build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    risk_gate = p["strategies"]["caerus_lyra"]["gates"]["risk"]
    assert risk_gate["status"] == "BLOCKED"
    assert risk_gate["calibration_status"] == "TRUE_CONCENTRATION_RISK"
    assert any(r.endswith("_above_calibrated_cap") for r in risk_gate["reason_codes"])


# ---------------------------------------------------------------------------
# Governance maturity integration: blocker counters reflect calibration.
# ---------------------------------------------------------------------------

def test_governance_maturity_tracks_blocker_breakdown(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_promotion_inputs(tmp_path, trade_date)
    # Build promotion governance + blocker audit first.
    build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    from research.governance_blocker_audit import build_governance_blocker_audit
    build_governance_blocker_audit(trade_date=trade_date, repo_root=tmp_path)
    m = build_governance_maturity(trade_date=trade_date, repo_root=tmp_path)
    assert "blockers_real" in m
    assert "blockers_configuration" in m
    assert "blockers_data_quality" in m
    assert "blockers_observation_window" in m
    # blocker_quality component must be present.
    assert any(c["component"] == "blocker_quality" for c in m["components"])


# ---------------------------------------------------------------------------
# Reclassification artifact (OLD vs NEW)
# ---------------------------------------------------------------------------

def test_reclassification_shows_old_and_new_decisions(tmp_path):
    trade_date = "2026-06-02"
    _write_clean_promotion_inputs(tmp_path, trade_date)
    build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    r = build_governance_reclassification(trade_date=trade_date, repo_root=tmp_path)
    assert r["schema_version"] == SCHEMA_VERSION_RECLASSIFICATION
    assert r["available"] is True
    strategies_seen = {row["strategy"] for row in r["comparisons"]}
    assert strategies_seen == set(active_shadow_security_selection_ids())
    # On the synthetic clean inputs every strategy has 5 or 10 positions.
    # Under OLD fixed caps (0.10 / 0.40 / 0.60), Orion (5 positions × 0.20)
    # and Lyra (5 positions × 0.20) would have been blocked on max_name and
    # top3. Under NEW calibrated caps they pass. Reclassification should
    # surface those changes for at least one strategy.
    changed = [row for row in r["comparisons"] if row["decision_changed"]]
    assert len(changed) >= 1


def test_reclassification_missing_inputs(tmp_path):
    r = build_governance_reclassification(trade_date="2026-06-02", repo_root=tmp_path)
    assert r["available"] is False
    assert "missing_risk_coverage" in r["reason_codes"]


# ---------------------------------------------------------------------------
# Packet integration smoke test
# ---------------------------------------------------------------------------

def test_packet_includes_calibration_and_reclassification_sections(tmp_path):
    from research.review_packet import build_research_review_packet
    trade_date = "2026-06-02"
    _write_clean_promotion_inputs(tmp_path, trade_date)
    build_promotion_governance(trade_date=trade_date, repo_root=tmp_path)
    build_governance_calibration(trade_date=trade_date, repo_root=tmp_path)
    build_governance_reclassification(trade_date=trade_date, repo_root=tmp_path)
    p = build_research_review_packet(trade_date=trade_date, repo_root=tmp_path)
    assert p["sections"]["governance_calibration"]["available"] is True
    assert p["sections"]["governance_reclassification"]["available"] is True
    fcs = p["sections"]["final_control_summary"]
    assert "true_blockers" in fcs
    assert "configuration_blockers" in fcs
    assert "eliminated_blockers" in fcs
    assert "current_blockers" in fcs
    md = (tmp_path / "outputs" / "research_review" / trade_date / "research_review.md").read_text()
    assert "Governance Calibration (FR-040)" in md
    assert "Governance Reclassification" in md
