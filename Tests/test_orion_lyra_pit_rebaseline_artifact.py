from __future__ import annotations

import json
from pathlib import Path


def test_orion_lyra_pit_rebaseline_artifact_is_decision_evidence() -> None:
    path = Path("outputs/research/pit_rebaseline/orion_lyra_matched_2026-06-17.json")
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == "caerus_orion_lyra_pit_rebaseline_v1"
    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["matched_pit_date_range"]["holdout_2025_forward"] == "excluded"
    assert payload["matched_pit_date_range"]["matched_return_observations"] >= 2500

    assert payload["inputs"]["universe_method"] == "pit_universe"
    assert payload["inputs"]["universe_family"] == "caerus_large_cap"
    assert payload["inputs"]["membership_sha256"]

    paired = payload["paired_significance"]
    assert paired["observation_count"] == payload["matched_pit_date_range"]["matched_return_observations"]
    assert paired["return_correlation"] is not None
    assert paired["t_stat"] is not None

    assert payload["statistical_conclusion"] == "NO_STATISTICALLY_MEANINGFUL_LEAD"
    assert payload["governance_classification"] == "REDUNDANT_CONTINUE_OBSERVING"

    overlap = payload["holdings_overlap"]
    assert overlap["average_holdings_overlap"] is not None
    assert overlap["average_active_share"] is not None

    assert set(payload["cost_sensitivity"]) == {"0_bps", "10_bps", "25_bps", "50_bps"}
    assert "panic" in payload["regime_decomposition"]
