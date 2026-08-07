from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_argo_phase_b_research_priority import build_argo_phase_b_research_priority


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _base_fixture(root: Path) -> None:
    date = "2026-06-17"
    _write_json(
        root / "outputs" / "research" / "argo" / f"argo_phase_a_evidence_framework_{date}.json",
        {
            "governance_label": "RESEARCH_ONLY",
            "sleeve_scores": [
                {"sleeve_id": "phoenix", "classification": "EXTERNAL_DEPENDENCY_BLOCKED"},
                {"sleeve_id": "orion", "classification": "EVIDENCE_READY"},
            ],
        },
    )
    _write_json(
        root / "outputs" / "research" / "pit_rebaseline" / f"orion_lyra_matched_{date}.json",
        {
            "active_share": {"average": 0.1919},
            "paired_significance": {"paired_t_stat": 0.0809},
        },
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_crisis_recovery_{date}.json",
        {"readiness_classification": "RESEARCH_ONLY_NOT_SHADOW_READY"},
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_b_risk_shaping_{date}.json",
        {
            "best_research_candidate": {
                "classification": "SHADOW_SPEC_CANDIDATE_RESEARCH_ONLY",
                "variant_id": "stop_loss_10pct",
            }
        },
    )
    _write_json(
        root / "outputs" / "research" / "phoenix_evidence" / f"phoenix_phase_c_liquidity_capacity_{date}.json",
        {"classification": "PENDING_LIQUIDITY"},
    )


def test_argo_phase_b_is_advisory_only_no_runtime_change(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    assert payload["governance_label"] == "RESEARCH_ONLY"
    assert payload["execution_impact"] == "NON_EXECUTIONAL"
    assert payload["production_impact"] == "research_only"
    assert payload["behavior_change_allowed"] is False
    assert payload["argo_role"] == "research_prioritization_engine_advisory_only"
    assert "no allocation" in payload["explicit_non_goals"]
    assert "no promotion" in payload["explicit_non_goals"]
    assert "no retirement" in payload["explicit_non_goals"]


def test_argo_phase_b_prioritizes_cassiopeia_after_phoenix_capacity_failure(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    ranking = payload["research_priority_ranking"]
    assert ranking[0]["sleeve_id"] == "cassiopeia"
    assert ranking[0]["priority_classification"] == "BLOCKED_DATA"
    assert ranking[0]["research_priority_rank"] == 1
    assert "event_contract_missing" in ranking[0]["blockers"]
    phoenix = next(row for row in ranking if row["sleeve_id"] == "phoenix")
    assert "capacity_below_5pct_adv_policy" in phoenix["blockers"]
    assert payload["highest_roi_research_task"]["sleeve_id"] == "cassiopeia"


def test_argo_phase_b_marks_lyra_independent_research_as_stop_work(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    lyra = next(row for row in payload["research_priority_ranking"] if row["sleeve_id"] == "lyra")
    assert "merge_watch_redundancy" in lyra["blockers"]
    assert "Stop treating Lyra as a separate promotion candidate" in lyra["research_to_stop"]


def test_argo_phase_b_output_order_is_deterministic(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    first = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=False)
    second = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=False)

    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert [row["research_priority_rank"] for row in first["research_priority_ranking"]] == list(range(1, 8))


def test_argo_phase_b_writes_research_artifacts(tmp_path: Path) -> None:
    _base_fixture(tmp_path)

    payload = build_argo_phase_b_research_priority(trade_date="2026-06-17", repo_root=tmp_path, write=True)

    json_path = Path(payload["artifact_paths"]["json"])
    md_path = Path(payload["artifact_paths"]["markdown"])
    assert json_path.exists()
    assert md_path.exists()
    persisted = json.loads(json_path.read_text(encoding="utf-8"))
    assert persisted["schema_version"] == "caerus_argo_phase_b_research_priority_v1"
    assert "NO_RUNTIME_CHANGE" in md_path.read_text(encoding="utf-8")
