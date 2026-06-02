from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research.dynamic_strategy_allocation import (  # noqa: E402
    SCHEMA_VERSION,
    STATIC_POLICIES,
    build_dynamic_strategy_allocation,
)
from research.regime_attribution import build_regime_attribution  # noqa: E402


def _write_nav(root: Path, rows: list[dict]) -> None:
    path = root / "outputs" / "shadow_candidates" / "performance" / "shadow_nav_series.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def _nav_rows(
    days: int,
    *,
    polaris_step: float = 0.0005,
    orion_step: float = 0.0008,
    lyra_step: float = 0.001,
    spy_step: float = 0.0004,
    start: str = "2024-01-02",
) -> list[dict]:
    dates = pd.date_range(start, periods=days, freq="B")
    rows = []
    for i, day in enumerate(dates):
        rows.append(
            {
                "date": day.date().isoformat(),
                "caerus_polaris": 1.0 + polaris_step * i,
                "caerus_orion": 1.0 + orion_step * i,
                "caerus_lyra": 1.0 + lyra_step * i,
                "spy_benchmark": 1.0 + spy_step * i,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_static_policy_weights_sum_to_one():
    for name, weights in STATIC_POLICIES.items():
        total = sum(float(w) for w in weights.values())
        assert abs(total - 1.0) < 1e-9, f"{name}: weights sum to {total}"


def test_artifact_records_research_only(tmp_path):
    rows = _nav_rows(120)
    _write_nav(tmp_path, rows)
    payload = build_dynamic_strategy_allocation(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["is_research_only"] is True
    assert payload["production_weights_modified"] is False
    # Recommendation defaults to no allocation change.
    assert payload["allocation_recommendation"] == "no_allocation_change_recommended"


def test_recommendation_only_when_governance_allows(tmp_path):
    rows = _nav_rows(120)
    _write_nav(tmp_path, rows)
    payload_default = build_dynamic_strategy_allocation(
        trade_date=rows[-1]["date"], repo_root=tmp_path
    )
    payload_allowed = build_dynamic_strategy_allocation(
        trade_date=rows[-1]["date"],
        repo_root=tmp_path,
        promotion_governance_allows_change=True,
    )
    assert payload_default["allocation_recommendation"] == "no_allocation_change_recommended"
    assert payload_allowed["allocation_recommendation"] in {row["policy"] for row in payload_allowed["ranking"]}


def test_no_production_files_are_written(tmp_path, monkeypatch):
    rows = _nav_rows(120)
    _write_nav(tmp_path, rows)
    # Sentinels: configs/ and outputs/strategy_weights/ must NOT exist after build.
    build_dynamic_strategy_allocation(
        trade_date=rows[-1]["date"],
        repo_root=tmp_path,
        promotion_governance_allows_change=True,
    )
    assert not (tmp_path / "configs").exists()
    assert not (tmp_path / "outputs" / "strategy_weights").exists()
    # Output directory IS created.
    out_json = tmp_path / "outputs" / "research" / "dynamic_strategy_allocation" / rows[-1]["date"] / "dynamic_strategy_allocation.json"
    assert out_json.exists()


def test_missing_strategy_returns_handled(tmp_path):
    # Build a NAV series with only polaris + spy (orion and lyra missing).
    rows = []
    dates = pd.date_range("2024-01-02", periods=120, freq="B")
    for i, day in enumerate(dates):
        rows.append(
            {
                "date": day.date().isoformat(),
                "caerus_polaris": 1.0 + 0.001 * i,
                "spy_benchmark": 1.0 + 0.0004 * i,
            }
        )
    _write_nav(tmp_path, rows)
    payload = build_dynamic_strategy_allocation(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert "strategy_returns_missing" in payload["reason_codes"]


def test_regime_conditioned_blocked_without_regime_attribution(tmp_path):
    rows = _nav_rows(120)
    _write_nav(tmp_path, rows)
    payload = build_dynamic_strategy_allocation(trade_date=rows[-1]["date"], repo_root=tmp_path)
    regime_policy = next(p for p in payload["policies"] if p["policy"] == "regime_conditioned_research_only")
    assert regime_policy["available"] is False
    assert "regime_attribution_unavailable" in regime_policy["reason_codes"]


def test_regime_conditioned_available_when_regime_present(tmp_path):
    rows = _nav_rows(200)
    _write_nav(tmp_path, rows)
    trade_date = rows[-1]["date"]
    build_regime_attribution(trade_date=trade_date, repo_root=tmp_path)
    payload = build_dynamic_strategy_allocation(trade_date=trade_date, repo_root=tmp_path)
    regime_policy = next(p for p in payload["policies"] if p["policy"] == "regime_conditioned_research_only")
    assert regime_policy["available"] is True
    assert regime_policy["observation_count"] >= 40


def test_deterministic_ranking(tmp_path):
    rows = _nav_rows(120)
    _write_nav(tmp_path, rows)
    first = build_dynamic_strategy_allocation(trade_date=rows[-1]["date"], repo_root=tmp_path)
    second = build_dynamic_strategy_allocation(trade_date=rows[-1]["date"], repo_root=tmp_path)
    assert first == second
    # Ranking should be sorted by risk_adjusted_score descending.
    scores = [row["risk_adjusted_score"] for row in first["ranking"]]
    assert scores == sorted(scores, reverse=True)


def test_weak_evidence_prevents_recommendation(tmp_path):
    rows = _nav_rows(20)  # below MIN_HISTORY_DAYS
    _write_nav(tmp_path, rows)
    payload = build_dynamic_strategy_allocation(
        trade_date=rows[-1]["date"],
        repo_root=tmp_path,
        promotion_governance_allows_change=True,
    )
    # Even with governance permission, weak evidence -> no recommendation.
    assert payload["allocation_recommendation"] == "no_allocation_change_recommended"
    for policy in payload["policies"]:
        if policy["policy"] != "regime_conditioned_research_only":
            assert policy["available"] is False
            assert "insufficient_history" in policy["reason_codes"]


def test_missing_nav_series_degrades_gracefully(tmp_path):
    payload = build_dynamic_strategy_allocation(trade_date="2026-06-02", repo_root=tmp_path)
    assert payload["available"] is False
    assert payload["is_research_only"] is True
    assert payload["production_weights_modified"] is False
    assert "missing_shadow_nav_series" in payload["reason_codes"]


def test_artifacts_are_written(tmp_path):
    rows = _nav_rows(60)
    _write_nav(tmp_path, rows)
    trade_date = rows[-1]["date"]
    build_dynamic_strategy_allocation(trade_date=trade_date, repo_root=tmp_path)
    json_path = tmp_path / "outputs" / "research" / "dynamic_strategy_allocation" / trade_date / "dynamic_strategy_allocation.json"
    md_path = tmp_path / "outputs" / "research" / "dynamic_strategy_allocation" / trade_date / "dynamic_strategy_allocation.md"
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    assert "Research Only" in md_path.read_text(encoding="utf-8")
