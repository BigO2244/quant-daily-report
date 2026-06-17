from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.research.build_phoenix_phase_c_liquidity_capacity import (
    _classification,
    _source_column_inventory,
)


def test_source_inventory_detects_missing_liquidity_fields(tmp_path: Path) -> None:
    cache = tmp_path / "sharadar_sep"
    cache.mkdir()
    path = cache / "ABC.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "closeadj", "close"])
        writer.writeheader()
        writer.writerow({"date": "2020-01-02", "closeadj": "10", "close": "10"})

    inventory = _source_column_inventory(cache)

    assert inventory["decision_grade_liquidity_available"] is False
    assert inventory["observed_columns"] == ["close", "closeadj", "date"]
    assert "volume" in inventory["missing_required_liquidity_fields"]
    assert "adv_60d" in inventory["missing_required_liquidity_fields"]


def test_classification_is_pending_when_pit_volume_is_missing() -> None:
    source_inventory = {
        "decision_grade_liquidity_available": False,
        "missing_required_liquidity_fields": ["volume", "dollar_volume"],
    }
    phase_b = {
        "readiness_conclusion": {
            "classification": "PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY",
            "shadow_readiness_work_justified": True,
        }
    }

    result = _classification(source_inventory, phase_b)

    assert result["classification"] == "PENDING_LIQUIDITY"
    assert result["is_shadow_ready"] is False
    assert result["is_not_viable"] is False
    assert "pit_volume_source_missing" in result["reason_codes"]


def test_generated_phase_c_liquidity_artifact_contract() -> None:
    path = Path("outputs/research/phoenix_evidence/phoenix_phase_c_liquidity_capacity_2026-06-17.json")
    assert path.exists(), "Generate with scripts/research/build_phoenix_phase_c_liquidity_capacity.py --date 2026-06-17"
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == "caerus_phoenix_phase_c_liquidity_capacity_v1"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "none"
    assert payload["output"] == "PENDING_LIQUIDITY"
    assert payload["classification"]["is_shadow_ready"] is False
    assert payload["classification"]["is_not_viable"] is False
    assert "pit_volume_source_missing" in payload["classification"]["reason_codes"]

    measurements = payload["measurements"]
    for metric in (
        "adv_participation",
        "position_liquidity",
        "slippage_sensitivity",
        "capacity_limits",
        "crisis_period_liquidity_degradation",
        "implementation_shortfall",
    ):
        assert measurements[metric]["status"] == "BLOCKED"
        assert measurements[metric]["value"] is None
        assert "pit_volume_source_missing" in measurements[metric]["reason_codes"]

    assert measurements["turnover"]["status"] == "MEASURED_FROM_PHASE_B"
    assert payload["pit_safe_inputs"]["source_column_inventory"]["observed_columns"] == ["close", "closeadj", "date"]
