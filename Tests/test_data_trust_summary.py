from __future__ import annotations

from pathlib import Path

from research_data.data_trust import build_data_trust_summary
from research_data.hydration import write_json
from scripts.data_hydration.validate_data_trust_summary import validate_data_trust_summary


def _observability_row(
    dataset_id: str,
    *,
    tier: str = "Tier 2",
    readiness_status: str = "OBSERVE_ONLY",
    validation_status: str = "PASS",
    pit_status: str = "PIT_SAFE_SAMPLE_AS_OF_DATED",
    freshness_status: str = "OK",
    lineage_status: str = "LINEAGE_RECORDED",
    blocker_reason: str = "",
    row_count: int = 1,
) -> dict[str, object]:
    return {
        "dataset_id": dataset_id,
        "dataset_name": dataset_id.replace("_", " ").title(),
        "tier": tier,
        "domain": "test",
        "readiness_status": readiness_status,
        "normalization_stage": "P2",
        "validation_status": validation_status,
        "freshness_status": freshness_status,
        "hydration_status": "OK" if readiness_status == "OBSERVE_ONLY" else "BLOCKED_CREDENTIALS",
        "PIT_safe_status": pit_status,
        "latest_ingestion_timestamp": "2026-06-24T12:00:00Z",
        "lineage_status": lineage_status,
        "artifact_exists": readiness_status == "OBSERVE_ONLY",
        "artifact_path": "data/normalized/test/sample.json" if readiness_status == "OBSERVE_ONLY" else None,
        "row_count": row_count,
        "source_artifact_count": 1 if readiness_status == "OBSERVE_ONLY" else 0,
        "input_artifact_count": 0,
        "blocker_reason": blocker_reason,
    }


def _write_observability(root: Path, rows: list[dict[str, object]]) -> Path:
    path = root / "data/manifests/research_data_observability.json"
    write_json(
        path,
        {
            "schema_version": "research_data_observability_v1",
            "generated_at": "2026-06-24T12:00:00Z",
            "as_of_date": "2026-06-24",
            "runtime_impact": "read_only_observability_no_trading_path_changes",
            "dataset_count": len(rows),
            "datasets": rows,
        },
    )
    return path


def test_data_trust_summary_warns_for_blocked_and_missing_datasets(tmp_path: Path) -> None:
    _write_observability(
        tmp_path,
        [
            _observability_row("ohlcv_prices", tier="Tier 1"),
            _observability_row(
                "analyst_estimate_revisions",
                tier="Tier 3",
                readiness_status="BLOCKED",
                blocker_reason="No approved estimates credentials in current shell.",
                row_count=0,
            ),
            _observability_row(
                "short_interest",
                tier="Tier 3",
                readiness_status="MISSING_ARTIFACT",
                blocker_reason="No stable schema-approved FINRA endpoint configured.",
                row_count=0,
            ),
        ],
    )

    summary = build_data_trust_summary(repo_root=tmp_path)

    assert summary["readiness_status"] == "WARN"
    assert summary["broker_submission_invoked"] is False
    assert summary["warning_count"] == 2
    assert summary["critical_count"] == 0
    assert (tmp_path / "outputs/data_trust/data_trust_summary.json").exists()
    markdown = (tmp_path / "outputs/data_trust/data_trust_summary.md").read_text(encoding="utf-8")
    assert "No approved estimates credentials" in markdown
    assert "Broker submission invoked: false" in markdown


def test_data_trust_summary_fails_on_pit_violation_or_missing_tier1(tmp_path: Path) -> None:
    _write_observability(
        tmp_path,
        [
            _observability_row(
                "security_master_pit",
                tier="Tier 1",
                readiness_status="MISSING_ARTIFACT",
                row_count=0,
            ),
            _observability_row(
                "macro_rates",
                pit_status="PIT_VIOLATION_FUTURE_RELEASE_DATE",
                validation_status="PASS",
            ),
        ],
    )

    summary = build_data_trust_summary(repo_root=tmp_path)

    rows = {row["dataset_id"]: row for row in summary["datasets"]}
    assert summary["readiness_status"] == "FAIL"
    assert rows["security_master_pit"]["risk_level"] == "CRITICAL"
    assert rows["macro_rates"]["risk_level"] == "CRITICAL"


def test_validate_data_trust_summary_accepts_clean_summary(tmp_path: Path) -> None:
    _write_observability(tmp_path, [_observability_row("ohlcv_prices", tier="Tier 1")])
    build_data_trust_summary(repo_root=tmp_path)

    errors = validate_data_trust_summary(tmp_path / "outputs/data_trust/data_trust_summary.json", repo_root=tmp_path)

    assert errors == []


def test_validate_data_trust_summary_detects_observability_drift(tmp_path: Path) -> None:
    source = _write_observability(tmp_path, [_observability_row("ohlcv_prices", tier="Tier 1")])
    build_data_trust_summary(repo_root=tmp_path)
    payload = source.read_text(encoding="utf-8")
    source.write_text(payload.replace("ohlcv_prices", "changed_prices"), encoding="utf-8")

    errors = validate_data_trust_summary(tmp_path / "outputs/data_trust/data_trust_summary.json", repo_root=tmp_path)

    assert "source_observability_sha256 mismatch" in errors
