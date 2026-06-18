from __future__ import annotations

import csv
import json
from pathlib import Path

from scripts.research.build_phoenix_phase_c_liquidity_capacity import (
    _candidate_inventory,
    _classification,
    _source_column_inventory,
)


def test_source_inventory_detects_missing_liquidity_fields(tmp_path: Path) -> None:
    path = tmp_path / "pit_liquidity_panel.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "closeadj", "close"])
        writer.writeheader()
        writer.writerow({"date": "2020-01-02", "closeadj": "10", "close": "10"})

    inventory = _source_column_inventory(path)

    assert inventory["decision_grade_liquidity_available"] is False
    assert inventory["observed_columns"] == ["close", "closeadj", "date"]
    assert "volume" in inventory["missing_required_liquidity_fields"]
    assert "ADV_60" in inventory["missing_required_liquidity_fields"]


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

    result = _classification(source_inventory, phase_b, {})

    assert result["classification"] == "PENDING_LIQUIDITY"
    assert result["is_shadow_ready"] is False
    assert result["is_not_viable"] is False
    assert "pit_volume_source_missing" in result["reason_codes"]


def test_candidate_inventory_measures_liquidity_from_panel() -> None:
    import pandas as pd

    events = [{
        "window_id": "w1",
        "entry_date": "2024-01-03",
        "status": "OK",
        "gross_exposure": 0.8,
        "turnover": 1.6,
        "selected": [{"ticker": "AAPL", "target_weight": 0.08, "vol_20d_ann_at_entry": 0.2}],
    }]
    panel = pd.DataFrame([{
        "ticker": "AAPL", "date": "2024-01-03", "closeadj": 20.0, "volume": 300.0,
        "dollar_volume": 6000.0, "ADV_20": 200.0, "ADV_60": 200.0,
        "dollar_ADV_20": 3500.0, "dollar_ADV_60": 4000.0,
    }])

    inv = _candidate_inventory(events, panel, capital=1000.0)

    assert inv["measured_candidate_row_count"] == 1
    assert inv["measurement_coverage"] == 1.0
    assert inv["capacity_at_5pct_adv"]["min"] == 2187.5
    assert inv["candidate_rows_sample"][0]["reason_codes"] == ["ok"]


def test_classification_shadow_ready_when_liquidity_is_decision_grade() -> None:
    source_inventory = {"decision_grade_liquidity_available": True}
    phase_b = {"readiness_conclusion": {"classification": "PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY"}}
    candidate_inventory = {
        "measurement_coverage": 1.0,
        "capacity_at_5pct_adv": {"min": 2_000_000.0},
        "dollar_adv_participation": {"max": 0.01},
        "implementation_shortfall_bps": {"max": 15.0},
    }

    result = _classification(source_inventory, phase_b, candidate_inventory)

    assert result["classification"] == "SHADOW_READY"
    assert result["is_shadow_ready"] is True
    assert "shadow_readiness_review_candidate" in result["reason_codes"]


def test_generated_phase_c_liquidity_artifact_contract() -> None:
    path = Path("outputs/research/phoenix_evidence/phoenix_phase_c_liquidity_capacity_2026-06-17.json")
    assert path.exists(), "Generate with scripts/research/build_phoenix_phase_c_liquidity_capacity.py --date 2026-06-17"
    payload = json.loads(path.read_text())

    assert payload["schema_version"] == "caerus_phoenix_phase_c_liquidity_capacity_v1"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "none"
    assert payload["output"] in {"PENDING_LIQUIDITY", "SHADOW_READY", "NOT_VIABLE"}
    assert payload["classification"]["is_not_viable"] is (payload["output"] == "NOT_VIABLE")

    measurements = payload["measurements"]
    for metric in (
        "adv_participation",
        "position_liquidity",
        "slippage_sensitivity",
        "capacity_limits",
        "crisis_period_liquidity_degradation",
        "implementation_shortfall",
    ):
        assert measurements[metric]["status"] in {"BLOCKED", "MEASURED", "MEASURED_PROXY"}

    assert measurements["turnover"]["status"] == "MEASURED_FROM_PHASE_B"
