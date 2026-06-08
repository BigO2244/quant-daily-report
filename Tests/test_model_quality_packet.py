from __future__ import annotations

import json
from pathlib import Path

from research_registry.research.model_quality_packet import SECTION_FILES, build_model_quality_packet


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def test_model_quality_packet_aggregates_sections(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    root = tmp_path / "outputs" / "model_quality" / trade_date
    for name, filename in SECTION_FILES.items():
        payload = {"date": trade_date, "status": "OK", "available": True, "reason_codes": ["ok"]}
        if name == "argo_regime_selection":
            payload["decision_grade_recommendation"] = False
        if name == "model_tournament":
            payload["current_leader"] = "caerus_orion"
            payload["decision_grade_leader"] = None
        _write_json(root / filename, payload)

    packet = build_model_quality_packet(trade_date=trade_date, repo_root=tmp_path)

    assert packet["available"] is True
    assert packet["executive_summary"]["strategy_change_decision_grade"] is False
    assert "NO_DECISION_GRADE_STRATEGY_CHANGE" in packet["reason_codes"]
    assert (root / "model_quality_packet.json").exists()


def test_model_quality_packet_missing_sections_degrade(tmp_path: Path) -> None:
    packet = build_model_quality_packet(trade_date="2026-06-02", repo_root=tmp_path)

    assert packet["available"] is False
    assert packet["status"] == "PARTIAL"
    assert any(code.startswith("MISSING_SECTION:") for code in packet["reason_codes"])


def test_model_quality_packet_optional_sections_do_not_block_availability(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    root = tmp_path / "outputs" / "model_quality" / trade_date
    for name, filename in SECTION_FILES.items():
        payload = {"date": trade_date, "status": "OK", "available": True, "reason_codes": ["ok"]}
        if name == "argo_regime_selection":
            payload["decision_grade_recommendation"] = False
        if name == "model_tournament":
            payload["current_leader"] = "caerus_lyra"
            payload["decision_grade_leader"] = None
        _write_json(root / filename, payload)

    packet = build_model_quality_packet(trade_date=trade_date, repo_root=tmp_path)

    assert packet["available"] is True
    assert packet["sections"]["portfolio_history_freshness"]["optional"] is True
    assert packet["sections"]["security_master_diagnostics"]["optional"] is True
    assert packet["sections"]["phoenix_phase_b_review"]["optional"] is True
    assert packet["sections"]["strategy_differentiation_deep_dive"]["optional"] is True
    assert packet["sections"]["argo_phase_b_validation"]["optional"] is True
    assert packet["sections"]["multi_asset_research_framework"]["optional"] is True
    assert packet["sections"]["dashboard_decision_grade"]["optional"] is True
    assert not any(code.startswith("MISSING_SECTION:portfolio_history_freshness") for code in packet["reason_codes"])


def test_model_quality_packet_includes_investment_confidence_optional_sections(tmp_path: Path) -> None:
    trade_date = "2026-06-02"
    root = tmp_path / "outputs" / "model_quality" / trade_date
    for name, filename in SECTION_FILES.items():
        payload = {"date": trade_date, "status": "OK", "available": True, "reason_codes": ["ok"]}
        if name == "argo_regime_selection":
            payload["decision_grade_recommendation"] = False
        if name == "model_tournament":
            payload["current_leader"] = "caerus_lyra"
            payload["decision_grade_leader"] = None
        _write_json(root / filename, payload)
    _write_json(root / "phoenix_phase_b_review.json", {"confidence": "LOW", "decision_grade": False, "reason_codes": ["ok"]})
    _write_json(root / "strategy_differentiation_deep_dive.json", {"retirement_watchlist": [{"strategy_id": "caerus_lyra"}], "reason_codes": ["ok"]})
    _write_json(root / "argo_phase_b_validation.json", {"decision_grade_recommendation": False, "reason_codes": ["ok"]})
    _write_json(root / "multi_asset_research_framework.json", {"status": "PARTIAL", "reason_codes": ["ok"]})
    _write_json(
        tmp_path / "web" / "dashboard" / "dashboard_data.json",
        {"sections": {"decision_grade": {"status": "PARTIAL", "reason_codes": ["MODEL_QUALITY_PACKET_MISSING"]}}},
    )

    packet = build_model_quality_packet(trade_date=trade_date, repo_root=tmp_path)

    assert packet["sections"]["phoenix_phase_b_review"]["confidence"] == "LOW"
    assert packet["sections"]["strategy_differentiation_deep_dive"]["retirement_watchlist"][0]["strategy_id"] == "caerus_lyra"
    assert packet["sections"]["dashboard_decision_grade"]["status"] == "PARTIAL"
    assert packet["executive_summary"]["strategy_differentiation_watchlist_count"] == 1
    assert packet["executive_summary"]["multi_asset_framework_status"] == "PARTIAL"
