from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

from core.step_summary import append_step_summary
from scripts.build_quant_dashboard import DashboardBuilder


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def test_dashboard_prefers_canonical_over_legacy(tmp_path: Path) -> None:
    repo_root = tmp_path
    run_root = repo_root / "outputs" / "runs" / "run_001"

    _write_json(
        repo_root / "outputs" / "latest_run.json",
        {
            "run_id": "run_001",
            "trade_date": "2026-03-09",
            "mode": "PAPER",
            "run_root": "outputs/runs/run_001",
        },
    )
    _write_json(
        run_root / "operator_summary.json",
        {
            "run_id": "run_001",
            "trade_date": "2026-03-09",
            "mode": "PAPER",
            "pretrade_status": "READY",
            "proposed_trades_count": 3,
            "executable_trades_count": 3,
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run_001",
            "status": "EXECUTED",
            "submitted_count": 3,
            "accepted_count": 3,
            "rejected_count": 0,
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run_001",
            "status": "READY",
            "trades": [{"ticker": "AAPL", "side": "BUY", "shares": 1}],
        },
    )
    _write_json(
        repo_root / "outputs" / "execution_email" / "2026-03-09.json",
        {
            "execution_status": "HALTED",
            "halt_reason": "legacy_should_not_win",
        },
    )

    builder = DashboardBuilder(repo_root)
    source, payload = builder._find_latest_execution_payload("2026-03-09", run_id="run_001")

    assert source is not None
    assert source.endswith("operator_summary.json")
    assert isinstance(payload, dict)
    assert payload.get("execution_status") == "EXECUTED"
    assert int(payload.get("submitted_count") or 0) == 3


def test_dashboard_falls_back_to_legacy_when_canonical_missing(tmp_path: Path) -> None:
    repo_root = tmp_path
    _write_json(
        repo_root / "outputs" / "execution_email" / "2026-03-09.json",
        {
            "execution_status": "HALTED",
            "halt_reason": "legacy_fallback",
            "trades": [],
        },
    )

    builder = DashboardBuilder(repo_root)
    source, payload = builder._find_latest_execution_payload("2026-03-09", run_id=None)

    assert source == "outputs/execution_email/2026-03-09.json"
    assert isinstance(payload, dict)
    assert payload.get("execution_status") == "HALTED"


def test_dashboard_precedence_stable_without_operator_summary(tmp_path: Path) -> None:
    repo_root = tmp_path
    run_root = repo_root / "outputs" / "runs" / "run_002"

    _write_json(
        repo_root / "outputs" / "latest_run.json",
        {
            "run_id": "run_002",
            "trade_date": "2026-03-09",
            "mode": "PAPER",
            "run_root": "outputs/runs/run_002",
        },
    )
    _write_json(
        run_root / "execution_results.json",
        {
            "run_id": "run_002",
            "status": "SKIPPED_DUPLICATE",
            "submitted_count": 2,
            "accepted_count": 2,
            "rejected_count": 0,
        },
    )
    _write_json(
        run_root / "execution_payload.json",
        {
            "run_id": "run_002",
            "status": "READY",
            "execution_status": "READY",
            "trades": [],
        },
    )

    builder = DashboardBuilder(repo_root)
    source, payload = builder._find_latest_execution_payload("2026-03-09", run_id="run_002")

    assert source == "outputs/runs/run_002/execution_results.json"
    assert isinstance(payload, dict)
    assert payload.get("execution_status") == "SKIPPED_DUPLICATE"


def test_bootstrap_generator_writes_canonical_when_run_root_available(tmp_path: Path, monkeypatch) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "generate_bootstrap_email_payload.py"

    run_root = tmp_path / "outputs" / "runs" / "bootstrap_run"
    _write_json(
        tmp_path / "outputs" / "latest_run.json",
        {
            "run_id": "bootstrap_run",
            "trade_date": "2026-03-09",
            "mode": "ALPACA",
            "run_root": str(run_root),
        },
    )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPORT_DATE", "2026-03-09")
    monkeypatch.setenv("RUN_ID", "bootstrap_run")
    monkeypatch.setenv("recon_data", json.dumps({"verdict": "FAIL", "diffs": {"AAPL": -1}}))

    runpy.run_path(str(script_path), run_name="__main__")

    legacy_path = tmp_path / "outputs" / "execution_email" / "2026-03-09.json"
    canonical_path = run_root / "execution_payload.json"
    operator_summary_path = run_root / "operator_summary.json"

    assert legacy_path.exists()
    assert canonical_path.exists()
    assert operator_summary_path.exists()

    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert canonical.get("status") == "HALTED"
    assert canonical.get("execution_status") == "HALTED"


def test_step_summary_written_when_env_set(tmp_path: Path, monkeypatch) -> None:
    summary_file = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    wrote = append_step_summary(["### Test", "- run_id: `abc`", "- status: `READY`"])

    assert wrote is True
    content = summary_file.read_text(encoding="utf-8")
    assert "### Test" in content
    assert "run_id: `abc`" in content
