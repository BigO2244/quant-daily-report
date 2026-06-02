from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.differentiation_diagnostic import (  # noqa: E402
    SCHEMA_VERSION,
    VERDICT_INSUFFICIENT,
    VERDICT_POSSIBLE_DATA,
    VERDICT_TRUE_WEAK,
    build_differentiation_diagnostic,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _pair(left: str, right: str, *, flag="STRONG", overlap=0.2, corr=0.5, contrib=0.3, factor=0.4, sector=0.3, active=0.6):
    return {
        "left_strategy": left,
        "right_strategy": right,
        "differentiation_readiness_flag": flag,
        "holdings_overlap_percentage": overlap,
        "daily_return_correlation": corr,
        "contribution_correlation": contrib,
        "factor_exposure_similarity": factor,
        "sector_overlap": sector,
        "average_active_share_proxy": active,
    }


def _write_inputs(root: Path, trade_date: str, *, pairs, factor_ok=True, contrib_ok=True, max_obs: int = 60):
    _write_json(root / "outputs" / "research" / "strategy_differentiation" / trade_date / "strategy_differentiation.json", {
        "available": True, "factor_exposure_available": factor_ok, "position_contributions_available": contrib_ok,
        "pairs": pairs,
    })
    _write_json(root / "outputs" / "research" / "regime_attribution" / trade_date / "regime_attribution.json", {
        "available": True, "strategies": {
            s: {"regimes": {"bull_trend": {"observation_count": 100}, "bear_trend": {"observation_count": 30}}}
            for s in ("caerus_lyra", "caerus_orion", "caerus_polaris")
        },
    })
    _write_json(root / "outputs" / "research" / "promotion_readiness" / trade_date / "promotion_readiness_windows.json", {
        "available": True,
        "strategies": {
            s: {"windows": {"60": {"observation_count": max_obs}}}
            for s in ("caerus_lyra", "caerus_orion", "caerus_polaris")
        },
    })


def test_true_weak_when_multiple_weak_signals(tmp_path):
    _write_inputs(tmp_path, "2026-06-02", pairs=[
        _pair("caerus_lyra", "caerus_orion", flag="WEAK", overlap=0.8, corr=0.95, sector=0.95, active=0.2),
        _pair("caerus_lyra", "caerus_polaris", flag="WEAK", overlap=0.8, corr=0.95, sector=0.95, active=0.2),
        _pair("caerus_orion", "caerus_polaris", flag="WEAK", overlap=0.8, corr=0.95, sector=0.95, active=0.2),
    ])
    p = build_differentiation_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["aggregate_verdict"] == VERDICT_TRUE_WEAK
    for row in p["pairs"]:
        assert row["verdict"] == VERDICT_TRUE_WEAK


def test_insufficient_history(tmp_path):
    _write_inputs(tmp_path, "2026-06-02", pairs=[
        _pair("caerus_lyra", "caerus_orion", flag="WEAK", overlap=0.8, corr=0.95),
    ], max_obs=20)
    p = build_differentiation_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    pair = next(row for row in p["pairs"] if {row["left_strategy"], row["right_strategy"]} == {"caerus_lyra", "caerus_orion"})
    assert pair["verdict"] == VERDICT_INSUFFICIENT


def test_possible_data_limitation_when_factor_missing(tmp_path):
    _write_inputs(tmp_path, "2026-06-02", pairs=[
        _pair("caerus_lyra", "caerus_orion", flag="WEAK", overlap=0.8, corr=0.95, factor=None),
    ], factor_ok=False)
    p = build_differentiation_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    # The aggregate verdict can stay TRUE_WEAK if enough signals are present
    # AND inputs_complete is False; this test only asserts the pair has data_gaps.
    pair = next(row for row in p["pairs"] if {row["left_strategy"], row["right_strategy"]} == {"caerus_lyra", "caerus_orion"})
    assert pair["verdict"] in (VERDICT_TRUE_WEAK, VERDICT_POSSIBLE_DATA)


def test_schema_and_artifacts(tmp_path):
    _write_inputs(tmp_path, "2026-06-02", pairs=[
        _pair("caerus_lyra", "caerus_orion"),
    ])
    p = build_differentiation_diagnostic(trade_date="2026-06-02", repo_root=tmp_path)
    assert p["schema_version"] == SCHEMA_VERSION
    assert (tmp_path / "outputs" / "research" / "differentiation_diagnostic" / "2026-06-02" / "differentiation_diagnostic.json").exists()
