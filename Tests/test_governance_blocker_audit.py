from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.governance_blocker_audit import (  # noqa: E402
    CLASSIFY_CONFIGURATION,
    CLASSIFY_DATA_QUALITY,
    CLASSIFY_OBSERVATION_WINDOW,
    CLASSIFY_REAL,
    SCHEMA_VERSION,
    build_governance_blocker_audit,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _bare_inputs(root: Path, trade_date: str) -> None:
    """Write all five upstream Tier 1/2/3 artifacts that the audit reads."""
    _write_json(root / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json", {
        "available": True, "blockers": [], "reason_codes": ["ok"], "stale_universe": False,
    })
    _write_json(root / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json", {
        "available": True, "coverage_ratio": 0.9, "symbols_missing_bars": [], "reason_codes": ["ok"],
    })
    _write_json(root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True,
        "pairs": [
            {"left_strategy": "caerus_lyra", "right_strategy": "caerus_polaris", "differentiation_readiness_flag": "STRONG"},
        ],
    })
    _write_json(root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json", {
        "available": True, "strategies": {
            "caerus_polaris": {"available": True, "position_count": 10, "max_single_name_weight": 0.10, "top3_concentration": 0.30, "top5_concentration": 0.50, "top10_concentration": 1.0, "sector_concentration": 0.30},
        }
    })
    _write_json(root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", {
        "available": True,
        "strategies": {
            "caerus_lyra": {"windows": {"20": {"observation_count": 20, "hit_rate": 0.6, "excess_return_vs_polaris": 0.02}, "60": {"observation_count": 60, "hit_rate": 0.6, "excess_return_vs_polaris": 0.02}}},
        }
    })


def test_security_master_missing_classified_as_data_quality(tmp_path):
    _bare_inputs(tmp_path, "2026-06-02")
    p = build_governance_blocker_audit(trade_date="2026-06-02", repo_root=tmp_path)
    sm = next(c for c in p["classifications"] if c["blocker"] == "security_master_missing")
    assert sm["classification"] == CLASSIFY_DATA_QUALITY
    assert sm["severity"] in {"HIGH", "MEDIUM"}


def test_planned_payload_missing_classified_as_data_quality(tmp_path):
    _bare_inputs(tmp_path, "2026-06-02")
    p = build_governance_blocker_audit(trade_date="2026-06-02", repo_root=tmp_path)
    payload = next(c for c in p["classifications"] if c["blocker"] == "planned_execution_payload_missing")
    assert payload["classification"] == CLASSIFY_DATA_QUALITY


def test_concentration_designed_classified_as_configuration(tmp_path):
    trade_date = "2026-06-02"
    _bare_inputs(tmp_path, trade_date)
    # Override risk coverage with a 5-position equal-weight strategy.
    _write_json(tmp_path / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json", {
        "available": True,
        "strategies": {
            "caerus_lyra": {"available": True, "position_count": 5, "max_single_name_weight": 0.20, "top3_concentration": 0.60, "top5_concentration": 1.0, "top10_concentration": 1.0, "sector_concentration": 0.40},
        }
    })
    p = build_governance_blocker_audit(trade_date=trade_date, repo_root=tmp_path)
    conc = next(c for c in p["classifications"] if c["blocker"] == "concentration_above_caps")
    assert conc["classification"] == CLASSIFY_CONFIGURATION


def test_observation_window_short_classified_as_observation_window(tmp_path):
    trade_date = "2026-06-02"
    _bare_inputs(tmp_path, trade_date)
    # Short window + weak differentiation → OBSERVATION_WINDOW classification for the weak_differentiation blocker.
    _write_json(tmp_path / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True,
        "pairs": [
            {"left_strategy": "caerus_lyra", "right_strategy": "caerus_polaris", "differentiation_readiness_flag": "WEAK"},
        ],
    })
    _write_json(tmp_path / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", {
        "available": True,
        "strategies": {
            "caerus_lyra": {"windows": {"20": {"observation_count": 20, "hit_rate": 0.5, "excess_return_vs_polaris": 0.01}}},
        }
    })
    p = build_governance_blocker_audit(trade_date=trade_date, repo_root=tmp_path)
    diff = next(c for c in p["classifications"] if c["blocker"] == "weak_differentiation")
    assert diff["classification"] == CLASSIFY_OBSERVATION_WINDOW


def test_weak_differentiation_at_60_days_classified_as_real(tmp_path):
    trade_date = "2026-06-02"
    _bare_inputs(tmp_path, trade_date)
    _write_json(tmp_path / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True,
        "pairs": [
            {"left_strategy": "caerus_lyra", "right_strategy": "caerus_polaris", "differentiation_readiness_flag": "WEAK"},
        ],
    })
    p = build_governance_blocker_audit(trade_date=trade_date, repo_root=tmp_path)
    diff = next(c for c in p["classifications"] if c["blocker"] == "weak_differentiation")
    assert diff["classification"] == CLASSIFY_REAL


def test_artifact_records_schema_and_counts(tmp_path):
    _bare_inputs(tmp_path, "2026-06-02")
    p = build_governance_blocker_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert set(p["classification_counts"].keys()) >= {"REAL", "DATA_QUALITY", "CONFIGURATION", "OBSERVATION_WINDOW"}
    assert (tmp_path / "outputs" / "research" / "governance_blocker_audit" / "2026-06-02" / "governance_blocker_audit.json").exists()
    assert (tmp_path / "outputs" / "research" / "governance_blocker_audit" / "2026-06-02" / "governance_blocker_audit.md").exists()


def test_deterministic_output(tmp_path):
    _bare_inputs(tmp_path, "2026-06-02")
    a = build_governance_blocker_audit(trade_date="2026-06-02", repo_root=tmp_path)
    b = build_governance_blocker_audit(trade_date="2026-06-02", repo_root=tmp_path)
    assert a == b
