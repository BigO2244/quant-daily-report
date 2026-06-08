from __future__ import annotations

import json
from pathlib import Path

from scripts.research.build_dashboard_v1 import DashboardV1Builder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")


def _write_model_quality(root: Path) -> None:
    date = "2026-06-08"
    model_dir = root / "outputs" / "model_quality" / date
    _write_json(
        model_dir / "model_quality_packet.json",
        {
            "date": date,
            "status": "OK",
            "executive_summary": {"strategy_change_decision_grade": True},
            "reason_codes": ["ok"],
        },
    )
    _write_json(
        model_dir / "model_tournament.json",
        {"date": date, "strategies": [{"strategy": "caerus_orion", "decision_grade": True}], "reason_codes": ["ok"]},
    )
    _write_json(
        model_dir / "argo_phase_b_validation.json",
        {
            "date": date,
            "recommendation_confidence": "MEDIUM",
            "decision_grade_recommendation": True,
            "evidence_blockers": [],
            "reason_codes": ["ok"],
        },
    )
    _write_json(
        model_dir / "strategy_differentiation_deep_dive.json",
        {"date": date, "redundancy_classification_counts": {"DISTINCT": 1}, "retirement_watchlist": [], "reason_codes": ["ok"]},
    )
    _write_json(model_dir / "phoenix_phase_b_review.json", {"date": date, "confidence": "LOW", "reason_codes": ["ok"]})
    _write_json(model_dir / "multi_asset_research_framework.json", {"date": date, "status": "DRAFT_RESEARCH", "reason_codes": ["ok"]})


def test_dashboard_decision_grade_section_ready_when_artifacts_present(tmp_path: Path) -> None:
    _write_model_quality(tmp_path)

    section = DashboardV1Builder(tmp_path, report_date="2026-06-08")._build_decision_grade()

    assert section["status"] == "READY"
    assert section["latest_model_quality_date"] == "2026-06-08"
    assert section["promotion_ready_count"] == 1
    assert section["decision_grade_strategy_change"] is True
    assert section["reason_codes"] == ["ok"]


def test_dashboard_decision_grade_missing_model_quality_degrades_visibly(tmp_path: Path) -> None:
    section = DashboardV1Builder(tmp_path, report_date="2026-06-08")._build_decision_grade()

    assert section["status"] == "PARTIAL"
    assert "MODEL_QUALITY_PACKET_MISSING" in section["reason_codes"]
    assert "MODEL_TOURNAMENT_MISSING" in section["reason_codes"]


def test_dashboard_decision_grade_mounts_exist() -> None:
    html = Path("web/dashboard/index.html").read_text(encoding="utf-8")

    assert "Decision Grade / Research Confidence" in html
    assert 'id="decision-grade-summary"' in html
    assert 'id="decision-grade-list"' in html


def test_dashboard_decision_grade_js_renderer_contract() -> None:
    js = Path("web/dashboard/quant_daily_executive.js").read_text(encoding="utf-8")

    assert "function renderDecisionGrade" in js
    assert "payload.sections?.decision_grade" in js
    assert "renderDecisionGrade(payload)" in js
