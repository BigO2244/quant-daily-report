from __future__ import annotations

from pathlib import Path

from research.evidence_hardening import (
    FULL_EVIDENCE,
    LOW_EVIDENCE,
    build_artifact_coverage_matrix,
    evaluate_run_artifact_coverage,
)


def _write(path: Path, text: str = "{}") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")


def test_artifact_coverage_classifies_full_evidence_run(tmp_path: Path) -> None:
    repo = tmp_path
    run = repo / "outputs" / "runs" / "2026-06-19T093000-0400_full"
    _write(run / "execution_payload.json", '{"trade_date":"2026-06-19"}')
    _write(run / "execution_results.json")
    _write(run / "operator_summary.json")
    _write(repo / "outputs" / "precompute" / "2026-06-19" / "planned_execution_payload.json")
    _write(run / "broker" / "recon_posttrade_2026-06-19.json")
    _write(run / "broker" / "orders_2026-06-19.json")
    _write(run / "audit" / "execution_target_attainment_2026-06-19.json")
    _write(run / "audit" / "execution_integrity.json")
    _write(run / "audit" / "execution_reliability_report_2026-06-19.json")

    row = evaluate_run_artifact_coverage(repo, run)

    assert row["coverage_class"] == FULL_EVIDENCE
    assert row["missing_required_dimensions"] == []


def test_artifact_coverage_matrix_flags_low_evidence(tmp_path: Path) -> None:
    run = tmp_path / "outputs" / "runs" / "2026-06-19T093000-0400_low"
    _write(run / "operator_summary.json")

    payload = build_artifact_coverage_matrix(tmp_path)

    assert payload["run_count"] == 1
    assert payload["decision_grade_run_count"] == 0
    assert payload["runs"][0]["coverage_class"] == LOW_EVIDENCE
    assert payload["pilot_capital_blocker"] is True

