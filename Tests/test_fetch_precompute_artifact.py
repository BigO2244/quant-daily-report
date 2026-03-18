from __future__ import annotations

import json
from pathlib import Path

from scripts.fetch_precompute_artifact import (
    artifact_name_for_report_date,
    fetch_precompute_artifact,
)


class _Result:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout


def test_artifact_name_is_report_date_specific() -> None:
    assert artifact_name_for_report_date("2026-03-18") == "alpaca-precompute-2026-03-18"


def test_fetch_precompute_artifact_returns_missing_when_no_successful_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    def _fake_run(args: list[str]) -> _Result:
        assert args[:3] == ["gh", "run", "list"]
        return _Result(stdout="[]")

    monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
    payload = fetch_precompute_artifact(
        report_date="2026-03-18",
        destination=tmp_path / "downloaded",
        repo="owner/repo",
        branch="main",
    )

    assert payload["precompute_bundle_found"] is False
    assert payload["bundle_source"] == "none"
    assert payload["bundle_status"] == "MISSING_PRECOMPUTE_BUNDLE"


def test_fetch_precompute_artifact_restores_bundle_from_artifact(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    download_root = tmp_path / "downloaded" / "alpaca-precompute-2026-03-18" / "outputs"
    bundle_dir = download_root / "precompute" / "2026-03-18"
    workflow_status_dir = download_root / "workflow_status" / "2026-03-18"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    workflow_status_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "contract.json").write_text("{}", encoding="utf-8")
    (bundle_dir / "daily_snapshot.json").write_text("{}", encoding="utf-8")
    (bundle_dir / "signals.json").write_text("{}", encoding="utf-8")
    (bundle_dir / "planned_execution_payload.json").write_text("{}", encoding="utf-8")
    (workflow_status_dir / "precompute_workflow_summary.json").write_text("{}", encoding="utf-8")

    calls: list[list[str]] = []

    def _fake_run(args: list[str]) -> _Result:
        calls.append(args)
        if args[:3] == ["gh", "run", "list"]:
            return _Result(
                stdout='[{"databaseId":12345,"status":"completed","conclusion":"success","headBranch":"main","createdAt":"2026-03-18T11:31:00Z"}]'
            )
        return _Result(stdout="")

    monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
    payload = fetch_precompute_artifact(
        report_date="2026-03-18",
        destination=tmp_path / "downloaded",
        repo="owner/repo",
        branch="main",
    )

    assert any(args[:3] == ["gh", "run", "download"] for args in calls)
    assert payload["precompute_bundle_found"] is True
    assert payload["bundle_source"] == "artifact"
    assert payload["bundle_status"] == "bundle_downloaded"
    assert (tmp_path / "outputs" / "precompute" / "2026-03-18" / "contract.json").exists()


def test_fetch_precompute_artifact_detects_stale_skipped_artifact(monkeypatch, tmp_path: Path) -> None:
    """Artifact was uploaded by a precompute run that the stale guard blocked — no contract.json present."""
    monkeypatch.chdir(tmp_path)
    # Simulate artifact containing only the workflow_status summary (stale guard skipped the planner).
    download_root = tmp_path / "downloaded" / "alpaca-precompute-2026-03-18" / "outputs"
    workflow_status_dir = download_root / "workflow_status" / "2026-03-18"
    workflow_status_dir.mkdir(parents=True, exist_ok=True)
    (workflow_status_dir / "precompute_workflow_summary.json").write_text(
        json.dumps({"bundle_status": "SKIPPED_AS_STALE", "workflow_kind": "precompute"}),
        encoding="utf-8",
    )
    # No contract.json, no bundle files.

    def _fake_run(args: list[str]) -> _Result:
        if args[:3] == ["gh", "run", "list"]:
            return _Result(
                stdout='[{"databaseId":77777,"status":"completed","conclusion":"success","headBranch":"main","createdAt":"2026-03-18T14:00:00Z"}]'
            )
        return _Result(stdout="")

    monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
    payload = fetch_precompute_artifact(
        report_date="2026-03-18",
        destination=tmp_path / "downloaded",
        repo="owner/repo",
        branch="main",
    )

    assert payload["precompute_bundle_found"] is False
    assert payload["bundle_source"] == "none"
    # Must surface the stale-skip status so operators know to use force_refresh_precompute=true.
    assert payload["bundle_status"] == "bundle_skipped_as_stale"


def test_fetch_precompute_artifact_treats_missing_summary_as_invalid(monkeypatch, tmp_path: Path) -> None:
    """Artifact downloaded but has neither contract.json nor a workflow summary — generic invalid."""
    monkeypatch.chdir(tmp_path)
    # Artifact directory exists but is empty after download.
    empty_dir = tmp_path / "downloaded" / "alpaca-precompute-2026-03-18"
    empty_dir.mkdir(parents=True, exist_ok=True)

    def _fake_run(args: list[str]) -> _Result:
        if args[:3] == ["gh", "run", "list"]:
            return _Result(
                stdout='[{"databaseId":88888,"status":"completed","conclusion":"success","headBranch":"main","createdAt":"2026-03-18T14:00:00Z"}]'
            )
        return _Result(stdout="")

    monkeypatch.setattr("scripts.fetch_precompute_artifact._run_command", _fake_run)
    payload = fetch_precompute_artifact(
        report_date="2026-03-18",
        destination=tmp_path / "downloaded",
        repo="owner/repo",
        branch="main",
    )

    assert payload["precompute_bundle_found"] is False
    assert payload["bundle_status"] in {"bundle_invalid", "MISSING_PRECOMPUTE_BUNDLE"}
