from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.governance_maturity import (  # noqa: E402
    SCHEMA_VERSION,
    TIER_DEVELOPING,
    TIER_EMERGING,
    TIER_IMMATURE,
    TIER_MATURE,
    TIER_PROMOTION_READY,
    build_governance_maturity,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_complete_inputs(root: Path, trade_date: str, *, max_obs: int = 60, coverage_ratio: float = 0.9) -> None:
    _write_json(root / "outputs" / "research" / "execution_timing" / trade_date / "execution_timing_summary.json", {
        "available": True, "coverage_ratio": coverage_ratio, "symbols_evaluated": 50, "reason_codes": ["ok"],
    })
    _write_json(root / "outputs" / "research" / "risk_coverage" / trade_date / "risk_coverage.json", {
        "available": True, "confidence": "HIGH",
        "strategies": {s: {"available": True, "confidence": "HIGH"} for s in ("caerus_lyra", "caerus_orion", "caerus_polaris")},
    })
    _write_json(root / "outputs" / "research" / "universe_governance" / trade_date / "universe_governance.json", {
        "available": True, "confidence": "HIGH",
        "symbol_checks": [{"symbol": s, "status": "ok"} for s in ("AAA", "BBB", "CCC")],
    })
    _write_json(root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True, "confidence": "HIGH",
        "factor_exposure_available": True, "position_contributions_available": True,
    })
    _write_json(root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", {
        "available": True,
        "strategies": {s: {"windows": {"60": {"observation_count": max_obs}}} for s in ("caerus_lyra", "caerus_orion")},
    })
    _write_json(root / "outputs" / "attribution" / trade_date / "position_attribution.json", {"available": True})
    _write_json(root / "outputs" / "decision_attribution" / trade_date / "strategy_decision_summary.json", {"available": True})


def test_promotion_ready_when_all_high(tmp_path):
    _write_complete_inputs(tmp_path, "2026-06-02", max_obs=60, coverage_ratio=1.0)
    p = build_governance_maturity(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["tier"] == TIER_PROMOTION_READY


def test_immature_when_nothing_present(tmp_path):
    p = build_governance_maturity(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["tier"] == TIER_IMMATURE


def test_partial_score_below_promotion_ready(tmp_path):
    """Knock down two components and verify the tier slides below
    PROMOTION_READY even though the rest are HIGH."""
    trade_date = "2026-06-02"
    _write_complete_inputs(tmp_path, trade_date, max_obs=30, coverage_ratio=0.3)
    p = build_governance_maturity(trade_date=trade_date, repo_root=tmp_path)
    assert p["tier"] != TIER_PROMOTION_READY
    assert p["total_score"] < 0.90


def test_artifacts_and_schema(tmp_path):
    _write_complete_inputs(tmp_path, "2026-06-02")
    p = build_governance_maturity(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert (tmp_path / "outputs" / "research" / "governance_maturity" / "2026-06-02" / "governance_maturity.json").exists()
    assert len(p["components"]) == 7


def test_deterministic(tmp_path):
    _write_complete_inputs(tmp_path, "2026-06-02")
    a = build_governance_maturity(trade_date="2026-06-02", repo_root=tmp_path)
    b = build_governance_maturity(trade_date="2026-06-02", repo_root=tmp_path)
    assert a == b
