import json
from pathlib import Path

from scripts.build_quant_dashboard import DashboardBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_builder_marks_early_preflight_halt_without_execution_fail(tmp_path):
    run_id = "run-abc123"
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": run_id,
            "mode": "paper",
        },
    )
    _write_json(
        tmp_path / "outputs/paper_state/canonical_positions.json",
        {
            "positions": {"AAPL": 1.0},
            "position_count": 1,
            "reason": "bootstrap_from_broker",
        },
    )
    _write_json(
        tmp_path / f"outputs/runs/{run_id}/logs/preflight_failure.json",
        {
            "halt_stage": "pretrade_reconciliation",
            "halt_reason": "pretrade_reconcile_failed:stale_state",
            "block_reason": "stale_state",
            "timestamp_utc": "2026-03-10T14:35:00Z",
            "recommended_action": "Bootstrap canonical snapshot from broker and retry.",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()

    run_meta = model["run_meta"]
    assert run_meta["overall_status"] == "WARNING"

    exceptions = model["exceptions"]
    preflight = next((e for e in exceptions if e.get("category") == "Preflight"), None)
    execution = next((e for e in exceptions if e.get("category") == "Execution"), None)
    reconciliation = next((e for e in exceptions if e.get("category") == "Reconciliation"), None)
    assert preflight is not None
    assert preflight["status"] == "fail"
    assert execution is not None
    assert execution["status"] == "warning"
    assert "did not start" in execution["message"].lower()
    assert reconciliation is not None
    assert reconciliation["status"] == "warning"


def test_builder_infers_early_halt_when_run_folder_is_meta_only(tmp_path):
    run_id = "run-inferred"
    run_root = tmp_path / f"outputs/runs/{run_id}"
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "meta.json").write_text("{}\n", encoding="utf-8")
    (run_root / "manifest.json").write_text("{}\n", encoding="utf-8")
    (run_root / "checksums.sha256").write_text("\n", encoding="utf-8")

    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-04",
            "run_id": run_id,
            "mode": "alpaca",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()

    run_meta = model["run_meta"]
    assert run_meta["overall_status"] == "WARNING"
    assert "inferred" in run_meta["status_banner"].lower()

    exceptions = model["exceptions"]
    preflight = next((e for e in exceptions if e.get("category") == "Preflight"), None)
    execution = next((e for e in exceptions if e.get("category") == "Execution"), None)
    reconciliation = next((e for e in exceptions if e.get("category") == "Reconciliation"), None)
    assert preflight is not None and preflight["status"] == "warning"
    assert execution is not None and execution["status"] == "warning"
    assert reconciliation is not None and reconciliation["status"] == "warning"


def test_builder_falls_back_to_legacy_canonical_snapshot_path(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-legacy",
            "mode": "paper",
        },
    )
    _write_json(
        tmp_path / "canonical-model-snapshot/canonical_positions.json",
        {
            "positions": {"MSFT": 2.0},
            "position_count": 1,
            "reason": "legacy_snapshot",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()

    checks = model["operating_checks"]
    canonical_check = next((c for c in checks if c.get("label") == "Canonical positions present"), None)
    assert canonical_check is not None
    assert canonical_check["status"] == "pass"
    assert canonical_check["detail"] == "canonical-model-snapshot/canonical_positions.json"
