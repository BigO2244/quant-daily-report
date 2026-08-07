from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_argo_phase_a_evidence_framework import build_argo_phase_a_evidence_framework


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _base_fixture(root: Path) -> None:
    date = "2026-06-17"
    _write_json(
        root / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{date}.json",
        {
            "correlation": 0.92,
            "orion": {"cumulative_return_multiple": 42.14, "turnover": 0.12, "max_drawdown": -0.31},
            "lyra": {"cumulative_return_multiple": 41.63, "turnover": 0.18, "max_drawdown": -0.35},
            "paired_t_stat": 0.08,
        },
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_crisis_recovery_{date}.json",
        {"readiness_classification": "RESEARCH_ONLY_NOT_SHADOW_READY"},
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_b_risk_shaping_{date}.json",
        {"classification": "PHOENIX_RISK_SHAPING_CANDIDATE_PENDING_LIQUIDITY"},
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_c_liquidity_capacity_{date}.json",
        {"decision": {"classification": "PENDING_LIQUIDITY"}},
    )


def test_argo_phase_a_marks_pending_phoenix_liquidity_not_ready(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_a_evidence_framework(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    phoenix = next(row for row in payload["sleeve_scores"] if row["sleeve_id"] == "phoenix")
    assert phoenix["classification"] == "NOT_READY"
    assert phoenix["blockers"] == ["pit_liquidity_source_missing"]
    assert all("qelx06" not in blocker.lower() for blocker in phoenix["blockers"])


def test_argo_phase_a_is_research_only_no_runtime_change(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_a_evidence_framework(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "research_only"
    assert payload["behavior_change_allowed"] is False
    assert payload["argo_role"] == "evidence_consumer_only"
    assert "no allocation" in payload["explicit_non_goals"]
    assert "no execution" in payload["explicit_non_goals"]


def test_argo_phase_a_output_order_is_deterministic(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    first = build_argo_phase_a_evidence_framework(trade_date="2026-06-17", repo_root=tmp_path, write=False)
    second = build_argo_phase_a_evidence_framework(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_argo_phase_a_writes_research_artifacts(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_a_evidence_framework(trade_date="2026-06-17", repo_root=tmp_path, write=True)

    json_path = Path(payload["artifact_paths"]["json"])
    md_path = Path(payload["artifact_paths"]["markdown"])
    assert json_path.exists()
    assert md_path.exists()
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "caerus_argo_phase_a_evidence_framework_v1"
    assert "NO_RUNTIME_CHANGE" in md_path.read_text(encoding="utf-8")
