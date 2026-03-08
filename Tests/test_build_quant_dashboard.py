import json
from pathlib import Path
from datetime import datetime, timezone

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


def test_builder_prefers_broker_snapshot_artifact(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-broker-artifact",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )
    _write_json(
        tmp_path / "outputs/broker/broker_snapshot_latest.json",
        {
            "portfolio_value": 101000.25,
            "equity": 101000.25,
            "cash": 20000.0,
            "buying_power": 60000.0,
            "as_of": "2026-03-10T15:55:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()

    broker = model["broker_snapshot"]
    assert broker["source"] == "artifact:outputs/broker/broker_snapshot_latest.json"
    assert broker["portfolio_value"] == 101000.25
    assert model["data_freshness"]["broker_vs_run_alignment"] == "aligned"


def test_builder_derives_and_persists_broker_snapshot(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-broker-derived",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )
    _write_json(
        tmp_path / "outputs/paper_state/canonical_positions.json",
        {
            "positions": {"AAPL": 2.0},
            "position_count": 1,
            "cash": 12000.0,
            "equity": 98000.0,
            "as_of": "2026-03-10T16:00:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()

    broker = model["broker_snapshot"]
    assert str(broker["source"]).startswith("derived:")
    persisted = tmp_path / "outputs/broker/broker_snapshot_latest.json"
    assert persisted.exists()


def test_builder_gracefully_handles_missing_broker_snapshot(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-missing-broker",
            "mode": "shadow",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    broker = model["broker_snapshot"]
    assert broker["status"] == "missing"
    assert model["data_freshness"]["broker_vs_run_alignment"] == "missing"


def test_builder_flags_broker_run_mismatch_as_warning(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-mismatch",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )
    _write_json(
        tmp_path / "outputs/broker/broker_snapshot_latest.json",
        {
            "portfolio_value": 100500.0,
            "equity": 100500.0,
            "cash": 15000.0,
            "buying_power": 55000.0,
            "as_of": "2026-03-11T09:30:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    assert model["data_freshness"]["broker_vs_run_alignment"] == "mismatch"
    exceptions = model["exceptions"]
    broker_exception = next((e for e in exceptions if e.get("category") == "Broker snapshot"), None)
    assert broker_exception is not None
    assert broker_exception["status"] == "warning"


def test_builder_includes_broker_snapshot_and_freshness_fields(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-fields",
            "mode": "paper",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    assert "governed_snapshot" in model
    assert "broker_snapshot" in model
    assert "data_freshness" in model
    assert "broker_vs_run_alignment" in model["data_freshness"]


def test_builder_broker_artifact_sets_authoritative_trust(tmp_path):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-authoritative",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )
    _write_json(
        tmp_path / "outputs/broker/broker_snapshot_latest.json",
        {
            "portfolio_value": 10000.0,
            "equity": 10000.0,
            "cash": 2500.0,
            "buying_power": 5000.0,
            "as_of": "2026-03-10T15:59:00Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    broker = model["broker_snapshot"]
    assert broker["trust_level"] == "authoritative"
    assert broker["suspicious"] is False


def test_builder_flags_suspicious_derived_broker_ratio(tmp_path):
    # Governed run value near 10k.
    perf = tmp_path / "outputs/alpha_assessment/canonical_performance.csv"
    perf.parent.mkdir(parents=True, exist_ok=True)
    perf.write_text(
        "date,strategy_nav,strategy_return\n"
        "2026-03-10,9999.55,0.0\n",
        encoding="utf-8",
    )
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-suspicious-derived",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )
    _write_json(
        tmp_path / "outputs/paper_state/canonical_positions.json",
        {
            "positions": {"AAPL": 2.0},
            "position_count": 1,
            "cash": 91911.93,
            "equity": 99853.01,
            "timestamp_utc": "2026-03-03T16:14:07Z",
        },
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    broker = model["broker_snapshot"]
    freshness = model["data_freshness"]

    assert broker["trust_level"] == "derived"
    assert broker["suspicious"] is True
    assert broker["display_equity"] is None
    assert freshness["suspicious_broker_value"] is True

    broker_exception = next((e for e in model["exceptions"] if e.get("category") == "Broker snapshot"), None)
    assert broker_exception is not None
    assert broker_exception["status"] == "warning"
    assert "Suspicious" in broker_exception["message"]


def test_builder_prefers_live_fetch_over_fallback_derivation(tmp_path, monkeypatch):
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-10",
            "run_id": "run-live-priority",
            "mode": "alpaca",
            "created_at": "2026-03-10T16:00:00Z",
        },
    )

    monkeypatch.setattr(DashboardBuilder, "_artifact_broker_snapshot", lambda self, run_id, report_date: (None, "missing"))
    monkeypatch.setattr(
        DashboardBuilder,
        "_maybe_live_broker_snapshot",
        lambda self, report_date: (
            {
                "portfolio_value": 10010.0,
                "equity": 10010.0,
                "cash": 2500.0,
                "buying_power": 5000.0,
                "market_value": 7510.0,
                "as_of": datetime.now(timezone.utc).isoformat(),
                "source": "live:alpaca_account",
                "source_detail": "live Alpaca account fetch",
                "trust_level": "authoritative",
                "status": "fresh",
                "suspicious": False,
                "confidence_note": "",
                "display_equity": 10010.0,
            },
            "live_fetch",
        ),
    )
    monkeypatch.setattr(
        DashboardBuilder,
        "_derived_broker_snapshot",
        lambda self, execution_payload, nav_df, canonical_positions, report_date: (
            {
                "portfolio_value": 50000.0,
                "equity": 50000.0,
                "cash": 10000.0,
                "buying_power": None,
                "market_value": 40000.0,
                "as_of": "2026-03-10T10:00:00Z",
                "source": "derived:nav_timeseries",
                "source_detail": "derived",
                "trust_level": "derived",
                "status": "fresh",
                "suspicious": False,
                "confidence_note": "",
                "display_equity": 50000.0,
            },
            "derived_nav_timeseries",
        ),
    )

    model = DashboardBuilder(repo_root=tmp_path).build()
    broker = model["broker_snapshot"]
    assert broker["source"] == "live:alpaca_account"
    assert broker["trust_level"] == "authoritative"


def test_run_selection_prefers_latest_successful_over_halted(tmp_path):
    """Test that dashboard selects latest successful run over newer halted run."""
    # Create a successful run
    successful_run_id = "2026-03-02T101004-0500_successful"
    _write_json(
        tmp_path / f"outputs/runs/{successful_run_id}/meta.json",
        {
            "report_date": "2026-03-02",
            "run_id": successful_run_id,
            "mode": "paper",
            "created_at": "2026-03-02T15:10:04Z",
        },
    )
    _write_json(
        tmp_path / f"outputs/runs/{successful_run_id}/snapshots/health_2026-03-02.json",
        {"status": "healthy"},
    )
    _write_json(
        tmp_path / f"outputs/runs/{successful_run_id}/snapshots/integrity_2026-03-02.json",
        {"status": "pass"},
    )
    
    # Create a newer halted/sparse run
    halted_run_id = "2026-03-04T120447-0500_halted"
    _write_json(
        tmp_path / f"outputs/runs/{halted_run_id}/meta.json",
        {
            "report_date": "2026-03-04",
            "run_id": halted_run_id,
            "mode": "paper",
            "created_at": "2026-03-04T17:04:47Z",
        },
    )
    _write_json(tmp_path / f"outputs/runs/{halted_run_id}/manifest.json", {})
    
    # Latest.json points to the newer halted run
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-04",
            "run_id": halted_run_id,
            "mode": "paper",
            "created_at": "2026-03-04T17:04:47Z",
        },
    )
    
    model = DashboardBuilder(repo_root=tmp_path).build()
    
    # Should select the successful run, not the halted one
    selected_run_meta = model["run_meta"]["selected_governed_run"]
    assert selected_run_meta["run_id"] == successful_run_id
    # Updated assertion for new selection logic that identifies mode
    assert "viable" in selected_run_meta["selection_reason"].lower()
    
    # Latest attempted should still be recorded
    latest_attempted_meta = model["run_meta"]["latest_attempted_run"]
    assert latest_attempted_meta["run_id"] == halted_run_id


def test_run_selection_metadata_included(tmp_path):
    """Test that run selection metadata is included in output."""
    run_id = "2026-03-04T120447-0500_test"
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-04",
            "run_id": run_id,
            "mode": "paper",
            "created_at": "2026-03-04T17:04:47Z",
        },
    )
    _write_json(
        tmp_path / f"outputs/runs/{run_id}/meta.json",
        {
            "report_date": "2026-03-04",
            "run_id": run_id,
            "mode": "paper",
        },
    )
    _write_json(tmp_path / f"outputs/runs/{run_id}/snapshots/health_2026-03-04.json", {"status": "healthy"})
    _write_json(tmp_path / f"outputs/runs/{run_id}/snapshots/integrity_2026-03-04.json", {"status": "pass"})
    
    model = DashboardBuilder(repo_root=tmp_path).build()
    
    assert "latest_attempted_run" in model["run_meta"]
    assert "selected_governed_run" in model["run_meta"]
    assert model["run_meta"]["latest_attempted_run"]["run_id"] == run_id
    assert model["run_meta"]["selected_governed_run"]["run_id"] == run_id
    assert "selection_reason" in model["run_meta"]["selected_governed_run"]


def test_chart_metadata_included(tmp_path):
    """Test that chart metadata is included in series output."""
    run_id = "run-chart-test"
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-04",
            "run_id": run_id,
            "mode": "paper",
        },
    )
    
    model = DashboardBuilder(repo_root=tmp_path).build()
    
    assert "chart_metadata" in model["series"]
    chart_meta = model["series"]["chart_metadata"]
    assert "nav_chart" in chart_meta
    assert "daily_returns_chart" in chart_meta
    assert "excess_returns_chart" in chart_meta
    
    # Check structure
    assert "x_axis_label" in chart_meta["nav_chart"]
    assert "y_axis_label" in chart_meta["nav_chart"]
    assert chart_meta["daily_returns_chart"]["baseline"] == 0.0
    assert chart_meta["excess_returns_chart"]["baseline"] == 0.0


def test_activity_includes_source_context(tmp_path):
    """Test that activity section includes source run context."""
    run_id = "run-activity-test"
    _write_json(
        tmp_path / "outputs/latest.json",
        {
            "report_date": "2026-03-04",
            "run_id": run_id,
            "mode": "paper",
        },
    )
    
    model = DashboardBuilder(repo_root=tmp_path).build()
    
    activity = model["activity"]
    assert "source_run_id" in activity
    assert "source_report_date" in activity
    assert "note" in activity

